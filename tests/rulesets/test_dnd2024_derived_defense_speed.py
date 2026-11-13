"""Unarmored Defense and derived Speed for the Barbarian and the Monk.

Two rules are under test, and each has exactly one authority:

* ``derive_armor_class`` stays the only Armor Class formula.  The class features
  contribute *which* ability is added to an unarmored base, and their declared
  equipment precondition decides whether that contribution is in force at all.
* ``derive_speed`` stays the only Speed formula.  Class features contribute a
  flat or level-scaled bonus, and the worn armor contributes the "-10 feet" a
  Strength requirement imposes.

Both are exercised through the real bundle and the real character chain, so no
assertion here invents an item, a category, or a number.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.rulesets.dnd2024.character.derivation import (
    derive_armor_class,
    derive_speed,
)
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.features import (
    ClassFeatureError,
    Dnd2024ClassFeatureResolver,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import advance, monk_sheet


LOCALE = "en"
SPECIES_SPEED = 30


# --------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _resolver(runtime: Dnd2024Runtime) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))


def _proxy(class_ref: str, level: int, *item_refs: str) -> dict:
    """The minimal canonical shape the feature boundary reads."""

    character: dict = {
        "build": {"class_levels": [{"class_ref": class_ref, "level": level}]},
    }
    if item_refs:
        character["equipment"] = {"item_refs": [str(ref) for ref in item_refs]}
    return character


def _draft(runtime: Dnd2024Runtime, class_ref: str, package_ref: str) -> dict:
    """A legal level-1 draft for a non-spellcasting class."""

    skills = {
        "class:barbarian": ["skill:athletics", "skill:perception"],
        "class:monk": ["skill:acrobatics", "skill:stealth"],
    }[class_ref]
    draft: dict = {
        "locale": LOCALE,
        "name": "Hero",
        "level": 1,
        "ability_method": "standard_array",
        "base_abilities": {
            "str": 15, "dex": 14, "con": 13, "int": 8, "wis": 12, "cha": 10,
        },
        "class_ref": class_ref,
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "class_skill_refs": skills,
        "equipment_package_ref": package_ref,
        "background_equipment_package_ref": "equipment_package:acolyte_b",
        "alignment": "neutral",
    }
    choices = runtime.builder_choices(None, draft)
    ability_refs = choices["background_ability_refs"]
    draft["background_ability_bonuses"] = {
        ability_refs[0].split(":", 1)[1]: 2,
        ability_refs[1].split(":", 1)[1]: 1,
    }
    draft["species_size"] = (choices["species_sizes"] or [""])[0]
    draft["species_skill_refs"] = [
        item["ref"] for item in choices["species_skills"]
    ][: choices["species_skill_count"]]
    draft["species_feat_refs"] = [
        item["ref"] for item in choices["species_feats"]
    ][: choices["species_feat_count"]]
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
    draft["language_refs"] = ["language:common", "language:dwarvish", "language:elvish"]
    draft["feat_choice_answers"] = {
        feat["feat_ref"]: {
            spec["id"]: [option["value"] for option in spec["options"]][: int(spec["count"])]
            for spec in feat["specs"]
        }
        for feat in choices["feat_choices"]
    }
    errors = runtime.validate_character(None, draft)
    assert errors == [], errors
    return draft


def _sheet(runtime: Dnd2024Runtime, class_ref: str, *, level: int = 1) -> dict:
    package_ref = {
        "class:barbarian": "equipment_package:barbarian_a",
        "class:monk": "equipment_package:monk_b",
    }[class_ref]
    sheet = runtime.finalize_character(None, _draft(runtime, class_ref, package_ref))
    if level > 1:
        sheet = advance(runtime, sheet, level)
    return sheet


def _reconciled(runtime: Dnd2024Runtime, sheet: dict, *item_refs: str) -> dict:
    """Re-derive the canonical record wearing exactly ``item_refs``."""

    canonical = deepcopy(sheet["ruleset_character"])
    canonical["equipment"]["item_refs"] = [str(ref) for ref in item_refs]
    Dnd2024CharacterStateReconciler(
        runtime.load_bundle(LOCALE),
    ).reconcile_canonical(canonical)
    return canonical


def _armor_class(
    runtime: Dnd2024Runtime, character: dict, abilities: dict, *item_refs: str,
    armor_category_refs=("armor_category:light", "armor_category:medium",
                         "armor_category:shield"),
) -> int:
    return derive_armor_class(
        abilities, list(item_refs), runtime.load_bundle(LOCALE),
        armor_category_refs=list(armor_category_refs),
        unarmored_ability_bonuses=_resolver(runtime).unarmored_ac_bonus_abilities(
            character
        ),
    )


# --------------------------------------------------------------------------
# 1. declaration, ownership and labels
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("owner", "ability", "level", "requirement"),
    [
        ("class:barbarian", "con", 1, {"unarmored": True}),
        ("class:monk", "wis", 1, {"unarmored": True, "no_shield": True}),
    ],
)
def test_unarmored_defense_is_declared_with_the_srd_precondition(
    runtime, owner, ability, level, requirement,
) -> None:
    definition = _resolver(runtime).catalog.features_by_key[(owner, "unarmored_defense")]

    assert definition.minimum_level == level
    assert definition.parameters["unarmored_ac_ability_bonus"] == ability
    assert definition.equipment_requirement.weapon_kinds == ()
    # Barbarian keeps the benefit with a Shield; the Monk does not.
    assert definition.equipment_requirement.unarmored is True
    assert definition.equipment_requirement.no_shield is requirement.get("no_shield", False)
    assert definition.equipment_requirement.no_heavy_armor is False
    assert definition.source_ref.startswith("srd-5.2.1:")
    assert definition.resource_id == ""
    assert definition.choice is None


def test_both_labels_exist_for_the_new_declarations(runtime) -> None:
    english = _resolver(runtime)
    chinese = Dnd2024ClassFeatureResolver(runtime.load_bundle("zh-CN"))

    assert english.catalog.label("unarmored_defense") == "Unarmored Defense"
    assert english.catalog.label("unarmored_movement") == "Unarmored Movement"
    assert english.catalog.label("fast_movement") == "Fast Movement"
    assert chinese.catalog.label("unarmored_defense") == "无甲防御"
    assert chinese.catalog.label("unarmored_movement") == "无甲移动"
    assert chinese.catalog.label("fast_movement") == "快速移动"


def test_a_character_without_the_class_owns_no_unarmored_defense(runtime) -> None:
    resolver = _resolver(runtime)

    for class_ref in ("class:wizard", "class:rogue", "class:fighter"):
        character = _proxy(class_ref, 20)
        assert resolver.has_feature(character, "unarmored_defense") is False
        assert resolver.unarmored_ac_bonus_abilities(character) == ()
        assert resolver.speed_bonus(character) == 0


def test_boundary_apis_degrade_to_nothing_without_a_class(runtime) -> None:
    resolver = _resolver(runtime)

    for character in (
        None,
        {},
        {"build": {}},
        {"build": {"class_levels": []}},
        {"build": {"class_levels": [{"class_ref": "", "level": 1}]}},
    ):
        assert resolver.unarmored_ac_bonus_abilities(character) == ()
        assert resolver.speed_bonus(character) == 0


# --------------------------------------------------------------------------
# 2. Barbarian and Monk Armor Class
# --------------------------------------------------------------------------

BARBARIAN_ABILITIES = {
    "str": 16, "dex": 14, "con": 16, "int": 8, "wis": 10, "cha": 8,
}
MONK_ABILITIES = {
    "str": 10, "dex": 16, "con": 12, "int": 8, "wis": 14, "cha": 8,
}


def test_barbarian_unarmored_armor_class_is_ten_plus_dex_plus_con(runtime) -> None:
    character = _proxy("class:barbarian", 1)

    assert _resolver(runtime).unarmored_ac_bonus_abilities(character) == ("con",)
    assert _armor_class(
        runtime, character, BARBARIAN_ABILITIES, armor_category_refs=[],
    ) == 15  # 10 + Dex 2 + Con 3


def test_barbarian_keeps_the_benefit_while_using_a_trained_shield(runtime) -> None:
    character = _proxy("class:barbarian", 1, "item:shield")

    assert _resolver(runtime).unarmored_ac_bonus_abilities(character) == ("con",)
    assert _armor_class(
        runtime, character, BARBARIAN_ABILITIES, "item:shield",
    ) == 17  # 15 + 2
    # An untrained shield adds nothing, and never suppresses the formula.
    assert _armor_class(
        runtime, character, BARBARIAN_ABILITIES, "item:shield",
        armor_category_refs=[],
    ) == 15


@pytest.mark.parametrize(
    ("armor_ref", "expected"),
    [
        ("item:leather_armor", 13),   # light 11 + Dex 2
        ("item:hide", 14),            # medium 12 + Dex 2
        ("item:chain_mail", 16),      # heavy 16, no Dexterity
    ],
)
def test_barbarian_unarmored_candidate_is_dropped_by_any_armor(
    runtime, armor_ref, expected,
) -> None:
    character = _proxy("class:barbarian", 1, armor_ref)

    assert _resolver(runtime).unarmored_ac_bonus_abilities(character) == ()
    assert _armor_class(
        runtime, character, BARBARIAN_ABILITIES, armor_ref,
    ) == expected


def test_monk_unarmored_armor_class_is_ten_plus_dex_plus_wis(runtime) -> None:
    character = _proxy("class:monk", 1)

    assert _resolver(runtime).unarmored_ac_bonus_abilities(character) == ("wis",)
    assert _armor_class(
        runtime, character, MONK_ABILITIES, armor_category_refs=[],
    ) == 15  # 10 + Dex 3 + Wis 2


def test_monk_loses_the_formula_when_wearing_armor_or_wielding_a_shield(
    runtime,
) -> None:
    resolver = _resolver(runtime)

    armored = _proxy("class:monk", 1, "item:leather_armor")
    shielded = _proxy("class:monk", 1, "item:shield")

    assert resolver.unarmored_ac_bonus_abilities(armored) == ()
    assert resolver.unarmored_ac_bonus_abilities(shielded) == ()
    # Leather keeps its own 11 + Dex 3; the Wisdom bonus must not leak in.
    assert _armor_class(runtime, armored, MONK_ABILITIES, "item:leather_armor") == 14
    # A Shield alone still grants only its own bonus: 10 + Dex 3 + 2.
    assert _armor_class(runtime, shielded, MONK_ABILITIES, "item:shield") == 15

    # Removing it restores the benefit, so nothing was written into the sheet.
    assert resolver.unarmored_ac_bonus_abilities(_proxy("class:monk", 1)) == ("wis",)


def test_multiple_unarmored_candidates_are_alternatives_not_a_sum(runtime) -> None:
    bundle = runtime.load_bundle(LOCALE)

    # The rule is "one base Armor Class at a time": the best candidate wins.
    assert derive_armor_class(
        BARBARIAN_ABILITIES, [], bundle,
        armor_category_refs=[], unarmored_ability_bonuses=("con", "wis"),
    ) == 15  # 10 + Dex 2 + Con 3 beats 10 + Dex 2 + Wis 0
    assert derive_armor_class(
        BARBARIAN_ABILITIES, [], bundle,
        armor_category_refs=[], unarmored_ability_bonuses=("con", "wis"),
    ) == derive_armor_class(
        BARBARIAN_ABILITIES, [], bundle,
        armor_category_refs=[], unarmored_ability_bonuses=("con",),
    )


def test_creation_and_reconciliation_agree_on_the_armor_class(runtime) -> None:
    monk = _sheet(runtime, "class:monk", level=1)
    created = monk["ruleset_character"]["derived"]["armor_class"]

    bare = _reconciled(runtime, monk)
    shielded = _reconciled(runtime, monk, "item:shield")
    armored = _reconciled(runtime, monk, "item:chain_mail")

    # 10 + Dexterity 2 + Wisdom 1 for the draft's final abilities.
    assert created == 13
    assert bare["derived"]["armor_class"] == created
    # A Monk has no Shield training, so the shield adds nothing and the
    # unarmored formula is already gone.
    assert shielded["derived"]["armor_class"] == 12
    assert armored["derived"]["armor_class"] == 16
    assert _reconciled(runtime, monk)["derived"]["armor_class"] == created


# --------------------------------------------------------------------------
# 3. Fast Movement and Unarmored Movement
# --------------------------------------------------------------------------

def test_fast_movement_is_flat_ten_except_under_heavy_armor(runtime) -> None:
    resolver = _resolver(runtime)

    assert resolver.has_feature(_proxy("class:barbarian", 4), "fast_movement") is False
    assert resolver.speed_bonus(_proxy("class:barbarian", 4)) == 0

    assert resolver.speed_bonus(_proxy("class:barbarian", 5)) == 10
    for armor_ref in ("item:leather_armor", "item:hide"):
        assert resolver.speed_bonus(
            _proxy("class:barbarian", 5, armor_ref)
        ) == 10
    # A Shield never interferes.
    assert resolver.speed_bonus(
        _proxy("class:barbarian", 5, "item:shield")
    ) == 10
    # Heavy armor is the one thing that switches it off.
    assert resolver.speed_bonus(
        _proxy("class:barbarian", 5, "item:chain_mail")
    ) == 0
    assert resolver.speed_bonus(
        _proxy("class:barbarian", 5, "item:plate")
    ) == 0


@pytest.mark.parametrize(
    ("level", "bonus"),
    [(2, 10), (5, 10), (6, 15), (10, 20), (13, 20), (14, 25), (18, 30), (20, 30)],
)
def test_unarmored_movement_follows_the_monk_table(runtime, level, bonus) -> None:
    assert _resolver(runtime).speed_bonus(_proxy("class:monk", level)) == bonus


def test_unarmored_movement_requires_no_armor_and_no_shield(runtime) -> None:
    resolver = _resolver(runtime)

    assert resolver.speed_bonus(_proxy("class:monk", 10, "item:leather_armor")) == 0
    assert resolver.speed_bonus(_proxy("class:monk", 10, "item:shield")) == 0
    assert resolver.speed_bonus(_proxy("class:monk", 10)) == 20
    # Losing the bonus never touches the declaration: removing the armor
    # restores the same value.
    assert resolver.speed_bonus(_proxy("class:monk", 10)) == 20


def test_derive_speed_is_the_only_place_numbers_are_combined(runtime) -> None:
    assert derive_speed(SPECIES_SPEED) == SPECIES_SPEED
    assert derive_speed(SPECIES_SPEED, feature_bonus=10) == 40
    assert derive_speed(
        SPECIES_SPEED, feature_bonus=10, armor_training={"speed_penalty": 10},
    ) == 30
    # Never negative, and a malformed bracket is ignored rather than guessed.
    assert derive_speed(5, armor_training={"speed_penalty": 15}) == 0
    assert derive_speed(30, armor_training={"speed_penalty": True}) == 30
    assert derive_speed(30, armor_training=None) == 30


def test_advancement_updates_speed_immediately(runtime) -> None:
    one = _sheet(runtime, "class:monk", level=1)
    assert one["ruleset_character"]["derived"]["speed"] == SPECIES_SPEED

    two = advance(runtime, one, 2)
    assert two["ruleset_character"]["derived"]["speed"] == SPECIES_SPEED + 10

    six = advance(runtime, two, 6)
    assert six["ruleset_character"]["derived"]["speed"] == SPECIES_SPEED + 15


def test_reconciliation_recomputes_speed_from_the_current_equipment(runtime) -> None:
    monk = _sheet(runtime, "class:monk", level=10)
    assert monk["ruleset_character"]["derived"]["speed"] == SPECIES_SPEED + 20

    armored = _reconciled(runtime, monk, "item:leather_armor")
    assert armored["derived"]["speed"] == SPECIES_SPEED

    shielded = _reconciled(runtime, monk, "item:shield")
    assert shielded["derived"]["speed"] == SPECIES_SPEED

    # Take it off again and the benefit returns.
    assert _reconciled(runtime, monk)["derived"]["speed"] == SPECIES_SPEED + 20


def test_heavy_armor_drops_fast_movement_in_the_derived_speed(runtime) -> None:
    barbarian = _sheet(runtime, "class:barbarian", level=5)
    assert barbarian["ruleset_character"]["derived"]["speed"] == SPECIES_SPEED + 10

    trained = _reconciled(runtime, barbarian, "item:chain_mail")
    derived = trained["derived"]
    # Heavy armor drops Fast Movement; this Barbarian has Strength 18 after the
    # background bonus, so Chain Mail's requirement is met and no penalty lands.
    assert derived["armor_training"]["speed_penalty"] == 0
    assert derived["speed"] == SPECIES_SPEED


# --------------------------------------------------------------------------
# 4. fail-closed declarations
# --------------------------------------------------------------------------

def _mutated(runtime: Dnd2024Runtime, path: tuple[str, ...], value) -> object:
    bundle = runtime.load_bundle(LOCALE)
    entities = deepcopy(bundle.entities)
    catalog = entities["class_feature_catalog"]["srd_class_features"]
    target = next(
        row for row in catalog["classes"]["barbarian"]["features"]
        if row["id"] == "fast_movement"
    )
    cursor = target
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    return replace(bundle, entities=entities)


@pytest.mark.parametrize(
    "value", [0, -10, "10", True, 121, 1.5],
)
def test_an_invalid_flat_speed_bonus_fails_closed(runtime, value) -> None:
    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(
            _mutated(runtime, ("parameters", "speed_bonus_ft"), value)
        )


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_a_non_boolean_no_heavy_armor_flag_fails_closed(runtime, value) -> None:
    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(
            _mutated(runtime, ("equipment_requirement", "no_heavy_armor"), value)
        )


def test_an_unknown_speed_track_fails_closed(runtime) -> None:
    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(
            _mutated(runtime, ("parameters", "speed_bonus_track"), "not_a_track")
        )
