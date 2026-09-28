"""World-report persistence, lifecycle and writer contracts."""

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.module_state import ModuleStateError
from src.engine.modules import world_reports as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v26_to_v27,
    migrate_game_state_payload,
)

VALUES = {
    "last_overreach": [{"player": "u1", "reason": "claimed NPC action"}],
    "last_world_legality": [{"player": "u1", "code": "location", "current": "town"}],
    "last_world_events": [
        {"event_id": "e1", "status": "applied"},
        {"event_id": "e2", "status": "failed", "error": "missing target"},
    ],
}


def test_upgrade_removes_legacy_keys_without_mutating_input():
    payload = {"instance_schema_version": 26, **deepcopy(VALUES)}
    before = deepcopy(payload)
    result = migrate_game_state_payload(payload)
    assert payload == before
    assert result["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert result["modules"][module.MODULE_NAME] == {"schema_version": 1, **VALUES}
    assert all(key not in result for key in VALUES)
    assert migrate_game_state_payload(result) == result
    single = _migrate_v26_to_v27(deepcopy(payload))
    assert _migrate_v26_to_v27(deepcopy(single)) == single


@pytest.mark.parametrize("slot", [{}, {"schema_version": 99, "opaque": [1]}, module.fresh()])
def test_existing_slot_wins_even_when_empty_or_unknown(slot):
    result = _migrate_v26_to_v27({"modules": {module.MODULE_NAME: slot}, **deepcopy(VALUES)})
    assert result["modules"][module.MODULE_NAME] is slot
    assert all(key not in result for key in VALUES)


@pytest.mark.parametrize("value", [None, "invalid", 4, False, {}])
def test_malformed_values_use_defaults(value):
    payload = {key: value for key in VALUES}
    assert _migrate_v26_to_v27(payload)["modules"][module.MODULE_NAME] == module.fresh()
    assert module.ensure({"schema_version": 1, **{key: value for key in VALUES}}) == module.fresh()


def test_missing_values_use_defaults():
    assert _migrate_v26_to_v27({})["modules"][module.MODULE_NAME] == module.fresh()
    assert module.ensure({"schema_version": 1}) == module.fresh()
    assert module.ensure(None) == module.fresh()


@pytest.mark.parametrize("field", VALUES)
def test_property_identity_replacement_and_mutation(field):
    instance = GameInstance(game_key=("web", "reports", "bot"))
    other = GameInstance(game_key=("web", "other", "bot"))
    value = deepcopy(VALUES[field])
    setattr(instance, field, value)
    assert getattr(instance, field) is value is instance.modules[module.MODULE_NAME][field]
    value.append({"mutation": True})
    assert getattr(instance, field) == value
    replacement = [{"replacement": True}]
    getattr(module, f"replace_{field}")(instance, replacement)
    assert getattr(instance, field) is replacement
    assert getattr(other, field) == []


@pytest.mark.parametrize("legacy", [True, False])
def test_codec_roundtrip_preserves_all_reports(legacy):
    instance = GameInstance(game_key=("web", "roundtrip", "bot"))
    payload = instance.to_dict()
    if legacy:
        payload["instance_schema_version"] = 26
        payload["modules"].pop(module.MODULE_NAME)
        payload.update(deepcopy(VALUES))
    else:
        payload["modules"][module.MODULE_NAME].update(deepcopy(VALUES))
    restored = GameInstance.from_dict(payload)
    saved = restored.to_dict()
    assert all(key not in saved for key in VALUES)
    assert saved["modules"][module.MODULE_NAME] == {"schema_version": 1, **VALUES}
    again = GameInstance.from_dict(saved)
    for field, value in VALUES.items():
        assert getattr(again, field) == value


@pytest.mark.asyncio
async def test_reset_retains_reports_and_reset_round_checks_clears_in_place():
    instance = GameInstance(game_key=("web", "reset", "bot"))
    values = deepcopy(VALUES)
    for field, value in values.items():
        setattr(instance, field, value)
    await instance.reset()
    for field, value in values.items():
        assert getattr(instance, field) is value
        assert value == VALUES[field]
    instance.reset_round_checks(prepared=True)
    for field, value in values.items():
        assert getattr(instance, field) is value
        assert value == []
    assert instance.round_checks_prepared is True


def test_unknown_versions_are_preserved_but_runtime_access_is_rejected():
    with pytest.raises(ValueError, match="unsupported game instance schema"):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})
    slot = {"schema_version": 99, "opaque": [1, 2]}
    before = deepcopy(slot)
    assert module.ensure(slot) is slot
    instance = GameInstance(game_key=("web", "future", "bot"), modules={module.MODULE_NAME: slot})
    for field in VALUES:
        with pytest.raises(ModuleStateError):
            getattr(instance, field)
        with pytest.raises(ModuleStateError):
            setattr(instance, field, deepcopy(VALUES[field]))
    saved = instance.to_dict()
    assert saved["modules"][module.MODULE_NAME] == before
    assert GameInstance.from_dict(saved).to_dict()["modules"][module.MODULE_NAME] == before
