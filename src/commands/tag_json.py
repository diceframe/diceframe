"""LLM JSON 回退解析与叙事提取。"""

from __future__ import annotations

import json


def safe_parse_json(text: str) -> dict:
    from src.llm.parser import _find_balanced_json, _repair_json

    if not text or not text.strip():
        raise ValueError("空内容")
    # 先尝试直接解析
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # 括号计数截取 JSON 段
    balanced = _find_balanced_json(text)
    if balanced:
        try:
            return json.loads(balanced)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_repair_json(balanced))
        except json.JSONDecodeError:
            pass
    # 全量修复后解析
    try:
        return json.loads(_repair_json(text))
    except json.JSONDecodeError:
        pass
    raise ValueError("无法解析 JSON")


def extract_narration_from_response(response) -> str:
    from src.llm.parser import sanitize_narration

    try:
        data = safe_parse_json(response.content)
        return sanitize_narration(data.get("narration", "") or response.content)
    except Exception:
        return sanitize_narration(response.narration or response.content)
