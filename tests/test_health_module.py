"""Health module persistence, live reporting, and encode-only event retention."""

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.health import health_payload, mark_health_event, record_health_event
from src.engine.module_state import ModuleStateError
from src.engine.modules import health as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v23_to_v24,
    migrate_game_state_payload,
)


def test_migration_moves_values_without_mutation_and_full_upgrade_is_idempotent():
    payload = {"instance_schema_version": 23, "health_events": [{"id": "event"}],
               "health_status": {"llm": "warning"},
               "modules": {"extension": {"schema_version": 99, "extra": [1]}}}
    before = deepcopy(payload)
    migrated = migrate_game_state_payload(payload)
    assert payload == before
    assert "health_events" not in migrated and "health_status" not in migrated
    assert migrated["modules"]["health"] == {"schema_version": 1, "health_events": payload["health_events"],
                                             "health_status": payload["health_status"]}
    assert migrated["modules"]["extension"] == payload["modules"]["extension"]
    assert migrate_game_state_payload(migrated) == migrated


@pytest.mark.parametrize("slot", [{}, {"schema_version": 99, "extra": [1]}, module.fresh()])
def test_single_step_preserves_existing_slot_and_is_idempotent(slot):
    payload = {"health_events": [{"old": True}], "health_status": {}, "modules": {"health": slot}}
    migrated = _migrate_v23_to_v24(payload)
    assert migrated["modules"]["health"] is slot
    before = deepcopy(migrated)
    assert _migrate_v23_to_v24(migrated) == before


@pytest.mark.parametrize("value", [None, False, 1, "invalid"])
def test_invalid_containers_default(value):
    assert _migrate_v23_to_v24({"health_events": value, "health_status": value})["modules"]["health"] == module.fresh()
    assert module.ensure({"schema_version": 1, "health_events": value, "health_status": value}) == module.fresh()
    assert module.ensure(value) == module.fresh()


def test_live_properties_replacement_and_roundtrip():
    instance = GameInstance(game_key=("web", "health", "bot"))
    events = [{"id": "one"}]
    status = {"save": "error"}
    instance.health_events = events
    instance.health_status = status
    assert instance.health_events is events is instance.modules["health"]["health_events"]
    assert instance.health_status is status is instance.modules["health"]["health_status"]
    instance.health_events.append({"id": "two"})
    saved = instance.to_dict()
    assert "health_events" not in saved and "health_status" not in saved
    restored = GameInstance.from_dict(saved)
    assert restored.health_events == events
    assert restored.health_status == status
    instance.health_events = []
    instance.health_status = {}
    assert events and status


@pytest.mark.parametrize("legacy", [True, False])
def test_only_encode_limits_events_without_mutating_input_or_live_alias(legacy):
    events = [{"id": str(i)} for i in range(105)]
    state = {"game_key": ["web", "retention", "bot"], "state": "created",
             "instance_schema_version": 23 if legacy else CURRENT_INSTANCE_SCHEMA_VERSION}
    if legacy:
        state.update(health_events=events, health_status={"llm": "warning"})
    else:
        state["modules"] = {"health": {"schema_version": 1, "health_events": events, "health_status": {"llm": "warning"}}}
    before = deepcopy(state)
    instance = GameInstance.from_dict(state)
    live = instance.health_events
    assert live == events  # The former decoder did not truncate.
    assert module.ensure(instance.modules["health"])["health_events"] is live
    saved = instance.to_dict()
    assert saved["modules"]["health"]["health_events"] == events[-100:]
    assert instance.health_events is live
    assert live == events and len(live) == 105
    assert state == before
    restored = GameInstance.from_dict(saved)
    assert restored.health_events == events[-100:]


def test_existing_reporting_and_resolution_update_live_slot():
    instance = GameInstance(game_key=("web", "report", "bot"))
    events = instance.health_events
    status = instance.health_status
    for i in range(105):
        event = record_health_event(instance, "save", str(i), "warning", "save warning")
    assert instance.health_events is events and len(events) == 100
    assert events[0]["code"] == "5"
    assert instance.health_status is status and status["save"] == "warning"
    assert mark_health_event(instance, event["id"], resolved=True)
    assert event not in health_payload(instance)["events"]
    assert event in health_payload(instance, include_resolved=True)["events"]


def test_future_versions_fail_closed_and_opaque_slot_survives_encoding():
    with pytest.raises(ValueError, match="unsupported game instance schema"):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})
    slot = {"schema_version": 99, "health_events": [1] * 105, "health_status": "opaque"}
    before = deepcopy(slot)
    assert module.ensure(slot) is slot
    instance = GameInstance(game_key=("web", "future", "bot"), modules={"health": slot})
    for field in ("health_events", "health_status"):
        with pytest.raises(ModuleStateError):
            getattr(instance, field)
        with pytest.raises(ModuleStateError):
            setattr(instance, field, None)
    assert instance.to_dict()["modules"]["health"] == before
