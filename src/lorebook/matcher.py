"""Lorebook 关键词匹配器 —— 在纯文本中匹配世界书条目关键词，支持递归触发、AND/NOT逻辑、常量条目、概率、分组、正则。"""

from __future__ import annotations

import json
import logging
import random
import re
from collections import deque

logger = logging.getLogger("trpg")

MAX_RECURSIVE_DEPTH = 3
MIN_FUZZY_KEY_LEN = 2       # 最短模糊匹配关键词长度


class KeywordMatcher:
    """关键词匹配器，支持精确匹配 + 模糊子串回退 + AND逻辑 + 常量条目。"""

    def __init__(self):
        self._index: dict[str, set[str]] = {}
        self._entries: dict[str, dict] = {}
        self._fuzzy_keys: list[str] = []

    def build(self, entries: list[dict]) -> None:
        """从条目列表构建索引。每个条目的 keywords 字段为 JSON 数组。"""
        self._index.clear()
        self._entries.clear()
        self._fuzzy_keys.clear()
        for entry in entries:
            eid = entry["id"]
            self._entries[eid] = entry
            keywords = entry.get("keywords", [])
            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except (json.JSONDecodeError, TypeError):
                    keywords = [keywords]
            for kw in keywords:
                kw = kw.strip()
                if kw:
                    self._index.setdefault(kw, set()).add(eid)
                    if len(kw) >= MIN_FUZZY_KEY_LEN and kw not in self._fuzzy_keys:
                        self._fuzzy_keys.append(kw)
        logger.info("关键词索引已构建: %d 关键词, %d 条目, %d 模糊键, %d 常量",
                     len(self._index), len(self._entries), len(self._fuzzy_keys),
                     len(self._get_constant_ids()))

    def match(self, text: str) -> list[dict]:
        """匹配文本中出现的所有关键词。常量条目始终包含。"""
        matched_ids: set[str] = set()
        # 精确匹配 + 正则匹配
        for keyword, eids in self._index.items():
            if self._match_keyword(keyword, text):
                matched_ids.update(eids)
        # NOT 模式条目：无论关键词是否出现，都作为候选，稍后由 _apply_match_mode 过滤
        for eid, entry in self._entries.items():
            if entry.get("match_mode", "any") in ("not_any", "not_all"):
                matched_ids.add(eid)
        # 模糊回退
        if not matched_ids:
            matched_ids.update(self._fuzzy_match(text))
        # 常量条目始终加入
        matched_ids.update(self._get_constant_ids())
        # 逻辑过滤（AND / NOT）—— 放在最后，确保过滤掉模糊匹配和常量中的矛盾条目
        matched_ids = self._apply_match_mode(matched_ids, text)
        # 概率过滤
        matched_ids = self._apply_probability(matched_ids)
        # 分组竞争
        matched_ids = self._apply_group_competition(matched_ids)
        return self._sort_by_tier(matched_ids)

    @staticmethod
    def _match_keyword(keyword: str, text: str) -> bool:
        """匹配关键词：支持普通文本和正则（/pattern/ 格式）。"""
        if keyword.startswith("/") and keyword.endswith("/") and len(keyword) > 2:
            try:
                return bool(re.search(keyword[1:-1], text))
            except re.error:
                return False
        return keyword in text

    def _apply_match_mode(self, matched_ids: set[str], text: str) -> set[str]:
        """逻辑过滤：AND（所有关键词须出现）/ NOT（关键词不出现才激活）。"""
        result = set(matched_ids)
        for eid in list(result):
            entry = self._entries.get(eid)
            if not entry:
                result.discard(eid)
                continue
            mode = entry.get("match_mode", "any")
            if mode == "any":
                continue
            keywords = entry.get("keywords", [])
            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except (json.JSONDecodeError, TypeError):
                    keywords = [keywords]
            if not keywords:
                result.discard(eid)
                continue
            if mode == "all" and not all(self._match_keyword(kw, text) for kw in keywords):
                result.discard(eid)
            elif mode == "not_any" and any(self._match_keyword(kw, text) for kw in keywords):
                result.discard(eid)
            elif mode == "not_all" and all(self._match_keyword(kw, text) for kw in keywords):
                result.discard(eid)
        return result

    def _apply_probability(self, matched_ids: set[str]) -> set[str]:
        """概率过滤：probability < 100 的条目按概率激活。"""
        result = set(matched_ids)
        for eid in list(result):
            entry = self._entries.get(eid)
            if not entry:
                result.discard(eid)
                continue
            prob = int(entry.get("probability", 100))
            if prob < 100 and random.randint(1, 100) > prob:
                result.discard(eid)
                logger.debug("概率过滤: %s (probability=%d) 未激活",
                            entry.get("name", eid), prob)
        return result

    def _apply_group_competition(self, matched_ids: set[str]) -> set[str]:
        """分组竞争：同 group 的条目仅保留 group_weight 最高的。"""
        groups: dict[str, list[tuple[str, int]]] = {}
        for eid in matched_ids:
            entry = self._entries.get(eid)
            if not entry:
                continue
            group = entry.get("group", "")
            if not group:
                continue
            weight = int(entry.get("group_weight", 1))
            groups.setdefault(group, []).append((eid, weight))
        removed: set[str] = set()
        for group, members in groups.items():
            if len(members) <= 1:
                continue
            members.sort(key=lambda x: -x[1])
            winner = members[0][0]
            for eid, _ in members[1:]:
                removed.add(eid)
            logger.debug("分组竞争: group=%s winner=%s removed=%d",
                        group, self._entries.get(winner, {}).get("name", winner),
                        len(members) - 1)
        return matched_ids - removed

    def _get_constant_ids(self) -> set[str]:
        return {eid for eid, entry in self._entries.items() if entry.get("is_constant")}

    def _fuzzy_match(self, text: str) -> set[str]:
        matched: set[str] = set()
        sorted_keys = sorted(self._fuzzy_keys, key=len, reverse=True)
        for kw in sorted_keys:
            for i in range(len(kw) - 1):
                sub = kw[i:i + 2]
                if sub in text:
                    matched.update(self._index.get(kw, set()))
                    break
        return matched

    def match_with_recursive(self, text: str, timed_state: dict[str, dict] | None = None) -> list[dict]:
        if not text or not self._index:
            sticky_ids = self._get_sticky_active_ids(timed_state)
            return self._sort_by_tier(sticky_ids | self._get_constant_ids())

        initial_ids: set[str] = set()

        # 时间效应：sticky 条目 active 时始终激活
        sticky_ids = self._get_sticky_active_ids(timed_state)
        initial_ids.update(sticky_ids)

        # 时间效应：cooldown/delay 活跃期间过滤掉对应条目的关键词
        filtered_ids = self._get_timed_blocked_ids(timed_state)

        for keyword, eids in self._index.items():
            if self._match_keyword(keyword, text):
                allowed_eids = eids - filtered_ids
                initial_ids.update(allowed_eids)
        # NOT 模式条目预选
        for eid, entry in self._entries.items():
            if entry.get("match_mode", "any") in ("not_any", "not_all"):
                if eid not in filtered_ids:
                    initial_ids.add(eid)
        initial_ids = self._apply_match_mode(initial_ids, text)
        if not initial_ids:
            initial_ids.update(self._fuzzy_match(text) - filtered_ids)
        initial_ids.update(self._get_constant_ids())

        visited: set[str] = set()
        queue: deque[str] = deque(initial_ids)
        depth = 0
        while queue and depth < MAX_RECURSIVE_DEPTH:
            for _ in range(len(queue)):
                eid = queue.popleft()
                if eid in visited:
                    continue
                visited.add(eid)
                entry = self._entries.get(eid)
                if not entry:
                    continue
                triggers = entry.get("triggers_recursive", [])
                if isinstance(triggers, str):
                    try:
                        triggers = json.loads(triggers)
                    except (json.JSONDecodeError, TypeError):
                        triggers = []
                for tid in triggers:
                    if tid not in visited and tid not in filtered_ids and self._entries.get(tid):
                        queue.append(tid)
            depth += 1

        # 更新 timed_state：新匹配到的条目若含 sticky/cooldown/delay 则记录
        if timed_state is not None:
            self._apply_time_effects(visited, timed_state)

        # 概率过滤
        visited = self._apply_probability(visited)
        # 分组竞争
        visited = self._apply_group_competition(visited)

        return self._sort_by_tier(visited)

    @staticmethod
    def _get_sticky_active_ids(timed_state: dict[str, dict] | None) -> set[str]:
        """获取当前处于 active 状态的 sticky 条目。"""
        if not timed_state:
            return set()
        return {eid for eid, state in timed_state.items()
                if state.get("status") == "active" and state.get("remaining", 0) > 0}

    @staticmethod
    def _get_timed_blocked_ids(timed_state: dict[str, dict] | None) -> set[str]:
        """获取当前被 cooldown 或 delay 阻止的条目 ID。"""
        if not timed_state:
            return set()
        return {eid for eid, state in timed_state.items()
                if state.get("status") in ("cooldown", "delayed") and state.get("remaining", 0) > 0}

    def _apply_time_effects(self, matched_ids: set[str], timed_state: dict[str, dict]) -> None:
        """匹配到条目后，检查其 sticky/cooldown/delay 并更新 timed_state。"""
        for eid in matched_ids:
            entry = self._entries.get(eid)
            if not entry:
                continue
            sticky = int(entry.get("sticky", 0))
            cooldown = int(entry.get("cooldown", 0))
            delay = int(entry.get("delay", 0))

            if sticky > 0 and eid not in timed_state:
                timed_state[eid] = {"status": "active", "remaining": sticky}
                logger.debug("世界书 sticky 激活: %s (duration=%d)", entry.get("name", eid), sticky)
            if cooldown > 0 and eid not in timed_state:
                timed_state[eid] = {"status": "cooldown", "remaining": cooldown}
                logger.debug("世界书 cooldown 开始: %s (duration=%d)", entry.get("name", eid), cooldown)
            if delay > 0 and eid not in timed_state:
                timed_state[eid] = {"status": "delayed", "remaining": delay}
                logger.debug("世界书 delay 开始: %s (duration=%d)", entry.get("name", eid), delay)

    def _sort_by_tier(self, entry_ids: set[str]) -> list[dict]:
        result = []
        for eid in entry_ids:
            if eid in self._entries:
                result.append(dict(self._entries[eid]))
        tier_order = {"core": 0, "background": 1, "archived": 2}
        result.sort(key=lambda e: (tier_order.get(e.get("tier", "background"), 1),
                                    int(e.get("order", 100))))
        return result

    def reload(self, entries: list[dict]) -> None:
        """重新构建索引（世界书更新后调用）。"""
        self.build(entries)
