"""长期记忆召回 —— 根据当前文本匹配 memory.db 中的相关记忆，支持向量召回。"""

from __future__ import annotations

import logging
from typing import List, Dict

from .delta import MemoryStore

logger = logging.getLogger("trpg")

_STOP_CHARS = set("，。！？；：""''（）【】《》…—·、, \t\n\r")


def _extract_ngrams(text: str, n: int = 2) -> set[str]:
    result: set[str] = set()
    clean = text.strip()
    for i in range(len(clean) - n + 1):
        ngram = clean[i:i + n]
        if not all(c in _STOP_CHARS for c in ngram):
            result.add(ngram)
    return result


def _extract_entities(text: str) -> list[str]:
    entities: list[str] = []
    fragments: list[str] = []
    current: list[str] = []
    prev_is_cjk: bool | None = None

    for ch in text:
        is_cjk = '\u4e00' <= ch <= '\u9fff'
        is_alpha = ch.isalpha()
        if is_cjk or is_alpha:
            current_is_cjk = is_cjk
            if prev_is_cjk is not None and current_is_cjk != prev_is_cjk:
                if current:
                    fragments.append(''.join(current))
                current = []
            current.append(ch)
            prev_is_cjk = current_is_cjk
        else:
            if current:
                fragments.append(''.join(current))
                current = []
            prev_is_cjk = None
    if current:
        fragments.append(''.join(current))

    for fragment in fragments:
        if len(fragment) < 2:
            continue
        has_cjk = any('\u4e00' <= c <= '\u9fff' for c in fragment)
        if has_cjk:
            for i in range(len(fragment) - 1):
                for w in range(2, 5):
                    if i + w <= len(fragment):
                        sub = fragment[i:i + w]
                        if '\u4e00' <= sub[0] <= '\u9fff':
                            entities.append(sub)
        else:
            entities.append(fragment)

    seen = set()
    result = []
    for e in entities:
        if e not in seen:
            seen.add(e)
            result.append(e)
    return result


def format_recalled(entries: list[dict]) -> str:
    if not entries:
        return ""
    lines = ["【相关记忆】"]
    for e in entries:
        lines.append(f"- {e['entity']}: {e['relation']} -> {e['value']}")
    return "\n".join(lines)


async def recall_and_format(store: MemoryStore, game_key: str, text: str,
                            limit: int = 10) -> str:
    """从 memory store 召回记忆并格式化。优先向量召回，fallback 文本匹配。"""
    try:
        entries = await recall_best(store, game_key, text, limit=limit)
        return format_recalled(entries)
    except Exception:
        logger.exception("记忆召回失败")
        return ""


async def recall_best(store: MemoryStore, game_key: str, text: str,
                      limit: int = 10) -> List[Dict]:
    """合并向量召回与文本匹配，取并集去重后按相关度排序。

    向量召回捕获语义相近但字面不同的记忆，文本匹配捕获精确实体命中。
    两者互补，合并后能覆盖更多场景，避免向量召回结果过少时遗漏。
    """
    emb_client = getattr(store, "embedding_client", None)
    vector_results: list[dict] = []

    # 1. 向量召回
    if emb_client:
        try:
            emb = await emb_client.embed(text)
            if emb:
                vector_results = store.recall_by_vector(game_key, emb, limit=limit)
                if vector_results:
                    logger.debug("向量召回: %d 条", len(vector_results))
        except Exception:
            logger.exception("向量召回异常，降级文本匹配")

    # 2. 文本匹配（改进版：实体提取 + n-gram 打分）
    text_results = recall_by_text_improved(store, game_key, text, limit=limit)
    if not text_results:
        # 3. 基础文本匹配（fallback）
        text_results = store.recall_by_text(game_key, text, limit=limit)

    # 合并去重：向量结果在前，文本结果补充不重复的
    seen_ids = set()
    merged: list[dict] = []
    for entry in vector_results + text_results:
        eid = entry.get("id")
        if eid and eid not in seen_ids:
            seen_ids.add(eid)
            merged.append(entry)
        elif not eid:
            merged.append(entry)
    return merged[:limit]




def recall_by_text_improved(store: MemoryStore, game_key: str, text: str,
                            limit: int = 10) -> List[Dict]:
    """改进版文本召回：使用实体提取 + n-gram 匹配。"""
    gk = str(game_key)
    if not text:
        return []

    entities = _extract_entities(text)
    ngrams = _extract_ngrams(text, n=2)

    all_rows = store._conn.execute(
        "SELECT * FROM memory_entries WHERE game_key=? AND status='active' "
        "ORDER BY updated_at DESC LIMIT 500",
        (gk,),
    ).fetchall()

    scored: list[tuple[int, dict]] = []
    for row in all_rows:
        entry = dict(row)
        entity = entry.get("entity", "")
        relation = entry.get("relation", "")
        value = entry.get("value", "")
        if not str(entity).strip():
            continue
        score = 0

        if entity and entity in text:
            score += 10
        if relation and relation in text:
            score += 5
        if value and value in text:
            score += 3

        for e in entities:
            if e == entity:
                score += 15
            elif entity and e in entity:
                score += 5

        if entity and ngrams:
            entity_ngrams = _extract_ngrams(entity, n=2)
            overlap = len(ngrams & entity_ngrams)
            score += overlap * 2

        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: (-x[0], x[1].get("confidence", 1.0)))
    return [e for _, e in scored[:limit]]
