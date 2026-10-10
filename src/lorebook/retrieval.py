"""通用 Hybrid Lore Retrieval —— 所有 Ruleset 共用的世界书检索层。

设计边界（施工方案 §4 / §19 / §27 / §28 / §40）：

- **只做检索。** 构造 retrieval query、调用既有 :class:`KeywordMatcher`、可选的语义
  检索、visibility 过滤、按 canonical entry id merge/dedupe，输出候选条目。本模块
  不写 WorldState、Memory、角色状态、Combat、Ruleset，也不持有第二份 Lore 数据。
- **不替换关键词检索。** 语义只是增强：keyword 命中与 semantic 命中并行存在，按
  canonical id 去重；最终排序继续尊重既有 ``tier`` / ``order``（不发明第二套排序）。
- **向量不是 authority。** 高 cosine 只代表"这条可能相关"，不代表"正在发生"；
  语义命中同样要过 visibility 与预算裁剪，也绝不会触发事件或改写世界事实。
- **失败不阻断回念。** 未配置 embedding 或 embedding 调用失败时，退回
  ``结构化锚点 + KeywordMatcher``，正常回合不失败（施工方案 §29 / §30）。
- **计时器归既有 authority。** semantic 候选不写 sticky/cooldown/delay；被 cooldown /
  delay 挡住的条目也不会仅因向量相似而进入上下文。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
from collections.abc import Callable, Sequence
from typing import Any

from src.engine import progression
from src.engine.language import DEFAULT_LANGUAGE
from src.engine.modules import lorebook_runtime, narrative_notes
from src.engine.world_legality import actor_location_fact_key
from src.engine.world_state import project_visible_state
from src.knowledge.visibility import entry_visible_to_viewer
from src.memory.embedding import cosine_similarity
from src.lorebook.matcher import BUDGET_CANDIDATE_SLACK, MIN_BUDGET_CANDIDATES
from src.lorebook.resolver import resolve_active_books
from src.lorebook.budget import (
    apply_token_budget,
    estimate_entry_chars,
    max_entries_within_budget,
)
from src.lorebook.activation import (
    CANONICAL_VECTOR_ACTIVATION,
    DEFAULT_VECTOR_ACTIVATION,
    evaluate_probability,
    timed_gate_blocked,
)
from src.lorebook.trace import ActivationTrace

logger = logging.getLogger("trpg")

# 语义召回规模与阈值：第一版内部常量，不作为永久产品契约（施工方案 §17）。
DEFAULT_SEMANTIC_TOP_K = 4
DEFAULT_SEMANTIC_THRESHOLD = 0.60

# 最终排序的 tier 优先级：与 KeywordMatcher._sort_by_tier 保持一致。
_TIER_RANK = {"core": 0, "background": 1, "archived": 2}

# embedding_profile 的版本前缀：profile 算法本身变化时必须让旧缓存整体失效。
EMBEDDING_PROFILE_VERSION = "v1"

# 单次缓存查询的 entry 数量上限，避免撞 SQLite 的参数个数限制。
_MAX_ENTRY_IDS_PER_QUERY = 400


# ---- Retrieval query（施工方案 §5 / §6） ------------------------------------


def lore_query_location(
    instance: Any,
    *,
    viewer_is_gm: bool = True,
    viewer_uid: str = "",
) -> str:
    """当前 canonical location，找不到就返回空串（省略该项，绝不阻断检索）。

    来源优先级（施工方案 §5）：WorldState 中当前 actor 的 canonical location，其次
    队伍其他成员的 location。读取统一走 :func:`project_visible_state`：非 GM 视角
    只拿到 public 事实，因此隐藏世界真相不会因为锚点而进入玩家路径。
    """

    players = getattr(instance, "players", None)
    if not isinstance(players, dict):
        players = {}

    ordered: list[str] = []
    if viewer_uid and viewer_uid in players:
        ordered.append(str(viewer_uid))
    ordered.extend(str(uid) for uid in sorted(players) if str(uid) not in ordered)
    if not ordered:
        return ""

    try:
        projection = project_visible_state(
            instance, viewer_uid=str(viewer_uid or ""), viewer_is_gm=bool(viewer_is_gm),
        )
    except Exception:  # 投影失败只意味着"找不到 location"，不影响检索
        logger.debug("Lore location 投影失败，省略 location 锚点", exc_info=True)
        return ""
    facts = projection.get("facts") if isinstance(projection, dict) else None
    if not isinstance(facts, dict):
        return ""

    for uid in ordered:
        fact = facts.get(actor_location_fact_key(uid))
        if isinstance(fact, dict):
            value = str(fact.get("value") or "").strip()
            if value:
                return value
    return ""


def present_npc_names(instance: Any, scene: str = "") -> list[str]:
    """当前场景中确实出现的 NPC 名（施工方案 §5 Case D）。

    第一版刻意不建 Entity Graph：只把 ``instance.npcs`` 里名字出现在当前 scene 文本
    中的那些当作 ``present_npc`` 锚点——"在场"是文本事实，不是名册事实。
    """

    npcs = getattr(instance, "npcs", None)
    if not isinstance(npcs, dict):
        return []
    haystack = str(scene or "").casefold()
    if not haystack:
        return []
    names: list[str] = []
    for npc_id, record in npcs.items():
        if isinstance(record, dict):
            name = str(
                record.get("character_name") or record.get("name") or npc_id or ""
            ).strip()
        else:
            name = str(npc_id or "").strip()
        if name and name.casefold() in haystack and name not in names:
            names.append(name)
    return sorted(names)


def build_lore_retrieval_query(
    instance: Any,
    actions_text: str,
    *,
    viewer_is_gm: bool = True,
    viewer_uid: str = "",
    location: str | None = None,
    present_npcs: Sequence[str] | None = None,
) -> str:
    """semantic query：带结构标签，供 embedding 使用（施工方案 §5）。"""

    values = _lore_query_values(
        instance, actions_text,
        viewer_is_gm=viewer_is_gm, viewer_uid=viewer_uid,
        location=location, present_npcs=present_npcs,
    )
    sections: list[str] = []
    if values["action"]:
        sections.append(f"[action]\n{values['action']}")
    if values["scene"]:
        sections.append(f"[scene]\n{values['scene']}")
    if values["location"]:
        sections.append(f"[location]\n{values['location']}")
    if values["present_npcs"]:
        sections.append("[present_npcs]\n" + "\n".join(values["present_npcs"]))
    return "\n\n".join(sections)


def build_lore_lexical_query(
    instance: Any,
    actions_text: str,
    *,
    viewer_is_gm: bool = True,
    viewer_uid: str = "",
    location: str | None = None,
    present_npcs: Sequence[str] | None = None,
) -> str:
    """lexical query：只有"值"，**不含** ``[action]`` / ``[scene]`` / ``[location]`` /
    ``[present_npcs]`` 这些结构标签，供 KeywordMatcher 使用。

    否则标签本身会参与关键词匹配：英文世界书里关键词若是 ``scene`` / ``location`` /
    ``action``，每一轮都会因为标签固定误命中。
    """

    values = _lore_query_values(
        instance, actions_text,
        viewer_is_gm=viewer_is_gm, viewer_uid=viewer_uid,
        location=location, present_npcs=present_npcs,
    )
    lines = [values["action"], values["scene"], values["location"], *values["present_npcs"]]
    return "\n\n".join(line for line in lines if line)


def build_lore_retrieval_queries(
    instance: Any,
    actions_text: str,
    *,
    viewer_is_gm: bool = True,
    viewer_uid: str = "",
) -> dict[str, str]:
    """一次解析锚点，返回 ``{"lexical": ..., "semantic": ...}``（不重复 anchor 解析）。"""

    anchors = lore_query_anchors(instance, viewer_is_gm=viewer_is_gm, viewer_uid=viewer_uid)
    kwargs = {
        "viewer_is_gm": viewer_is_gm,
        "viewer_uid": str(viewer_uid or ""),
        "location": anchors["location"],
        "present_npcs": anchors["present_npcs"],
    }
    return {
        "lexical": build_lore_lexical_query(instance, actions_text, **kwargs),
        "semantic": build_lore_retrieval_query(instance, actions_text, **kwargs),
    }


def lore_query_anchors(
    instance: Any, *, viewer_is_gm: bool = True, viewer_uid: str = "",
) -> dict[str, Any]:
    """本轮锚点：scene / canonical location / 在场 NPC。"""

    scene = str(narrative_notes.scene(instance) or "").strip()
    return {
        "scene": scene,
        "location": lore_query_location(
            instance, viewer_is_gm=viewer_is_gm, viewer_uid=str(viewer_uid or ""),
        ),
        "present_npcs": present_npc_names(instance, scene),
    }


def _lore_query_values(
    instance: Any,
    actions_text: str,
    *,
    viewer_is_gm: bool = True,
    viewer_uid: str = "",
    location: str | None = None,
    present_npcs: Sequence[str] | None = None,
) -> dict[str, Any]:
    """lexical / semantic 两种 query 共用的"值"解析（只解析一次）。"""

    anchors = lore_query_anchors(instance, viewer_is_gm=viewer_is_gm, viewer_uid=viewer_uid)
    resolved_location = anchors["location"] if location is None else str(location or "").strip()
    resolved_npcs = anchors["present_npcs"] if present_npcs is None else [
        str(name).strip() for name in present_npcs if str(name).strip()
    ]
    return {
        "action": str(actions_text or "").strip(),
        "scene": anchors["scene"],
        "location": resolved_location,
        "present_npcs": resolved_npcs,
    }


def normalize_vector(value: object) -> list[float] | None:
    """把任意向量输入收敛成"全是 finite float"的列表，否则 None（fail-soft 边界）。

    非 list/tuple、空、元素不能 ``float()``、NaN、Inf / -Inf 一律返回 None：坏向量只应
    让语义检索跳过该条目或本轮，绝不能让正常回合崩在 ``cosine_similarity`` 里。
    """

    if not isinstance(value, (list, tuple)) or not value:
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool):
            return None
        try:
            number = float(item)
        except (TypeError, ValueError):
            return None
        if math.isnan(number) or math.isinf(number):
            return None
        numbers.append(number)
    return numbers


# ---- Embedding 文本 / profile / content hash（施工方案 §10 / §13 / §14) ------


def lore_entry_embedding_text(entry: dict) -> str:
    """一个 Lore Entry 送入 embedding 的文本：只取语义字段。

    只包含 ``name`` / ``type`` / ``keywords`` / ``content``；matcher 运行时元数据
    （sticky / cooldown / delay / probability / group_weight / order / visible_to 等）
    不参与向量，否则"多久触发一次""谁能看见"会污染语义空间。
    """

    if not isinstance(entry, dict):
        return ""
    name = str(entry.get("name") or "").strip()
    entry_type = str(entry.get("type") or "other").strip()
    keywords = entry.get("keywords", [])
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except (json.JSONDecodeError, TypeError):
            keywords = [keywords]
    if not isinstance(keywords, (list, tuple, set)):
        keywords = []
    keyword_text = ", ".join(
        str(item).strip() for item in keywords if str(item).strip()
    )
    content = str(entry.get("content") or "").strip()

    lines = [f"Name: {name}", f"Type: {entry_type}"]
    if keyword_text:
        lines.append(f"Keywords: {keyword_text}")
    if content:
        lines.append(f"Content: {content}")
    return "\n".join(lines)


def lore_entry_content_hash(entry: dict) -> str:
    """对真正送入 EmbeddingClient 的文本计算 SHA-256（施工方案 §14）。"""

    text = lore_entry_embedding_text(entry)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_profile(client: Any) -> str:
    """embedding 缓存 profile：模型 / 端点 / max_input 的 hash（施工方案 §13）。

    换模型或换服务后旧向量不得混用，因此这些身份都进 profile。**绝不**包含 API key /
    token / secret：profile 会写进数据库。
    """

    payload = {
        "version": EMBEDDING_PROFILE_VERSION,
        "model": str(getattr(client, "model", "") or ""),
        "endpoint": str(getattr(client, "base_url", "") or ""),
        "max_input": int(getattr(client, "max_input_chars", 0) or 0),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _viewer_kind(viewer_is_gm: bool, viewer_uid: str | None) -> str:
    """把检索视角映射到 ``src.knowledge.visibility`` 的 gm / character / party。"""

    if viewer_is_gm:
        return "gm"
    return "character" if str(viewer_uid or "").strip() else "party"


class LoreRetriever:
    """通用世界书检索器：所有走标准回合管线的 Ruleset 自动享受同一套检索。"""

    def __init__(
        self,
        matcher: Any,
        *,
        store: Any | None = None,
        load_world_template: Callable[[str, str], Any] | None = None,
        content_projection: Any | None = None,
        embedding_client_provider: Callable[[], Any] | None = None,
        semantic_top_k: int = DEFAULT_SEMANTIC_TOP_K,
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    ) -> None:
        self._matcher = matcher
        self._store = store
        self._load_world_template = load_world_template
        self._content_projection = content_projection
        self._embedding_client_provider = embedding_client_provider
        self._semantic_top_k = max(0, int(semantic_top_k))
        self._semantic_threshold = float(semantic_threshold)
        self._scope: tuple[str, ...] | None = None
        self._entries: list[dict] = []
        self._world_id = ""
        self._language = DEFAULT_LANGUAGE
        self.last_activation_trace: list[dict[str, Any]] = []
        self._legacy_world_mode = False

    # ---- 世界作用域 ---------------------------------------------------------

    def ensure_world(self, world_id: str, language: str = "") -> None:
        """确保匹配器与语义候选都已加载当前世界的条目（按 world + language 缓存）。"""

        if not world_id or self._store is None:
            return
        # The compatibility facade reads the primary world book, so its cache key
        # must carry that book's revision too: canonical entry CRUD bumps it and
        # the next retrieve has to see the new state.
        revision = 0
        if hasattr(self._store, "get_lorebook"):
            book = self._store.get_lorebook(self._store.primary_world_book_id(str(world_id)))
            revision = int((book or {}).get("revision", 0) or 0)
        # scope[1] stays the bare language so the locale guard in
        # ensure_lore_context keeps working; the revision rides as a third slot.
        scope = (str(world_id or ""), str(language or DEFAULT_LANGUAGE), str(revision))
        if scope == self._scope:
            return
        if self._content_projection is not None:
            entries = self._content_projection.for_world_authoring(str(world_id))
        else:
            entries = self._store.list_entries(str(world_id))
        if self._load_world_template is not None:
            from src.content.worlds import localize_lorebook_entries

            entries = localize_lorebook_entries(
                entries, self._load_world_template(str(world_id), scope[1]),
            )
        self._matcher.build(entries)
        self._entries = list(entries)
        self._world_id, self._language = scope[0], scope[1]
        self._scope = scope
        self._legacy_world_mode = True

    def ensure_lore_context(self, instance: Any, *, viewer_is_gm: bool = True,
                            viewer_uid: str = "", action_actor_uids: Sequence[str] | None = None) -> None:
        """Load all canonical books bound to the current runtime context.

        The world-only ``ensure_world`` method remains the compatibility façade for
        map/NPC projections. Narrative retrieval uses this resolver-backed path.
        Stores from older callers that do not expose bindings automatically fall back
        to that façade.
        """
        world_id = str(getattr(instance, "world_id", "") or "")
        if self._legacy_world_mode and action_actor_uids is None and self._scope and self._scope[0] == world_id:
            return
        instance_language = str(getattr(instance, "language", "") or "")
        # Explicit callers may use ensure_world(world, language) for locale
        # characterization; do not immediately overwrite that scope from a
        # lightweight test/runtime instance carrying a stale language field.
        if (self._scope and self._scope[0] == world_id and "|books:" not in self._scope[1]
                and self._scope[1] == self._language and instance_language
                and instance_language != self._language):
            return
        language = instance_language or self._language or DEFAULT_LANGUAGE
        if not world_id or self._store is None or not hasattr(self._store, "list_bindings"):
            self.ensure_world(world_id, language)
            return
        refs = resolve_active_books(
            instance, "gm" if viewer_is_gm else ("character" if viewer_uid else "party"),
            viewer_uid, list(action_actor_uids or getattr(instance, "action_actor_uids", []) or []), store=self._store,
        )
        # An empty result has two very different meanings. A store with no
        # bindings at all is a legacy store where the world façade is the only
        # content path. A store that *has* bindings but resolved none of them is a
        # deliberate "nothing is active" (a disabled Book, or a scope this viewer
        # cannot see) — falling back to the legacy world read there would put
        # deliberately disabled content straight back into context.
        if not refs and not self._store.list_bindings():
            self.ensure_world(world_id, language)
            return
        book_ids = [ref.book_id for ref in refs]
        # The fingerprint carries the Book's monotonic revision, which every
        # entry mutation bumps. updated_at alone is second-precision and would
        # keep a stale matcher across two edits inside the same second.
        scope = (world_id, f"{language}|books:" + ",".join(
            f"{ref.book_id}@{ref.updated_at}@{ref.revision}@{ref.order}"
            for ref in refs
        ))
        if scope == self._scope:
            return
        entries: list[dict] = []
        for ref in refs:
            book_entries = self._store.list_book_entries(ref.book_id)
            for entry in book_entries:
                row = dict(entry)
                row["_lorebook_id"] = ref.book_id
                row["_lorebook_order"] = ref.order
                row["_lorebook_token_budget"] = ref.token_budget
                row["_lorebook_scan_depth"] = ref.scan_depth
                row["_lorebook_recursive_scanning"] = ref.recursive_scanning
                row["_lorebook_fuzzy_enabled"] = ref.fuzzy_enabled
                settings = ref.settings or {}
                row["_lorebook_vector_activation"] = str(
                    settings.get("vector_activation", DEFAULT_VECTOR_ACTIVATION) or DEFAULT_VECTOR_ACTIVATION
                )
                entries.append(row)
        self._matcher.build(entries)
        self._entries = entries
        self._world_id, self._language, self._scope = world_id, language, scope
        self._legacy_world_mode = False

    def invalidate_world(self, world_id: str) -> None:
        """世界内容或语言变化后强制下一次重建（与旧 matcher 失效语义一致）。"""

        if self._scope is not None and self._scope[0] == str(world_id or ""):
            self._scope = None

    @property
    def world_entries(self) -> list[dict]:
        """当前作用域下的 canonical 条目（只读用途，如语义候选与诊断）。"""

        return list(self._entries)

    def resolve_active_books(self, instance: Any, *, viewer_is_gm: bool = True,
                             viewer_uid: str = "", action_actor_uids: Sequence[str] | None = None) -> list[dict]:
        """Return stable book refs for diagnostics and multi-book callers.

        The existing ``ensure_world`` facade remains the default world-content path;
        this additive API lets round/KP integrations opt into bindings without making
        frontend code aware of storage details.
        """
        refs = resolve_active_books(
            instance, "gm" if viewer_is_gm else ("character" if viewer_uid else "party"),
            viewer_uid, list(action_actor_uids or []), store=self._store,
        )
        return [{"book_id": ref.book_id, "order": ref.order, "binding_id": ref.binding_id, "role": ref.role} for ref in refs]

    # ---- 检索主入口 ---------------------------------------------------------

    async def retrieve(
        self,
        instance: Any,
        actions_text: str,
        *,
        viewer_is_gm: bool = True,
        viewer_uid: str | None = None,
        viewer_name: str = "",
        mutate_timers: bool = True,
        action_actor_uids: Sequence[str] | None = None,
        overall_budget: int | None = None,
    ) -> list[dict]:
        """返回本轮应当进入上下文的 canonical 条目（已按视角过滤、按 id 去重）。

        ``mutate_timers=True``（正常回合 / swipe）沿用既有语义：匹配到的 sticky /
        cooldown / delay 会写回实例的计时状态。``False``（玩家问答）用副本匹配，
        观察计时器但不改变它们。

        ``overall_budget`` 是整体 lore 预算（字符），由调用方从既有的 prompt /
        context 预算派生（``llm.context_builder.lore_char_budget``）。预算先逐
        book 裁剪、merge，再整体裁剪一次；它同时收紧 recursion 的候选上限，
        避免大 Book 先无限展开、最后才裁。传 ``None`` 表示本次不设整体上限，
        最终仍由 context_builder 兜底。
        """

        self.ensure_lore_context(
            instance, viewer_is_gm=viewer_is_gm, viewer_uid=str(viewer_uid or ""),
            action_actor_uids=action_actor_uids,
        )
        anchors = lore_query_anchors(
            instance, viewer_is_gm=viewer_is_gm, viewer_uid=str(viewer_uid or ""),
        )
        queries = build_lore_retrieval_queries(
            instance, actions_text,
            viewer_is_gm=viewer_is_gm, viewer_uid=str(viewer_uid or ""),
        )

        timed_state = self._timed_state(instance, mutate_timers=mutate_timers)

        def _visible(entry: dict) -> bool:
            return bool(self._visible_entries(
                [entry], viewer_is_gm=viewer_is_gm, viewer_uid=viewer_uid,
                viewer_name=viewer_name,
            ))

        # 语义候选先于激活发现：semantic / vector 只是候选发现通道，候选必须与
        # lexical 候选一起汇入同一套 activation eligibility pipeline，因此
        # probability / group / timed / visibility 语义在两条通道上完全一致。
        semantic_ids, semantic_scores = await self._semantic_candidates(
            queries["semantic"],
            timed_state=timed_state,
            viewer_is_gm=viewer_is_gm,
            viewer_uid=viewer_uid,
            viewer_name=viewer_name,
        )
        # vector_only 条目只能被语义通道发现，不能被关键词通道（含 recursion
        # 扫描）激活。delay 门用 DiceFrame 的 authoritative turn tick 判定。
        activated = self._matcher.match_with_recursive(
            queries["lexical"],
            timed_state=timed_state,
            is_visible=_visible,
            is_candidate=lambda entry: self._vector_mode(entry) != "vector_only",
            extra_candidates=semantic_ids,
            current_tick=progression.current_round(instance),
            max_activated=self._candidate_cap(overall_budget),
        )
        hits = []
        for entry in activated:
            row = dict(entry)
            score = semantic_scores.get(str(row.get("id") or ""))
            if score is not None:
                row["_semantic_score"] = score
            hits.append(row)

        merged, budget_omitted = self._apply_budgets(hits, overall_budget)
        self.last_activation_trace = self._build_trace(
            hits, semantic_scores, merged,
            lexical_query=queries["lexical"],
            viewer_is_gm=viewer_is_gm, viewer_uid=viewer_uid,
            budget_omitted=budget_omitted,
        )
        try:
            setattr(instance, "lorebook_activation_trace", list(self.last_activation_trace))
        except Exception:
            pass

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Lore retrieval: world=%s language=%s scene=%s location=%s present_npcs=%s "
                "semantic_candidates=%s final_hits=%s",
                self._world_id, self._language, anchors["scene"], anchors["location"],
                anchors["present_npcs"],
                list(semantic_ids), [entry.get("id") for entry in merged],
            )
        return merged

    # ---- 内部步骤 -----------------------------------------------------------

    @staticmethod
    def _order_of(entry: dict) -> int:
        try:
            return int(entry.get("order", 100))
        except (TypeError, ValueError):
            return 100

    @classmethod
    def _sort_key(cls, entry: dict, *, source_rank: int, score: float) -> tuple:
        """Hybrid 最终排序键（施工方案 review Blocker A）。

        ``tier`` 最高优先，其次 ``order``；``source priority``（keyword 优先 pure
        semantic）只在同 tier + 同 order 时作为 tie-break，绝不允许压过 tier/order；
        同级 semantic 之间用 cosine 分数降序；最后用 canonical id 保证稳定。
        """

        return (
            _TIER_RANK.get(str(entry.get("tier") or "background"), 1),
            cls._order_of(entry),
            int(source_rank),
            -float(score or 0.0),
            str(entry.get("id") or ""),
        )

    @classmethod
    def _merge_and_sort(cls, keyword_hits: list[dict], semantic_hits: list[dict]) -> list[dict]:
        """按 canonical id 去重后统一稳定排序（keyword 命中已去重，语义只补新条目）。"""

        scored = [
            (entry, 0, 0.0) for entry in keyword_hits
        ] + [
            (entry, 1, float(entry.get("_semantic_score") or 0.0)) for entry in semantic_hits
        ]
        scored.sort(key=lambda item: cls._sort_key(item[0], source_rank=item[1], score=item[2]))
        return [entry for entry, _source, _score in scored]

    @staticmethod
    def _timed_state(instance: Any, *, mutate_timers: bool) -> dict[str, dict] | None:
        state = lorebook_runtime.timers(instance)
        if not isinstance(state, dict):
            return None
        return state if mutate_timers else copy.deepcopy(state)

    @staticmethod
    def _visible_entries(
        entries: Sequence[dict],
        *,
        viewer_is_gm: bool,
        viewer_uid: str | None,
        viewer_name: str = "",
    ) -> list[dict]:
        """按视角过滤候选（施工方案 §21）。非 GM 视角 fail-closed。"""

        kind = _viewer_kind(viewer_is_gm, viewer_uid)
        if kind == "gm":
            return [dict(entry) for entry in entries if isinstance(entry, dict)]
        uid = str(viewer_uid or "")
        return [
            dict(entry)
            for entry in entries
            if isinstance(entry, dict)
            and entry_visible_to_viewer(entry, kind, uid, viewer_name)
        ]

    def _embedding_client(self) -> Any | None:
        if self._embedding_client_provider is None:
            return None
        try:
            return self._embedding_client_provider()
        except Exception:  # provider 只是取现有客户端，取不到就等于未配置
            logger.debug("读取 embedding 客户端失败，跳过语义检索", exc_info=True)
            return None

    async def _semantic_candidates(
        self,
        query: str,
        *,
        timed_state: dict[str, dict] | None,
        viewer_is_gm: bool,
        viewer_uid: str | None,
        viewer_name: str,
    ) -> tuple[set[str], dict[str, float]]:
        """语义候选发现：只负责"找出可能的条目"，不做 activation 决策。

        返回 ``(candidate_ids, {id: score})``。这些候选随后与 lexical 候选一起
        进入 :meth:`KeywordMatcher.match_with_recursive` 的统一 eligibility
        pipeline（enabled / visibility / timed / probability / group / budget），
        因此 semantic-only 条目不会绕过任何 activation 条件。
        任何失败都只意味着"本轮没有语义增强"。
        """

        if self._semantic_top_k <= 0 or not query or not self._entries or self._store is None:
            return set(), {}
        client = self._embedding_client()
        if client is None:
            return set(), {}

        candidates = self._visible_entries(
            list(self._entries),
            viewer_is_gm=viewer_is_gm,
            viewer_uid=viewer_uid,
            viewer_name=viewer_name,
        )
        candidates = [
            entry for entry in candidates
            if self._vector_mode(entry) in {"hybrid", "vector_only"}
            and not self._timer_blocked(str(entry.get("id") or ""), timed_state)
        ]
        if not candidates:
            return set(), {}

        # 共享 matcher / 世界作用域是 per-runtime 的，embedding 调用会让出事件循环；
        # 语言必须在第一个 await 之前快照，否则另一个世界的 ensure_world 会让本轮
        # 缓存键跳到别的语言上。
        language = self._language

        try:
            query_vector = normalize_vector(await client.embed(query))
        except Exception:
            logger.warning("Lore semantic query embedding 失败，跳过语义检索", exc_info=True)
            return set(), {}
        if query_vector is None:
            logger.warning("Lore semantic query embedding 不是合法向量，跳过语义检索")
            return set(), {}

        vectors = await self._entry_vectors(client, candidates, query_vector, language=language)
        if not vectors:
            return set(), {}

        scored: list[tuple[float, dict]] = []
        for entry in candidates:
            vector = vectors.get(str(entry.get("id") or ""))
            if not vector:
                continue
            score = cosine_similarity(list(query_vector), vector)
            if score >= self._semantic_threshold:
                scored.append((score, entry))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))

        ids: set[str] = set()
        scores: dict[str, float] = {}
        for score, entry in scored[: self._semantic_top_k]:
            entry_id = str(entry.get("id") or "")
            if not entry_id:
                continue
            ids.add(entry_id)
            # 诊断字段：只在语义命中上标注分数，不改变 matcher 既有字段语义。
            scores[entry_id] = round(float(score), 4)
        return ids, scores

    @staticmethod
    def _vector_mode(entry: dict[str, Any]) -> str:
        """Resolve one entry's effective vector activation mode.

        Contract::

            off         = 不参与 semantic candidate discovery
            hybrid      = keyword + semantic
            vector_only = 只允许 semantic discovery，不允许 lexical discovery

        ``off`` is an explicit author decision and is honoured literally — it is
        **not** a synonym for inherit. Only a missing / empty field inherits the
        book-level default (world-template and legacy projections carry no
        canonical field and therefore keep the pre-v2 semantic enhancement).
        Legacy v4 lore is migrated to an explicit ``hybrid`` by the v6 schema
        migration instead of being reinterpreted on every retrieval.
        """

        raw = entry.get("vector_activation")
        mode = str(raw if raw is not None else "").strip().lower()
        if mode in CANONICAL_VECTOR_ACTIVATION:
            return mode
        return LoreRetriever._book_vector_default(entry)

    @staticmethod
    def _book_vector_default(entry: dict[str, Any]) -> str:
        """Book-level inheritance target for entries with no explicit mode."""

        default = str(entry.get("_lorebook_vector_activation") or "").strip().lower()
        return default if default in CANONICAL_VECTOR_ACTIVATION else DEFAULT_VECTOR_ACTIVATION

    @staticmethod
    def _apply_book_budgets(entries: list[dict]) -> tuple[list[dict], list[str]]:
        grouped: dict[str, list[dict]] = {}
        for entry in entries:
            grouped.setdefault(str(entry.get("_lorebook_id") or "__legacy__"), []).append(entry)
        included: list[dict] = []
        omitted: list[str] = []
        for rows in grouped.values():
            budget = int(rows[0].get("_lorebook_token_budget", 0) or 0)
            selected, dropped = apply_token_budget(rows, budget or None)
            omitted.extend(dropped)
            for row in selected:
                copy_row = dict(row)
                copy_row["_budget_state"] = "included"
                included.append(copy_row)
        return LoreRetriever._merge_and_sort(included, []), omitted

    @classmethod
    def _apply_budgets(
        cls, entries: list[dict], overall_budget: int | None,
    ) -> tuple[list[dict], list[str]]:
        """per-book budget → merge → overall lore budget（Wave C 的两级预算）。

        整体预算的数值由调用方从既有 context 预算派生；这里只负责裁剪，绝不自己
        推导 provider context-window。单位是字符，与 context_builder 一致，因此
        用字符估算器而不是 per-book 的 token 估算器。
        """

        merged, omitted = cls._apply_book_budgets(entries)
        if overall_budget is None or overall_budget <= 0:
            return merged, omitted
        selected, dropped = apply_token_budget(
            merged, overall_budget, estimate=estimate_entry_chars,
        )
        omitted.extend(dropped)
        for row in selected:
            row["_budget_state"] = "included"
        if dropped:
            logger.debug(
                "整体 lore 预算裁剪: budget=%d chars kept=%d omitted=%d",
                overall_budget, len(selected), len(dropped),
            )
        return cls._merge_and_sort(selected, []), omitted

    def _candidate_cap(self, overall_budget: int | None) -> int | None:
        """由整体预算推出 recursion 的候选上限（deterministic，宁松不紧）。

        上界按「最便宜的条目能装多少个」算，再放宽 ``BUDGET_CANDIDATE_SLACK`` 倍，
        因此只会掐住真正失控的展开，不会把预算本来装得下的条目挡在外面。
        """

        if overall_budget is None or overall_budget <= 0:
            return None
        fits = max_entries_within_budget(
            list(self._entries), overall_budget, estimate=estimate_entry_chars,
        )
        if fits is None:
            return None
        return max(MIN_BUDGET_CANDIDATES, fits * BUDGET_CANDIDATE_SLACK)

    def _build_trace(
        self, activated: Sequence[dict], semantic_scores: dict[str, float], final_hits: Sequence[dict],
        *, lexical_query: str, viewer_is_gm: bool, viewer_uid: str | None,
        budget_omitted: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """Build the ActivationTrace from the decisions the run actually made.

        Every reason comes from an authority that actually ran: keyword matching
        from ``keyword_decision``, and probability rolls / group competition /
        timed gates / recursion parentage from the matcher's per-candidate
        decision log (``KeywordMatcher.last_decisions``). Nothing here re-derives
        a verdict the activation path did not compute, so the trace cannot claim
        a rejection reason that never happened.

        Player traces never reveal a hidden row or even its count.
        """

        semantic_ids = set(semantic_scores)
        final_ids = {str(row.get("id") or "") for row in final_hits}
        omitted_by_budget = {str(eid) for eid in budget_omitted}
        activated_ids = {str(row.get("id") or "") for row in activated}
        decisions = dict(getattr(self._matcher, "last_decisions", {}) or {})
        rows: list[dict[str, Any]] = []
        for entry in self._entries:
            entry_id = str(entry.get("id") or "")
            if not entry_id:
                continue
            visible = viewer_is_gm or bool(self._visible_entries([entry], viewer_is_gm=viewer_is_gm, viewer_uid=viewer_uid))
            if not viewer_is_gm and not visible:
                # Player traces must not reveal hidden rows or even their count.
                continue
            decision = self._matcher.keyword_decision(entry, lexical_query)
            keyword_source = bool(decision["matched"]) and self._vector_mode(entry) != "vector_only"
            semantic_source = entry_id in semantic_ids
            score = semantic_scores.get(entry_id)
            recorded = decisions.get(entry_id, {})
            included = entry_id in final_ids
            trace = ActivationTrace(
                entry_id=entry_id,
                book_id=str(entry.get("_lorebook_id") or entry.get("book_id") or ""),
                candidate_sources=[
                    source for source, present in (("keyword", keyword_source), ("semantic", semantic_source)) if present
                ],
                matched_keys=list(decision["matched_keys"]),
                secondary_matches=list(decision["secondary_matches"]),
                primary_result=decision["primary_ok"],
                secondary_result=decision["secondary_ok"],
                semantic_score=float(score) if score is not None else None,
                recursion_parent=str(recorded.get("parent") or "") or None,
                recursion_depth=int(recorded.get("depth", 0) or 0),
                probability=recorded.get("probability"),
                group=recorded.get("group"),
                timed=recorded.get("timed"),
                visibility="visible" if visible else "hidden",
                budget="included" if included else "omitted",
                final_state="included" if included else "omitted",
                reason_code=self._reason_code(
                    entry_id, recorded, included=included,
                    activated=entry_id in activated_ids,
                    omitted_by_budget=entry_id in omitted_by_budget,
                    visible=visible,
                ),
            )
            row = trace.to_dict(safe=not viewer_is_gm)
            if row:
                rows.append(row)
        return rows

    @staticmethod
    def _reason_code(
        entry_id: str, recorded: dict[str, Any], *, included: bool, activated: bool,
        omitted_by_budget: bool, visible: bool,
    ) -> str:
        """Why this entry ended where it did — one code from the real decision.

        Order matters: a budget omission is only reported for an entry that
        actually won activation, so ``budget`` can never mask the gate that
        really rejected it.
        """

        if not visible:
            return "hidden"
        if activated and omitted_by_budget:
            return "budget"
        if activated and not included:
            return "budget"
        recorded_reason = str(recorded.get("reason_code") or "")
        if recorded_reason:
            return recorded_reason
        if not recorded:
            # 从未成为候选：既没被关键词发现，也没被语义召回。
            return "not_a_candidate"
        return "matched" if included else "not_matched"

    async def _entry_vectors(
        self,
        client: Any,
        candidates: Sequence[dict],
        query_vector: Sequence[float],
        *,
        language: str,
    ) -> dict[str, list[float]]:
        """读取 / 补齐条目向量缓存，返回 ``entry_id -> vector``。

        缓存缺失、content_hash 变化、维度与 query 不一致（换模型或坏数据）都算 stale，
        这一批一起重新 embed 并写回缓存（施工方案 §15 / §31）。
        """

        profile = embedding_profile(client)
        ids = [str(entry.get("id") or "") for entry in candidates]
        store = self._store
        if store is None:
            return {}
        cached = store.load_embedding_cache(ids, language, profile)
        query_size = len(list(query_vector))

        fresh: dict[str, list[float]] = {}
        stale: list[dict] = []
        for entry in candidates:
            entry_id = str(entry.get("id") or "")
            if not entry_id:
                continue
            record = cached.get(entry_id)
            if not isinstance(record, dict):
                stale.append(entry)
                continue
            if record.get("content_hash") != lore_entry_content_hash(entry):
                stale.append(entry)
                continue
            vector = normalize_vector(record.get("embedding"))
            if vector is None:
                # 坏向量（非数字 / NaN / Inf）按 stale 处理：重新 embedding，不 crash。
                logger.warning("Lore 缓存向量非法 (entry=%s)，重新 embedding", entry_id)
                stale.append(entry)
                continue
            if query_size and len(vector) != query_size:
                # 维度不一致：跳过 stale cache，本轮重新 embed 而不是 crash。
                logger.warning(
                    "Lore embedding 维度不一致 (entry=%s cached=%d query=%d)，重新 embedding",
                    entry_id, len(vector), query_size,
                )
                stale.append(entry)
                continue
            fresh[entry_id] = vector

        if not stale:
            return fresh

        texts = [lore_entry_embedding_text(entry) for entry in stale]
        try:
            vectors = await client.embed_batch(texts)
        except Exception:
            logger.warning("Lore entry embedding 失败，本轮跳过语义检索", exc_info=True)
            return fresh
        if not isinstance(vectors, list) or len(vectors) != len(stale):
            logger.warning("Lore entry embedding 结果不可用，本轮跳过语义检索")
            return fresh

        rows: list[dict] = []
        for entry, raw_vector in zip(stale, vectors):
            entry_id = str(entry.get("id") or "")
            values = normalize_vector(raw_vector)
            if not entry_id or values is None:
                if entry_id:
                    logger.warning("Lore batch embedding 返回非法向量 (entry=%s)，跳过该条目", entry_id)
                continue
            if query_size and len(values) != query_size:
                logger.warning(
                    "Lore embedding 维度与 query 不一致 (entry=%s)，本轮跳过该条目", entry_id,
                )
                continue
            fresh[entry_id] = values
            rows.append({
                "entry_id": entry_id,
                "language": language,
                "embedding_profile": profile,
                "content_hash": lore_entry_content_hash(entry),
                "embedding": values,
            })
        if rows:
            try:
                store.save_embedding_cache(rows)
            except Exception:  # 缓存写失败不该影响本轮召回
                logger.warning("Lore embedding 缓存写入失败（本轮结果仍可用）", exc_info=True)
        return fresh

    @staticmethod
    def _timer_blocked(entry_id: str, timed_state: dict[str, dict] | None) -> bool:
        """被 cooldown 挡住的条目不应仅因向量相似进入上下文（施工方案 §23）。

        判据与 matcher 的 eligibility gate 共用 :func:`timed_gate_blocked`，
        这里只是语义通道的提前剪枝，不是第二套规则。
        """

        if not entry_id or not timed_state:
            return False
        return timed_gate_blocked(timed_state.get(entry_id))
