"""Persistence, privacy filtering and live aliases for private channels."""

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.module_state import ModuleStateError
from src.engine.modules import private_channels as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v21_to_v22,
    migrate_game_state_payload,
)


def test_migration_moves_legacy_values_without_mutating_input():
    payload = {
        "instance_schema_version": 21,
        "private_log": {"p1": [{"text": "secret"}]},
        "table_talk": [{"id": "party", "visibility": "party"}],
        "modules": {"extension": {"schema_version": 9, "opaque": True}},
    }
    before = deepcopy(payload)
    migrated = migrate_game_state_payload(payload)
    assert payload == before
    assert "private_log" not in migrated and "table_talk" not in migrated
    assert migrated["modules"]["private_channels"] == {
        "schema_version": 1, "private_log": payload["private_log"], "table_talk": payload["table_talk"],
    }
    assert migrated["modules"]["extension"] == payload["modules"]["extension"]
    assert migrate_game_state_payload(migrated) == migrated


@pytest.mark.parametrize("slot", [{}, {"schema_version": 99, "opaque": [1]}, module.fresh()])
def test_migration_preserves_existing_slots_and_is_idempotent(slot):
    payload = {"instance_schema_version": 21, "private_log": {"old": []}, "table_talk": [],
               "modules": {"private_channels": slot}}
    migrated = _migrate_v21_to_v22(payload)
    assert migrated["modules"]["private_channels"] is slot
    before = deepcopy(migrated)
    assert _migrate_v21_to_v22(migrated) == before


@pytest.mark.parametrize("value", [None, False, 1, "invalid"])
def test_missing_or_invalid_containers_default(value):
    payload = _migrate_v21_to_v22({"private_log": value, "table_talk": value})
    assert payload["modules"]["private_channels"] == module.fresh()
    assert module.ensure({"schema_version": 1, "private_log": value, "table_talk": value}) == module.fresh()
    assert module.ensure(value) == module.fresh()


def test_properties_keep_assigned_objects_and_round_trip():
    instance = GameInstance(game_key=("web", "private-module", "bot"))
    logs = {"p1": [{"text": "secret"}]}
    talks = [{"id": "a", "visibility": "party"}]
    instance.private_log = logs
    instance.table_talk = talks
    slot = instance.modules["private_channels"]
    assert instance.private_log is logs is slot["private_log"]
    assert instance.table_talk is talks is slot["table_talk"]
    instance.private_log["p1"].append({"text": "another secret"})
    saved = instance.to_dict()
    assert "private_log" not in saved and "table_talk" not in saved
    restored = GameInstance.from_dict(saved)
    assert restored.private_log == logs
    assert restored.table_talk == talks
    assert restored.to_dict()["modules"]["private_channels"] == saved["modules"]["private_channels"]
    instance.private_log = {}
    instance.table_talk = []
    assert logs and talks  # replacement does not clear the old containers


def test_decode_filters_before_truncating_and_ensure_keeps_list_identity():
    entries = [{"id": str(i), "visibility": "party"} for i in range(60)]
    entries.extend([None, "invalid", {"id": "secret", "visibility": "private"}, {}])
    slot = {"schema_version": 1, "private_log": {}, "table_talk": entries}
    assert module.ensure(slot) is slot
    assert slot["table_talk"] is entries
    assert [item["id"] for item in entries] == [str(i) for i in range(10, 60)]
    before = deepcopy(slot)
    assert module.ensure(slot) == before
    instance = GameInstance.from_dict({"game_key": ["web", "filter", "bot"], "state": "created",
                                      "table_talk": entries + [{"visibility": "private"}]})
    assert instance.table_talk == entries


def test_failed_save_removal_preserves_live_list_and_other_exchanges():
    instance = GameInstance(game_key=("web", "rollback", "bot"))
    entries = [{"id": "keep", "visibility": "party"}, {"id": "remove", "visibility": "party"}]
    instance.table_talk = entries
    module.remove_table_talk_exchange(instance, "remove")
    assert instance.table_talk is entries
    assert entries == [{"id": "keep", "visibility": "party"}]


def test_future_instance_and_module_versions_fail_closed():
    with pytest.raises(ValueError, match="unsupported game instance schema"):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})
    slot = {"schema_version": 99, "table_talk": [{"visibility": "private"}], "extra": [1]}
    before = deepcopy(slot)
    assert module.ensure(slot) is slot
    instance = GameInstance(game_key=("web", "future", "bot"), modules={"private_channels": slot})
    for field in ("private_log", "table_talk"):
        with pytest.raises(ModuleStateError):
            getattr(instance, field)
        with pytest.raises(ModuleStateError):
            setattr(instance, field, None)
    assert instance.to_dict()["modules"]["private_channels"] == before
