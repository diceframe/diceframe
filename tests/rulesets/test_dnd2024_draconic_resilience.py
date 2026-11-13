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
from src.rulesets.dnd2024.progression import ProgressionCatalogError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


LOCALE = "en"
FEATURE_ID = "draconic_resilience"
_SUBCLASS_BY_CLASS = {"class:sorcerer": "subclass:draconic_sorcery"}


# --------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _proxy(class_ref: str, level: int) -> dict:
    character: dict = {
        "build": {"class_levels": [{"class_ref": class_ref, "level": level}]},
    }
    subclass_ref = _SUBCLASS_BY_CLASS.get(class_ref)
    if subclass_ref:
        # Draconic Resilience is a subclass feature: without a persisted
        # selection the character must not own it.
        character["features"] = {"subclass_ref": subclass_ref}
    return character


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


def _reconciled_state(
    runtime: Dnd2024Runtime,
    sheet: dict,
    *,
    worn: tuple[str, ...] = (),
    carried: tuple[str, ...] = (),
) -> dict:
    """One live state with an explicit worn / packed split."""

    live = deepcopy(sheet)
    live["equipment"] = [{"name": name} for name in worn]
    live["inventory"] = [{"name": name, "quantity": 1} for name in carried]
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
    ("max_hp_bonus_first", 3.0),
    ("max_hp_bonus_first", 21),
    ("max_hp_bonus_per_level_after", 21),
    ("max_hp_bonus_per_level_after", "1"),
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
        armor_category_refs=[],
        unarmored_ability_bonuses=resolver.unarmored_ac_bonus_abilities(
            _proxy("class:sorcerer", 3)
        ),
    )

    assert armor_class == 12  # 10 + Dex 2 beats 10 + Dex 2 + Cha -1
    assert armor_class == derive_armor_class(
        abilities, [], runtime.load_bundle(LOCALE), armor_category_refs=[],
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


def test_untrained_shield_does_not_add_to_the_draconic_formula(runtime) -> None:
    """Sorcerers have no shield training, so the shield grants no +2."""

    sheet = _sorcerer_sheet(runtime, 3)
    live = _reconciled(runtime, sheet, [{"name": "Shield"}])

    assert live["ruleset_character"]["derived"]["armor_class"] == 15
    assert live["armor_class"] == 15


def test_the_ac_candidate_follows_the_declaration_being_in_force(runtime) -> None:
    """The unarmored-AC candidate list reads *active* declarations only.

    ``draconic_resilience`` ships without an equipment precondition, so the
    shipped catalog alone cannot tell "declared" from "in force": as far as
    this feature goes every Sorcerer is unarmored-eligible.  The test adds the
    precondition Barbarian and Monk carry and checks both sides of it, which is
    exactly the difference between reading ``features_for`` and
    ``active_features_for`` -- without that gap, a regression back to
    ``features_for`` would be invisible here.
    """

    bundle = runtime.load_bundle(LOCALE)
    entities = deepcopy(bundle.entities)
    features = entities["class_feature_catalog"]["srd_class_features"]["classes"][
        "sorcerer"
    ]["features"]
    target = next(item for item in features if item["id"] == FEATURE_ID)
    target["equipment_requirement"] = {"unarmored": True}
    resolver = Dnd2024ClassFeatureResolver(replace(bundle, entities=entities))

    bare = _proxy("class:sorcerer", 3)
    armored = {
        **_proxy("class:sorcerer", 3),
        "equipment": {"item_refs": ["item:leather_armor"]},
    }

    assert resolver.unarmored_ac_bonus_abilities(bare) == ("cha",)
    assert resolver.unarmored_ac_bonus_abilities(armored) == ()
    # Ownership never changes with equipment; only the benefit is gated.
    assert resolver.has_feature(armored, FEATURE_ID) is True


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


# --------------------------------------------------------------------------
# §6.1 declaration, ownership and boundary degradation (matrix items 1-5)
# --------------------------------------------------------------------------

def test_declaration_pins_the_srd_page_owner_and_both_labels(runtime) -> None:
    english = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    chinese = Dnd2024ClassFeatureResolver(runtime.load_bundle("zh-CN"))
    definition = english.catalog.features_by_key[
        ("subclass:draconic_sorcery", FEATURE_ID)
    ]

    assert definition.owner == "subclass:draconic_sorcery"
    assert definition.minimum_level == 3
    # p52 是 v1 的旧页码，官方 SRD 5.2.1 的 Draconic Sorcery 在 p69。
    assert definition.source_ref == "srd-5.2.1:p69:draconic-resilience"
    assert english.catalog.label(FEATURE_ID) == "Draconic Resilience"
    assert chinese.catalog.label(FEATURE_ID) == "龙族体魄"
    assert english.catalog.label("sorcerer") == "Sorcerer"
    assert chinese.catalog.label("sorcerer") == "术士"

    view = next(
        item for item in english.feature_views(_proxy("class:sorcerer", 3))
        if item.id == FEATURE_ID
    )
    assert dict(view.values) == {
        "unarmored_ac_ability_bonus": "cha",
        "max_hp_bonus_first": 3,
        "max_hp_bonus_per_level_after": 1,
    }
    assert view.active is True
    # 没有 choice 的特性不得凭空多出 selection 字段。
    assert view.selection is None
    assert "selection" not in view.to_dict()


def test_an_unselected_or_foreign_subclass_owns_nothing(runtime) -> None:
    resolver = _resolver(runtime)
    unselected = [
        {"build": {"class_levels": [{"class_ref": "class:sorcerer", "level": 3}]}},
        {
            "build": {"class_levels": [{"class_ref": "class:sorcerer", "level": 3}]},
            "features": {},
        },
        {
            "build": {"class_levels": [{"class_ref": "class:sorcerer", "level": 3}]},
            "features": {"subclass_ref": ""},
        },
        # 伪造一个属于别的职业的子职：Sorcerer 3 也不该因此获得它。
        {
            "build": {"class_levels": [{"class_ref": "class:sorcerer", "level": 3}]},
            "features": {"subclass_ref": "subclass:champion"},
        },
    ]
    for character in unselected:
        assert resolver.has_feature(character, FEATURE_ID) is False
        assert resolver.has_feature(
            character, FEATURE_ID, owner="subclass:draconic_sorcery",
        ) is False
        assert resolver.unarmored_ac_bonus_abilities(character) == ()
        assert resolver.feature_max_hp_bonus(character) == 0


def test_boundary_apis_degrade_to_nothing_without_a_class(runtime) -> None:
    resolver = _resolver(runtime)
    for character in (
        None,
        {},
        {"build": {}},
        {"build": {"class_levels": []}},
        {"build": {"class_levels": [{"class_ref": "class:sorcerer", "level": 0}]}},
        {"build": {"class_levels": [{"class_ref": "", "level": 3}]}},
        {"build": {"class_levels": [
            {"class_ref": "class:sorcerer", "level": 3},
            {"class_ref": "class:wizard", "level": 3},
        ]}},
    ):
        assert resolver.unarmored_ac_bonus_abilities(character) == ()
        assert resolver.feature_max_hp_bonus(character) == 0


# --------------------------------------------------------------------------
# §6.2 hit point maximum and advancement (matrix items 6-12)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("level", "bonus"), [(1, 0), (2, 0), (3, 3), (4, 4), (5, 5), (20, 20)],
)
def test_hp_bonus_is_three_then_one_per_extra_level(runtime, level, bonus) -> None:
    assert _resolver(runtime).feature_max_hp_bonus(
        _proxy("class:sorcerer", level)
    ) == bonus


def test_each_level_up_reports_the_feature_hp_components(runtime) -> None:
    sheet = _sorcerer_sheet(runtime, 2)
    expected = {2: (0, 3, 3), 3: (3, 4, 1), 4: (4, 5, 1)}
    for from_level in (2, 3, 4):
        preview = runtime.preview_advancement(None, sheet, {})
        choices = {} if preview["ok"] else _advance_choices(
            preview, sheet["ruleset_character"]
        )
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        assert preview["from_level"] == from_level
        hp = preview["diff"]["hp"]
        assert (
            hp["feature_bonus_before"],
            hp["feature_bonus_after"],
            hp["feature_bonus_gain"],
        ) == expected[from_level]
        # 特性加成只进一次：总量减去特性增量必须等于既有分量之和。
        assert hp["gain"] - hp["feature_bonus_gain"] == (
            hp["base"] + hp["constitution_modifier"] + hp["retrospective_constitution"]
        )
        sheet = runtime.apply_advancement(None, sheet, choices)


def test_the_existing_hp_components_are_not_disturbed(runtime) -> None:
    sheet = _sorcerer_sheet(runtime, 3)
    preview = runtime.preview_advancement(None, sheet, {})
    choices = _advance_choices(preview, sheet["ruleset_character"])
    choices["ability_score_increases"] = {"con": 2}
    preview = runtime.preview_advancement(None, sheet, choices)
    assert preview["ok"] is True, preview["errors"]
    hp = preview["diff"]["hp"]

    assert hp["base"] == 4                        # d6 固定值，与特性无关
    assert hp["constitution_modifier"] == 2       # 13 -> 15
    assert hp["retrospective_constitution"] == 3  # 前三级各回顾 +1
    assert hp["feature_bonus_gain"] == 1
    assert hp["gain"] == 4 + 2 + 3 + 1


def test_current_hit_points_grow_by_the_same_gain_only_once(runtime) -> None:
    wounded = _sorcerer_sheet(runtime, 2)
    wounded["ruleset_character"]["resources"]["hp"] = 5
    preview = runtime.preview_advancement(None, wounded, {})
    choices = _advance_choices(preview, wounded["ruleset_character"])
    preview = runtime.preview_advancement(None, wounded, choices)
    gain = preview["diff"]["hp"]["gain"]
    advanced = runtime.apply_advancement(None, wounded, choices)["ruleset_character"]
    assert advanced["resources"]["hp"] == 5 + gain
    assert advanced["resources"]["hp"] <= advanced["resources"]["max_hp"]

    full = _sorcerer_sheet(runtime, 2)
    preview = runtime.preview_advancement(None, full, {})
    choices = _advance_choices(preview, full["ruleset_character"])
    healed = runtime.apply_advancement(None, full, choices)["ruleset_character"]
    assert healed["resources"]["hp"] == healed["resources"]["max_hp"]


def test_history_replay_rebuilds_the_same_hp_and_armor_class(runtime) -> None:
    chained = _sorcerer_sheet(runtime, 5)
    canonical = deepcopy(chained["ruleset_character"])
    expected_max_hp = canonical["resources"]["max_hp"]
    expected_ac = canonical["derived"]["armor_class"]
    assert expected_max_hp == _base_max_hp(5) + 5
    assert expected_ac == 15

    canonical["resources"]["max_hp"] = 9999
    canonical["resources"]["hp"] = 9999
    canonical["derived"]["armor_class"] = 99
    tampered = {
        **chained,
        "ruleset_character": canonical,
        "max_hp": 9999,
        "armor_class": 99,
    }

    rebuilt = runtime.normalize_character_submission(None, tampered, LOCALE)

    rebuilt_canonical = rebuilt["ruleset_character"]
    assert rebuilt_canonical["resources"]["max_hp"] == expected_max_hp
    assert rebuilt_canonical["resources"]["hp"] == expected_max_hp
    assert rebuilt_canonical["derived"]["armor_class"] == expected_ac
    assert _resolver(runtime).has_feature(rebuilt_canonical, FEATURE_ID) is True
    assert _resolver(runtime).feature_max_hp_bonus(rebuilt_canonical) == 5
    history = rebuilt_canonical["progression"]["history"]
    assert len(history) == 4
    assert [
        row["diff"]["hp"]["feature_bonus_gain"] for row in history
    ] == [0, 3, 1, 1]


def test_an_invalid_subclass_choice_fails_before_any_hit_point_change(runtime) -> None:
    two = _sorcerer_sheet(runtime, 2)
    resources = deepcopy(two["ruleset_character"]["resources"])

    missing = runtime.preview_advancement(None, two, {})
    assert missing["ok"] is False
    assert any(item["id"] == "subclass_ref" for item in missing["requirements"])

    forged = runtime.preview_advancement(None, two, {"subclass_ref": "subclass:champion"})
    assert forged["ok"] is False

    with pytest.raises(ProgressionCatalogError):
        runtime.apply_advancement(None, two, {"subclass_ref": "subclass:champion"})
    # 失败路径不得先加 3 再报错。
    assert two["ruleset_character"]["resources"] == resources


# --------------------------------------------------------------------------
# §6.3 unarmored armor class and equipment changes (matrix items 13-22)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("armor_name", "expected"), [("Hide", 14), ("Chain Mail", 16)])
def test_worn_armor_replaces_the_draconic_candidate(runtime, armor_name, expected) -> None:
    sheet = _sorcerer_sheet(runtime, 3)
    live = _reconciled_state(runtime, sheet, worn=(armor_name,))

    assert live["ruleset_character"]["derived"]["armor_class"] == expected
    assert live["armor_class"] == expected
    # 14（中甲 12 + Dex 2）低于 15，证明没有和 Draconic 候选取最大值。
    if armor_name == "Hide":
        assert expected < 15


def test_a_trained_shield_stacks_on_the_draconic_base(runtime) -> None:
    abilities = {"str": 8, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 16}
    bundle = runtime.load_bundle(LOCALE)
    bonus = _resolver(runtime).unarmored_ac_bonus_abilities(_proxy("class:sorcerer", 3))

    assert derive_armor_class(
        abilities, ["item:shield"], bundle,
        armor_category_refs=["armor_category:shield"],
        unarmored_ability_bonuses=bonus,
    ) == 17  # 10 + Dex 2 + Cha 3（只持盾仍算无甲），再加盾牌 +2
    assert derive_armor_class(
        abilities, ["item:shield"], bundle, armor_category_refs=[],
        unarmored_ability_bonuses=bonus,
    ) == 15  # 未受训盾牌不加值，也不会把 Draconic 候选顶掉


def test_armor_in_the_pack_leaves_the_unarmored_formula_in_force(runtime) -> None:
    sheet = _sorcerer_sheet(runtime, 3)

    packed = _reconciled_state(runtime, sheet, carried=("Leather Armor",))
    assert packed["ruleset_character"]["derived"]["armor_class"] == 15
    assert packed["armor_class"] == 15

    worn = _reconciled_state(runtime, packed, worn=("Leather Armor",))
    assert worn["ruleset_character"]["derived"]["armor_class"] == 13
    assert worn["armor_class"] == 13

    taken_off = _reconciled_state(runtime, worn, carried=("Leather Armor",))
    assert taken_off["ruleset_character"]["derived"]["armor_class"] == 15
    assert taken_off["armor_class"] == 15


def test_every_reconciliation_keeps_both_armor_class_fields_equal(runtime) -> None:
    current = _sorcerer_sheet(runtime, 3)
    seen: list[int] = []
    for worn in [(), ("Leather Armor",), ("Leather Armor", "Shield"), ()]:
        current = _reconciled_state(runtime, current, worn=worn)
        canonical = current["ruleset_character"]
        assert current["armor_class"] == canonical["derived"]["armor_class"]
        seen.append(canonical["derived"]["armor_class"])

    # Sorcerer 没有盾牌受训，所以第三态与第二态同值。
    assert seen == [15, 13, 13, 15]


def test_the_asi_level_recomputes_armor_class_immediately(runtime) -> None:
    three = _sorcerer_sheet(runtime, 3)
    assert three["ruleset_character"]["derived"]["armor_class"] == 15

    preview = runtime.preview_advancement(None, three, {})
    choices = _advance_choices(preview, three["ruleset_character"])
    choices["ability_score_increases"] = {"cha": 2}
    preview = runtime.preview_advancement(None, three, choices)
    assert preview["ok"] is True, preview["errors"]
    four = runtime.apply_advancement(None, three, choices)["ruleset_character"]

    assert four["abilities"]["cha"] == 18
    assert four["derived"]["armor_class"] == 16  # 10 + Dex 2 + Cha 4
    assert four["resources"]["max_hp"] == _base_max_hp(4) + 4


def test_defense_and_draconic_never_combine_into_a_second_formula(runtime) -> None:
    abilities = {"str": 8, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 16}
    bundle = runtime.load_bundle(LOCALE)
    bonus = _resolver(runtime).unarmored_ac_bonus_abilities(_proxy("class:sorcerer", 3))

    assert derive_armor_class(
        abilities, [], bundle, armor_category_refs=[],
        unarmored_ability_bonuses=bonus, feat_refs=["feat:defense"],
    ) == 15  # Defense 需要甲，未穿甲时 +0，也不会另起一套公式
    assert derive_armor_class(
        abilities, ["item:leather_armor"], bundle, armor_category_refs=[],
        unarmored_ability_bonuses=bonus, feat_refs=["feat:defense"],
    ) == 14  # 11 + Dex 2 + Defense 1，Draconic 候选完全退出


def test_armored_containment_and_dex_caps_still_hold(runtime) -> None:
    bundle = runtime.load_bundle(LOCALE)
    high_dex = {"str": 8, "dex": 20, "con": 13, "int": 12, "wis": 10, "cha": 16}
    bonus = _resolver(runtime).unarmored_ac_bonus_abilities(_proxy("class:sorcerer", 3))

    assert derive_armor_class(
        high_dex, ["item:chain_shirt"], bundle, armor_category_refs=[],
        unarmored_ability_bonuses=bonus,
    ) == 15  # 13 + Dex 被 cap 到 2
    assert derive_armor_class(
        high_dex, [], bundle, armor_category_refs=[],
        unarmored_ability_bonuses=bonus,
    ) == 18  # 无甲时 Dex 5 与 Cha 3 都要算

    warnings: list[str] = []
    armored = derive_armor_class(
        high_dex, ["item:leather_armor", "item:plate"], bundle,
        armor_category_refs=[], unarmored_ability_bonuses=bonus, warnings=warnings,
    )
    assert armored == 18  # 多件甲取一件最好的，不相加
    assert any("extra armor" in item for item in warnings)
