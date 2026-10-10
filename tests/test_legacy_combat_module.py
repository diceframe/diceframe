"""Compatibility combat projection storage, migration and coercion contracts."""

from copy import deepcopy
from dataclasses import fields

import pytest

from src.engine.game_instance import GameInstance
from src.engine.module_state import ModuleStateError
from src.engine.modules import legacy_combat as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION, _migrate_v32_to_v33, migrate_game_state_payload,
)
from src.engine.modules import ruleset_runtime

VALUES = {
    "combat_active": True,
    "combat_enemies": [{"name": "guard", "hp": 12}],
    "combat_state": "active",
    "initiative_order": ["u1", "guard"],
    "initiative_current": 1,
}


def new_instance(**kwargs):
    return GameInstance(game_key=("test", "legacy-combat", "bot"), **kwargs)


def populated_instance():
    instance = new_instance()
    for key, value in deepcopy(VALUES).items():
        getattr(module, f"replace_{key}")(instance, value)
    return instance


def test_migration_moves_fields_without_mutating_input_and_is_idempotent():
    original = {"instance_schema_version": 32, **deepcopy(VALUES), "opaque": {"items": [1]}}
    before = deepcopy(original)
    migrated = migrate_game_state_payload(original)
    assert original == before
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert migrated["modules"][module.MODULE_NAME] == {"schema_version": 1, **VALUES}
    assert not VALUES.keys() & migrated.keys()
    assert migrate_game_state_payload(migrated) == migrated
    step = _migrate_v32_to_v33(deepcopy(original))
    assert _migrate_v32_to_v33(deepcopy(step)) == step
    migrated["modules"][module.MODULE_NAME]["combat_enemies"][0]["hp"] = 1
    assert original == before


@pytest.mark.parametrize("slot", [{}, {"schema_version": 1, **VALUES}, {"schema_version": 99, "opaque": [1]}])
def test_existing_slot_wins(slot):
    original = {"instance_schema_version": 32, **VALUES, "modules": {module.MODULE_NAME: slot}}
    before = deepcopy(original)
    migrated = migrate_game_state_payload(original)
    assert migrated["modules"][module.MODULE_NAME] == slot
    assert not VALUES.keys() & migrated.keys()
    assert original == before


@pytest.mark.parametrize("key,value,expected", [
    ("combat_active", "false", True), ("combat_active", [], False), ("combat_active", None, False),
    ("combat_enemies", None, []), ("combat_enemies", {}, []),
    ("combat_state", None, "none"), ("combat_state", 5, "none"),
    ("initiative_order", None, []), ("initiative_order", "u1", []),
    ("initiative_current", True, 0), ("initiative_current", False, 0),
    ("initiative_current", "3", 0), ("initiative_current", 1.5, 0),
    ("initiative_current", None, 0),
])
def test_invalid_types_repaired_identically_in_ensure_and_migration(key, value, expected):
    values = {**deepcopy(VALUES), key: value}
    raw = {"schema_version": 1, **values}
    assert module.ensure(raw) is raw
    assert raw == {"schema_version": 1, **values, key: expected}
    migrated = migrate_game_state_payload({"instance_schema_version": 32, **values})
    assert migrated["modules"][module.MODULE_NAME] == raw


@pytest.mark.parametrize("raw", [None, [], 12, "bad"])
def test_missing_or_malformed_slots_materialize_defaults(raw):
    instance = new_instance(modules={module.MODULE_NAME: raw})
    assert instance.modules[module.MODULE_NAME] == module.fresh()
    assert module.ensure(raw) == module.fresh()
    assert module.ensure({"schema_version": 1}) == module.fresh()
    migrated = migrate_game_state_payload({"instance_schema_version": 32, "modules": raw})
    assert migrated["modules"][module.MODULE_NAME] == module.fresh()


def test_valid_values_and_list_contents_are_preserved():
    raw = {"schema_version": 1, **deepcopy(VALUES), "combat_state": "custom", "initiative_current": -2}
    raw["combat_enemies"].extend([None, 4, "opaque"])
    raw["initiative_order"].extend([None, {"opaque": True}])
    objects = dict(raw)
    assert module.ensure(raw) is raw
    assert all(raw[key] is value for key, value in objects.items())
    restored = GameInstance.from_dict({
        "game_key": ["test", "legacy-combat", "bot"], "state": "waiting",
        "instance_schema_version": 32, **{key: raw[key] for key in VALUES},
    })
    assert restored.modules[module.MODULE_NAME] == raw


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
            getattr(module, key)(instance)
        with pytest.raises(ModuleStateError):
            getattr(module, f"replace_{key}")(instance, value)
    assert instance.modules == before
    encoded = instance.to_dict()
    assert encoded["modules"][module.MODULE_NAME] == raw
    assert GameInstance.from_dict(encoded).modules[module.MODULE_NAME] == raw


def test_properties_use_live_slot_and_roundtrip_has_one_storage_owner():
    instance = populated_instance()
    other = new_instance()
    slot = instance.modules[module.MODULE_NAME]
    assert slot is not other.modules[module.MODULE_NAME]
    assert not VALUES.keys() & {item.name for item in fields(instance)}
    assert not VALUES.keys() & vars(instance).keys()
    for key, value in deepcopy(VALUES).items():
        getattr(module, f"replace_{key}")(instance, value)
        assert slot[key] is value
        assert getattr(module, key)(instance) is slot[key]
    assert module.combat_enemies(instance) is not module.combat_enemies(other)
    assert module.initiative_order(instance) is not module.initiative_order(other)
    module.combat_enemies(instance)[0]["hp"] -= 3
    encoded = instance.to_dict()
    assert not VALUES.keys() & encoded.keys()
    restored = GameInstance.from_dict(deepcopy(encoded))
    assert restored.modules[module.MODULE_NAME] == slot
    instance.replace_persisted_state_from(restored)
    assert instance.modules[module.MODULE_NAME] == slot


def test_begin_copies_order_and_end_preserves_enemies_and_order_identity():
    instance = populated_instance()
    enemies = module.combat_enemies(instance)
    order = ["guard", "u1"]
    instance.begin_combat(order)
    assert module.initiative_order(instance) == order
    assert module.initiative_order(instance) is not order
    assert module.combat_state(instance) == "active"
    assert module.combat_active(instance) is True
    assert module.initiative_current(instance) == 0
    stored_order = module.initiative_order(instance)
    instance.end_combat()
    assert module.initiative_order(instance) is stored_order
    assert stored_order == []
    assert module.combat_enemies(instance) is enemies
    assert module.combat_state(instance) == "none"
    assert module.combat_active(instance) is False


@pytest.mark.parametrize("status", ["active", "ended", "none", None])
def test_ruleset_projection_preserves_authority_and_enemies(status):
    instance = populated_instance()
    combat = {"status": status, "initiative": ["player:u1"], "turn_index": "2"}
    ruleset_runtime.replace_state(instance, {"combat": combat})
    before = deepcopy(ruleset_runtime.state(instance))
    enemies = module.combat_enemies(instance)
    module.project_from_ruleset(instance, combat)
    assert module.combat_state(instance) == ("active" if status == "active" else "none")
    assert module.combat_active(instance) is (status == "active")
    assert module.initiative_order(instance) == combat["initiative"]
    assert module.initiative_order(instance) is not combat["initiative"]
    assert module.initiative_current(instance) == 2
    assert module.combat_enemies(instance) is enemies
    assert ruleset_runtime.state(instance) == before
    assert ruleset_runtime.state(instance)["combat"] is combat


@pytest.mark.parametrize("kind", ["entity", "transaction"])
def test_restore_paths_keep_enemy_asymmetry_and_deepcopy(kind):
    instance = populated_instance()
    enemies = module.combat_enemies(instance)
    snapshot = {
        "combat_enemies": [{"hp": 7}], "combat_state": 123,
        "combat_active": "truthy", "initiative_order": [{"opaque": [1]}],
        "initiative_current": "3",
    }
    operation = module.restore_from_entity_snapshot if kind == "entity" else module.restore_from_transaction
    operation(instance, snapshot)
    assert module.combat_state(instance) == "123"
    assert module.combat_active(instance) is True
    assert module.initiative_current(instance) == 3
    assert module.initiative_order(instance) == snapshot["initiative_order"]
    assert module.initiative_order(instance) is not snapshot["initiative_order"]
    module.initiative_order(instance)[0]["opaque"].append(2)
    assert snapshot["initiative_order"][0]["opaque"] == [1]
    if kind == "entity":
        assert module.combat_enemies(instance) == snapshot["combat_enemies"]
        assert module.combat_enemies(instance) is not snapshot["combat_enemies"]
        module.combat_enemies(instance)[0]["hp"] = 1
        assert snapshot["combat_enemies"][0]["hp"] == 7
    else:
        assert module.combat_enemies(instance) is enemies
        assert enemies == VALUES["combat_enemies"]


def test_entity_restore_defaults_and_transaction_keeps_its_distinct_coercions():
    instance = populated_instance()
    module.restore_from_entity_snapshot(instance, {})
    assert instance.modules[module.MODULE_NAME] == module.fresh()
    module.restore_from_transaction(instance, {
        "combat_state": None, "combat_active": 0,
        "initiative_order": ("u1",), "initiative_current": "2",
    })
    assert module.combat_state(instance) == "None"
    assert module.initiative_order(instance) == ("u1",)
    assert module.initiative_current(instance) == 2


def test_retired_combat_facades_are_not_game_instance_attributes():
    instance = new_instance()
    for key in VALUES:
        assert not hasattr(GameInstance, key)
        assert not hasattr(instance, key)
        with pytest.raises(AttributeError, match="was removed"):
            setattr(instance, key, VALUES[key])
    assert instance.modules[module.MODULE_NAME] == module.fresh()


@pytest.mark.asyncio
async def test_reset_clears_all_fields_preserving_list_identity():
    instance = populated_instance()
    enemies, order = module.combat_enemies(instance), module.initiative_order(instance)
    await instance.reset()
    assert instance.modules[module.MODULE_NAME] == module.fresh()
    assert module.combat_enemies(instance) is enemies
    assert module.initiative_order(instance) is order
