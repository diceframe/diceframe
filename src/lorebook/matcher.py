"""Lorebook 关键词匹配器 —— 统一 activation eligibility pipeline。

所有候选（lexical / fuzzy / semantic-only）都必须汇入同一个 eligibility pipeline：

    candidate scan
    → primary / secondary / regex / case / whole-word
    → enabled
    → candidate-channel gate (vector_only 不得被关键词/递归扫描发现)
    → timed gate (cooldown / delay)，每个候选每次都过
    → visibility (fail closed, before recursion)
    → probability
    → inclusion-group competition
    → entry 真正 activated
    → activated entry 才允许写 timed activation state
    → activated entry content 才允许进入 recursion buffer
    → recursive candidates 再走同一 pipeline
    → budget / final ordering (由调用方负责)

因此被 probability 拒绝或 group 竞争落选的条目不会递归、也不会写 sticky /
cooldown / delay；不可见条目更不会通过 recursion 影响其它可见条目。
"""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any, Callable

from src.lorebook.activation import (
    arm_timed_activation,
    delay_gate_blocked,
    evaluate_probability,
    matched_key_score,
    normalize_primary_match_mode,
    normalize_selective_logic,
    sticky_active,
    timed_gate_blocked,
)
from src.lorebook.budget import entry_sort_key

logger = logging.getLogger("trpg")

MAX_RECURSIVE_DEPTH = 3
MIN_FUZZY_KEY_LEN = 2       # 最短模糊匹配关键词长度

# Deterministic recursion work limits. These bound **runaway recursion**, not the
# initial candidate set: every direct / constant / semantic seed reaches the
# activation and budget ranking, and only recursive expansion is capped. Walking
# the frontier in ``entry_sort_key`` order keeps any cutoff from changing the
# final ordering (a late id can no longer evict a high-priority entry).
MAX_RECURSION_STEPS = 2000      # 递归展开最多评估多少个候选
MAX_ACTIVATED_ENTRIES = 400     # 递归展开期间的 activated 上限
MIN_BUDGET_CANDIDATES = 32      # 预算推导出的上限再低也不少于这个数
BUDGET_CANDIDATE_SLACK = 2      # 预算上界的放宽倍数，只掐真正失控的展开

# ``group_scoring`` 的确定性词汇：其余值一律按「默认竞争」处理。
_GROUP_ALLOW_ALL = frozenset({"all", "allow_all"})
_GROUP_SCORING_ON = frozenset({"score", "matched_keys", "matched", "use_group_scoring"})


class KeywordMatcher:
    """关键词匹配器，支持精确匹配 + 模糊子串回退 + 逻辑门 + 概率 + 分组竞争。"""

    def __init__(self, *, rng: Callable[[], float] | None = None):
        self._index: dict[str, set[str]] = {}
        self._entries: dict[str, dict] = {}
        self._fuzzy_keys: list[str] = []
        self._rng = rng
        # 最近一次 activation 的逐条判定与工作量截断原因（ActivationTrace 用）。
        self.last_decisions: dict[str, dict[str, Any]] = {}
        self.last_cutoff: str = ""

    def build(self, entries: list[dict]) -> None:
        """从条目列表构建索引。每个条目的 keywords 字段为 JSON 数组。"""
        self._index.clear()
        self._entries.clear()
        self._fuzzy_keys.clear()
        for entry in entries:
            eid = entry["id"]
            self._entries[eid] = entry
            keywords = self._keys(entry, "keywords") + self._keys(entry, "secondary_keys")
            for kw in keywords:
                kw = kw.strip()
                if kw:
                    self._index.setdefault(kw, set()).add(eid)
                    if len(kw) >= MIN_FUZZY_KEY_LEN and kw not in self._fuzzy_keys:
                        self._fuzzy_keys.append(kw)
        logger.info("关键词索引已构建: %d 关键词, %d 条目, %d 模糊键, %d 常量",
                     len(self._index), len(self._entries), len(self._fuzzy_keys),
                     len(self._get_constant_ids()))

    def match(self, text: str, *, is_visible: Callable[[dict], bool] | None = None) -> list[dict]:
        """匹配文本中出现的所有关键词（无递归）。常量条目始终包含。"""
        return self._activate(
            text, timed_state=None, is_visible=is_visible,
            is_candidate=None, extra_candidates=None, current_tick=None,
        )

    def match_with_recursive(
        self,
        text: str,
        timed_state: dict[str, dict] | None = None,
        *,
        is_visible: Callable[[dict], bool] | None = None,
        is_candidate: Callable[[dict], bool] | None = None,
        extra_candidates: object = None,
        current_tick: int | None = None,
        max_activated: int | None = None,
        max_steps: int | None = None,
    ) -> list[dict]:
        """统一 activation pipeline（含递归）。

        ``is_visible`` 必须在 recursion 之前生效：不可见条目不得成为结果、不得
        参与递归、也不得写 timed state。``is_candidate`` 只限制本通道的候选发现
        （例如 vector_only 条目不能被关键词通道发现），不影响安全边界。
        ``extra_candidates`` 是其它候选来源（如 semantic 检索）发现的 entry id，
        它们必须走同一套 eligibility。``current_tick`` 是 DiceFrame 的
        authoritative turn tick（``GameInstance.round_number``），用于 ``delay``
        前置门；``None`` 表示调用方没有 tick authority，则跳过该门。
        ``max_activated`` / ``max_steps`` 是 deterministic 的递归工作量上限，
        调用方可按 overall lore budget 收紧（见 ``budget.max_entries_within_budget``）。

        每次调用都会把逐条候选的真实判定记录到 :attr:`last_decisions`，供
        ActivationTrace 使用——trace 的原因必须来自实际执行的判定，不能事后重算。
        """

        return self._activate(
            text, timed_state=timed_state, is_visible=is_visible,
            is_candidate=is_candidate, extra_candidates=extra_candidates,
            current_tick=current_tick, max_activated=max_activated, max_steps=max_steps,
        )

    # ---- 统一 pipeline ------------------------------------------------------

    def _activate(
        self,
        text: str,
        *,
        timed_state: dict[str, dict] | None,
        is_visible: Callable[[dict], bool] | None,
        is_candidate: Callable[[dict], bool] | None,
        extra_candidates: object,
        current_tick: int | None,
        max_activated: int | None = None,
        max_steps: int | None = None,
    ) -> list[dict]:
        visible = is_visible if is_visible is not None else (lambda entry: True)
        discover = is_candidate if is_candidate is not None else (lambda entry: True)
        blocked = self._get_timed_blocked_ids(timed_state)
        sticky = self._get_sticky_active_ids(timed_state)
        step_limit = MAX_RECURSION_STEPS if max_steps is None else max(1, int(max_steps))
        activated_limit = (
            MAX_ACTIVATED_ENTRIES if max_activated is None else max(1, int(max_activated))
        )
        self.last_decisions = {}
        self.last_cutoff = ""

        semantic_seeds = {str(cid) for cid in (extra_candidates or ())} & set(self._entries)
        # lexical_seeds 只记录 keyword 通道（含 sticky / constant）发现的条目，
        # 用于把 timed activation state 的写入权保留给 keyword authority。
        # lexical_discovered 额外保留「被 timed gate 挡掉之前」发现的候选，这样
        # trace 才能说出它们是因为 cooldown / delay 落选，而不是从未被发现。
        lexical_seeds: set[str] = set()
        lexical_discovered: set[str] = set()
        if text and self._index:
            exact = {
                eid for eid in self._candidate_ids(text)
                if discover(self._entries[eid])
            }
            lexical_discovered |= exact
            lexical = exact - blocked
            if not lexical:
                fuzzy = {
                    eid for eid in self._fuzzy_match(text)
                    if discover(self._entries[eid])
                }
                lexical_discovered |= fuzzy
                lexical = fuzzy - blocked
            lexical_seeds |= lexical
        constants = self._get_constant_ids()
        lexical_seeds |= sticky | constants
        lexical_discovered |= sticky | constants
        seeds = (semantic_seeds | lexical_seeds) - blocked

        for eid in sorted((semantic_seeds | lexical_discovered) & blocked):
            entry = self._entries.get(eid)
            if entry is None:
                continue
            state = (timed_state or {}).get(eid)
            self._record(
                eid,
                channel="semantic" if eid in semantic_seeds and eid not in lexical_discovered else "keyword",
                outcome="rejected",
                reason_code="delay" if self._legacy_delay_blocked(state) else "cooldown",
                timed=self._timed_snapshot(eid, entry, timed_state, current_tick),
            )

        activated: set[str] = set()
        evaluated: set[str] = set()
        probability_cache: dict[str, bool] = {}
        group_decided: dict[str, str] = {}
        # frontier: entry id -> 该条目被发现的文本（供 group scoring 使用）。
        frontier: dict[str, str] = {eid: text for eid in sorted(seeds)}
        # scanned: 本轮通过「关键词扫描 activated content」发现的候选。它们属于
        # keyword 通道，因此必须和 initial lexical seeds 一样接受 is_candidate
        # 过滤（例如 vector_only 不得被关键词通道发现）。作者显式声明的
        # triggers_recursive 是显式边，不算关键词发现，不受该过滤限制。
        scanned: set[str] = set()
        parents: dict[str, str] = {}
        depth = 0
        steps = 0

        for eid in seeds:
            channel = "semantic" if eid in semantic_seeds and eid not in lexical_seeds else (
                "constant" if eid in self._get_constant_ids() else
                "sticky" if eid in sticky else "keyword"
            )
            self._record(eid, channel=channel, depth=0)

        # legacy ``triggers_recursive`` 边保留历史 depth 行为（compatibility 层）。
        # canonical / ST 递归（扫描 activated content 发现）不再被固定
        # ``MAX_RECURSIVE_DEPTH`` 截断，它的边界来自 cycle guard、max_steps /
        # max_activated、book recursive_scanning、scan_depth、non_recursable、
        # prevent_further_recursion 与 recursion_level。
        legacy_edges: set[str] = set()

        while frontier:
            if depth >= MAX_RECURSIVE_DEPTH and legacy_edges:
                frontier = {
                    eid: text for eid, text in frontier.items() if eid not in legacy_edges
                }
                legacy_edges = set()
                if not frontier:
                    break
            batch: dict[str, str] = {}
            # frontier 一律按最终 inclusion 顺序（``entry_sort_key``：constant →
            # direct match → priority → insertion order → recursive → semantic-only
            # → id）求值。这样任何 cutoff 都只会砍掉「最终排序里本来也靠后」的
            # 候选，不会因为 id 靠前就挤掉高 priority / constant 条目。
            ordered = sorted(
                frontier,
                key=lambda eid: self._frontier_sort_key(
                    eid, direct=eid in lexical_seeds, recursive=depth > 0,
                ),
            )
            for eid in ordered:
                entry = self._entries.get(eid)
                if entry is None or eid in evaluated:
                    continue
                if depth > 0:
                    # work cutoff 只限制 runaway recursion：initial direct /
                    # constant / semantic seeds 必须全部进入 activation 与预算排序。
                    if steps >= step_limit:
                        self.last_cutoff = "max_steps"
                        break
                    steps += 1
                evaluated.add(eid)
                reason = self._eligibility_reason(
                    entry, depth=depth, visible=visible,
                    discover=discover if eid in scanned else None,
                    timed_state=timed_state, current_tick=current_tick,
                )
                self._record(
                    eid, depth=depth, parent=parents.get(eid, ""),
                    timed=self._timed_snapshot(eid, entry, timed_state, current_tick),
                )
                if reason is not None:
                    self._record(eid, outcome="rejected", reason_code=reason)
                    continue
                if not self._probability_ok(eid, entry, probability_cache):
                    self._record(eid, outcome="rejected", reason_code="probability_rejected")
                    continue
                batch[eid] = frontier[eid]
            winners = self._group_winners(batch, group_decided)
            for eid in batch:
                if eid not in winners:
                    self._record(eid, outcome="rejected", reason_code="group_lost")
            activated |= winners
            for eid in winners:
                self._record(
                    eid, outcome="activated",
                    reason_code="recursive" if self._decision_depth(eid) > 0 else (
                        "semantic" if self._decision_channel(eid) == "semantic" else "keyword"
                    ),
                )
            if depth > 0 and len(activated) >= activated_limit:
                # 递归展开的 budget cutoff：再展开也不可能进入最终预算。
                self.last_cutoff = self.last_cutoff or "max_activated"
                break
            if self.last_cutoff == "max_steps":
                break
            frontier, scanned = {}, set()
            all_children: set[str] = set()
            all_scanned: set[str] = set()
            for eid in sorted(winners):
                children, child_scanned = self._children_of(eid, excluded=evaluated | activated)
                for cid in children:
                    parents.setdefault(cid, eid)
                frontier.update(children)
                all_children |= set(children)
                all_scanned |= child_scanned
                scanned |= child_scanned
            # 只有「没有任何 parent 通过 content 扫描到达」的子条目才算 legacy 边。
            legacy_edges = all_children - all_scanned
            depth += 1

        if self.last_cutoff:
            logger.warning(
                "世界书递归触发 %s 上限（steps=%d, activated=%d）：本轮停止继续展开",
                self.last_cutoff, steps, len(activated),
            )

        # 只有真正 activated 的条目才允许写 timed activation state，且写入权保留给
        # keyword authority：纯 semantic 命中只参与召回，不写 sticky/cooldown。
        if timed_state is not None:
            self._apply_time_effects(
                activated & lexical_seeds, timed_state, current_tick=current_tick,
            )
        result = self._sort_by_tier(activated)
        # 把「这个条目是怎么进来的」带给最终 sorter，让
        # ``entry_sort_key`` 的 direct / recursive / semantic-only 三段真正生效，
        # 而不是永远落在默认值上。
        for row in result:
            eid = str(row.get("id") or "")
            row["_direct_match"] = eid in lexical_seeds
            row["_recursive"] = self._decision_depth(eid) > 0
            row["_semantic_only"] = eid in semantic_seeds and eid not in lexical_seeds
        return result

    def _eligibility_ok(self, entry: dict, **kwargs) -> bool:
        """布尔外观，保留给只关心「过没过」的调用方。"""

        return self._eligibility_reason(entry, **kwargs) is None

    def _eligibility_reason(
        self,
        entry: dict,
        *,
        depth: int,
        visible: Callable[[dict], bool],
        discover: Callable[[dict], bool] | None = None,
        timed_state: dict[str, dict] | None = None,
        current_tick: int | None = None,
    ) -> str | None:
        """enabled + visibility + timed/recursion eligibility，fail closed。

        返回 ``None`` 表示通过，否则返回具体被哪一道门拒绝——ActivationTrace 的
        ``reason_code`` 直接用这个值，因此原因永远来自实际执行的判定。

        timed gate 属于**每一次** candidate eligibility，不是只对 initial seeds
        执行一次——否则 recursion frontier 里的子条目可以绕过自己的 cooldown /
        delay。``discover`` 非 None 时表示该候选来自关键词扫描通道，需要额外接受
        通道过滤。
        """

        if not bool(entry.get("enabled", True)):
            return "disabled"
        if not visible(entry):
            return "hidden"
        if discover is not None and not discover(entry):
            return "vector_channel"
        entry_id = str(entry.get("id") or "")
        state = (timed_state or {}).get(entry_id)
        if timed_gate_blocked(state):
            return "delay" if self._legacy_delay_blocked(state) else "cooldown"
        if delay_gate_blocked(entry, current_tick=current_tick) and not sticky_active(state):
            return "delay"
        recursive_pass = depth > 0
        if bool(entry.get("delay_until_recursion", False)) and not recursive_pass:
            return "delay_until_recursion"
        if recursive_pass:
            # non_recursable 只限制"通过递归被到达"；直接命中的条目仍可传播。
            if bool(entry.get("non_recursable", False)):
                return "non_recursable"
            level = int(entry.get("recursion_level", 0) or 0)
            if level > 0 and depth < level:
                return "recursion_level"
            configured = int(entry.get("scan_depth", 0) or 0)
            if configured > 0 and depth > configured:
                return "scan_depth"
        return None

    @staticmethod
    def _legacy_delay_blocked(state: dict | None) -> bool:
        if not isinstance(state, dict):
            return False
        if str(state.get("status", "")) in ("delayed", "delay"):
            return int(state.get("remaining", 0) or 0) > 0
        return max(0, int(state.get("delay_remaining", 0) or 0)) > 0

    def _timed_snapshot(
        self, eid: str, entry: dict, timed_state: dict[str, dict] | None,
        current_tick: int | None,
    ) -> dict[str, Any]:
        state = (timed_state or {}).get(eid)
        return {
            "sticky_active": sticky_active(state),
            "sticky_remaining": int((state or {}).get("sticky_remaining", 0) or 0),
            "cooldown_blocked": timed_gate_blocked(state) and not self._legacy_delay_blocked(state),
            "cooldown_remaining": int((state or {}).get("cooldown_remaining", 0) or 0),
            "pending_cooldown": int((state or {}).get("pending_cooldown", 0) or 0),
            "delay_blocked": delay_gate_blocked(entry, current_tick=current_tick)
            or self._legacy_delay_blocked(state),
            "delay": max(0, int(entry.get("delay", 0) or 0)),
            "current_tick": current_tick,
        }

    # ---- 逐条候选判定记录（ActivationTrace 的唯一事实来源）-------------------

    def _record(self, eid: str, **fields: Any) -> None:
        row = self.last_decisions.setdefault(
            eid, {"entry_id": eid, "channel": "", "depth": 0, "parent": "",
                  "probability": None, "group": None, "timed": None,
                  "outcome": "candidate", "reason_code": ""},
        )
        for key, value in fields.items():
            if value is not None or key in ("probability", "group", "timed"):
                row[key] = value

    def _decision_depth(self, eid: str) -> int:
        return int(self.last_decisions.get(eid, {}).get("depth", 0) or 0)

    def _decision_channel(self, eid: str) -> str:
        return str(self.last_decisions.get(eid, {}).get("channel", "") or "")

    def _probability_ok(self, eid: str, entry: dict, cache: dict[str, bool]) -> bool:
        """概率判定每轮每条目只 roll 一次；被拒者不递归、不写 timed state。"""

        if eid not in cache:
            accepted, trace = evaluate_probability(entry, rng=self._rng or random.random)
            cache[eid] = bool(accepted)
            self._record(eid, probability=trace)
            if not accepted:
                logger.debug("概率过滤: %s (probability=%s) 未激活",
                             entry.get("name", eid), entry.get("probability", 100))
        return cache[eid]

    def _children_of(self, eid: str, *, excluded: set[str]) -> tuple[dict[str, str], set[str]]:
        """activated entry 的内容才允许进入 recursion buffer。

        返回 ``(children, scanned)``：``scanned`` 是其中通过关键词扫描 content 发现
        的子集，调用方据此对它们施加 keyword 通道过滤。
        """

        entry = self._entries.get(eid)
        if entry is None or bool(entry.get("prevent_further_recursion", False)):
            return {}, set()
        children: dict[str, str] = {}
        scanned: set[str] = set()
        child_text = str(entry.get("content", "") or "")
        if child_text and bool(entry.get("_lorebook_recursive_scanning", True)):
            for cid in sorted(self._candidate_ids(child_text)):
                if cid not in excluded:
                    children[cid] = child_text
                    scanned.add(cid)
        triggers = entry.get("triggers_recursive", [])
        if isinstance(triggers, str):
            try:
                triggers = json.loads(triggers)
            except (json.JSONDecodeError, TypeError):
                triggers = []
        for tid in triggers if isinstance(triggers, (list, tuple, set)) else []:
            tid = str(tid)
            if tid not in excluded and tid in self._entries:
                children[tid] = child_text
                scanned.discard(tid)
        return children, scanned

    # ---- 关键词语义 ---------------------------------------------------------

    @staticmethod
    def _keys(entry: dict, field: str) -> list[str]:
        value = entry.get(field, [])
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                value = [value]
        return [str(item).strip() for item in value or [] if str(item).strip()] if isinstance(value, (list, tuple, set)) else []

    @classmethod
    def _match_keyword(cls, keyword: str, text: str, entry: dict | None = None) -> bool:
        """Match a key with entry-level case, whole-word and regex settings."""
        entry = entry or {}
        case_sensitive = bool(entry.get("case_sensitive", False))
        whole_word = bool(entry.get("match_whole_words", False))
        use_regex = bool(entry.get("use_regex", False))
        # Only the safely-mappable regex subset may run. An entry whose adapter
        # cleared this flag (JavaScript pattern with no faithful Python
        # equivalent) keeps its raw key as data but must never be executed, so the
        # key cannot match. The matcher stays format-neutral: it reads a canonical
        # flag rather than branching on the import source.
        if use_regex and not bool(entry.get("regex_executable", True)):
            return False
        pattern = keyword
        if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
            use_regex = True
            pattern = pattern[1:-1]
        flags = 0 if case_sensitive else re.IGNORECASE
        if whole_word and not use_regex:
            pattern = rf"(?<!\w){re.escape(pattern)}(?!\w)"
            use_regex = True
        if use_regex:
            try:
                return bool(re.search(pattern, text, flags))
            except re.error:
                return False
        return pattern in text if case_sensitive else pattern.casefold() in text.casefold()

    @staticmethod
    def _fuzzy_hit(keyword: str, entry: dict, text: str) -> bool:
        """Fuzzy matching is only a fallback for plain, case-insensitive keys."""

        if entry.get("use_regex") or entry.get("match_whole_words") or str(keyword).startswith("/"):
            return False
        if entry.get("case_sensitive", False):
            return False
        needle = keyword.casefold()
        haystack = text.casefold()
        return any(needle[i:i + 2] in haystack for i in range(len(needle) - 1))

    @staticmethod
    def _primary_mode(entry: dict) -> str:
        """Legacy DiceFrame primary ``match_mode`` (separate from ST logic)."""

        return normalize_primary_match_mode(entry.get("match_mode", "any"))

    @staticmethod
    def _selective_logic(entry: dict) -> str:
        """SillyTavern secondary-key logic: an additional filter, never a primary match."""

        return normalize_selective_logic(entry.get("selective_logic"))

    @classmethod
    def _keyword_decision(cls, entry: dict, text: str, *, fuzzy: bool = False) -> dict[str, Any]:
        """Primary + secondary keyword semantics for one entry — single authority.

        ``_entry_matches`` and the ActivationTrace both read this, so the trace
        can never report a reason that the activation path did not compute.
        """

        primary_keys = cls._keys(entry, "keywords")
        secondary_keys = cls._keys(entry, "secondary_keys")
        mode = cls._primary_mode(entry)
        logic = cls._selective_logic(entry)
        if not primary_keys and not secondary_keys:
            return {"matched": False, "primary_ok": False, "secondary_ok": None,
                    "matched_keys": [], "secondary_matches": [],
                    "mode": mode, "logic": logic}
        # Fuzzy matching is a book/entry-scoped fallback, so it stays opt-out.
        fuzzy = fuzzy and bool(entry.get("_lorebook_fuzzy_enabled", True))
        primary_hits = [
            cls._match_keyword(key, text, entry) or (fuzzy and cls._fuzzy_hit(key, entry, text))
            for key in primary_keys
        ]
        if mode == "all":
            primary_ok = bool(primary_keys) and all(primary_hits)
        elif mode == "not_any":
            primary_ok = not any(primary_hits)
        elif mode == "not_all":
            primary_ok = not (bool(primary_keys) and all(primary_hits))
        else:
            primary_ok = any(primary_hits)
        secondary_ok: bool | None = None
        secondary_hits = [False] * len(secondary_keys)
        # ``selective`` 是 canonical 的「secondary filter 是否启用」开关，与
        # ``secondary_keys`` 是两件事：false 时 keys 仍然保留为数据，但不参与 gate。
        if primary_ok and secondary_keys and bool(entry.get("selective", True)):
            # ST Optional Filter: primary already matched, secondary is a filter.
            secondary_hits = [
                cls._match_keyword(key, text, entry) or (fuzzy and cls._fuzzy_hit(key, entry, text))
                for key in secondary_keys
            ]
            if logic == "and_all":
                secondary_ok = all(secondary_hits)
            elif logic == "not_any":
                secondary_ok = not any(secondary_hits)
            elif logic == "not_all":
                secondary_ok = not all(secondary_hits)
            else:
                secondary_ok = any(secondary_hits)
        matched = bool(primary_ok) and (secondary_ok is not False)
        return {
            "matched": matched,
            "primary_ok": bool(primary_ok),
            "secondary_ok": secondary_ok,
            "matched_keys": [k for k, hit in zip(primary_keys, primary_hits) if hit],
            "secondary_matches": [k for k, hit in zip(secondary_keys, secondary_hits) if hit],
            "mode": mode,
            "logic": logic,
        }

    @classmethod
    def _entry_matches(cls, entry: dict, text: str, *, fuzzy: bool = False) -> bool:
        """Primary semantics + ST selective secondary semantics.

        The primary key must match first. Secondary keys are only an additional
        gate, so a NOT_ANY / NOT_ALL secondary logic can never rescue an entry
        whose primary key did not hit (and can never negate the primary hit).
        """

        return cls._keyword_decision(entry, text, fuzzy=fuzzy)["matched"]

    def keyword_decision(self, entry: dict, text: str) -> dict[str, Any]:
        """Public read-only view of the keyword-channel decision (for ActivationTrace).

        Diagnostics must reuse the activation authority instead of recomputing
        keyword semantics, otherwise the trace can report a different reason.
        """

        return self._keyword_decision(entry, text)

    def _candidate_ids(self, text: str, *, fuzzy: bool = False) -> set[str]:
        return {
            eid for eid, entry in self._entries.items()
            if self._entry_matches(entry, text, fuzzy=fuzzy)
        }

    def _fuzzy_match(self, text: str) -> set[str]:
        return self._candidate_ids(text, fuzzy=True)

    # ---- 分组竞争 -----------------------------------------------------------

    @classmethod
    def _group_names(cls, entry: dict) -> list[str]:
        names = list(cls._keys(entry, "groups"))
        single = str(entry.get("group", "") or "").strip()
        if single:
            names.append(single)
        return list(dict.fromkeys(names))

    @staticmethod
    def _group_allow_all(entry: dict) -> bool:
        return str(entry.get("group_scoring", "") or "").strip().lower() in _GROUP_ALLOW_ALL

    @staticmethod
    def _group_scoring_on(entry: dict) -> bool:
        return str(entry.get("group_scoring", "") or "").strip().lower() in _GROUP_SCORING_ON

    def _score_of(self, eid: str, text: str) -> int:
        entry = self._entries.get(eid, {})
        primary = self._keys(entry, "keywords")
        secondary = self._keys(entry, "secondary_keys")
        return matched_key_score(
            entry,
            primary_hits=[self._match_keyword(key, text, entry) for key in primary],
            secondary_hits=[self._match_keyword(key, text, entry) for key in secondary],
        )

    def _group_winners(self, batch: dict[str, str], decided: dict[str, str]) -> set[str]:
        """Inclusion-group competition：loser 不算 activated、不递归、不写 timed state。

        ``batch`` 是本 pass 已通过普通 eligibility 的候选；``decided`` 记录早前
        pass 已决出 winner 的分组，后到的成员一律落选。
        """

        groups: dict[str, list[str]] = {}
        for eid in batch:
            for name in self._group_names(self._entries.get(eid, {})):
                groups.setdefault(name, []).append(eid)

        winners = set(batch)
        for eid in batch:
            names = self._group_names(self._entries.get(eid, {}))
            if names:
                self._record(eid, group={
                    "names": list(names),
                    "score": self._score_of(eid, batch[eid]),
                    "prioritized": bool(self._entries.get(eid, {}).get("prioritize_inclusion", False)),
                    "outcome": "uncontested",
                })
        for name, members in groups.items():
            if any(self._group_allow_all(self._entries.get(m, {})) for m in members):
                continue
            if name in decided:
                winners -= {m for m in members if m != decided[name]}
                self._record_group_outcome(name, members, decided[name])
                continue
            if len(members) == 1:
                decided[name] = members[0]
                continue
            pool = list(members)
            if any(self._group_scoring_on(self._entries.get(m, {})) for m in members):
                best = max(self._score_of(m, batch[m]) for m in members)
                pool = [m for m in members if self._score_of(m, batch[m]) == best]
            prioritized = [
                m for m in pool
                if bool(self._entries.get(m, {}).get("prioritize_inclusion", False))
            ]
            if prioritized:
                # SillyTavern Prioritize Inclusion 依赖较高的 insertion order，
                # 因此 order 必须降序，绝不能写成 ascending。
                prioritized.sort(key=lambda m: (
                    -int(self._entries.get(m, {}).get("priority", 0) or 0),
                    -int(self._entries.get(m, {}).get("order", 100) or 100),
                    str(m),
                ))
                winner = prioritized[0]
            else:
                winner = self._weighted_pick(sorted(pool))
            decided[name] = winner
            winners -= {m for m in members if m != winner}
            self._record_group_outcome(name, members, winner)
            logger.debug("分组竞争: group=%s winner=%s removed=%d",
                         name, self._entries.get(winner, {}).get("name", winner),
                         len(members) - 1)
        return winners

    def _record_group_outcome(self, name: str, members: list[str], winner: str) -> None:
        """把 group 竞争的 winner/loser 写进判定记录（trace 要区分 group_lost）。"""

        for member in members:
            row = self.last_decisions.get(member)
            group = dict(row.get("group") or {}) if row else {}
            group.update({
                "contested_group": name,
                "outcome": "winner" if member == winner else "lost",
                "winner": winner,
            })
            self._record(member, group=group)

    def _weighted_pick(self, pool: list[str]) -> str:
        """group_weight weighted random，RNG 可注入以保证确定性。"""

        if not pool:
            return ""
        if len(pool) == 1:
            return pool[0]
        weights = {m: max(1, int(self._entries.get(m, {}).get("group_weight", 1) or 1)) for m in pool}
        roll = (self._rng or random.random)() * sum(weights.values())
        for eid in pool:
            roll -= weights[eid]
            if roll < 0:
                return eid
        return pool[-1]

    # ---- timed state --------------------------------------------------------

    def _get_constant_ids(self) -> set[str]:
        return {eid for eid, entry in self._entries.items() if entry.get("is_constant")}

    @staticmethod
    def _get_sticky_active_ids(timed_state: dict[str, dict] | None) -> set[str]:
        """获取当前处于 active 状态的 sticky 条目。"""
        if not timed_state:
            return set()
        return {eid for eid, state in timed_state.items() if sticky_active(state)}

    @staticmethod
    def _get_timed_blocked_ids(timed_state: dict[str, dict] | None) -> set[str]:
        """获取当前被 cooldown（或 legacy delay 计数器）阻止的条目 ID。

        与 :meth:`_eligibility_ok` 共用同一个 :func:`timed_gate_blocked` 判据，
        避免 seed 入口和 eligibility 两处判断漂移。
        """
        if not timed_state:
            return set()
        return {eid for eid, state in timed_state.items() if timed_gate_blocked(state)}

    def _apply_time_effects(
        self, matched_ids: set[str], timed_state: dict[str, dict],
        *, current_tick: int | None = None,
    ) -> None:
        """activated 条目才写 sticky/cooldown。

        ``delay`` 不在这里落计数器：它是对 authoritative turn tick 的前置门，
        不是激活后的倒计时，所以不会变成与 sticky/cooldown 并列的第二套状态机。
        """
        for eid in matched_ids:
            entry = self._entries.get(eid)
            if not entry:
                continue
            sticky = max(0, int(entry.get("sticky", 0) or 0))
            cooldown = max(0, int(entry.get("cooldown", 0) or 0))
            if sticky <= 0 and cooldown <= 0:
                continue

            state = timed_state.setdefault(eid, {})
            before = (state.get("sticky_remaining", 0), state.get("cooldown_remaining", 0))
            arm_timed_activation(
                state, sticky=sticky, cooldown=cooldown,
                activated_tick=int(current_tick or 0),
            )
            if before != (state.get("sticky_remaining", 0), state.get("cooldown_remaining", 0)):
                logger.debug(
                    "世界书 timed 激活: %s (sticky=%d, cooldown=%d, cooldown 在 sticky 结束后开始)",
                    entry.get("name", eid), sticky, cooldown,
                )
            # Do not emit the legacy status/remaining shape.  Legacy input is
            # still read by the compatibility predicates above, while all
            # newly-mutated runtime state uses independent counters.

    def _sort_by_tier(self, entry_ids: set[str]) -> list[dict]:
        result = []
        for eid in entry_ids:
            if eid in self._entries:
                result.append(dict(self._entries[eid]))
        tier_order = {"core": 0, "background": 1, "archived": 2}
        # 末位用 canonical id 兜底：否则同 tier / 同 order 的条目会按 set 迭代顺序
        # 输出，跨进程不可复现（施工单要求排序以 stable id 收尾）。
        result.sort(key=lambda e: (tier_order.get(e.get("tier", "background"), 1),
                                    int(e.get("order", 100)),
                                    str(e.get("id", ""))))
        return result

    def _frontier_sort_key(self, eid: str, *, direct: bool, recursive: bool) -> tuple:
        """frontier 求值顺序 = 最终 inclusion 顺序（单一事实来源 ``entry_sort_key``）。

        work cutoff 只被允许砍掉这个序列的尾部，因此「低 priority + id 靠前」不可能
        提前占满 cap 而把「高 priority / constant + id 靠后」挤出最终排序。
        """

        entry = self._entries.get(eid)
        if entry is None:
            return (1, 1, 0, 100, 1 if recursive else 0, 1, str(eid))
        return entry_sort_key(
            {**entry, "_direct_match": direct},
            recursive=recursive,
            semantic_only=False,
        )

    def reload(self, entries: list[dict]) -> None:
        """重新构建索引（世界书更新后调用）。"""
        self.build(entries)
