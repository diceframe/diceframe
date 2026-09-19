"""Entity / Relation record ops 测试（WR-03，母方案 §96）。

覆盖：注册 / 退役 / 关系增改删的 happy path、fail-closed（重复 id、未知对象、
未知字段、非法词表）、批原子性、容器上限、定时事件不可使用 record op、
以及 record 与 fact 在同一批 op 中的组合；最后验证 record 随整轮回滚恢复。
"""

from __future__ import annotations

import asyncio

import pytest

from src.engine.game_instance import GameInstance
from src.engine.world_state import (
    MAX_ENTITIES,
    WorldStateError,
    apply_world_ops,
    world_entities,
    world_relations,
)


def make_instance() -> GameInstance:
    return GameInstance(game_key=("web", "wr2-record-ops", "bot"))


# ---------- Entity ----------


def test_register_and_retire_entity_round_trip() -> None:
    instance = make_instance()
    summary = apply_world_ops(instance, [
        {"op": "register_entity", "entity_id": "npc:count", "kind": "npc",
         "source_ref": "module:castle"},
    ])
    entity = world_entities(instance.world_state)["npc:count"]
    assert entity["status"] == "active"
    assert entity["kind"] == "npc"
    assert entity["source_ref"] == "module:castle"
    assert entity["created_revision"] == summary["revision"]

    apply_world_ops(instance, [{"op": "retire_entity", "entity_id": "npc:count"}])
    retired = world_entities(instance.world_state)["npc:count"]
    assert retired["status"] == "retired"
    assert retired["kind"] == "npc"  # 其余字段保持不变
    assert retired["created_revision"] == entity["created_revision"]


def test_register_entity_defaults_to_public_visibility() -> None:
    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "register_entity", "entity_id": "npc:innkeeper", "kind": "npc"},
    ])
    assert world_entities(instance.world_state)["npc:innkeeper"]["visibility"] == "public"


def test_register_duplicate_entity_id_fails_closed() -> None:
    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "register_entity", "entity_id": "npc:count", "kind": "npc"},
    ])
    with pytest.raises(WorldStateError, match="reuses entity id"):
        apply_world_ops(instance, [
            {"op": "register_entity", "entity_id": "npc:count", "kind": "npc"},
        ])


def test_retire_unknown_or_inactive_entity_fails_closed() -> None:
    instance = make_instance()
    with pytest.raises(WorldStateError, match="unknown entity"):
        apply_world_ops(instance, [{"op": "retire_entity", "entity_id": "npc:ghost"}])
    apply_world_ops(instance, [
        {"op": "register_entity", "entity_id": "npc:count", "kind": "npc"},
        {"op": "retire_entity", "entity_id": "npc:count"},
    ])
    with pytest.raises(WorldStateError, match="inactive entity"):
        apply_world_ops(instance, [{"op": "retire_entity", "entity_id": "npc:count"}])


def test_record_ops_reject_unknown_fields_and_kinds() -> None:
    instance = make_instance()
    with pytest.raises(WorldStateError, match="unknown field"):
        apply_world_ops(instance, [
            {"op": "register_entity", "entity_id": "npc:x", "kind": "npc", "hp": 10},
        ])
    with pytest.raises(WorldStateError, match="kind is invalid"):
        apply_world_ops(instance, [
            {"op": "register_entity", "entity_id": "npc:x", "kind": "dragon"},
        ])
    with pytest.raises(WorldStateError, match="unknown field"):
        apply_world_ops(instance, [{"op": "retire_entity", "entity_id": "npc:x", "hard": True}])


# ---------- Relation ----------


def _add_bridge_relation(instance: GameInstance) -> None:
    apply_world_ops(instance, [
        {"op": "add_relation", "relation_id": "rel:east-harbor", "kind": "connects",
         "from_ref": "location:east_village", "to_ref": "location:harbor"},
    ])


def test_add_update_remove_relation_round_trip() -> None:
    instance = make_instance()
    _add_bridge_relation(instance)
    relation = world_relations(instance.world_state)["rel:east-harbor"]
    assert relation["status"] == "active"
    assert relation["from_ref"] == "location:east_village"

    apply_world_ops(instance, [
        {"op": "set_relation_status", "relation_id": "rel:east-harbor", "status": "severed"},
    ])
    assert world_relations(instance.world_state)["rel:east-harbor"]["status"] == "severed"

    apply_world_ops(instance, [{"op": "remove_relation", "relation_id": "rel:east-harbor"}])
    assert world_relations(instance.world_state) == {}
    # 移除后可重建（新的 revision、回到 active）。
    _add_bridge_relation(instance)
    assert world_relations(instance.world_state)["rel:east-harbor"]["status"] == "active"


def test_relation_ops_fail_closed() -> None:
    instance = make_instance()
    with pytest.raises(WorldStateError, match="unknown relation"):
        apply_world_ops(instance, [
            {"op": "set_relation_status", "relation_id": "rel:none", "status": "severed"},
        ])
    with pytest.raises(WorldStateError, match="unknown relation"):
        apply_world_ops(instance, [{"op": "remove_relation", "relation_id": "rel:none"}])
    _add_bridge_relation(instance)
    with pytest.raises(WorldStateError, match="reuses relation id"):
        _add_bridge_relation(instance)
    with pytest.raises(WorldStateError, match="status is invalid"):
        apply_world_ops(instance, [
            {"op": "set_relation_status", "relation_id": "rel:east-harbor", "status": "broken"},
        ])
    with pytest.raises(WorldStateError, match="from_ref is not a canonical id"):
        apply_world_ops(instance, [
            {"op": "add_relation", "relation_id": "rel:bad", "kind": "connects",
             "from_ref": "东村", "to_ref": "location:harbor"},
        ])


# ---------- 批原子性 / 上限 / 事件隔离 ----------


def test_record_batch_is_atomic() -> None:
    instance = make_instance()
    with pytest.raises(WorldStateError):
        apply_world_ops(instance, [
            {"op": "register_entity", "entity_id": "npc:count", "kind": "npc"},
            {"op": "set_fact", "key": "a.b", "value": 1},
            {"op": "retire_entity", "entity_id": "npc:ghost"},
        ])
    assert world_entities(instance.world_state) == {}
    assert fact_absent(instance, "a.b")


def fact_absent(instance: GameInstance, key: str) -> bool:
    from src.engine.world_state import fact_value

    return fact_value(instance.world_state, key) is None


def test_entity_container_bound_is_enforced() -> None:
    instance = make_instance()
    for index in range(MAX_ENTITIES):
        instance.world_state["entities"][f"npc:e{index}"] = {
            "entity_id": f"npc:e{index}", "kind": "npc", "status": "active",
            "visibility": "public", "source_ref": None, "created_revision": 0,
        }
    with pytest.raises(WorldStateError, match=f"exceeds {MAX_ENTITIES} entities"):
        apply_world_ops(instance, [
            {"op": "register_entity", "entity_id": "npc:overflow", "kind": "npc"},
        ])


def test_scheduled_events_cannot_use_record_ops() -> None:
    instance = make_instance()
    with pytest.raises(WorldStateError, match="cannot use"):
        apply_world_ops(instance, [
            {"op": "schedule_event", "event_id": "night.ambush",
             "due_at": {"day": 1, "minute": 1200},
             "ops": [{"op": "register_entity", "entity_id": "npc:ambusher", "kind": "creature"}]},
        ])


def test_records_and_facts_compose_in_one_batch() -> None:
    instance = make_instance()
    summary = apply_world_ops(instance, [
        {"op": "register_entity", "entity_id": "npc:count", "kind": "npc"},
        {"op": "add_relation", "relation_id": "rel:count-castle", "kind": "located_at",
         "from_ref": "npc:count", "to_ref": "location:castle"},
        {"op": "set_fact", "key": "actor:count.location", "value": "castle"},
    ])
    assert [item["op"] for item in summary["applied"]] == [
        "register_entity", "add_relation", "set_fact",
    ]
    assert set(world_entities(instance.world_state)) == {"npc:count"}
    assert set(world_relations(instance.world_state)) == {"rel:count-castle"}


# ---------- 整轮回滚恢复 ----------


def test_registered_entity_is_reverted_by_whole_round_rollback() -> None:
    instance = make_instance()
    instance.solo_mode = True
    instance.players["p1"] = {"character_name": "Alice", "character_sheet": {"hp": 10}}
    asyncio.run(instance.start_round())
    asyncio.run(instance.add_action("p1", "召唤伯爵"))
    assert asyncio.run(instance.try_advance()) is True
    apply_world_ops(instance, [
        {"op": "register_entity", "entity_id": "npc:count", "kind": "npc"},
        {"op": "add_relation", "relation_id": "rel:count-castle", "kind": "located_at",
         "from_ref": "npc:count", "to_ref": "location:castle"},
    ])
    asyncio.run(instance.finish_judgment("伯爵出现了。"))

    assert asyncio.run(instance.rollback_last_round()) is not None
    assert world_entities(instance.world_state) == {}
    assert world_relations(instance.world_state) == {}
