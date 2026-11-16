"""Early free-source authority and later Font slots must compose in both modes."""

from copy import deepcopy

import pytest

import test_dnd2024_font_of_magic as font
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.events import EventBatchError


@pytest.fixture(scope="module")
def runtime():
    return Dnd2024Runtime()


@pytest.mark.parametrize("mode", ["combat", "exploration"])
@pytest.mark.parametrize("slot_level", [0, 1])
@pytest.mark.parametrize("ordinary_count", [0, 1])
def test_free_and_paid_sources_remain_distinct_with_font_slots(
    runtime, mode, slot_level, ordinary_count
):
    sheet = font._sorcerer(runtime, 5)
    character = sheet["ruleset_character"]
    # A legal Magic Initiate choice on the existing Acolyte character. Keep
    # it feat-only so ability/source selection also traverses the shared owner.
    entry = next(
        row for row in character["spellcasting"]["magic_initiate"]
        if row["feat_ref"] == "feat:magic_initiate_cleric"
    )
    entry["level_1_spell_id"] = "cure_wounds"
    entry.pop("free_cast_used", None)
    block = character["spellcasting"]["class"]
    block["font_slots"] = {"1": 1}
    block["slots_current"]["1"] = ordinary_count
    character["resources"]["hp"] = 1
    engine, instance = (
        font._combat(runtime, sheet) if mode == "combat"
        else font._exploration(runtime, sheet)
    )
    before = deepcopy(font._canonical(instance))
    if mode == "exploration":
        offered = next(
            row for row in engine.available_intents(instance, "gm")
            if row["type"] == "exploration.cast_spell"
        )
        levels = next(
            row["available_slot_levels"] for row in offered["spells"]
            if row["spell_ref"] == "spell:cure_wounds"
        )
        assert levels[:2] == [0, 1]
        assert levels.count(1) == 1
    intent = {
        "intent_id": "combined-sources",
        "type": "cast_spell" if mode == "combat" else "exploration.cast_spell",
        "submitted_by": "gm", "actor_id": "player:gm",
        "expected_version": font._state(instance)["version"],
        "spell_ref": "spell:cure_wounds", "slot_level": slot_level,
        "target_ids": ["player:gm"],
    }
    result = engine.resolve_intent(instance, intent, font._FixedDie())
    assert result["ok"], result
    # Planning cannot consume either candidate source.
    assert font._canonical(instance) == before
    costs = [
        event for event in result["event_batch"]["events"] if event["type"] in {
            "dnd2024.spell.font_slot_spent", "dnd2024.spell.slot_spent",
            "dnd2024.feat_spell.free_cast_spent",
        }
    ]
    expected_cost = (
        "dnd2024.spell.font_slot_spent" if slot_level
        else "dnd2024.feat_spell.free_cast_spent"
    )
    assert [event["type"] for event in costs] == [expected_cost]
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    canonical = font._canonical(instance)
    assert font._slots(instance) == before["spellcasting"]["class"]["slots_current"]
    assert font._font(instance) == ({} if slot_level else {"1": 1})
    feat = next(
        row for row in canonical["spellcasting"]["magic_initiate"]
        if row["feat_ref"] == "feat:magic_initiate_cleric"
    )
    assert bool(feat.get("free_cast_used")) == (slot_level == 0)
    if mode == "combat":
        assert bool(font._economy(instance).get("slot_spell_cast")) == bool(slot_level)
    after = deepcopy(canonical)
    assert engine.apply_batch(instance, result["event_batch"])["duplicate"]
    assert font._canonical(instance) == after


@pytest.mark.parametrize("target_kind", ["player", "companion"])
def test_font_healing_conditions_only_change_when_the_batch_commits(runtime, target_kind):
    sheet = font._sorcerer(runtime, 5)
    canonical = sheet["ruleset_character"]
    block = canonical["spellcasting"]["class"]
    block["prepared_spell_refs"].append("spell:cure_wounds")
    block["font_slots"] = {"1": 1}
    engine, instance = font._exploration(runtime, sheet)
    engine._state(instance)
    target = deepcopy(canonical)
    target["resources"]["hp"] = 0
    target["conditions"] = {
        "unconscious": {"source": "zero_hp"}, "stable": {},
        "death_saves": {"successes": 1, "failures": 2},
    }
    if target_kind == "player":
        instance.players["ally"] = {
            "character_name": "Ally", "character_sheet": {"ruleset_character": target},
        }

        def current_target():
            return font._canonical(instance, "ally")
    else:
        font._state(instance)["party"]["companions"]["ally"] = {
            "active": True, "name": "Ally", "ruleset_character": target,
        }

        def current_target():
            return font._state(instance)["party"]["companions"]["ally"]["ruleset_character"]

    before = deepcopy(current_target())
    result = engine.resolve_intent(instance, {
        "intent_id": "font-healing-atomic", "type": "exploration.cast_spell",
        "submitted_by": "gm", "actor_id": "player:gm",
        "expected_version": font._state(instance)["version"],
        "spell_ref": "spell:cure_wounds", "slot_level": 1,
        "target_ids": [f"{target_kind}:ally"],
    }, font._FixedDie())
    assert result["ok"], result
    assert current_target() == before
    font._canonical(instance)["spellcasting"]["class"]["font_slots"] = {}
    with pytest.raises(EventBatchError, match="already spent"):
        engine.apply_batch(instance, result["event_batch"])
    assert current_target() == before
    font._canonical(instance)["spellcasting"]["class"]["font_slots"] = {"1": 1}
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    healed = current_target()
    assert healed["resources"]["hp"] > 0
    assert "unconscious" not in healed["conditions"]
    assert "stable" not in healed["conditions"]
    assert healed["conditions"]["death_saves"] == {"successes": 0, "failures": 0}
