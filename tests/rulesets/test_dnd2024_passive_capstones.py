"""S0-F24: Primal Champion, Body and Mind, Slippery Mind, Indomitable Might.

Two of the four features change the character's scores, one changes the
proficiency set, and one changes the total of a check after every modifier has
been summed.  The first three are visible in the sheet, so the tests build the
character through the real builder and the real level-up chain and read the
canonical record.  Indomitable Might is not visible anywhere until a check
happens, so it is driven through ``_strength_total_replacement`` -- the one
place the total is rewritten -- with an actor view taken from a real combat.

Three conventions matter when reading the numbers:

* ``monk_draft`` starts from Acolyte; every character here uses Soldier instead,
  because Soldier is the only background whose ability options include Strength
  (``ability:str``/``dex``/``con``).  A +2 there is what puts Strength at 17
  before level 20, which is the case the SRD's "17 -> 21" describes.
* Ability Score Improvements cap at 20, so the ASI rotation never targets an
  ability that would cross it; Primal Champion is the only thing that lifts a
  score past 20 here.
* ``advancement_choices`` answers every required slot, but the ASI target is
  overridden per level so ``str`` is untouched until level 20.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.derivation import apply_feature_ability_bonuses
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.models import ClassFeatureError
from src.rulesets.dnd2024.features.validation import (
    validate_ability_bonus_parameters,
    validate_passive_parameters,
    validate_saving_throw_proficiency_parameters,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, advancement_choices
from test_dnd2024_survivor import _instance

BARBARIAN = "class:barbarian"
MONK = "class:monk"
ROGUE = "class:rogue"
BERSERKER = "subclass:path_of_the_berserker"

# 15/14/13/12/10/8 各用一次：标准数组的唯一合法排列由 builder 校验。
BARBARIAN_ABILITIES = {"str": 15, "dex": 13, "con": 14, "int": 8, "wis": 12, "cha": 10}
MONK_ABILITIES = {"str": 10, "dex": 15, "con": 14, "int": 8, "wis": 13, "cha": 12}
ROGUE_ABILITIES = {"str": 10, "dex": 15, "con": 14, "int": 12, "wis": 13, "cha": 8}

# 升到 19/20 级要穿过四个 ASI：全部投到别处，力量只在 20 级被 Primal
# Champion 提升，17 -> 21 才是"低值仍只加 4"的原样。
BARBARIAN_ASI = ["dex", "dex", "wis", "wis"]
MONK_ASI = ["con", "con", "str", "str"]
ROGUE_ASI = ["str", "str", "con", "con", "wis", "wis"]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _draft(
    runtime: Dnd2024Runtime, class_ref: str, skills: list[str], package: str,
    abilities: dict, background_bonus: dict,
) -> dict:
    """A level-1 draft whose background grants the stats the case needs."""

    draft: dict = {
        "locale": "en", "name": "Hero", "level": 1,
        "ability_method": "standard_array", "base_abilities": dict(abilities),
        "class_ref": class_ref, "species_ref": "species:human",
        "background_ref": "background:soldier",
        "class_skill_refs": list(skills),
        "equipment_package_ref": package,
        "background_equipment_package_ref": "equipment_package:soldier_a",
        "background_ability_bonuses": dict(background_bonus),
        "alignment": "neutral",
    }
    choices = runtime.builder_choices(None, draft)
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


def _advance(
    runtime: Dnd2024Runtime, sheet: dict, level: int, rotation: list[str],
    subclass: str = "",
) -> dict:
    """Advance one character to ``level``, steering every ASI off Strength."""

    steps = 0
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        for requirement in preview["requirements"]:
            if str(requirement["id"]) != "ability_score_increases":
                continue
            allowed = requirement.get("allowed", "any")
            ability = (
                rotation[steps % len(rotation)] if allowed == "any" else str(allowed[0])
            )
            picks["ability_score_increases"] = {ability: int(requirement.get("total", 2))}
        if subclass and "subclass_ref" in picks:
            picks["subclass_ref"] = subclass
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
        steps += 1
    return sheet


def _sheet(
    runtime: Dnd2024Runtime, class_ref: str, level: int, skills: list[str],
    package: str, abilities: dict, background_bonus: dict, rotation: list[str],
    subclass: str = "",
) -> dict:
    draft = _draft(runtime, class_ref, skills, package, abilities, background_bonus)
    sheet = runtime.finalize_character(None, draft)
    if level > 1:
        sheet = _advance(runtime, sheet, level, rotation, subclass)
    return sheet


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def bundle(runtime: Dnd2024Runtime):
    return runtime.load_bundle("en")


@pytest.fixture(scope="module")
def features(bundle) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(bundle)


@pytest.fixture(scope="module")
def barbarian_19(runtime) -> dict:
    return _sheet(
        runtime, BARBARIAN, 19, ["skill:perception", "skill:survival"],
        "equipment_package:barbarian_a", BARBARIAN_ABILITIES,
        {"str": 2, "con": 1}, BARBARIAN_ASI, BERSERKER,
    )


@pytest.fixture(scope="module")
def barbarian_20(runtime, barbarian_19) -> dict:
    return _advance(runtime, deepcopy(barbarian_19), 20, BARBARIAN_ASI, BERSERKER)


@pytest.fixture(scope="module")
def monk_19(runtime) -> dict:
    return _sheet(
        runtime, MONK, 19, ["skill:stealth", "skill:insight"],
        "equipment_package:monk_b", MONK_ABILITIES,
        {"dex": 2, "con": 1}, MONK_ASI,
    )


@pytest.fixture(scope="module")
def monk_20(runtime, monk_19) -> dict:
    return _advance(runtime, deepcopy(monk_19), 20, MONK_ASI)


@pytest.fixture(scope="module")
def rogue_14(runtime) -> dict:
    return _sheet(
        runtime, ROGUE, 14,
        ["skill:perception", "skill:stealth", "skill:investigation", "skill:insight"],
        "equipment_package:rogue_a", ROGUE_ABILITIES,
        {"dex": 2, "con": 1}, ROGUE_ASI,
    )


@pytest.fixture(scope="module")
def rogue_15(runtime, rogue_14) -> dict:
    return _advance(runtime, deepcopy(rogue_14), 15, ROGUE_ASI)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _canonical(sheet: dict) -> dict:
    return sheet["ruleset_character"]


def _feature_state(class_ref: str, level: int, subclass: str = "") -> dict:
    state: dict = {"build": {"class_levels": [{"class_ref": class_ref, "level": level}]}}
    if subclass:
        state["features"] = {"subclass_ref": subclass}
    return state


def _goblin() -> dict:
    return {
        "id": "goblin-1", "name": "Goblin", "hp": 40, "armor_class": 12,
        "speed": 30, "position": 5, "initiative_modifier": 2,
        "creature_type": "humanoid",
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"str": 0, "dex": 2, "con": 0, "wis": -1},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _start(engine, instance, rolls=(20, 3)) -> list[dict]:
    state = ruleset_runtime.state(instance)
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "enemies": [_goblin()],
    }, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _combat(bundle, sheet: dict):
    """A started combat with this sheet as ``player:gm``, plus its view."""

    engine, instance = _instance(bundle, deepcopy(sheet))
    _start(engine, instance)
    combat = ruleset_runtime.state(instance)["combat"]
    return engine, instance, combat


def _intent(instance, **body) -> dict:
    state = ruleset_runtime.state(instance)
    return {
        "intent_id": f"{body.get('type')}-{state['version']}",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm", **body,
    }


def _run(engine, instance, intent: dict, rolls=()) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _check(events: list[dict], kind: str) -> dict:
    return next(
        event for event in events
        if event.get("type") == "check.resolved" and event.get("kind") == kind
    )


# ---------------------------------------------------------------------------
# 1. 真实授予
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("class_ref", "feature_id", "level", "subclass"),
    [
        (BARBARIAN, "indomitable_might", 18, ""),
        (BARBARIAN, "primal_champion", 20, ""),
        (MONK, "body_and_mind", 20, ""),
        (ROGUE, "slippery_mind", 15, ""),
    ],
)
def test_each_feature_arrives_exactly_at_its_own_level(
    features, class_ref, feature_id, level, subclass,
) -> None:
    """One level early is nothing; the threshold itself is the grant."""

    assert features.has_feature(
        _feature_state(class_ref, level, subclass), feature_id,
    ) is True
    assert features.has_feature(
        _feature_state(class_ref, level - 1, subclass), feature_id,
    ) is False


@pytest.mark.parametrize(
    ("class_ref", "feature_id"),
    [
        (ROGUE, "primal_champion"),
        (MONK, "indomitable_might"),
        (BARBARIAN, "slippery_mind"),
        (BARBARIAN, "body_and_mind"),
    ],
)
def test_another_class_never_owns_the_feature(
    features, class_ref, feature_id,
) -> None:
    """Level 20 in the wrong class grants nothing at all."""

    assert features.has_feature(_feature_state(class_ref, 20), feature_id) is False


def test_a_forged_feature_id_on_the_sheet_grants_nothing(features, rogue_15) -> None:
    """The level table decides ownership, never a list written into the sheet."""

    forged = deepcopy(rogue_15)
    forged["ruleset_character"]["build"]["feature_ids"] = [
        "primal_champion", "body_and_mind", "indomitable_might",
    ]
    assert features.has_feature(_canonical(forged), "primal_champion") is False
    assert features.has_feature(_canonical(forged), "body_and_mind") is False
    assert features.has_feature(_canonical(forged), "indomitable_might") is False
    assert _canonical(forged)["build"]["level"] == 15


# ---------------------------------------------------------------------------
# 2-4. 属性提升：+4、上限 25、不叠加、不残留
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "expected"),
    [(17, 21), (21, 25), (24, 25), (25, 25), (8, 12)],
)
def test_primal_champion_adds_four_within_the_cap(before, expected) -> None:
    """``+4, to a maximum of 25`` is a raise, never "set the score to 25"."""

    abilities, applied = apply_feature_ability_bonuses(
        {"str": before, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        {"str": (4, 25), "con": (4, 25)}, {},
    )
    assert abilities["str"] == expected
    assert applied["str"] == expected - before


def test_the_cap_only_bounds_the_score_it_names() -> None:
    """A cap on Strength is not a cap on every other ability."""

    abilities, _ = apply_feature_ability_bonuses(
        {"str": 25, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        {"str": (4, 25)}, {},
    )
    assert abilities == {"str": 25, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}


def test_body_and_mind_only_moves_dexterity_and_wisdom(monk_20) -> None:
    canonical = _canonical(monk_20)
    assert canonical["build"]["level"] == 20
    assert canonical["abilities"]["dex"] == 21
    assert canonical["abilities"]["wis"] == 17
    # 其余四属性与升级前完全一致：特性只声明了这两项。
    assert canonical["abilities"]["str"] == 18
    assert canonical["abilities"]["con"] == 16
    assert canonical["abilities"]["int"] == 8
    assert canonical["abilities"]["cha"] == 12
    assert canonical["derived"]["ability_bonus_applied"] == {"dex": 4, "wis": 4}


def test_primal_champion_raises_strength_and_constitution(barbarian_20, barbarian_19) -> None:
    before = _canonical(barbarian_19)["abilities"]
    after = _canonical(barbarian_20)["abilities"]
    assert (before["str"], before["con"]) == (17, 15)
    assert (after["str"], after["con"]) == (21, 19)
    for ability in ("dex", "int", "wis", "cha"):
        assert after[ability] == before[ability]
    assert _canonical(barbarian_20)["derived"]["ability_bonus_applied"] == {
        "str": 4, "con": 4,
    }


def test_the_build_record_is_never_rewritten(barbarian_20) -> None:
    """``base_abilities`` and the background answer stay what the player chose."""

    build = _canonical(barbarian_20)["build"]
    assert build["base_abilities"]["str"] == 15
    assert build["background_ability_bonuses"] == {"str": 2, "con": 1}


def test_applying_the_bonus_twice_changes_nothing(barbarian_20) -> None:
    """The record is the reason a second pass is a no-op, not a lucky accident."""

    canonical = _canonical(barbarian_20)
    grants = {"str": (4, 25), "con": (4, 25)}
    once, record = apply_feature_ability_bonuses(
        canonical["abilities"], grants, canonical["derived"]["ability_bonus_applied"],
    )
    twice, record_again = apply_feature_ability_bonuses(
        once, grants, record,
    )
    assert once == twice == canonical["abilities"]
    assert record == record_again == {"str": 4, "con": 4}


def test_losing_the_feature_gives_the_points_back() -> None:
    """A card that no longer owns the grant must not keep the +4."""

    abilities, record = apply_feature_ability_bonuses(
        {"str": 21, "dex": 10, "con": 19, "int": 10, "wis": 10, "cha": 10},
        {}, {"str": 4, "con": 4},
    )
    assert abilities["str"] == 17
    assert abilities["con"] == 15
    assert record == {}


def test_an_asi_and_the_feature_do_not_eat_each_other() -> None:
    """The ASI is added first, then only the feature's own share is replaced."""

    after_asi = {"str": 19, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}
    abilities, record = apply_feature_ability_bonuses(
        after_asi, {"str": (4, 25)}, {"str": 2},
    )
    assert abilities["str"] == 21
    assert record == {"str": 4}


# ---------------------------------------------------------------------------
# 5-7. 派生数字、HP 与 AC
# ---------------------------------------------------------------------------


def test_the_new_scores_reach_every_derived_number(barbarian_19, barbarian_20) -> None:
    before = _canonical(barbarian_19)
    after = _canonical(barbarian_20)
    # 力量 +4 -> 修正 +2；体质 +4 -> 修正 +2。
    assert after["derived"]["saving_throws"]["str"] == (
        before["derived"]["saving_throws"]["str"] + 2
    )
    assert after["derived"]["saving_throws"]["con"] == (
        before["derived"]["saving_throws"]["con"] + 2
    )
    # 无甲防御 10+Dex+Con：只有体质动了，所以 AC 只涨 2。
    assert after["derived"]["armor_class"] == before["derived"]["armor_class"] + 2
    # Dex 没动，先攻不变；Wis 没动，被动感知不变。
    assert after["derived"]["initiative"] == before["derived"]["initiative"]
    assert after["derived"]["passive_perception"] == before["derived"]["passive_perception"]


def test_a_dexterity_feature_moves_initiative_armor_class_and_skills(
    bundle, monk_19, monk_20,
) -> None:
    before = _canonical(monk_19)
    after = _canonical(monk_20)
    assert after["derived"]["initiative"] == before["derived"]["initiative"] + 2
    # 无甲防御 10+Dex+Wis：Dex +4、Wis +4 各 +2 修正，正好 +4。
    assert after["derived"]["armor_class"] == before["derived"]["armor_class"] + 4
    assert after["derived"]["passive_perception"] == before["derived"]["passive_perception"] + 2

    # 技能走的是战斗视图里的实际加值：潜行（敏捷）与洞悉（感知）各涨 2。
    engine, instance, combat = _combat(bundle, monk_19)
    old = engine._actor_view(instance, combat, "player:gm")["skill_values"]
    engine, instance, combat = _combat(bundle, monk_20)
    new = engine._actor_view(instance, combat, "player:gm")["skill_values"]
    assert new["stealth"] == old["stealth"] + 2
    assert new["insight"] == old["insight"] + 2
    assert new["athletics"] == old["athletics"]


def test_constitution_raises_hit_points_once(runtime, barbarian_19) -> None:
    """Con +4 lifts max HP retroactively -- once, and not as a cure spell."""

    damaged = deepcopy(barbarian_19)
    damaged["ruleset_character"]["resources"]["hp"] = 50
    preview = runtime.preview_advancement(None, damaged, {})
    picks = advancement_choices(preview)
    picks["subclass_ref"] = BERSERKER
    preview = runtime.preview_advancement(None, damaged, picks)
    assert preview["ok"] is True, preview["errors"]
    detail = preview["diff"]["hp"]
    # 固定值 7 + 体质 4 = 11 的本级增长，加上 20 级回溯的 2×19 = 38。
    assert detail["base"] == 7
    assert detail["constitution_modifier"] == 4
    assert detail["retrospective_constitution"] == 38
    assert detail["gain"] == 49

    applied = runtime.apply_advancement(None, damaged, picks)
    canonical = _canonical(applied)
    assert canonical["resources"]["hp"] == 99  # 50 + 49，与升级规则同源
    # 协调再跑一次不会把 +4 体质或那 49 点再加一遍。
    applied["equipment"] = []
    applied["inventory"] = []
    Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(applied)
    assert canonical["resources"]["hp"] == 99
    assert canonical["abilities"]["con"] == 19


def test_armor_class_is_derived_from_the_new_scores_not_a_snapshot(
    bundle, monk_20,
) -> None:
    """协调（换装）后 AC 仍由当前 Dex/Wis 算出，不保留升级前的旧快照。"""

    sheet = deepcopy(monk_20)
    canonical = _canonical(sheet)
    sheet["equipment"] = []
    sheet["inventory"] = []
    summary = Dnd2024CharacterStateReconciler(bundle).reconcile(sheet)
    assert summary is not None
    assert canonical["abilities"]["dex"] == 21
    assert canonical["derived"]["armor_class"] == (
        10
        + (canonical["abilities"]["dex"] - 10) // 2
        + (canonical["abilities"]["wis"] - 10) // 2
    )
    # 协调不改属性：它不是第二个加值入口。
    assert canonical["derived"]["ability_bonus_applied"] == {"dex": 4, "wis": 4}
    assert canonical["abilities"]["dex"] == 21


# ---------------------------------------------------------------------------
# 8-9. Slippery Mind
# ---------------------------------------------------------------------------


def test_slippery_mind_adds_wisdom_and_charisma(rogue_14, rogue_15) -> None:
    before = _canonical(rogue_14)
    after = _canonical(rogue_15)
    assert after["build"]["level"] == 15
    assert before["proficiencies"]["saving_throw_refs"] == ["ability:dex", "ability:int"]
    assert after["proficiencies"]["saving_throw_refs"] == [
        "ability:cha", "ability:dex", "ability:int", "ability:wis",
    ]
    # 来源随授予一起记下：集合本身看不出这两条是谁给的。
    assert after["derived"]["saving_throw_proficiency_sources"] == {
        "wis": "slippery_mind", "cha": "slippery_mind",
    }


def test_a_proficient_save_never_doubles_the_bonus(rogue_14, rogue_15) -> None:
    before = _canonical(rogue_14)
    after = _canonical(rogue_15)
    bonus = int(after["derived"]["proficiency_bonus"])
    for ability in ("wis", "cha"):
        modifier = (after["abilities"][ability] - 10) // 2
        # 恰好一次 PB：职业本来就没给这两项，特性也不能给第二次。
        assert after["derived"]["saving_throws"][ability] == modifier + bonus
    # 职业自带的两个豁免不因这次合并而变：只跟着属性与 PB 走。
    for ability in ("dex", "int"):
        assert after["derived"]["saving_throws"][ability] == (
            (after["abilities"][ability] - 10) // 2 + bonus
        )
    # 14 -> 15 级 PB 没变，属性也没变，这两项就该原样不动。
    assert after["derived"]["saving_throws"]["dex"] == (
        before["derived"]["saving_throws"]["dex"]
    )


def test_the_two_new_saves_follow_the_ability_and_the_bonus(rogue_15) -> None:
    canonical = _canonical(rogue_15)
    bonus = int(canonical["derived"]["proficiency_bonus"])
    for ability in ("str", "con"):
        assert canonical["derived"]["saving_throws"][ability] == (
            (canonical["abilities"][ability] - 10) // 2
        )
    assert canonical["derived"]["saving_throws"]["wis"] == (
        (canonical["abilities"]["wis"] - 10) // 2 + bonus
    )


# ---------------------------------------------------------------------------
# 10. fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "overrides"),
    [
        (("class:barbarian", "primal_champion"), {"ability_score_cap": 30}),
        (("class:barbarian", "primal_champion"), {"ability_score_cap": None}),
        (("class:barbarian", "primal_champion"), {"ability_bonus_str": True}),
        (("class:barbarian", "primal_champion"), {"ability_bonus_str": -4}),
        (("class:barbarian", "primal_champion"), {"ability_bonus_luck": 4}),
        (("class:monk", "body_and_mind"), {"ability_bonus_dex": 4.0}),
    ],
)
def test_malformed_ability_bonuses_fail_closed(features, key, overrides) -> None:
    definition = features.catalog.features_by_key[key]
    malformed = replace(
        definition, parameters={**definition.parameters, **overrides},
    )
    with pytest.raises(ClassFeatureError):
        validate_ability_bonus_parameters(malformed)


@pytest.mark.parametrize(
    "overrides",
    [
        {"saving_throw_proficiencies": "wis"},
        {"saving_throw_proficiencies": []},
        {"saving_throw_proficiencies": ["wis", "wis"]},
        {"saving_throw_proficiencies": ["wis", "luck"]},
        {"saving_throw_proficiencies": [1, "cha"]},
    ],
)
def test_malformed_proficiency_grants_fail_closed(features, overrides) -> None:
    definition = features.catalog.features_by_key[("class:rogue", "slippery_mind")]
    malformed = replace(
        definition, parameters={**definition.parameters, **overrides},
    )
    with pytest.raises(ClassFeatureError):
        validate_saving_throw_proficiency_parameters(malformed)


def test_a_truthy_replacement_flag_fails_closed(features) -> None:
    """``"yes"`` is not ``true``: a lookalike must never arm the replacement."""

    definition = features.catalog.features_by_key[("class:barbarian", "indomitable_might")]
    malformed = replace(
        definition,
        parameters={"strength_total_replacement": "yes"},
    )
    with pytest.raises(ClassFeatureError):
        validate_passive_parameters(malformed)


def test_a_cap_without_a_bonus_fails_closed(features) -> None:
    definition = features.catalog.features_by_key[("class:monk", "body_and_mind")]
    malformed = replace(
        definition,
        parameters={"ability_score_cap": 25},
    )
    with pytest.raises(ClassFeatureError):
        validate_ability_bonus_parameters(malformed)


# ---------------------------------------------------------------------------
# 11-20. Indomitable Might
# ---------------------------------------------------------------------------


def test_a_low_total_becomes_the_strength_score(bundle, barbarian_20) -> None:
    engine, instance, combat = _combat(bundle, barbarian_20)
    view = engine._actor_view(instance, combat, "player:gm")
    assert view["abilities"]["str"] == 21
    total, audit = engine._strength_total_replacement(view, "str", 5)
    assert total == 21
    assert audit == {
        "ordinary_total": 5,
        "replacement_score": 21,
        "total_replaced": True,
        "feature_id": "indomitable_might",
    }


def test_a_total_at_or_above_the_score_is_left_alone(bundle, barbarian_20) -> None:
    engine, instance, combat = _combat(bundle, barbarian_20)
    view = engine._actor_view(instance, combat, "player:gm")
    for ordinary in (21, 22, 30):
        total, audit = engine._strength_total_replacement(view, "str", ordinary)
        assert total == ordinary
        assert audit["total_replaced"] is False
        assert audit["ordinary_total"] == ordinary


def test_the_natural_face_is_never_rewritten(bundle, barbarian_20) -> None:
    """Natural 3 + 2 is still natural 3: only the total may be replaced."""

    engine, instance, combat = _combat(bundle, barbarian_20)
    view = engine._actor_view(instance, combat, "player:gm")
    check = {
        "type": "check.resolved", "kind": "saving_throw", "ability": "str",
        "natural": 3, "rolls": [3], "modifier": 2, "total": 5,
        "target": 15, "success": False,
    }
    total, audit = engine._strength_total_replacement(view, "str", check["total"])
    judged = {**check, "total": total, "success": total >= 15, **audit}
    assert judged["natural"] == 3
    assert judged["rolls"] == [3]
    assert judged["modifier"] == 2
    assert judged["ordinary_total"] == 5
    assert judged["total"] == 21
    assert judged["total_replaced"] is True
    assert judged["success"] is True
    # 替换没有制造暴击，也没有动天然骰。
    assert "critical" not in judged


def test_modifiers_are_summed_before_the_replacement(bundle, barbarian_20) -> None:
    """It is neither ``max(natural, score) + modifier`` nor a d20 floor."""

    engine, instance, combat = _combat(bundle, barbarian_20)
    view = engine._actor_view(instance, combat, "player:gm")
    # 天然 1 加上一串加值后 ordinary_total=22 > 21：保持 22，不回落到骰面。
    total, audit = engine._strength_total_replacement(view, "str", 22)
    assert total == 22
    assert audit["total_replaced"] is False
    # 天然 1 只有 +1 时 ordinary_total=2：替换成 21，而不是 1+1。
    total, audit = engine._strength_total_replacement(view, "str", 2)
    assert total == 21
    assert audit["ordinary_total"] == 2


def test_only_strength_reads_the_replacement(bundle, barbarian_20) -> None:
    engine, instance, combat = _combat(bundle, barbarian_20)
    view = engine._actor_view(instance, combat, "player:gm")
    for ability in ("dex", "con", "int", "wis", "cha", ""):
        total, audit = engine._strength_total_replacement(view, ability, 3)
        assert total == 3
        assert audit == {}


def test_a_character_without_the_feature_keeps_its_total(bundle, rogue_15) -> None:
    engine, instance, combat = _combat(bundle, rogue_15)
    view = engine._actor_view(instance, combat, "player:gm")
    total, audit = engine._strength_total_replacement(view, "str", 3)
    assert total == 3
    assert audit == {}


def test_the_replacement_score_follows_the_current_strength(bundle, barbarian_19, barbarian_20) -> None:
    """18/19 级用当时的 17，20 级用新值 21：没有把旧值固化下来。"""

    engine, instance, combat = _combat(bundle, barbarian_19)
    view = engine._actor_view(instance, combat, "player:gm")
    _, before = engine._strength_total_replacement(view, "str", 1)
    assert before["replacement_score"] == 17

    engine, instance, combat = _combat(bundle, barbarian_20)
    view = engine._actor_view(instance, combat, "player:gm")
    _, after = engine._strength_total_replacement(view, "str", 1)
    assert after["replacement_score"] == 21


def test_no_die_is_rolled_for_the_replacement(bundle, barbarian_20) -> None:
    """The RNG order of the batch is untouched: no die is added or rerolled."""

    engine, instance, combat = _combat(bundle, barbarian_20)
    view = engine._actor_view(instance, combat, "player:gm")
    total, audit = engine._strength_total_replacement(view, "str", 3)
    assert (total, audit["total_replaced"]) == (21, True)
    # 审计只带三个数字与来源：没有骰面、没有掷骰记录可加，所以 RNG 顺序不变。
    assert set(audit) == {
        "ordinary_total", "replacement_score", "total_replaced", "feature_id",
    }
    assert "rolls" not in audit and "natural" not in audit


def test_an_attack_roll_never_reads_the_replacement(bundle, barbarian_20) -> None:
    engine, instance, combat = _combat(bundle, barbarian_20)
    events = _run(engine, instance, _intent(
        instance, type="attack", target_id="enemy:goblin-1",
        weapon_ref="item:greataxe",
    ), [15, 3, 3, 3, 3])
    attack = _check(events, "attack")
    assert attack["natural"] >= 1
    assert "ordinary_total" not in attack
    assert "replacement_score" not in attack
    assert "total_replaced" not in attack
    assert "feature_id" not in attack


def test_a_death_save_never_reads_the_replacement(bundle, barbarian_20) -> None:
    engine, instance, combat = _combat(bundle, barbarian_20)
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 0
    events = _run(engine, instance, _intent(
        instance, type="death_save", actor_id="player:gm",
    ), [3])
    event = _check(events, "death_save")
    assert "ordinary_total" not in event
    assert "total_replaced" not in event
    assert event["natural"] == 3


def test_the_public_feed_keeps_the_audit_fields(bundle, barbarian_20) -> None:
    """The three audit numbers survive the presentation whitelist."""

    _engine, instance, _combat_state = _combat(bundle, barbarian_20)
    ruleset_runtime.replace_event_ledger(instance, [{
        "batch_id": "f24-1", "intent_id": "f24-1", "intent_type": "combat.start",
        "result_version": 1,
        "events": [
            {"type": "dnd2024.combat.started"},
            {
                "type": "check.resolved", "kind": "saving_throw",
                "actor_id": "player:gm", "ability": "str", "natural": 3,
                "rolls": [3], "modifier": 2, "total": 21, "target": 15,
                "success": True, "ordinary_total": 5, "replacement_score": 21,
                "total_replaced": True, "feature_id": "indomitable_might",
            },
        ],
    }])
    feed = Dnd2024Runtime._recent_combat_events(instance)
    projected = next(
        event for event in feed
        if event.get("type") == "check.resolved" and event.get("kind") == "saving_throw"
    )
    assert projected["natural"] == 3
    assert projected["rolls"] == [3]
    assert projected["ordinary_total"] == 5
    assert projected["replacement_score"] == 21
    assert projected["total_replaced"] is True


def test_the_sheet_round_trip_keeps_the_new_scores(bundle, monk_20) -> None:
    """save/load 之后属性、熟练与派生值都还是同一份。"""

    import json

    reloaded = json.loads(json.dumps(monk_20))
    before = _canonical(monk_20)
    after = _canonical(reloaded)
    assert after["abilities"] == before["abilities"]
    assert after["derived"]["ability_bonus_applied"] == {"dex": 4, "wis": 4}
    assert after["derived"]["armor_class"] == before["derived"]["armor_class"]
    assert after["derived"]["saving_throws"] == before["derived"]["saving_throws"]

    reloaded["equipment"] = []
    reloaded["inventory"] = []
    Dnd2024CharacterStateReconciler(bundle).reconcile(reloaded)
    assert after["abilities"] == before["abilities"]


def test_the_advancement_is_deterministic(runtime, barbarian_19) -> None:
    """同一次升级重算两次得到同一组数字，没有隐藏的 RNG。"""

    first = _advance(runtime, deepcopy(barbarian_19), 20, BARBARIAN_ASI, BERSERKER)
    second = _advance(runtime, deepcopy(barbarian_19), 20, BARBARIAN_ASI, BERSERKER)
    assert _canonical(first)["abilities"] == _canonical(second)["abilities"]
    assert _canonical(first)["resources"]["hp"] == _canonical(second)["resources"]["hp"]
    assert _canonical(first)["derived"] == _canonical(second)["derived"]

