"""Track R0 persistence, timer ownership and forward-schema regressions."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.engine.game_instance import GameInstance
from src.engine.module_state import (
    ModuleStateError,
    ensure_module_states,
    get_module_state,
    registered_module_states,
    set_module_state,
)
from src.engine.modules import lorebook_runtime
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v16_to_v17,
    migrate_game_state_payload,
)


def _timer(sticky=0, cooldown=0, pending=0, delay=0, tick=0):
    return {
        "sticky_remaining": sticky,
        "cooldown_remaining": cooldown,
        "delay_remaining": delay,
        "pending_cooldown": pending,
        "activated_tick": tick,
    }


def test_fresh_instance_has_registered_slots():
    first = GameInstance(game_key=("web", "t", "u"))
    second = GameInstance(game_key=("web", "t2", "u"))
    assert first.modules["lorebook_runtime"] == {"schema_version": 1, "timers": {}}
    for spec in registered_module_states():
        assert first.modules[spec.name]["schema_version"] == spec.schema_version
    lorebook_runtime.timers(first)["e1"] = _timer(sticky=2)
    assert lorebook_runtime.timers(second) == {}


def test_property_proxy_returns_live_dict():
    instance = GameInstance(game_key=("web", "t", "u"))
    timers = lorebook_runtime.timers(instance)
    assert timers is instance.modules["lorebook_runtime"]["timers"]
    timers["e1"] = _timer(sticky=2)
    instance.update_lorebook_timed_state()
    assert lorebook_runtime.timers(instance) is timers
    assert instance.modules["lorebook_runtime"]["timers"]["e1"]["sticky_remaining"] == 1


def test_property_setter_replaces_slot_timers():
    instance = GameInstance(game_key=("web", "t", "u"))
    previous = lorebook_runtime.timers(instance)
    lorebook_runtime.replace_timers(instance, {"e1": {"status": "cooldown", "remaining": 2}})
    assert lorebook_runtime.timers(instance) is instance.modules["lorebook_runtime"]["timers"]
    assert lorebook_runtime.timers(instance) is not previous
    assert lorebook_runtime.timers(instance) == {"e1": _timer(cooldown=2)}


@pytest.mark.parametrize(("legacy", "expected"), [
    ({"e1": {"remaining": 3, "status": "active"}}, {"e1": _timer(sticky=3)}),
    ({"e1": {"remaining": 2, "status": "cooldown"}}, {"e1": _timer(cooldown=2)}),
    ({"e1": {"remaining": 2, "status": "delayed"}}, {}),
    ({"e1": {"sticky_remaining": 3, "cooldown_remaining": 2}}, {"e1": _timer(sticky=3, pending=2)}),
    ({"e1": _timer(delay=2, tick=5)}, {"e1": _timer(delay=2, tick=5)}),
    (None, {}),
    ([], {}),
])
def test_migration_16_to_17_moves_legacy_timers(legacy, expected):
    payload = {
        "game_key": ["web", "t", "u"],
        "instance_schema_version": 16,
        "lorebook_timed_state": legacy,
        "opaque": {"preserved": [1, 2]},
    }
    original = deepcopy(payload)
    migrated = migrate_game_state_payload(payload)
    assert payload == original
    assert "lorebook_timed_state" not in migrated
    assert migrated["modules"]["lorebook_runtime"] == {"schema_version": 1, "timers": expected}
    assert migrated["opaque"] == original["opaque"]
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION


def test_migration_16_to_17_is_idempotent():
    migrated = _migrate_v16_to_v17({
        "instance_schema_version": 16,
        "lorebook_timed_state": {"e1": {"remaining": 3, "status": "active"}},
    })
    current = migrate_game_state_payload(migrated)
    assert migrate_game_state_payload(current) == current
    assert _migrate_v16_to_v17(deepcopy(migrated)) == migrated


@pytest.mark.parametrize("slot", [
    {"schema_version": 1, "timers": {"e1": _timer(sticky=7)}, "extra": [1]},
    {"schema_version": 99, "x": 1},
])
def test_existing_slot_wins_over_legacy_timers(slot):
    migrated = migrate_game_state_payload({
        "instance_schema_version": 16,
        "lorebook_timed_state": {"e1": {"remaining": 3, "status": "active"}},
        "modules": {"lorebook_runtime": deepcopy(slot), "future_thing": {"schema_version": 7}},
    })
    assert migrated["modules"]["lorebook_runtime"] == slot
    assert migrated["modules"]["future_thing"] == {"schema_version": 7}
    assert "lorebook_timed_state" not in migrated


def test_migration_rejects_future_version():
    with pytest.raises(ValueError, match="unsupported game instance schema version"):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})


@pytest.mark.parametrize("modules", [None, [], "bad", {"lorebook_runtime": None}])
def test_corrupt_or_missing_container_gets_fresh_slots(modules):
    repaired = ensure_module_states(modules)
    assert repaired["lorebook_runtime"] == {"schema_version": 1, "timers": {}}
    assert ensure_module_states(deepcopy(repaired)) == repaired


@pytest.mark.parametrize("bad", ["not-a-number", [1], {"x": 1}, float("inf")])
def test_corrupt_timer_does_not_discard_other_entries(bad):
    raw = {"good": _timer(sticky=2), "bad": {"sticky_remaining": bad, "activated_tick": bad}}
    assert lorebook_runtime.normalize_timers(raw) == {"good": _timer(sticky=2)}


def test_unknown_module_schema_is_kept_verbatim():
    slot = {"schema_version": 99, "x": {"private": [1, 2]}}
    modules = {"lorebook_runtime": slot}
    assert ensure_module_states(modules)["lorebook_runtime"] is slot
    instance = GameInstance(game_key=("web", "t", "u"), modules=modules)
    saved = instance.to_dict()
    restored = GameInstance.from_dict(saved)
    assert restored.to_dict()["modules"]["lorebook_runtime"] == slot
    before = deepcopy(restored.modules)
    with pytest.raises(ModuleStateError, match="unsupported lorebook_runtime module schema"):
        lorebook_runtime.replace_timers(restored, {})
    with pytest.raises(ModuleStateError):
        set_module_state(restored, "lorebook_runtime", lorebook_runtime.fresh())
    assert restored.modules == before


def test_unregistered_module_key_survives_round_trip():
    instance = GameInstance(game_key=("web", "t", "u"), modules={
        "future_thing": {"schema_version": 4, "a": {"values": [1, 2]}},
    })
    restored = GameInstance.from_dict(instance.to_dict())
    assert restored.modules["future_thing"] == instance.modules["future_thing"]
    assert restored.modules["future_thing"] is not instance.modules["future_thing"]


def test_save_load_round_trip_preserves_timers():
    instance = GameInstance(game_key=("web", "t", "u"))
    expected = {"sticky": _timer(sticky=3, pending=2, tick=4), "cooldown": _timer(cooldown=2)}
    lorebook_runtime.replace_timers(instance, deepcopy(expected))
    payload = instance.to_dict()
    assert "lorebook_timed_state" not in payload
    restored = GameInstance.from_dict(payload)
    assert lorebook_runtime.timers(restored) == expected
    assert lorebook_runtime.timers(restored) is restored.modules["lorebook_runtime"]["timers"]
    restored.update_lorebook_timed_state()
    assert lorebook_runtime.timers(instance) == expected


def test_old_save_load_migrates_before_constructing_instance():
    restored = GameInstance.from_dict({
        "game_key": ["web", "t", "u"], "state": "created",
        "instance_schema_version": 16,
        "lorebook_timed_state": {"e1": {"remaining": 3, "status": "active"}},
    })
    assert lorebook_runtime.timers(restored) == {"e1": _timer(sticky=3)}
    assert restored.instance_schema_version == CURRENT_INSTANCE_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_reset_clears_live_module_timer_dict():
    instance = GameInstance(game_key=("web", "t", "u"))
    timers = lorebook_runtime.timers(instance)
    timers["e1"] = _timer(sticky=2)
    await instance.reset()
    assert instance.modules["lorebook_runtime"]["timers"] is timers
    assert timers == {}


def test_staged_state_replacement_copies_modules_without_runtime_locks():
    instance = GameInstance(game_key=("web", "t", "u"))
    lock = instance._lock
    staged = GameInstance.from_dict(instance.to_dict())
    lorebook_runtime.timers(staged)["e1"] = _timer(sticky=3)
    instance.replace_persisted_state_from(staged)
    assert instance.modules == staged.modules
    assert lorebook_runtime.timers(instance) is not lorebook_runtime.timers(staged)
    assert instance._lock is lock
    lorebook_runtime.timers(staged).clear()
    assert lorebook_runtime.timers(instance) == {"e1": _timer(sticky=3)}


def test_registry_access_rejects_missing_container_and_unregistered_module():
    with pytest.raises(ModuleStateError, match="no module state container"):
        get_module_state(SimpleNamespace(), "lorebook_runtime")
    with pytest.raises(ModuleStateError, match="unknown module state"):
        get_module_state(SimpleNamespace(modules={}), "unregistered")


def test_slot_replacement_returns_live_dict_and_rejects_future_input():
    instance = GameInstance(game_key=("web", "t", "u"))
    slot = set_module_state(instance, "lorebook_runtime", {
        "schema_version": 1, "timers": {"e1": _timer(sticky=3)},
    })
    assert slot is instance.modules["lorebook_runtime"]
    assert lorebook_runtime.timers(instance) == {"e1": _timer(sticky=3)}
    with pytest.raises(ModuleStateError):
        set_module_state(instance, "lorebook_runtime", {"schema_version": 99})
    assert instance.modules["lorebook_runtime"] is slot
