"""Structured gates 测试（ADV2-02，母方案 §25/§118）。

覆盖：§25 词表逐类求值（world 四类 / adventure 两类 / rules 经 adapter）、
AND 语义、证据不足=不满足（rules adapter 缺席 / 读取异常）、未知 gate 类型
与未知字段 fail closed、adv2 图契约接受合法 gate。
"""

from __future__ import annotations

import pytest

from src.adventures.gates import (
    GATE_TYPES,
    GateError,
    gates_satisfied,
    validate_gates,
)
from src.engine.game_instance import GameInstance
from src.engine.world_state import apply_world_ops


def make_instance() -> GameInstance:
    return GameInstance(game_key=("web", "adv2-gates", "bot"))


def _populated_instance() -> GameInstance:
    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "location:bridge.passable", "value": False},
        {"op": "register_entity", "entity_id": "npc:count", "kind": "npc"},
        {"op": "add_relation", "relation_id": "rel:bridge-road", "kind": "connects",
         "from_ref": "location:east", "to_ref": "location:harbor"},
        {"op": "start_process", "process_id": "process:ritual", "kind": "ritual"},
        {"op": "complete_process", "process_id": "process:ritual"},
    ])
    return instance


@pytest.mark.parametrize("gate,expected", [
    ({"type": "world.fact_equals", "key": "location:bridge.passable", "value": False}, True),
    ({"type": "world.fact_equals", "key": "location:bridge.passable", "value": True}, False),
    ({"type": "world.entity_status", "id": "npc:count", "value": "active"}, True),
    ({"type": "world.entity_status", "id": "npc:count", "value": "retired"}, False),
    ({"type": "world.relation_status", "id": "rel:bridge-road", "value": "active"}, True),
    ({"type": "world.relation_status", "id": "rel:bridge-road", "value": "severed"}, False),
    ({"type": "world.process_status", "id": "process:ritual", "value": "completed"}, True),
    ({"type": "world.process_status", "id": "process:ritual", "value": "running"}, False),
])
def test_world_gates_evaluate_against_authoritative_state(gate: dict, expected: bool) -> None:
    assert gates_satisfied([gate], instance=_populated_instance(), progress={}) is expected


def test_adventure_gates_use_progress() -> None:
    progress = {"completed_milestones": ["mile_castle"], "completed_objectives": ["obj_crown"]}
    assert gates_satisfied(
        [{"type": "adventure.milestone", "id": "mile_castle", "value": True}],
        instance=make_instance(), progress=progress,
    )
    assert gates_satisfied(
        [{"type": "adventure.objective_status", "id": "obj_crown", "value": "completed"}],
        instance=make_instance(), progress=progress,
    )
    assert not gates_satisfied(
        [{"type": "adventure.objective_status", "id": "obj_crown", "value": "active"}],
        instance=make_instance(), progress=progress,
    )


def test_rules_gates_need_evaluator_and_default_to_unsatisfied() -> None:
    gate = {"type": "rules.party_level", "id": "party", "value": 5}
    # adapter 缺席：证据不足 → 不满足（不猜）。
    assert gates_satisfied([gate], instance=make_instance(), progress={}) is False
    assert gates_satisfied(
        [gate], instance=make_instance(), progress={},
        rules_evaluator=lambda gate_type, gate_id, value: value == 5,
    )


def test_gates_are_and_semantics() -> None:
    instance = _populated_instance()
    assert gates_satisfied([
        {"type": "world.entity_status", "id": "npc:count", "value": "active"},
        {"type": "world.relation_status", "id": "rel:bridge-road", "value": "active"},
    ], instance=instance, progress={})
    assert not gates_satisfied([
        {"type": "world.entity_status", "id": "npc:count", "value": "active"},
        {"type": "world.relation_status", "id": "rel:bridge-road", "value": "severed"},
    ], instance=instance, progress={})


def test_gate_vocabulary_is_closed_and_fail_closed() -> None:
    with pytest.raises(GateError, match="not supported"):
        validate_gates([{"type": "world.weather", "id": "x", "value": "rain"}], label="t")
    with pytest.raises(GateError, match="unknown field"):
        validate_gates([{"type": "world.fact_equals", "key": "a.b", "value": 1, "expr": "1+1"}], label="t")
    with pytest.raises(GateError, match="exceed"):
        validate_gates(
            [{"type": "world.fact_equals", "key": "a.b", "value": 1}] * 9, label="t",
        )
    assert len(GATE_TYPES) == 9


def test_corrupt_world_degrades_to_unsatisfied() -> None:
    instance = make_instance()
    instance.world_state = {"schema_version": 99, "facts": {}}
    assert gates_satisfied(
        [{"type": "world.fact_equals", "key": "a.b", "value": 1}],
        instance=instance, progress={},
    ) is False
