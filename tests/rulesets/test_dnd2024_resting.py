from __future__ import annotations

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.resting import Dnd2024RestEngine, RestError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


def _quick_character(preset_id: str = "stalwart_guardian") -> tuple[Dnd2024Runtime, dict]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    draft = {**preset["draft"], "locale": "en", "name": "Rest Tester"}
    return runtime, runtime.finalize_character(None, draft)["ruleset_character"]


def test_creation_initializes_class_resources_without_touching_hp() -> None:
    runtime, fighter = _quick_character()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))

    synced = engine.sync_resources(fighter)

    assert synced["resources"]["class"] == {
        "second_wind": {
            "current": 2,
            "maximum": 2,
            "source_ref": "srd-5.2.1:p48:second-wind",
        }
    }
    assert synced["resources"]["hp"] == fighter["resources"]["hp"]


def test_short_rest_spends_hit_dice_and_uses_partial_resource_recovery() -> None:
    runtime, fighter = _quick_character()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))
    fighter = engine.sync_resources(fighter)
    fighter["resources"]["hp"] = 2
    fighter["resources"]["class"]["second_wind"]["current"] = 0

    result = engine.complete_short_rest(fighter, {"d10": [6]})
    rested = result["character"]

    assert rested["resources"]["hp"] == 10
    assert rested["resources"]["hit_dice"] == {"d10": 0}
    assert rested["resources"]["class"]["second_wind"]["current"] == 1
    assert result["source_ref"] == "srd-5.2.1:p188:short-rest"


def test_long_rest_restores_all_2024_hit_dice_slots_resources_and_ends_concentration() -> None:
    runtime, wizard = _quick_character("curious_arcanist")
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))
    wizard = engine.sync_resources(wizard)
    wizard["resources"]["hp"] = 1
    wizard["resources"]["hit_dice"]["d6"] = 0
    wizard["resources"]["class"]["arcane_recovery"]["current"] = 0
    wizard["spellcasting"]["class"]["slots_current"] = {"1": 0}
    wizard["spellcasting"]["class"]["concentration"] = {"spell_ref": "spell:detect_magic"}
    wizard["conditions"] = {"exhaustion": 2}

    result = engine.complete_long_rest(wizard)
    rested = result["character"]

    assert rested["resources"]["hp"] == rested["resources"]["max_hp"]
    assert rested["resources"]["hit_dice"] == {"d6": 1}
    assert rested["resources"]["class"]["arcane_recovery"]["current"] == 1
    assert rested["spellcasting"]["class"]["slots_current"] == {"1": 2}
    assert rested["spellcasting"]["class"]["concentration"] is None
    assert rested["conditions"]["exhaustion"] == 1


def test_pact_magic_slots_return_on_short_rest() -> None:
    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))
    warlock = {
        "build": {"level": 2, "class_levels": [{"class_ref": "class:warlock", "level": 2}]},
        "abilities": {"str": 8, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 16},
        "resources": {"hp": 12, "max_hp": 16, "hit_dice": {"d8": 2}},
        "spellcasting": {"class": {
            "slot_profile": "pact", "slots_current": {"1": 0}, "slots_max": {"1": 2},
            "concentration": None,
        }},
    }

    rested = engine.complete_short_rest(warlock)["character"]

    assert rested["spellcasting"]["class"]["slots_current"] == {"1": 2}
    assert rested["resources"]["class"]["magical_cunning"]["maximum"] == 1


def test_rest_rejects_zero_hp_and_invalid_hit_die_rolls() -> None:
    runtime, fighter = _quick_character()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))
    fighter["resources"]["hp"] = 0
    with pytest.raises(RestError, match="at least 1 HP"):
        engine.complete_long_rest(fighter)
    fighter["resources"]["hp"] = 1
    with pytest.raises(RestError, match="1 to 10"):
        engine.complete_short_rest(fighter, {"d10": [11]})


def test_runtime_rest_returns_legacy_projection_and_canonical_events() -> None:
    runtime, fighter = _quick_character()
    fighter["resources"]["hp"] = 3

    result = runtime.complete_rest(
        None, {"ruleset_character": fighter}, "short", {"d10": [5]},
    )

    assert result["character"]["hp"] == 10
    assert result["character"]["ruleset_character"]["resources"]["hp"] == 10
    assert result["events"][0]["type"] == "spend_hit_die"


def test_long_rest_resets_feature_usage_and_short_rest_does_not() -> None:
    """The feature usage ledger is long-rest scoped, not a class resource pool.

    ``features.usage`` is not a consumable resource: it only ever increases
    during the day and is cleared by exactly one rest type.  A short rest that
    silently reset it would give the first free Overchannel back every time.
    """

    runtime, fighter = _quick_character()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))
    fighter["features"] = {"usage": [{
        "owner": "subclass:evoker", "feature_id": "overchannel",
        "scope": "long_rest", "count": 2,
    }]}

    short = engine.complete_short_rest(deepcopy(fighter), {"d10": [5]})
    assert "reset_feature_usage" not in [event["type"] for event in short["events"]]
    assert short["character"]["features"]["usage"][0]["count"] == 2

    long = engine.complete_long_rest(deepcopy(fighter))
    resets = [event for event in long["events"] if event["type"] == "reset_feature_usage"]
    assert len(resets) == 1
    assert (resets[0]["before"], resets[0]["after"]) == (2, 0)
    assert "usage" not in long["character"].get("features", {})
