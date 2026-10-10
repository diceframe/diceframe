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
V1_FRESH = {"schema_version": 1, "summary": {}, "key_facts": [], "confirmed_items": [], "game_time": ""}


def test_migration_moves_notes_without_mutation_and_is_idempotent():
    payload = {"instance_schema_version": 24, "summary": {"narrative": "A meeting"},
               "key_facts": ["the gate is open"], "confirmed_items": ["sword"],
               "game_time": "  第三纪元，暮色\n", "modules": {"extension": {"schema_version": 99}}}
    before = deepcopy(payload)
    migrated = migrate_game_state_payload(payload)
    assert payload == before
    assert all(field not in migrated for field in FIELDS)
    notes = migrated["modules"]["narrative_notes"]
    assert {field: notes[field] for field in FIELDS} == {field: payload[field] for field in FIELDS}
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
    assert _migrate_v24_to_v25(payload)["modules"]["narrative_notes"] == V1_FRESH
    assert module.ensure({"schema_version": 2, **{field: value for field in FIELDS}}) == module.fresh()
    assert module.ensure(value) == module.fresh()


def test_wrong_container_types_default():
    payload = {"summary": [], "key_facts": {}, "confirmed_items": "sword", "game_time": ["dusk"]}
    assert _migrate_v24_to_v25(payload)["modules"]["narrative_notes"] == V1_FRESH
    assert module.ensure({"schema_version": 2, "summary": [], "key_facts": {},
                          "confirmed_items": "sword", "game_time": ["dusk"]}) == module.fresh()


def test_live_accessors_replace_objects_and_roundtrip():
    instance = GameInstance(game_key=("web", "notes", "bot"))
    summary, key_facts, confirmed = {"narrative": "A meeting"}, ["gate"], ["sword"]
    module.replace_summary(instance, summary)
    module.replace_key_facts(instance, key_facts)
    module.replace_confirmed_items(instance, confirmed)
    module.replace_game_time(instance, "Third Age, dusk")
    slot = instance.modules["narrative_notes"]
    assert module.summary(instance) is summary is slot["summary"]
    assert module.key_facts(instance) is key_facts is slot["key_facts"]
    assert module.confirmed_items(instance) is confirmed is slot["confirmed_items"]
    assert module.game_time(instance) == "Third Age, dusk" == slot["game_time"]
    module.summary(instance)["extra"] = "kept"
    module.key_facts(instance).append("bridge")
    module.confirmed_items(instance).append("rope")
    saved = instance.to_dict()
    assert all(field not in saved for field in FIELDS)
    restored = GameInstance.from_dict(saved)
    assert restored.modules["narrative_notes"] == instance.modules["narrative_notes"]
    fresh = module.fresh()
    module.replace_summary(instance, fresh["summary"])
    assert module.summary(instance) is fresh["summary"]
    module.replace_key_facts(instance, fresh["key_facts"])
    assert module.key_facts(instance) is fresh["key_facts"]
    module.replace_confirmed_items(instance, fresh["confirmed_items"])
    assert module.confirmed_items(instance) is fresh["confirmed_items"]
    module.replace_game_time(instance, fresh["game_time"])
    assert module.game_time(instance) is fresh["game_time"]
    assert summary and key_facts and confirmed


@pytest.mark.parametrize("game_time", ["", "14:00", "  第三纪元，暮色\n", "not-a-calendar 0001"])
def test_game_time_preserved_verbatim_without_affecting_progression(game_time):
    instance = GameInstance.from_dict({"game_key": ["web", "clock", "bot"], "state": "created",
                                       "instance_schema_version": 20, "round_number": 7, "game_time": game_time})
    assert module.game_time(instance) == game_time
    assert instance.round_number == 7
    restored = GameInstance.from_dict(instance.to_dict())
    assert module.game_time(restored) == game_time
    assert restored.round_number == 7


def test_aggregate_methods_keep_summary_and_confirmed_item_behavior():
    instance = GameInstance(game_key=("web", "mutators", "bot"))
    summary = module.summary(instance)
    confirmed = module.confirmed_items(instance)
    instance.set_summary_narrative("A summary")
    assert summary == {"narrative": "A summary"}
    facts = ["one", "two"]
    instance.set_key_facts(facts)
    assert module.key_facts(instance) == facts and module.key_facts(instance) is not facts
    instance.add_confirmed_items(["sword", "sword", "rope", "torch"], limit=2)
    assert module.confirmed_items(instance) is confirmed
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
            getattr(module, field)(instance)
        with pytest.raises(ModuleStateError):
            getattr(module, f"replace_{field}")(instance, None)
    assert instance.to_dict()["modules"]["narrative_notes"] == before


def test_game_instance_has_no_narrative_notes_facades():
    instance = GameInstance(game_key=("web", "notes", "u"))
    for name in (*FIELDS, "scene"):
        assert not hasattr(GameInstance, name)
        assert not hasattr(instance, name)
