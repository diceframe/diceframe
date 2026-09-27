"""Session statistics migration, ownership, and lifecycle contracts."""

from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timezone

import pytest

from src.engine.game_instance import GameInstance
from src.engine.module_state import ModuleStateError
from src.engine.modules import session_stats as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v29_to_v30,
    migrate_game_state_payload,
)

VALUES = {
    "total_llm_calls": 12,
    "total_tokens": 3456,
    "started_at": "2026-09-23T10:00:00+00:00",
    "last_activity": "2026-09-23T11:00:00+00:00",
}


def new_instance(**kwargs):
    return GameInstance(game_key=("test", "stats", "bot"), **kwargs)


def test_migration_moves_fields_without_mutating_input_and_is_idempotent():
    original = {"instance_schema_version": 29, **VALUES, "opaque": {"items": [1]}}
    before = deepcopy(original)
    migrated = migrate_game_state_payload(original)
    assert original == before
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert migrated["modules"][module.MODULE_NAME] == {"schema_version": 1, **VALUES}
    assert not VALUES.keys() & migrated.keys()
    assert migrate_game_state_payload(migrated) == migrated
    step = _migrate_v29_to_v30(deepcopy(original))
    assert _migrate_v29_to_v30(deepcopy(step)) == step
    migrated["opaque"]["items"].append(2)
    assert original == before


@pytest.mark.parametrize("slot", [{}, {"schema_version": 1, **VALUES}, {"schema_version": 99, "opaque": [1]}])
def test_existing_slot_wins(slot):
    original = {"instance_schema_version": 29, **VALUES, "modules": {module.MODULE_NAME: slot}}
    before = deepcopy(original)
    migrated = migrate_game_state_payload(original)
    assert migrated["modules"][module.MODULE_NAME] == slot
    assert not VALUES.keys() & migrated.keys()
    assert original == before


@pytest.mark.parametrize("value", [None, True, False, -1, 1.5, "12", [], {}])
def test_invalid_counters_default_in_migration_and_ensure(value):
    stats = {**VALUES, "total_llm_calls": value, "total_tokens": value}
    expected = {"schema_version": 1, **VALUES, "total_llm_calls": 0, "total_tokens": 0}
    assert module.ensure({"schema_version": 1, **stats}) == expected
    migrated = migrate_game_state_payload({"instance_schema_version": 29, **stats})
    assert migrated["modules"][module.MODULE_NAME] == expected


@pytest.mark.parametrize("value", [None, True, 1, [], {}])
def test_invalid_timestamps_default_in_migration_and_ensure(value):
    stats = {**VALUES, "started_at": value, "last_activity": value}
    expected = {"schema_version": 1, **VALUES, "started_at": "", "last_activity": ""}
    assert module.ensure({"schema_version": 1, **stats}) == expected
    migrated = migrate_game_state_payload({"instance_schema_version": 29, **stats})
    assert migrated["modules"][module.MODULE_NAME] == expected


@pytest.mark.parametrize("raw", [None, [], 12, "bad"])
def test_missing_or_malformed_slot_materializes_defaults(raw):
    instance = new_instance(modules={module.MODULE_NAME: raw})
    assert instance.modules[module.MODULE_NAME] == module.fresh()
    assert module.ensure(raw) == module.fresh()
    assert module.ensure({"schema_version": 1}) == module.fresh()
    migrated = migrate_game_state_payload({"instance_schema_version": 29, "modules": raw})
    assert migrated["modules"][module.MODULE_NAME] == module.fresh()


def test_future_instance_version_is_rejected():
    with pytest.raises(ValueError, match="unsupported game instance schema"):
        GameInstance.from_dict({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})


def test_unknown_module_version_is_preserved_and_access_fails_closed():
    raw = {"schema_version": 99, "opaque": [1]}
    assert module.ensure(raw) is raw
    instance = new_instance(modules={module.MODULE_NAME: raw})
    before = deepcopy(instance.modules)
    for key, value in VALUES.items():
        with pytest.raises(ModuleStateError):
            getattr(instance, key)
        with pytest.raises(ModuleStateError):
            setattr(instance, key, value)
    for operation in (module.touch, module.mark_started, module.record_llm_usage, module.reset):
        with pytest.raises(ModuleStateError):
            operation(instance)
    assert instance.modules == before
    assert instance.to_dict()["modules"][module.MODULE_NAME] == raw


def test_properties_use_live_slot_and_roundtrip_has_one_storage_owner():
    instance = new_instance()
    other = new_instance()
    slot = instance.modules[module.MODULE_NAME]
    assert slot is not other.modules[module.MODULE_NAME]
    assert not VALUES.keys() & {item.name for item in fields(instance)}
    assert not VALUES.keys() & instance.__dict__.keys()
    for key, value in VALUES.items():
        setattr(instance, key, value)
        assert slot[key] is value
        assert getattr(instance, key) is slot[key]
    slot["total_tokens"] = 5678
    assert instance.total_tokens == 5678
    encoded = instance.to_dict()
    assert not VALUES.keys() & encoded.keys()
    restored = GameInstance.from_dict(deepcopy(encoded))
    assert restored.modules[module.MODULE_NAME] == slot
    instance.replace_persisted_state_from(restored)
    assert instance.modules[module.MODULE_NAME] == slot


@pytest.mark.asyncio
async def test_reset_clears_all_four_statistics():
    instance = new_instance()
    for key, value in VALUES.items():
        setattr(instance, key, value)
    await instance.reset()
    assert instance.modules[module.MODULE_NAME] == module.fresh()


@pytest.mark.asyncio
async def test_activate_twice_preserves_start_time_and_touches_activity(monkeypatch):
    stamps = iter(["first-start", "first-touch", "second-touch"])

    class Clock:
        @staticmethod
        def now(zone):
            assert zone is timezone.utc
            return Clock()

        def isoformat(self):
            return next(stamps)

    monkeypatch.setattr(module, "datetime", Clock)
    instance = new_instance()
    await instance.activate()
    assert (instance.started_at, instance.last_activity) == ("first-start", "first-touch")
    await instance.activate()
    assert (instance.started_at, instance.last_activity) == ("first-start", "second-touch")


def test_touch_preserves_explicit_timestamp_including_empty_string():
    instance = new_instance()
    module.touch(instance, at=VALUES["last_activity"])
    assert instance.last_activity == VALUES["last_activity"]
    module.touch(instance, at="")
    assert instance.last_activity == ""
    before = datetime.now(timezone.utc)
    module.touch(instance)
    assert before <= datetime.fromisoformat(instance.last_activity) <= datetime.now(timezone.utc)


def test_record_usage_preserves_coercion_clamping_and_does_not_touch_time():
    instance = new_instance()
    instance.last_activity = VALUES["last_activity"]
    instance.record_llm_usage(12)
    module.record_llm_usage(instance, -10, calls=-2)
    assert (instance.total_tokens, instance.total_llm_calls) == (12, 1)
    module.record_llm_usage(instance, "7", calls="2")
    module.record_llm_usage(instance, None, calls=None)
    assert (instance.total_tokens, instance.total_llm_calls) == (19, 3)
    assert instance.last_activity == VALUES["last_activity"]
