from __future__ import annotations

import pytest

from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.progression import (
    Dnd2024AdvancementEngine,
    Dnd2024ProgressionCatalog,
    ProgressionCatalogError,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


@pytest.fixture(scope="module")
def catalog() -> Dnd2024ProgressionCatalog:
    return Dnd2024ProgressionCatalog.from_bundle(Dnd2024Runtime().load_bundle("en"))


def test_all_srd_classes_have_twenty_valid_progression_rows(
    catalog: Dnd2024ProgressionCatalog,
) -> None:
    assert len(catalog.classes) == 12
    for class_id in catalog.classes:
        rows = catalog.range(f"class:{class_id}")
        assert [row["level"] for row in rows] == list(range(1, 21))
        assert rows[0]["proficiency_bonus"] == 2
        assert rows[-1]["proficiency_bonus"] == 6
        assert all(row["source_ref"].startswith("srd-5.2.1:") for row in rows)


def test_progression_spot_checks_match_srd_5_2_1_tables(
    catalog: Dnd2024ProgressionCatalog,
) -> None:
    barbarian_9 = catalog.snapshot("class:barbarian", 9)
    assert barbarian_9["tracks"] == {
        "rages": 4,
        "rage_damage": 3,
        "weapon_mastery": 3,
    }
    assert barbarian_9["gained_feature_ids"] == ["brutal_strike"]

    fighter_17 = catalog.snapshot("class:fighter", 17)
    assert fighter_17["tracks"]["action_surge"] == 2
    assert fighter_17["tracks"]["indomitable"] == 3

    wizard_20 = catalog.snapshot("class:wizard", 20)
    assert wizard_20["tracks"]["prepared_spells"] == 25
    assert wizard_20["spell_slots"] == {
        "1": 4, "2": 3, "3": 3, "4": 3, "5": 3,
        "6": 2, "7": 2, "8": 1, "9": 1,
    }

    paladin_17 = catalog.snapshot("class:paladin", 17)
    assert paladin_17["spell_slots"] == {"1": 4, "2": 3, "3": 3, "4": 3, "5": 1}

    warlock_11 = catalog.snapshot("class:warlock", 11)
    assert warlock_11["spell_slots"] == {"5": 3}
    assert warlock_11["tracks"]["eldritch_invocations"] == 7


@pytest.mark.parametrize("level", [0, 21, True, 1.5])
def test_progression_rejects_levels_outside_the_srd_table(
    catalog: Dnd2024ProgressionCatalog, level: object,
) -> None:
    with pytest.raises(ProgressionCatalogError):
        catalog.snapshot("class:fighter", level)  # type: ignore[arg-type]


def test_progression_rejects_unknown_class(catalog: Dnd2024ProgressionCatalog) -> None:
    with pytest.raises(ProgressionCatalogError, match="unknown class progression"):
        catalog.snapshot("class:invented", 1)


def _quick_character(preset_id: str = "stalwart_guardian") -> tuple[Dnd2024Runtime, dict]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    draft = {**preset["draft"], "locale": "en", "name": "Level Tester"}
    return runtime, runtime.finalize_character(None, draft)["ruleset_character"]


def test_advancement_preview_is_pure_and_reports_exact_level_diff() -> None:
    runtime, character = _quick_character()
    engine = Dnd2024AdvancementEngine(runtime.load_bundle("en"))

    preview = engine.preview_next_level(character)

    assert preview["ok"] is True
    assert preview["from_level"] == 1
    assert preview["to_level"] == 2
    assert preview["diff"]["gained_feature_ids"] == ["action_surge", "tactical_mind"]
    assert preview["diff"]["hp"]["gain"] == 8
    assert character["build"]["level"] == 1


def test_advancement_requires_subclass_and_ability_score_choices() -> None:
    runtime, character = _quick_character()
    engine = Dnd2024AdvancementEngine(runtime.load_bundle("en"))
    level_two = engine.apply_next_level(character)

    missing_subclass = engine.preview_next_level(level_two)
    assert missing_subclass["ok"] is False
    assert missing_subclass["requirements"][0]["id"] == "subclass_ref"

    level_three = engine.apply_next_level(
        level_two, {"subclass_ref": "subclass:champion"}
    )
    assert level_three["features"]["subclass_ref"] == "subclass:champion"
    assert {"improved_critical", "remarkable_athlete"}.issubset(
        {item["id"] for item in level_three["features"]["class_feature_grants"]}
    )
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    assert resolver.has_feature(
        level_three, "improved_critical", owner="subclass:champion",
    ) is True

    missing_asi = engine.preview_next_level(level_three)
    assert missing_asi["ok"] is False
    assert missing_asi["requirements"][0]["id"] == "ability_score_increases"

    level_four = engine.apply_next_level(
        level_three, {"ability_score_increases": {"con": 2}}
    )
    assert level_four["abilities"]["con"] == 16
    assert level_four["resources"]["max_hp"] == 40
    assert level_four["derived"]["proficiency_bonus"] == 2
    assert [item["to_level"] for item in level_four["progression"]["history"]] == [2, 3, 4]


def test_advancement_rejects_tampered_or_out_of_phase_choices() -> None:
    runtime, character = _quick_character()
    engine = Dnd2024AdvancementEngine(runtime.load_bundle("en"))

    invalid_hp = engine.preview_next_level(character, {"hp_method": "rolled", "hp_roll": 11})
    assert invalid_hp["ok"] is False
    assert any("hp_roll" in error for error in invalid_hp["errors"])

    invalid_asi = engine.preview_next_level(
        character, {"ability_score_increases": {"str": 2}}
    )
    assert invalid_asi["ok"] is False
    assert "ability_score_increases is not available at this level" in invalid_asi["errors"]


def test_high_level_submission_is_rebuilt_from_creation_and_advancement_history() -> None:
    runtime, canonical = _quick_character()
    level_two = runtime.apply_advancement(None, {"ruleset_character": canonical})
    level_two["ruleset_character"]["resources"]["max_hp"] = 9999
    level_two["ruleset_character"]["derived"]["proficiency_bonus"] = 99
    level_two["max_hp"] = 9999

    normalized = runtime.normalize_character_submission(None, level_two, "en")

    assert normalized["level"] == 2
    assert normalized["max_hp"] == 20
    assert normalized["ruleset_character"]["resources"]["max_hp"] == 20
    assert normalized["ruleset_character"]["derived"]["proficiency_bonus"] == 2


def test_spellcaster_advancement_requires_level_appropriate_full_selection() -> None:
    runtime, wizard = _quick_character("curious_arcanist")
    engine = Dnd2024AdvancementEngine(runtime.load_bundle("en"))

    missing = engine.preview_next_level(wizard)
    assert missing["ok"] is False
    assert any(item["id"] == "class_spell_choices" for item in missing["requirements"])

    initial = wizard["build"]["class_spell_choices"]
    level_two_choices = {
        "cantrip_refs": initial["cantrip_refs"],
        "prepared_spell_refs": [*initial["prepared_spell_refs"], "spell:shield"],
        "spellbook_refs": [*initial["spellbook_refs"], "spell:shield", "spell:grease"],
    }
    level_two = engine.apply_next_level(
        wizard, {"class_spell_choices": level_two_choices}
    )

    class_magic = level_two["spellcasting"]["class"]
    assert class_magic["prepared_capacity"] == 5
    assert class_magic["prepared_spell_refs"][-1] == "spell:shield"
    assert class_magic["slots_current"] == {"1": 3}
    assert len(class_magic["spellbook_refs"]) == 8

    invalid = engine.preview_next_level(
        wizard,
        {"class_spell_choices": {
            **level_two_choices,
            "prepared_spell_refs": [*initial["prepared_spell_refs"], "spell:fireball"],
        }},
    )
    assert invalid["ok"] is False
    assert any("not eligible" in error for error in invalid["errors"])


def test_general_feat_prerequisites_and_ability_pattern_are_enforced() -> None:
    runtime, character = _quick_character()
    engine = Dnd2024AdvancementEngine(runtime.load_bundle("en"))
    level_two = engine.apply_next_level(character)
    level_three = engine.apply_next_level(
        level_two, {"subclass_ref": "subclass:champion"}
    )

    grappler = engine.preview_next_level(
        level_three,
        {"feat_ref": "feat:grappler", "ability_score_increases": {"str": 1}},
    )

    assert grappler["ok"] is True
    assert grappler["diff"]["advancement_feat_ref"] == "feat:grappler"
    assert grappler["diff"]["abilities"]["str"] == level_three["abilities"]["str"] + 1
    assert grappler["requirements"][0]["total"] == 1
    applied = engine.apply_next_level(
        level_three,
        {"feat_ref": "feat:grappler", "ability_score_increases": {"str": 1}},
    )
    assert "feat:grappler" in applied["features"]["feat_refs"]


def test_epic_boon_opens_the_level_twenty_path() -> None:
    runtime, current = _quick_character()
    engine = Dnd2024AdvancementEngine(runtime.load_bundle("en"))
    for target_level in range(2, 21):
        choices: dict = {}
        gained = engine.catalog.snapshot("class:fighter", target_level)["gained_feature_ids"]
        if target_level == 3:
            choices["subclass_ref"] = "subclass:champion"
        if "ability_score_improvement" in gained:
            ability = next(
                key for key, score in current["abilities"].items() if int(score) <= 18
            )
            choices.update({
                "feat_ref": "feat:ability_score_improvement",
                "ability_score_increases": {ability: 2},
            })
        if "epic_boon" in gained:
            choices.update({
                "epic_boon_ref": "feat:boon_of_truesight",
                "ability_score_increases": {"wis": 1},
            })
        current = engine.apply_next_level(current, choices)

    assert current["build"]["level"] == 20
    assert current["progression"]["level"] == 20
    assert len(current["progression"]["history"]) == 19
    assert "feat:boon_of_truesight" in current["features"]["feat_refs"]
