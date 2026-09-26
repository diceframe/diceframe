"""Narrative-note migration and live aggregate compatibility contracts."""

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.module_state import ModuleStateError
from src.engine.modules import narrative_notes as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v24_to_v25,
    migrate_game_state_payload,
)

FIELDS = ("summary", "key_facts", "confirmed_items", "game_time")


def test_migration_moves_notes_without_mutation_and_is_idempotent():
    payload = {"instance_schema_version": 24, "summary": {"narrative": "A meeting"},
               "key_facts": ["the gate is open"], "confirmed_items": ["sword"],
               "game_time": "  第三纪元，暮色\n", "modules": {"extension": {"schema_version": 99}}}
    before = deepcopy(payload)
    migrated = migrate_game_state_payload(payload)
    assert payload == before
    assert all(field not in migrated for field in FIELDS)
    assert migrated["modules"]["narrative_notes"] == {"schema_version": 1, **{field: payload[field] for field in FIELDS}}
    assert migrated["modules"]["extension"] == payload["modules"]["extension"]
    assert migrate_game_state_payload(migrated) == migrated


@pytest.mark.parametrize("slot", [{}, {"schema_version": 99, "opaque": True}, module.fresh()])
def test_single_step_keeps_existing_slot_and_is_idempotent(slot):
    payload = {"modules": {"narrative_notes": slot}, "summary": {"legacy": True}, "game_time": "legacy"}
    migrated = _migrate_v24_to_v25(payload)
    assert migrated["modules"]["narrative_notes"] is slot
    assert all(field not in migrated for field in FIELDS)
    before = deepcopy(migrated)
    assert _migrate_v24_to_v25(migrated) == before


@pytest.mark.parametrize("value", [None, False, 1, 2.5])
def test_invalid_values_default_without_guessing(value):
    payload = {field: value for field in FIELDS}
    assert _migrate_v24_to_v25(payload)["modules"]["narrative_notes"] == module.fresh()
    assert module.ensure({"schema_version": 1, **{field: value for field in FIELDS}}) == module.fresh()
    assert module.ensure(value) == module.fresh()


def test_wrong_container_types_default():
    payload = {"summary": [], "key_facts": {}, "confirmed_items": "sword", "game_time": ["dusk"]}
    assert _migrate_v24_to_v25(payload)["modules"]["narrative_notes"] == module.fresh()
    assert module.ensure({"schema_version": 1, "summary": [], "key_facts": {},
                          "confirmed_items": "sword", "game_time": ["dusk"]}) == module.fresh()


def test_live_properties_replace_objects_and_roundtrip():
    instance = GameInstance(game_key=("web", "notes", "bot"))
    values = {"summary": {"narrative": "A meeting"}, "key_facts": ["gate"],
              "confirmed_items": ["sword"], "game_time": "Third Age, dusk"}
    for field, value in values.items():
        setattr(instance, field, value)
        assert getattr(instance, field) is value is instance.modules["narrative_notes"][field]
    instance.summary["extra"] = "kept"
    instance.key_facts.append("bridge")
    instance.confirmed_items.append("rope")
    saved = instance.to_dict()
    assert all(field not in saved for field in FIELDS)
    restored = GameInstance.from_dict(saved)
    assert restored.modules["narrative_notes"] == instance.modules["narrative_notes"]
    for field, default in module.fresh().items():
        if field in FIELDS:
            setattr(instance, field, default)
            assert getattr(instance, field) is default
            assert values[field]


@pytest.mark.parametrize("game_time", ["", "14:00", "  第三纪元，暮色\n", "not-a-calendar 0001"])
def test_game_time_preserved_verbatim_without_affecting_progression(game_time):
    instance = GameInstance.from_dict({"game_key": ["web", "clock", "bot"], "state": "created",
                                       "instance_schema_version": 20, "round_number": 7, "game_time": game_time})
    assert instance.game_time == game_time
    assert instance.round_number == 7
    restored = GameInstance.from_dict(instance.to_dict())
    assert restored.game_time == game_time
    assert restored.round_number == 7


def test_aggregate_methods_keep_summary_and_confirmed_item_behavior():
    instance = GameInstance(game_key=("web", "mutators", "bot"))
    summary = instance.summary
    confirmed = instance.confirmed_items
    instance.set_summary_narrative("A summary")
    assert summary == {"narrative": "A summary"}
    facts = ["one", "two"]
    instance.set_key_facts(facts)
    assert instance.key_facts == facts and instance.key_facts is not facts
    instance.add_confirmed_items(["sword", "sword", "rope", "torch"], limit=2)
    assert instance.confirmed_items is confirmed
    assert confirmed == ["rope", "torch"]


def test_future_instance_and_module_versions_reject_runtime_access():
    with pytest.raises(ValueError, match="unsupported game instance schema"):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})
    slot = {"schema_version": 99, "summary": ["opaque"], "game_time": {"clock": 3}}
    before = deepcopy(slot)
    assert module.ensure(slot) is slot
    instance = GameInstance(game_key=("web", "future", "bot"), modules={"narrative_notes": slot})
    for field in FIELDS:
        with pytest.raises(ModuleStateError):
            getattr(instance, field)
        with pytest.raises(ModuleStateError):
            setattr(instance, field, None)
    assert instance.to_dict()["modules"]["narrative_notes"] == before
