"""Legality + Relation 测试（WR-08，母方案 §101）。

新增证据源：``severed`` 的 ``connects`` 关系阻断声明路线（方向无关、
visibility 不软化判定、活跃关系不阻断、无关系 fail-open）。同时锁定
v1 fact 路径与旧存档（空 relations）行为完全不变。
"""

from __future__ import annotations

import asyncio

import pytest

from src.engine.game_instance import GameInstance
from src.engine.world_legality import evaluate_world_requirements
from src.engine.world_state import apply_world_ops


def make_instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "wr2-legality-rel", "bot"))
    instance.players["p1"] = {"character_name": "Alice", "character_sheet": {"hp": 10}}
    return instance


def _establish_locations(instance: GameInstance) -> None:
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "actor:p1.location", "value": "east_village"},
        {"op": "add_relation", "relation_id": "rel:east-bridge", "kind": "connects",
         "from_ref": "location:east_village", "to_ref": "location:bridge"},
        {"op": "add_relation", "relation_id": "rel:bridge-harbor", "kind": "connects",
         "from_ref": "location:bridge", "to_ref": "location:harbor"},
    ])


def _move_requirement(destination: str = "harbor") -> list[dict]:
    return [{
        "player": "p1", "kind": "move",
        "location": destination,
        "via": ["east_village", "bridge"],
    }]


def test_severed_connect_relation_blocks_the_route() -> None:
    instance = make_instance()
    _establish_locations(instance)
    apply_world_ops(instance, [
        {"op": "set_relation_status", "relation_id": "rel:bridge-harbor", "status": "severed"},
    ])
    result = evaluate_world_requirements(instance, _move_requirement())
    assert result["applied"] == []
    note = result["notes"][0]
    assert note["code"] == "ROUTE_IMPASSABLE"
    # 被切断的是 bridge→harbor 一段：不可达的是 harbor。
    assert note["location"] == "harbor"


def test_active_connect_relation_does_not_block() -> None:
    instance = make_instance()
    _establish_locations(instance)
    result = evaluate_world_requirements(instance, _move_requirement())
    assert result["notes"] == []
    assert result["applied"][0]["location"] == "harbor"


def test_severed_relation_is_direction_agnostic() -> None:
    """关系按无向证据匹配：存储方向 harbor→bridge，路线 bridge→harbor 同样被切。"""
    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "actor:p1.location", "value": "east_village"},
        {"op": "add_relation", "relation_id": "rel:harbor-bridge", "kind": "connects",
         "from_ref": "location:harbor", "to_ref": "location:bridge"},
    ])
    requirement = [{"player": "p1", "kind": "move", "location": "harbor", "via": ["bridge"]}]
    result = evaluate_world_requirements(instance, requirement)
    assert result["notes"] == []  # 关系活跃：bridge→harbor 可通行

    apply_world_ops(instance, [
        {"op": "set_relation_status", "relation_id": "rel:harbor-bridge", "status": "severed"},
    ])
    result = evaluate_world_requirements(instance, requirement)
    assert result["notes"][0]["location"] == "harbor"


def test_unknown_connection_never_blocks() -> None:
    instance = make_instance()
    _establish_locations(instance)
    # east_village 与 harbor 之间没有登记任何 connects 关系：无证据不阻断。
    requirement = [{
        "player": "p1", "kind": "move", "location": "harbor", "via": ["east_village"],
    }]
    result = evaluate_world_requirements(instance, requirement)
    assert result["notes"] == []


def test_fact_passable_path_still_blocks_first() -> None:
    instance = make_instance()
    _establish_locations(instance)
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "location:bridge.passable", "value": False},
    ])
    result = evaluate_world_requirements(instance, _move_requirement())
    assert result["applied"] == []
    assert result["notes"][0]["location"] == "bridge"


def test_relation_endpoints_establish_known_locations() -> None:
    instance = make_instance()
    _establish_locations(instance)
    # harbor 只作为 relation endpoint 存在（没有任何 location:harbor.* fact），
    # 依旧算"世界已知的地点"，可参与判定。
    result = evaluate_world_requirements(instance, _move_requirement())
    assert result["notes"] == []
    assert result["applied"][0]["location"] == "harbor"


def test_old_save_without_relations_keeps_fact_only_behaviour() -> None:
    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "actor:p1.location", "value": "east_village"},
        {"op": "set_fact", "key": "location:harbor.passable", "value": False},
    ])
    result = evaluate_world_requirements(instance, [{
        "player": "p1", "kind": "move", "location": "harbor",
    }])
    assert result["notes"][0]["code"] == "ROUTE_IMPASSABLE"
    assert result["notes"][0]["location"] == "harbor"


def test_severed_relation_survives_rollback_restoration() -> None:
    """severed 状态随世界容器快照走：rollback 恢复到 severed 之前，则不再阻断。"""
    instance = make_instance()
    instance.solo_mode = True
    _establish_locations(instance)
    asyncio.run(instance.start_round())
    asyncio.run(instance.add_action("p1", "过桥"))
    assert asyncio.run(instance.try_advance()) is True

    # 本轮判定里切断桥路 → rollback → 关系回到 active，路线恢复。
    apply_world_ops(instance, [
        {"op": "set_relation_status", "relation_id": "rel:bridge-harbor", "status": "severed"},
    ])
    asyncio.run(instance.finish_judgment("桥断成了两截。"))
    assert asyncio.run(instance.rollback_last_round()) is not None
    result = evaluate_world_requirements(instance, _move_requirement())
    assert result["notes"] == []


@pytest.mark.parametrize("visibility", ["public", "gm"])
def test_relation_visibility_never_softens_legality(visibility: str) -> None:
    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "actor:p1.location", "value": "east_village"},
        {"op": "add_relation", "relation_id": "rel:bridge-harbor", "kind": "connects",
         "from_ref": "location:bridge", "to_ref": "location:harbor",
         "visibility": visibility},
        {"op": "set_relation_status", "relation_id": "rel:bridge-harbor", "status": "severed"},
    ])
    result = evaluate_world_requirements(instance, _move_requirement())
    assert result["notes"][0]["code"] == "ROUTE_IMPASSABLE"
