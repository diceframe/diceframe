"""Persistence and live compatibility contracts for round presentation."""

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.module_state import ModuleStateError
from src.engine.modules import round_presentation as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v25_to_v26,
    migrate_game_state_payload,
)

VALUES = {
    "gm_directives": [{"id": "private", "text": "A hidden instruction"}],
    "quick_actions": ["Look around", "Wait"],
    "last_state_update": {"hp": "10"},
    "last_token_budget_bump": {"kind": "narrative", "from": 100, "to": 200},
    "pending_combat_results": [{"damage": 5}],
}


def test_upgrade_removes_legacy_keys_without_mutating_input():
    payload = {"instance_schema_version": 25, **deepcopy(VALUES)}
    before = deepcopy(payload)
    result = migrate_game_state_payload(payload)
    assert payload == before
    assert result["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert result["modules"][module.MODULE_NAME] == {"schema_version": 1, **VALUES}
    assert all(key not in result for key in VALUES)
    assert migrate_game_state_payload(result) == result
    single = _migrate_v25_to_v26(deepcopy(payload))
    assert _migrate_v25_to_v26(deepcopy(single)) == single


@pytest.mark.parametrize("slot", [{}, {"schema_version": 99, "opaque": [1]}, module.fresh()])
def test_existing_slot_wins_even_when_empty_or_unknown(slot):
    payload = {"modules": {module.MODULE_NAME: slot}, **deepcopy(VALUES)}
    result = _migrate_v25_to_v26(payload)
    assert result["modules"][module.MODULE_NAME] is slot
    assert all(key not in result for key in VALUES)


@pytest.mark.parametrize("value", [None, "invalid", 4, False])
def test_malformed_values_use_defaults(value):
    payload = {key: value for key in VALUES}
    assert _migrate_v25_to_v26(payload)["modules"][module.MODULE_NAME] == module.fresh()
    assert module.ensure({"schema_version": 1, **{key: value for key in VALUES}}) == module.fresh()
    assert module.ensure(value) == module.fresh()


def test_missing_values_use_defaults():
    assert _migrate_v25_to_v26({})["modules"][module.MODULE_NAME] == module.fresh()
    assert module.ensure({"schema_version": 1}) == module.fresh()


@pytest.mark.parametrize("field", VALUES)
def test_property_identity_replacement_and_mutation(field):
    instance = GameInstance(game_key=("web", "presentation", "bot"))
    other = GameInstance(game_key=("web", "other", "bot"))
    value = deepcopy(VALUES[field])
    setattr(instance, field, value)
    assert getattr(instance, field) is value is instance.modules[module.MODULE_NAME][field]
    if isinstance(value, list):
        value.append("mutation")
    else:
        value["mutation"] = True
    assert getattr(instance, field) == value
    replacement = [] if isinstance(value, list) else {}
    setattr(instance, field, replacement)
    assert getattr(instance, field) is replacement
    assert getattr(other, field) == module.fresh()[field]


@pytest.mark.parametrize("legacy", [True, False])
def test_codec_roundtrip_preserves_all_values(legacy):
    instance = GameInstance(game_key=("web", "roundtrip", "bot"))
    payload = instance.to_dict()
    if legacy:
        payload["instance_schema_version"] = 25
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


def test_existing_mutation_methods_keep_their_behavior():
    instance = GameInstance(game_key=("web", "methods", "bot"))
    instance.set_quick_actions(["", "  ", "Wait"])
    assert instance.quick_actions == ["Wait"]
    instance.add_gm_directive({"id": "keep"})
    instance.add_gm_directive({"id": "consume"})
    instance.consume_gm_directives({"consume"})
    assert instance.gm_directives == [{"id": "keep"}]
    instance.set_state_update_recap({})
    assert instance.last_state_update is None
    instance.set_state_update_recap({"hp": "10"})
    assert instance.last_state_update == {"hp": "10"}
    instance.set_token_budget_bump(100, 200)
    assert instance.last_token_budget_bump == VALUES["last_token_budget_bump"]
    instance.set_token_budget_bump(100, 100)
    assert instance.last_token_budget_bump is None
    instance.record_combat_result({"damage": 5})
    instance.set_token_budget_bump(100, 200)
    instance.begin_round_processing()
    assert instance.pending_combat_results == []
    assert instance.last_token_budget_bump is None
    assert instance.quick_actions == ["Wait"]
    assert instance.gm_directives == [{"id": "keep"}]


@pytest.mark.asyncio
async def test_reset_clears_all_five_fields_and_preserves_list_identity():
    instance = GameInstance(game_key=("web", "reset", "bot"))
    for field, value in deepcopy(VALUES).items():
        setattr(instance, field, value)
    lists = {field: getattr(instance, field) for field in VALUES if isinstance(VALUES[field], list)}
    await instance.reset()
    assert instance.modules[module.MODULE_NAME] == module.fresh()
    for field, value in lists.items():
        assert getattr(instance, field) is value


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
