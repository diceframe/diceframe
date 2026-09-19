"""World Runtime v2 契约测试（WR-01）。

只测契约层本身：canonical id / source_ref / Entity / Relation / Process /
WorldEvent 的 shape 校验。不涉及持久化与 GameInstance（那是 WR-02/03 的 scope）。
"""

from __future__ import annotations

import pytest

from src.engine.world import (
    ENTITY_KINDS,
    PROCESS_STATUSES,
    RELATION_KINDS,
    SOURCE_REF_KINDS,
    WORLD_EVENT_KINDS,
    WorldContractError,
    canonical_id,
    validate_entity_record,
    validate_process_record,
    validate_relation_record,
    validate_source_ref,
    validate_world_event_record,
)


# ---------- canonical id ----------


@pytest.mark.parametrize("value", [
    "npc:count", "location:old_bridge", "actor:p1.location",
    "player:abc123", "ritual:clearing.status", "A1",
])
def test_canonical_ids_accept_world_coordinates(value: str) -> None:
    assert canonical_id(value) == value


@pytest.mark.parametrize("value", [
    "", " 带空格", "中文地名", "npc:中文", "-leading-dash",
    "has/slash", "x" * 121, None, 42,
])
def test_canonical_ids_reject_display_names_and_paths(value: object) -> None:
    with pytest.raises(WorldContractError):
        canonical_id(value)


# ---------- source_ref ----------


@pytest.mark.parametrize("source_kind", SOURCE_REF_KINDS)
def test_source_ref_accepts_every_declared_kind(source_kind: str) -> None:
    assert validate_source_ref(f"{source_kind}:castle-module") == f"{source_kind}:castle-module"


@pytest.mark.parametrize("value", [
    "module:", "module:中文名", "unknown_kind:castle", ":castle",
    "module:castle extra", 42, None, "module:" + "x" * 200,
])
def test_source_ref_rejects_bad_shapes_and_kinds(value: object) -> None:
    with pytest.raises(WorldContractError):
        validate_source_ref(value)


# ---------- Entity ----------


def _entity(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "entity_id": "npc:count",
        "kind": "npc",
        "status": "active",
        "visibility": "public",
        "source_ref": "module:castle-module",
        "created_revision": 3,
    }
    record.update(overrides)
    return record


def test_entity_record_round_trips_valid_shape() -> None:
    validated = validate_entity_record(_entity())
    assert validated["entity_id"] == "npc:count"
    assert validated["source_ref"] == "module:castle-module"


def test_entity_record_rejects_mechanics_and_unknown_fields() -> None:
    # 母方案 §14：HP/AC/金币/先攻永远不进 entity —— 未知字段 fail closed。
    for field in ("hp", "armor_class", "wallet", "initiative", "memory"):
        with pytest.raises(WorldContractError):
            validate_entity_record(_entity(**{field: 10}))


@pytest.mark.parametrize("overrides", [
    {"entity_id": "坏 id"},
    {"kind": "dragon"},
    {"status": "deleted"},
    {"visibility": "party"},
    {"created_revision": -1},
    {"created_revision": True},
    {"source_ref": "not-a-source-ref"},
])
def test_entity_record_fail_closed_on_bad_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(WorldContractError):
        validate_entity_record(_entity(**overrides))


def test_entity_source_ref_is_optional() -> None:
    validated = validate_entity_record(_entity(source_ref=None))
    assert validated["source_ref"] is None


# ---------- Relation ----------


def _relation(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "relation_id": "rel:east-harbor",
        "kind": "connects",
        "from_ref": "location:east_village",
        "to_ref": "location:harbor",
        "status": "active",
        "visibility": "public",
        "source_ref": "adventure:castle",
        "created_revision": 1,
    }
    record.update(overrides)
    return record


def test_relation_record_round_trips_valid_shape() -> None:
    validated = validate_relation_record(_relation())
    assert validated["from_ref"] == "location:east_village"
    assert validated["to_ref"] == "location:harbor"


@pytest.mark.parametrize("kind", RELATION_KINDS)
def test_relation_kinds_are_the_declared_vocabulary(kind: str) -> None:
    assert validate_relation_record(_relation(kind=kind))["kind"] == kind


def test_relation_severed_status_is_valid() -> None:
    validated = validate_relation_record(_relation(status="severed"))
    assert validated["status"] == "severed"


@pytest.mark.parametrize("overrides", [
    {"kind": "loves"},
    {"status": "broken"},
    {"from_ref": "npc:不存在"},
    {"visibility": "secret"},
])
def test_relation_record_fail_closed_on_bad_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(WorldContractError):
        validate_relation_record(_relation(**overrides))


# ---------- Process ----------


def _process(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "process_id": "process:clearing-ritual",
        "kind": "ritual",
        "status": "running",
        "participants": ["npc:warlock"],
        "location": "location:clearing",
        "started_at": {"day": 1, "minute": 600},
        "due_at": {"day": 1, "minute": 840},
        "visibility": "gm",
        "source_ref": "adventure:castle",
    }
    record.update(overrides)
    return record


def test_process_record_round_trips_valid_shape() -> None:
    validated = validate_process_record(_process())
    assert validated["started_at"] == {"day": 1, "minute": 600}
    assert validated["due_at"] == {"day": 1, "minute": 840}


@pytest.mark.parametrize("status", PROCESS_STATUSES)
def test_process_statuses_are_the_declared_vocabulary(status: str) -> None:
    assert validate_process_record(_process(status=status))["status"] == status


@pytest.mark.parametrize("overrides", [
    {"status": "pending"},
    {"started_at": None},
    {"started_at": {"day": 0, "minute": 0}},
    {"started_at": {"day": 1, "minute": 1440}},
    {"started_at": "2026-09-19T00:00:00Z"},
    {"due_at": {"day": 1}},
    {"participants": "npc:warlock"},
    {"participants": [f"npc:{i}" for i in range(33)]},
    {"participants": ["不是 canonical"]},
])
def test_process_record_fail_closed_on_bad_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(WorldContractError):
        validate_process_record(_process(**overrides))


def test_process_due_at_is_optional() -> None:
    validated = validate_process_record(_process(due_at=None))
    assert validated["due_at"] is None


# ---------- WorldEvent ----------


def _world_event(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "event_id": "evt:bridge-destroyed-1",
        "kind": "relation_status_changed",
        "revision": 12,
        "clock": {"day": 2, "minute": 30},
        "source_round": 4,
        "visibility": "public",
        "subject": "rel:east-harbor",
        "summary": "旧桥被摧毁",
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize("kind", list(WORLD_EVENT_KINDS))
def test_world_event_kinds_are_the_declared_vocabulary(kind: str) -> None:
    assert validate_world_event_record(_world_event(kind=kind))["kind"] == kind


def test_world_event_round_trips_valid_shape() -> None:
    validated = validate_world_event_record(_world_event())
    assert validated["clock"] == {"day": 2, "minute": 30}
    assert validated["summary"] == "旧桥被摧毁"


@pytest.mark.parametrize("overrides", [
    {"kind": "narration_happened"},
    {"revision": -1},
    {"clock": {"day": 1}},
    {"clock": None},
    {"source_round": True},
    {"visibility": "gm_only"},
    {"summary": "x" * 401},
    {"payload": {"arbitrary": "data"}},
])
def test_world_event_fail_closed_on_bad_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(WorldContractError):
        validate_world_event_record(_world_event(**overrides))


def test_entity_kinds_come_from_the_declared_vocabulary() -> None:
    assert set(ENTITY_KINDS) == {
        "pc", "npc", "creature", "location", "item", "object", "faction",
    }
