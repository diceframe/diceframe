"""Draconic Resilience (Sorcerer 3): unarmored AC formula and HP maximum bonus.

The slice's contract is split across two authorities that must never diverge:
the class feature boundary reports *which* unarmored ability is added and how
much the Hit Point maximum grows, while ``derive_armor_class`` remains the one
formula that turns those declarations into numbers.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.rulesets.dnd2024.character.derivation import derive_armor_class
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.combat.engine import Dnd2024CombatEngine
from src.rulesets.dnd2024.features import (
    ClassFeatureError,
    Dnd2024ClassFeatureResolver,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


LOCALE = "en"
FEATURE_ID = "draconic_resilience"


# --------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _proxy(class_ref: str, level: int) -> dict:
    return {"build": {"class_levels": [{"class_ref": class_ref, "level": level}]}}


def _resolver(runtime: Dnd2024Runtime) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))


def _sorcerer_draft(runtime: Dnd2024Runtime) -> dict:
    """A legal level-1 draconic-sorcerer draft: Dex 14, Cha 16, Con 13."""

    choices = runtime.builder_choices(None, {
        "locale": LOCALE,
        "class_ref": "class:sorcerer",
        "background_ref": "background:acolyte",
        "species_ref": "species:human",
    })
    class_entity = runtime.load_bundle(LOCALE).get("class", "sorcerer")
    draft = {
        "locale": LOCALE,
        "name": "Draconic Hero",
        "level": 1,
        "alignment": "neutral_good",
        "ability_method": "standard_array",
        "base_abilities": {
            "str": 8, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 15,
        },
        "background_ability_bonuses": {"int": 2, "cha": 1},
        "class_ref": "class:sorcerer",
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "class_skill_refs": ["skill:arcana", "skill:deception"],
        "equipment_package_ref": choices["equipment_packages"][0]["ref"],
        "background_equipment_package_ref": choices["background_equipment_packages"][0]["ref"],
        "language_refs": ["language:common", "language:dwarvish", "language:elvish"],
        "class_spell_choices": class_entity["recommended_spell_choices"],
        "feat_choice_answers": {
            "feat:magic_initiate_cleric": {
                "spellcasting_ability": ["wis"],
                "cantrips": ["guidance", "light"],
                "level_1_spell": ["bless"],
            },
        },
    }
    choices = runtime.builder_choices(None, draft)
    if choices["species_sizes"]:
        draft["species_size"] = "medium" if "medium" in choices["species_sizes"] else (
            choices["species_sizes"][0]
        )
    if choices["species_skill_count"]:
        draft["species_skill_refs"] = ["skill:perception"]
    if choices["species_feat_count"]:
        draft["species_feat_refs"] = ["feat:alert"]
    if choices["class_tool_count"]:
        draft["class_tool_refs"] = [
            item["ref"] for item in choices["class_tools"][:choices["class_tool_count"]]
        ]
    return draft


def _base_max_hp(level: int, con_modifier: int = 1) -> int:
    """The pre-feature formula: d6 hit die, fixed 4, no species HP bonus."""

    return max(1, 6 + con_modifier) + max(0, level - 1) * max(1, 4 + con_modifier)


def _spell_choice_answers(requirement: dict, canonical: dict) -> dict:
    casting = canonical["spellcasting"]["class"]
    cantrips = list(casting.get("cantrip_refs") or [])
    prepared = list(casting.get("prepared_spell_refs") or [])
    for spell in requirement["cantrips"]:
        if len(cantrips) >= int(requirement["cantrip_count"]):
            break
        if spell["ref"] not in cantrips:
            cantrips.append(spell["ref"])
    for spell in requirement["leveled_spells"]:
        if len(prepared) >= int(requirement["prepared_spell_count"]):
            break
        if spell["ref"] not in prepared:
            prepared.append(spell["ref"])
    return {
        "cantrip_refs": cantrips[: int(requirement["cantrip_count"])],
        "prepared_spell_refs": prepared[: int(requirement["prepared_spell_count"])],
    }


def _advance_choices(preview: dict, canonical: dict) -> dict:
    choices: dict = {}
    for requirement in preview["requirements"]:
        requirement_id = str(requirement["id"])
        if requirement_id == "subclass_ref":
            choices["subclass_ref"] = str(requirement["options"][0]["value"])
        elif requirement_id in {"feat_ref", "epic_boon_ref"}:
            available = [
                option for option in requirement["options"] if option.get("available")
            ]
            choices[requirement_id] = str(available[0]["value"])
        elif requirement_id == "ability_score_increases":
            allowed = requirement.get("allowed", "any")
            ability = "str" if allowed == "any" else str(allowed[0])
            choices[requirement_id] = {ability: int(requirement.get("total", 2))}
        elif requirement_id == "class_spell_choices":
            choices[requirement_id] = _spell_choice_answers(requirement, canonical)
    return choices


def _advance_once(runtime: Dnd2024Runtime, sheet: dict) -> dict:
    preview = runtime.preview_advancement(None, sheet, {})
    choices = {} if preview["ok"] else _advance_choices(
        preview, sheet["ruleset_character"]
    )
    if not preview["ok"]:
        preview = runtime.preview_advancement(None, sheet, choices)
    assert preview["ok"] is True, preview["errors"]
    return runtime.apply_advancement(None, sheet, choices)


def _sorcerer_sheet(runtime: Dnd2024Runtime, level: int) -> dict:
    sheet = runtime.finalize_character(None, _sorcerer_draft(runtime))
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        sheet = _advance_once(runtime, sheet)
    return sheet


def _reconciled(runtime: Dnd2024Runtime, sheet: dict, rows: list[dict]) -> dict:
    live = deepcopy(sheet)
    live["equipment"] = rows
    Dnd2024CharacterStateReconciler(
        runtime.load_bundle(LOCALE), locale_bundles=(runtime.load_bundle(LOCALE),),
    ).reconcile(live)
    return live


def _mutated(runtime: Dnd2024Runtime, key: str, value: object):
    bundle = runtime.load_bundle(LOCALE)
    entities = deepcopy(bundle.entities)
    entities["class_feature_catalog"]["srd_class_features"]["classes"]["sorcerer"][
        "features"
    ][0]["parameters"][key] = value
    return replace(bundle, entities=entities)


# --------------------------------------------------------------------------
# 1. declaration validation
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("key", "value"), [
    ("unarmored_ac_ability_bonus", "luck"),
    ("max_hp_bonus_first", -1),
    ("max_hp_bonus_first", "3"),
    ("max_hp_bonus_first", True),
    ("max_hp_bonus_first", 21),
    ("max_hp_bonus_per_level_after", 21),
])
def test_catalog_rejects_invalid_defense_parameters(runtime, key, value) -> None:
    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_mutated(runtime, key, value))


# --------------------------------------------------------------------------
# 2. ownership
# --------------------------------------------------------------------------

def test_subclass_grant_owns_the_feature_at_level_three(runtime) -> None:
    resolver = _resolver(runtime)

    assert resolver.has_feature(_proxy("class:sorcerer", 3), FEATURE_ID) is True
    assert resolver.has_feature(_proxy("class:sorcerer", 2), FEATURE_ID) is False
    assert resolver.has_feature(_proxy("class:wizard", 3), FEATURE_ID) is False


# --------------------------------------------------------------------------
# 3-5. creation values across levels
# --------------------------------------------------------------------------

def test_level_three_sorcerer_derives_unarmored_ac_and_hp_bonus(runtime) -> None:
    sheet = _sorcerer_sheet(runtime, 3)
    canonical = sheet["ruleset_character"]

    assert canonical["derived"]["armor_class"] == 15  # 10 + 2 (Dex) + 3 (Cha)
    assert canonical["resources"]["max_hp"] == _base_max_hp(3) + 3
    assert canonical["resources"]["hp"] == canonical["resources"]["max_hp"]


def test_level_one_and_two_have_no_draconic_benefit(runtime) -> None:
    one = _sorcerer_sheet(runtime, 1)["ruleset_character"]
    two = _sorcerer_sheet(runtime, 2)["ruleset_character"]

    for canonical in (one, two):
        assert canonical["derived"]["armor_class"] == 12  # 10 + 2 (Dex)
    assert one["resources"]["max_hp"] == _base_max_hp(1)
    assert two["resources"]["max_hp"] == _base_max_hp(2)


@pytest.mark.parametrize("level", [5, 20])
def test_hp_bonus_is_three_then_one_per_level(runtime, level) -> None:
    sheet = _sorcerer_sheet(runtime, level)
    canonical = sheet["ruleset_character"]

    expected_bonus = level
    assert canonical["resources"]["max_hp"] == _base_max_hp(level) + expected_bonus
    assert _resolver(runtime).feature_max_hp_bonus(
        _proxy("class:sorcerer", level)
    ) == expected_bonus


# --------------------------------------------------------------------------
# 6-7. advancement deltas and formula parity
# --------------------------------------------------------------------------

def test_level_up_gain_includes_only_the_incremental_hp_bonus(runtime) -> None:
    two = _sorcerer_sheet(runtime, 2)
    preview = runtime.preview_advancement(None, two, {})
    choices = _advance_choices(preview, two["ruleset_character"])
    preview = runtime.preview_advancement(None, two, choices)
    assert preview["ok"] is True, preview["errors"]
    # 2 -> 3: fixed 4 + Con 1 + the feature's first +3.
    assert preview["diff"]["hp"]["gain"] == 8
    three = runtime.apply_advancement(None, two, choices)
    assert three["ruleset_character"]["resources"]["max_hp"] == _base_max_hp(3) + 3

    next_preview = runtime.preview_advancement(None, three, {})
    next_choices = _advance_choices(next_preview, three["ruleset_character"])
    next_preview = runtime.preview_advancement(None, three, next_choices)
    assert next_preview["ok"] is True, next_preview["errors"]
    # 3 -> 4: fixed 4 + Con 1 + one more feature level (+1).
    assert next_preview["diff"]["hp"]["gain"] == 6


def test_advancement_chain_matches_the_derivation_formula(runtime) -> None:
    sheet = _sorcerer_sheet(runtime, 5)
    canonical = sheet["ruleset_character"]

    assert canonical["resources"]["max_hp"] == _base_max_hp(5) + 5
    assert canonical["resources"]["max_hp"] == 32
    assert canonical["derived"]["armor_class"] == 15


# --------------------------------------------------------------------------
# 8. one base AC at a time
# --------------------------------------------------------------------------

def test_negative_ability_modifier_falls_back_to_the_baseline(runtime) -> None:
    abilities = {
        "str": 8, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 8,
    }
    resolver = _resolver(runtime)
    armor_class = derive_armor_class(
        abilities, [], runtime.load_bundle(LOCALE),
        unarmored_ability_bonuses=resolver.unarmored_ac_bonus_abilities(
            _proxy("class:sorcerer", 3)
        ),
    )

    assert armor_class == 12  # 10 + Dex 2 beats 10 + Dex 2 + Cha -1
    assert armor_class == derive_armor_class(
        abilities, [], runtime.load_bundle(LOCALE),
    )


# --------------------------------------------------------------------------
# 9-11. equipment reconciliation
# --------------------------------------------------------------------------

def test_wearing_armor_suppresses_the_draconic_formula(runtime) -> None:
    sheet = _sorcerer_sheet(runtime, 3)
    live = _reconciled(runtime, sheet, [{"name": "Leather Armor"}])
    canonical = live["ruleset_character"]

    assert canonical["derived"]["armor_class"] == 13  # 11 + Dex 2
    assert live["armor_class"] == 13
    assert live.get("ruleset_operation_log")


def test_unarmored_shield_keeps_the_draconic_formula_plus_two(runtime) -> None:
    sheet = _sorcerer_sheet(runtime, 3)
    live = _reconciled(runtime, sheet, [{"name": "Shield"}])

    assert live["ruleset_character"]["derived"]["armor_class"] == 17  # 15 + 2
    assert live["armor_class"] == 17


def test_removing_armor_returns_to_the_draconic_ac(runtime) -> None:
    sheet = _sorcerer_sheet(runtime, 3)
    armored = _reconciled(runtime, sheet, [{"name": "Leather Armor"}])
    unarmored = _reconciled(runtime, armored, [])

    assert unarmored["ruleset_character"]["derived"]["armor_class"] == 15
    assert unarmored["armor_class"] == 15


# --------------------------------------------------------------------------
# 12-13. combat reads the canonical number; legacy sheets are not retro-fixed
# --------------------------------------------------------------------------

def test_combat_view_reads_the_derived_armor_class(runtime) -> None:
    sheet = _sorcerer_sheet(runtime, 3)
    canonical = sheet["ruleset_character"]
    view = Dnd2024CombatEngine(
        bundle=runtime.load_bundle(LOCALE)
    )._player_view("p1", canonical)

    assert view["armor_class"] == canonical["derived"]["armor_class"] == 15


def test_legacy_sheet_is_not_retroactively_repaired(runtime) -> None:
    sheet = _sorcerer_sheet(runtime, 3)
    legacy = deepcopy(sheet["ruleset_character"])
    # A character that reached Sorcerer 3 before this slice: its stored HP and
    # AC are the old facts and carry no feature projection.
    legacy["resources"]["max_hp"] = _base_max_hp(3)
    legacy["resources"]["hp"] = _base_max_hp(3)
    legacy["derived"]["armor_class"] = 12
    legacy["features"].pop("class_features", None)

    view = Dnd2024CombatEngine(
        bundle=runtime.load_bundle(LOCALE)
    )._player_view("p1", legacy)

    assert view["armor_class"] == 12
    assert legacy["resources"]["max_hp"] == _base_max_hp(3)
    assert legacy["derived"]["armor_class"] == 12
    # The boundary would report the bonus for this level; nothing applies it.
    assert _resolver(runtime).feature_max_hp_bonus(legacy) == 3
