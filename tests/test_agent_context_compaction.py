"""三轮复审验收（#788 择优部分）：压缩兜底合法性与首条任务结构保留。

计费聚合与失败链上报由 usage ledger（fix/agent-budget-ledger）负责；
本文件只钉住上下文压缩的三个契约。
"""
from __future__ import annotations

import json
import sys

from src.webui.services.assistant_agent_framework import (
    HISTORY_TOTAL_CHARS,
    _bound_history,
    _compact_json_text,
)


def test_compact_fallback_always_produces_valid_json_within_limit():
    """review 第三轮 P1-2 复现：密集反斜杠 + 嵌套 JSON 的兜底必须合法且在限内。"""
    nasty = {"ids": [{"id": "a" * 60 + "\\\\" + f"{i}", "note": "\"quoted\" \\ backslash"}
                     for i in range(20)], "payload": "汉" * 4000}
    text = json.dumps(nasty, ensure_ascii=False)
    bounded = _compact_json_text(text, 4000)
    assert len(bounded) <= 4000, len(bounded)
    json.loads(bounded)  # 必须合法


def test_bound_history_keeps_first_task_structure_with_huge_payload():
    """80K 首条 JSON + 6×12K 观察：首条 kind/parts/任务目标保留，总量有界。"""
    payload = json.dumps({
        "goal": "FIRST_TASK_GOAL",
        "conversation": [{"role": "user", "content": "汉" * 8000}] * 12,
        "capability_catalog": {"capabilities": [
            {"name": f"cap_{i:03}", "label": "L" * 300} for i in range(160)],
        },
    }, ensure_ascii=False)
    first = {"kind": "request",
             "parts": [{"part_kind": "user-prompt", "content": payload}]}
    messages = [first] + [
        {"kind": "request",
         "parts": [{"part_kind": "tool-return", "content": json.dumps(
             {"ok": True, "data": "汉" * 12000})}]} for _ in range(6)]
    bounded = _bound_history(messages)
    wire = json.dumps(bounded, ensure_ascii=False)
    assert len(wire) <= HISTORY_TOTAL_CHARS + len(payload) // 2, len(wire)
    assert "FIRST_TASK_GOAL" in wire
    assert all("parts" in message for message in bounded)
    first_bounded = json.dumps(bounded[0], ensure_ascii=False)
    # 首条经结构化压缩而非截断：合法 JSON 且保留目标标记
    assert json.loads(first_bounded)  # 合法
    assert "FIRST_TASK_GOAL" in first_bounded


def test_describe_large_schema_keeps_parameter_contracts():
    """review 第三轮 P2-3：describe 大 schema 压缩保留参数名 / 类型 / required。"""
    sys.path.insert(0, ".")
    from src.webui.services.assistant_agent import _bounded_observation
    props = {f"param_{i}": {"type": "string", "description": "描述" * 300,
             "enum": ["a", "b"]} for i in range(40)}
    result = {"name": "big_action", "label": "Big", "effect": "write",
              "version": 3, "parameters": {"type": "object",
              "required": ["param_0", "param_1"], "properties": props}}
    bounded = json.loads(_bounded_observation(result, limit=4000))
    assert bounded["name"] == "big_action"
    params = bounded["parameters"]
    assert params["schema_truncated"] is True
    assert set(params["required"]) == {"param_0", "param_1"}
    assert len(params["properties"]) == 40
    assert params["properties"]["param_0"]["type"] == "string"
    assert params["properties"]["param_0"]["required"] is True
    assert json.dumps(bounded, ensure_ascii=False) and len(json.dumps(bounded)) <= 6000
