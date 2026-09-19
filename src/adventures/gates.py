"""Structured adventure gates (ADV2-02, 母方案 §25/§118).

v2 transition 的条件词表——**有限枚举，不是 DSL**（§25 硬边界：无 eval /
javascript / python / 表达式语言）。每个条件是一个结构化断言：

\`\`\`json
{"type": "world.fact_equals", "key": "location:bridge.passable", "value": false}
\`\`\`

词表（母方案 §25）：

\`\`\`text
world.fact_equals / world.entity_status / world.relation_status / world.process_status
adventure.milestone / adventure.objective_status
rules.outcome / rules.party_level / rules.item_possession   （经 runtime adapter）
\`\`\`

求值语义：

- 一个 transition 的 conditions 列表是 **AND**：全部满足才开放；
- **证据不足 = 不满足**（rules adapter 缺席 / 读取失败 → False）——gate 永远
  不能凭"不知道"放行（与 §32C 的"未知≠false"分工：legality 阻断的是玩家
  行动矛盾，gate 决定的是剧情分支走向，走向错误比走向保守更危险时选保守）；
- rules.* 条件经调用方注入的 evaluator（可调用）求值，返回值必须为 bool。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.engine.world_state import (
    fact_value,
    world_entities,
    world_processes,
    world_relations,
)

GATE_TYPES = (
    "world.fact_equals",
    "world.entity_status",
    "world.relation_status",
    "world.process_status",
    "adventure.milestone",
    "adventure.objective_status",
    "rules.outcome",
    "rules.party_level",
    "rules.item_possession",
)

MAX_GATES_PER_TRANSITION = 8


class GateError(ValueError):
    """A gate is malformed or cannot be evaluated: fail closed."""


def validate_gate(raw: Any) -> dict[str, Any]:
    """Validate one structured gate; unknown fields / types fail closed."""

    if not isinstance(raw, dict):
        raise GateError("gate must be an object")
    gate_type = str(raw.get("type") or "")
    if gate_type not in GATE_TYPES:
        raise GateError(f"gate type is not supported: {gate_type!r}")
    allowed = {"type", "key", "value", "id"}
    extra = sorted(set(raw) - allowed)
    if extra:
        raise GateError(f"gate has unknown field: {extra[0]!r}")
    # 词表字段约定：world.fact_equals 用 key；其余用 id；value 一律必填。
    gate = {"type": gate_type, "value": raw.get("value")}
    if gate_type == "world.fact_equals":
        gate["key"] = str(raw.get("key") or "")
        if not gate["key"]:
            raise GateError("world.fact_equals requires key")
    elif gate_type in ("adventure.milestone", "adventure.objective_status", "rules.item_possession", "rules.outcome", "rules.party_level"):
        gate["id"] = str(raw.get("id") or "")
        if not gate["id"]:
            raise GateError(f"{gate_type} requires id")
    else:
        gate["id"] = str(raw.get("id") or "")
        if not gate["id"]:
            raise GateError(f"{gate_type} requires id")
    return gate


def validate_gates(raw: Any, *, label: str) -> list[dict[str, Any]]:
    """Validate a transition's conditions list (bounded, all-or-nothing)."""

    if raw is None:
        return []
    if not isinstance(raw, list):
        raise GateError(f"{label} conditions must be an array")
    if len(raw) > MAX_GATES_PER_TRANSITION:
        raise GateError(f"{label} conditions exceed {MAX_GATES_PER_TRANSITION}")
    return [validate_gate(gate) for gate in raw]


def _gate_satisfied(
    gate: Mapping[str, Any],
    *,
    instance: Any,
    progress: Mapping[str, Any],
    rules_evaluator: Callable[[str, str, Any], bool] | None,
) -> bool:
    gate_type = str(gate.get("type") or "")
    expected = gate.get("value")
    try:
        if gate_type == "world.fact_equals":
            return fact_value(getattr(instance, "world_state", None), str(gate.get("key") or "")) == expected
        if gate_type == "world.entity_status":
            entity = world_entities(getattr(instance, "world_state", None)).get(str(gate.get("id") or ""))
            return entity is not None and entity.get("status") == expected
        if gate_type == "world.relation_status":
            relation = world_relations(getattr(instance, "world_state", None)).get(str(gate.get("id") or ""))
            return relation is not None and relation.get("status") == expected
        if gate_type == "world.process_status":
            process = world_processes(getattr(instance, "world_state", None)).get(str(gate.get("id") or ""))
            return process is not None and process.get("status") == expected
        if gate_type == "adventure.milestone":
            return str(gate.get("id") or "") in {
                str(item) for item in (progress.get("completed_milestones") or [])
            }
        if gate_type == "adventure.objective_status":
            objective_id = str(gate.get("id") or "")
            completed = {str(item) for item in (progress.get("completed_objectives") or [])}
            status = "completed" if objective_id in completed else "active"
            return status == str(expected or "")
        if gate_type.startswith("rules."):
            if rules_evaluator is None:
                return False  # 证据不足：不满足（不猜）。
            result = rules_evaluator(gate_type, str(gate.get("id") or ""), expected)
            return bool(result)
    except Exception:  # noqa: BLE001 - 读取失败 = 证据不足 = 不满足
        return False
    return False


def gates_satisfied(
    gates: list[dict[str, Any]],
    *,
    instance: Any,
    progress: Mapping[str, Any],
    rules_evaluator: Callable[[str, str, Any], bool] | None = None,
) -> bool:
    """AND over all gates; empty list = open."""

    return all(
        _gate_satisfied(gate, instance=instance, progress=progress, rules_evaluator=rules_evaluator)
        for gate in gates
    )


__all__ = [
    "GATE_TYPES",
    "MAX_GATES_PER_TRANSITION",
    "GateError",
    "gates_satisfied",
    "validate_gate",
    "validate_gates",
]
