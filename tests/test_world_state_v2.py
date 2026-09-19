"""WorldState v2 容器与迁移测试（WR-02）。

覆盖母方案 §67 / §95 的硬性要求：

- ``fresh_world_state`` 是 v2 形状（entities / relations / processes 空容器）；
- ``ensure_world_state`` 把 v1 容器幂等升级到 v2，旧 facts / clock /
  scheduled_events 逐字保留，新容器为空且**不猜测**任何实体；
- 实例迁移 15 → 16 与 ``ensure_world_state`` 产出完全一致的形状，重复迁移
  幂等，未知 world schema 不被改写；
- v2 记录容器在写入路径 fail closed：损坏记录 / key 与记录 id 不一致 /
  机制字段（HP 等）一律拒绝；
- 三个容器的防御性读取端：损坏容器读作空，不抛异常、不猜数据。
"""

from __future__ import annotations

import pytest

from src.engine.game_instance import GameInstance
from src.engine.world_state import (
    WORLD_STATE_SCHEMA_VERSION,
    WorldStateError,
    apply_world_ops,
    ensure_world_state,
    fresh_world_state,
    world_entities,
    world_processes,
    world_relations,
)
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    migrate_game_state_payload,
)


def test_fresh_world_state_is_v2() -> None:
    state = fresh_world_state()
    assert state["schema_version"] == 2
    assert state["entities"] == {}
    assert state["relations"] == {}
    assert state["processes"] == {}
    assert state["facts"] == {}
    assert state["scheduled_events"] == {}


def test_ensure_world_state_upgrades_v1_idempotently_without_guessing() -> None:
    v1 = {
        "schema_version": 1,
        "revision": 5,
        "clock": {"day": 2, "minute": 90},
        "facts": {
            "actor:p1.location": {
                "value": "village_east", "visibility": "public",
                "source_round": 3, "updated_revision": 5,
            },
        },
        "scheduled_events": {
            "night.ambush": {
                "event_id": "night.ambush", "due_at": {"day": 2, "minute": 100},
                "status": "pending", "label": "伏击",
                "ops": [{"op": "set_fact", "key": "world.alert", "value": "high"}],
            },
        },
    }
    upgraded = ensure_world_state(v1)
    assert upgraded["schema_version"] == WORLD_STATE_SCHEMA_VERSION
    assert upgraded["revision"] == 5
    assert upgraded["clock"] == {"day": 2, "minute": 90}
    assert upgraded["facts"] == v1["facts"]
    assert upgraded["scheduled_events"] == v1["scheduled_events"]
    assert upgraded["entities"] == {}
    assert upgraded["relations"] == {}
    assert upgraded["processes"] == {}
    # 幂等：重复 ensure 不再改变形状。
    assert ensure_world_state(upgraded) == upgraded
    # 原始输入不被就地修改（caller 的对象保持 v1）。
    assert v1["schema_version"] == 1


def test_ensure_world_state_leaves_unknown_schema_untouched() -> None:
    unknown = {"schema_version": 99, "revision": 1, "facts": {}}
    assert ensure_world_state(unknown) == unknown


def _v15_payload() -> dict[str, object]:
    return {
        "game_key": ["web", "wr2", "bot"],
        "instance_schema_version": 15,
        "world_state": {
            "schema_version": 1,
            "revision": 4,
            "clock": {"day": 1, "minute": 120},
            "facts": {
                "location:bridge.passable": {
                    "value": False, "visibility": "public",
                    "source_round": 2, "updated_revision": 4,
                },
            },
            "scheduled_events": {},
        },
    }


def test_instance_migration_15_to_16_upgrades_world_container() -> None:
    migrated = migrate_game_state_payload(_v15_payload())
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    world = migrated["world_state"]
    assert world["schema_version"] == 2
    assert world["revision"] == 4
    assert world["facts"] == _v15_payload()["world_state"]["facts"]  # type: ignore[index]
    assert world["entities"] == {} and world["relations"] == {} and world["processes"] == {}
    # 幂等：对已迁移 payload 重复迁移，世界容器逐字不变。
    assert migrate_game_state_payload(migrated) == migrated


def test_instance_migration_gives_legacy_saves_a_v2_container() -> None:
    migrated = migrate_game_state_payload({
        "game_key": ["web", "older", "bot"],
        "instance_schema_version": 12,
        "players": {"p1": {"character_sheet": {"hp": 8}}},
    })
    assert migrated["world_state"] == fresh_world_state()


def test_instance_migration_does_not_rewrite_unknown_world_schema() -> None:
    payload = {
        "game_key": ["web", "future", "bot"],
        "instance_schema_version": 15,
        "world_state": {"schema_version": 99, "revision": 2, "facts": {}},
    }
    migrated = migrate_game_state_payload(payload)
    assert migrated["world_state"]["schema_version"] == 99


# ---------- v2 记录容器的 fail-closed 校验 ----------


def _entity_record() -> dict[str, object]:
    return {
        "entity_id": "npc:count",
        "kind": "npc",
        "status": "active",
        "visibility": "public",
        "source_ref": "module:castle",
        "created_revision": 1,
    }


def _instance_with_container(name: str, content: object) -> GameInstance:
    instance = GameInstance(game_key=("web", "wr2-records", "bot"))
    state = fresh_world_state()
    state[name] = content
    instance.world_state = state
    return instance


def test_valid_entity_record_passes_the_write_path() -> None:
    instance = _instance_with_container("entities", {"npc:count": _entity_record()})
    summary = apply_world_ops(instance, [{"op": "set_fact", "key": "a.b", "value": 1}])
    assert summary["revision"] == 1


@pytest.mark.parametrize("mutation", [
    {"kind": "dragon"},
    {"status": "deleted"},
    {"hp": 12},
])
def test_corrupt_entity_record_fails_closed(mutation: dict[str, object]) -> None:
    record = _entity_record()
    record.update(mutation)
    instance = _instance_with_container("entities", {"npc:count": record})
    with pytest.raises(WorldStateError):
        apply_world_ops(instance, [{"op": "set_fact", "key": "a.b", "value": 1}])


def test_entity_record_id_must_match_container_key() -> None:
    instance = _instance_with_container("entities", {"npc:impostor": _entity_record()})
    with pytest.raises(WorldStateError):
        apply_world_ops(instance, [{"op": "set_fact", "key": "a.b", "value": 1}])


def test_corrupt_process_record_fails_closed() -> None:
    record = {
        "process_id": "process:ritual",
        "kind": "ritual",
        "status": "running",
        "participants": [],
        "location": None,
        "started_at": "2026-09-19",  # wall clock：非法
        "due_at": None,
        "visibility": "gm",
        "source_ref": None,
    }
    instance = _instance_with_container("processes", {"process:ritual": record})
    with pytest.raises(WorldStateError):
        apply_world_ops(instance, [{"op": "set_fact", "key": "a.b", "value": 1}])


def test_missing_v2_container_fails_closed_on_write() -> None:
    instance = GameInstance(game_key=("web", "wr2-records", "bot"))
    state = fresh_world_state()
    del state["processes"]
    instance.world_state = state
    with pytest.raises(WorldStateError):
        apply_world_ops(instance, [{"op": "set_fact", "key": "a.b", "value": 1}])


# ---------- 防御性读取端 ----------


@pytest.mark.parametrize("container", ["entities", "relations", "processes"])
def test_record_readers_degrade_silently_on_corrupt_containers(container: str) -> None:
    instance = GameInstance(game_key=("web", "wr2-read", "bot"))
    state = fresh_world_state()
    state[container] = "garbage"
    instance.world_state = state
    reader = {"entities": world_entities, "relations": world_relations, "processes": world_processes}[container]
    assert reader(instance.world_state) == {}


def test_record_readers_return_copies() -> None:
    instance = GameInstance(game_key=("web", "wr2-read", "bot"))
    state = fresh_world_state()
    record = _entity_record()
    state["entities"] = {"npc:count": record}
    snapshot = world_entities(state)
    snapshot["npc:count"]["status"] = "retired"
    assert state["entities"]["npc:count"]["status"] == "active"
