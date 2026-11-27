"""One declaration for "this feat raises an ability", and one shape for its form.

Nine feats raise ability scores.  Eight say "one point from these abilities";
Ability Score Improvement says "two points, as +2 or +1/+1".  That is one
declaration with different numbers, not two mechanisms -- and it used to be three
copies of the rule, read by different consumers:

* the catalogue, read by the submit validator;
* two literal blocks in ``_missing_advancements``, read by the form;
* defaults in the preview builder.

So the form asked for "2 points, any ability" and the validator then rejected it
when the feat was Grappler (1 point, Strength or Dexterity) or Spell Recall
(intelligence, wisdom or charisma only).  These tests pin the single source.
"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.features.feat_models import (
    FeatDefinitionError,
    load_feat_definitions,
    parse_feat,
)
from src.rulesets.dnd2024.progression.advancement import Dnd2024AdvancementEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"


def _feat(**overrides):
    base = {
        "schema_version": 1,
        "kind": "feat",
        "id": "probe_feat",
        "category": "general",
        "source_ref": "srd-5.2.1:p87:probe",
        "automation_level": "guided",
        "content_pack": "srd-5.2.1",
    }
    base.update(overrides)
    return base


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle(LOCALE)


@pytest.fixture(scope="module")
def engine(bundle):
    return Dnd2024AdvancementEngine(bundle)


# ---------------------------------------------------------------------------
# 1. 声明：一套词汇，两种数字
# ---------------------------------------------------------------------------


def test_eight_feats_are_one_point_and_asi_is_two(bundle) -> None:
    definitions = load_feat_definitions(bundle)
    with_increase = {
        feat_id: definition.ability_increase
        for feat_id, definition in definitions.items()
        if definition.ability_increase is not None
    }
    assert len(with_increase) == 9
    one_point = {
        feat_id: profile for feat_id, profile in with_increase.items()
        if profile.points == 1
    }
    assert len(one_point) == 8
    assert all(profile.pattern == "1" for profile in one_point.values())
    asi = with_increase["ability_score_improvement"]
    assert (asi.points, asi.pattern, asi.maximum) == (2, "2_or_1_1", 20)
    assert asi.allowed == ()  # "any"


def test_the_single_point_feats_differ_only_in_which_abilities(bundle) -> None:
    definitions = load_feat_definitions(bundle)
    grappler = definitions["grappler"].ability_increase
    recall = definitions["boon_of_spell_recall"].ability_increase
    assert grappler is not None and recall is not None
    assert grappler.allowed == ("str", "dex")
    assert recall.allowed == ("int", "wis", "cha")
    assert (grappler.maximum, recall.maximum) == (20, 30)
    assert not grappler.allows("con")
    assert recall.allows("cha")


def test_points_and_pattern_must_agree() -> None:
    # 声明 2 点却只允许 +1，是自相矛盾的写法。
    with pytest.raises(FeatDefinitionError):
        parse_feat(_feat(ability_increase={
            "points": 2, "pattern": "1", "maximum": 20, "allowed": "any",
        }))


def test_an_unknown_ability_is_rejected() -> None:
    with pytest.raises(FeatDefinitionError):
        parse_feat(_feat(ability_increase={
            "points": 1, "pattern": "1", "maximum": 20, "allowed": ["luck"],
        }))


def test_the_old_field_name_is_rejected_with_a_pointer() -> None:
    with pytest.raises(FeatDefinitionError) as captured:
        parse_feat(_feat(ability_choice={"points": 1}))
    assert "ability_increase" in str(captured.value)


# ---------------------------------------------------------------------------
# 2. 派生：表单读的是同一份声明
# ---------------------------------------------------------------------------


def test_the_general_slot_defaults_to_ability_score_improvement(engine) -> None:
    profile = engine._slot_increase_default("general")
    assert profile is not None
    assert (profile.points, profile.pattern, profile.allowed) == (2, "2_or_1_1", ())


def test_an_epic_slot_has_no_default_so_it_shows_the_widest(engine) -> None:
    # 没有默认恩惠；7 条里两条限制属性，五条是 any，所以并集是"任意"。
    profile = engine._slot_increase_default("epic_boon")
    assert profile is not None
    assert (profile.points, profile.pattern, profile.maximum) == (1, "1", 30)
    assert profile.allowed == ()


def test_a_single_option_profile_means_no_question(engine) -> None:
    # 今天没有这种数据（最小的 allowed 是两项），但 PHB 的 "+1 Strength" 会出现；
    # 规则现在定下来：只有一项可填时不再问玩家。
    from src.rulesets.dnd2024.features import AbilityIncrease

    requirement = engine._ability_increase_requirement(
        AbilityIncrease(points=1, allowed=("str",), maximum=20, pattern="1"),
    )
    assert requirement["auto"] == {"ability": "str", "points": 1}
    multi = engine._ability_increase_requirement(
        AbilityIncrease(points=1, allowed=("str", "dex"), maximum=20, pattern="1"),
    )
    assert multi["auto"] is None


def test_each_option_carries_its_own_profile(engine) -> None:
    """The form can switch profiles when the feat is picked.

    This is the projection that had no consumer (GAP-56); it is what makes
    "Grappler asks for one point of Strength or Dexterity" possible at all.
    """

    character = {"features": {}, "proficiencies": {}, "abilities": {}}
    snapshot = engine.catalog.snapshot("class:fighter", 4)
    options = {
        option["value"]: option["ability_increase"]
        for option in engine._feat_options("general", character, 4, snapshot)
    }
    assert options["feat:ability_score_improvement"] == {
        "points": 2, "allowed": "any", "maximum": 20,
        "pattern": "2_or_1_1", "auto": None,
    }
    assert options["feat:grappler"] == {
        "points": 1, "allowed": ["str", "dex"], "maximum": 20,
        "pattern": "1", "auto": None,
    }
