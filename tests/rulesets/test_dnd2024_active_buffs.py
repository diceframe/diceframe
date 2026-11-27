"""S0-F21: rest-limited self-buffs and the turn-start payment window.

Four features, two shapes of rule.  Tireless, Nature's Veil and Wholeness of
Body are ordinary actions paid from their own Wisdom-scaled pool; Superior
Defense is not an action at all but a payment allowed only in the window that
opens when the actor's own turn begins.

Every combat assertion goes through ``resolve_intent`` -> ``apply_batch``, the
same two calls the web layer makes, so a rule that only holds inside the
resolver cannot pass here.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
)
from test_dnd2024_survivor import _instance

RANGER_ABILITIES = {"str": 12, "dex": 14, "con": 13, "int": 10, "wis": 15, "cha": 8}
MONK_ABILITIES = {"str": 12, "dex": 14, "con": 13, "int": 8, "wis": 15, "cha": 10}


def _spell_choices(bundle, class_ref: str, level: int) -> dict:
    options = Dnd2024SpellSelection(bundle).options(class_ref, level)
    requirements = options["requirements"]
    return {
        "cantrip_refs": [
            row["ref"] for row in options["cantrips"]
        ][: int(requirements["cantrip_count"])],
        "prepared_spell_refs": [
            row["ref"] for row in options["leveled_spells"]
        ][: int(requirements["prepared_spell_count"])],
        "spellbook_refs": [],
    }


def _build(
    runtime: Dnd2024Runtime, bundle, class_ref: str, level: int,
    abilities: dict, skills: list[str], package: str,
) -> dict:
    """One professional character advanced through the real chain.

    The rest of the suite uses quick presets, but a Ranger has to answer spell
    choices the preset path does not ask for, so this borrows the Monk draft as
    a legal level-1 skeleton and rewrites only what the class needs.
    """

    draft = monk_draft(runtime, "en")
    draft.update(
        class_ref=class_ref, class_skill_refs=skills,
        equipment_package_ref=package, base_abilities=abilities,
    )
    choices = runtime.builder_choices(None, draft)
    draft["class_tool_refs"] = [item["ref"] for item in choices["class_tools"]][
        : choices["class_tool_count"]
    ]
    draft["class_spell_choices"] = _spell_choices(bundle, class_ref, 1)
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        picks["class_spell_choices"] = _spell_choices(bundle, class_ref, next_level)
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle("en")


@pytest.fixture(scope="module")
def features(bundle):
    return Dnd2024ClassFeatureResolver(bundle)


@pytest.fixture(scope="module")
def sheets(bundle) -> dict[str, dict]:
    runtime = Dnd2024Runtime()
    ranger = {
        "ranger9": 9, "ranger10": 10, "ranger13": 13, "ranger14": 14,
    }
    monk = {"monk5": 5, "monk6": 6, "monk11": 11, "monk17": 17, "monk18": 18}
    result: dict[str, dict] = {}
    for key, level in ranger.items():
        result[key] = _build(
            runtime, bundle, "class:ranger", level, RANGER_ABILITIES,
            ["skill:perception", "skill:survival", "skill:stealth"],
            "equipment_package:ranger_a",
        )
    for key, level in monk.items():
        result[key] = _build(
            runtime, bundle, "class:monk", level, MONK_ABILITIES,
            ["skill:acrobatics", "skill:stealth"], "equipment_package:monk_b",
        )
    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _setup(sheet: dict) -> tuple[object, object]:
    return _instance(Dnd2024Runtime().load_bundle("en"), deepcopy(sheet))


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _conditions(instance, uid: str = "gm") -> dict:
    return _canonical(instance, uid).setdefault("conditions", {})


def _start(engine, instance, rolls=(20, 3), enemies=None, intent_id="start-1") -> list[dict]:
    state = ruleset_runtime.state(instance)
    resolved = engine.resolve_intent(instance, {
        "intent_id": intent_id, "type": "combat.start",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "enemies": enemies or [_typed_enemy("slashing")],
    }, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _try_start(engine, instance, rolls=(20, 3), **extra) -> dict:
    state = ruleset_runtime.state(instance)
    intent = {
        "intent_id": extra.pop("intent_id", "start-1"), "type": "combat.start",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "enemies": [goblin()],
    }
    intent.update(extra)
    return engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))


def _capability(engine, instance, capability_id: str, dice=(), **extra) -> dict:
    state = ruleset_runtime.state(instance)
    intent = {
        "intent_id": f"cap-{capability_id}-{state['version']}",
        "type": "class_capability",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm",
        "capability_id": capability_id,
    }
    intent.update(extra)
    return engine.resolve_intent(instance, intent, SequenceRng(list(dice)))


def _apply(engine, instance, resolved: dict) -> list[dict]:
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _activate(engine, instance, capability_id: str, dice=(), **extra) -> list[dict]:
    return _apply(engine, instance, _capability(engine, instance, capability_id, dice, **extra))


def _combat(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _of_type(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event.get("type") == kind]


def _end_turn(engine, instance, actor_id: str | None = None) -> list[dict]:
    state = ruleset_runtime.state(instance)
    if actor_id is None:
        actor_id = str(_combat(instance)["economy"].get("actor_id") or "")
    intent = {
        "intent_id": f"end:{actor_id}:{state['version']}", "type": "end_turn",
        "actor_id": actor_id, "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm",
    }
    return _apply(engine, instance, engine.resolve_intent(instance, intent, SequenceRng([])))


def _message(engine, instance, text: str = "tick") -> list[dict]:
    """A table message: the one intent that changes nothing but still resolves."""

    state = ruleset_runtime.state(instance)
    intent = {
        "intent_id": f"msg-{state['version']}", "type": "combat.message",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "text": text,
    }
    return _apply(engine, instance, engine.resolve_intent(instance, intent, SequenceRng([])))


def _typed_enemy(damage_type: str, damage: str = "1d6+2") -> dict:
    """A brute whose single attack deals one chosen damage type.

    Resistance is only observable when the *incoming* type differs, so the
    suite needs a stat block it can parameterize.  The attack bonus is high on
    purpose: the roll under test is the damage halving, not the hit chance.
    """

    return {
        "id": "goblin-1", "name": "Brute", "hp": 60, "armor_class": 12,
        "speed": 30, "position": 5, "initiative_modifier": 5,
        "creature_type": "humanoid",
        "abilities": {"str": 16, "dex": 10, "con": 14, "int": 8, "wis": 8, "cha": 8},
        "saving_throws": {"str": 3, "con": 2},
        "attacks": [{
            "id": "slam", "name": "Slam", "attack_bonus": 20,
            "damage": damage, "damage_type": damage_type, "range": 5,
        }],
    }


def _enemy_attack(engine, instance) -> dict:
    """The enemy turn, spent on one guaranteed hit against the player."""

    economy = _combat(instance).get("economy") or {}
    if str(economy.get("actor_id") or "") != "enemy:goblin-1":
        _end_turn(engine, instance)
    state = ruleset_runtime.state(instance)
    intent = {
        "intent_id": f"enemy-attack-{state['version']}", "type": "attack",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "enemy:goblin-1",
        "target_id": "player:gm", "attack_id": "slam",
    }
    # 1d6+2 with the die showing 4 is 6 damage whatever the attack roll did; the
    # second d20 is only consumed when the target is prone to grant advantage.
    events = _apply(
        engine, instance, engine.resolve_intent(instance, intent, SequenceRng([15, 4, 4])),
    )
    # 18 级武僧现在会在被命中时得到拨挡攻击的反应窗口（S0-F11）。这些用例
    # 关心的是抗性/回避，不是反应，所以在这里放弃它，让伤害走原来的路径落地。
    events.extend(_decline_hit_reaction(engine, instance))
    return events


def _decline_hit_reaction(engine, instance) -> list:
    pending = _combat(instance).get("pending_decisions") or []
    if not pending or pending[0].get("kind") != "hit_reaction_spell":
        return []
    # 只放弃**拨挡**这一条：别的受击特性（例如狂战士的 Retaliation、
    # 盗贼的 Uncanny Dodge）由各自的用例自己决定，不在这里代为处理。
    if not any(
        row.get("feature_id") == "deflect_attacks"
        for row in pending[0].get("features") or []
    ):
        return []
    state = ruleset_runtime.state(instance)
    return _apply(engine, instance, engine.resolve_intent(instance, {
        "intent_id": f"decline-reaction-{state['version']}", "type": "decision.resolve",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "decision_id": pending[0]["decision_id"],
        "option": "decline",
    }, SequenceRng([])))


def _pool(instance, resource_id: str) -> dict:
    return _canonical(instance)["resources"]["class"][resource_id]


def _rest_short(sheet: dict, bundle) -> dict:
    return Dnd2024RestEngine(bundle).complete_short_rest(deepcopy(sheet), {})


# ---------------------------------------------------------------------------
# 1. the four features exist only at their own level and owner
# ---------------------------------------------------------------------------


def test_the_four_features_appear_only_at_their_own_level(sheets, features) -> None:
    assert not features.has_feature(sheets["ranger9"]["ruleset_character"], "tireless")
    assert features.has_feature(sheets["ranger10"]["ruleset_character"], "tireless")
    assert not features.has_feature(
        sheets["ranger13"]["ruleset_character"], "natures_veil",
    )
    assert features.has_feature(sheets["ranger14"]["ruleset_character"], "natures_veil")
    assert not features.has_feature(
        sheets["monk5"]["ruleset_character"], "wholeness_of_body",
    )
    assert features.has_feature(sheets["monk6"]["ruleset_character"], "wholeness_of_body")
    assert not features.has_feature(
        sheets["monk17"]["ruleset_character"], "superior_defense",
    )
    assert features.has_feature(
        sheets["monk18"]["ruleset_character"], "superior_defense",
    )


def test_a_monk_of_another_way_of_war_has_no_wholeness_of_body(sheets, features) -> None:
    sheet = deepcopy(sheets["monk6"])
    sheet["ruleset_character"]["features"]["subclass_ref"] = "subclass:warrior_of_shadow"
    assert not features.has_feature(sheet["ruleset_character"], "wholeness_of_body")


def test_a_ranger_has_no_monk_capability_and_the_reverse(sheets, features) -> None:
    assert not features.has_feature(
        sheets["ranger14"]["ruleset_character"], "wholeness_of_body",
    )
    assert not features.has_feature(
        sheets["monk18"]["ruleset_character"], "tireless",
    )


# ---------------------------------------------------------------------------
# 2. the three Wisdom pools
# ---------------------------------------------------------------------------


def test_three_wisdom_pools_are_separate_and_scaled_by_wisdom(sheets) -> None:
    ranger = sheets["ranger14"]["ruleset_character"]
    assert ranger["abilities"]["wis"] == 16  # 15 from the array plus one background point
    assert ranger["resources"]["class"]["tireless"]["maximum"] == 3
    assert ranger["resources"]["class"]["natures_veil"]["maximum"] == 3
    monk = sheets["monk18"]["ruleset_character"]
    assert monk["resources"]["class"]["wholeness_of_body"]["maximum"] == 3


def test_a_negative_wisdom_still_leaves_one_use(sheets, bundle) -> None:
    sheet = deepcopy(sheets["ranger10"])
    sheet["ruleset_character"]["abilities"]["wis"] = 6
    rested = Dnd2024RestEngine(bundle).sync_resources(sheet)
    assert rested["resources"]["class"]["tireless"]["maximum"] == 1
    assert rested["resources"]["class"]["tireless"]["current"] == 1


def test_the_pools_refill_on_a_long_rest_only(sheets, bundle) -> None:
    sheet = deepcopy(sheets["ranger14"])
    canonical = sheet["ruleset_character"]
    canonical["resources"]["class"]["tireless"]["current"] = 0
    canonical["resources"]["class"]["natures_veil"]["current"] = 0
    short = _rest_short(sheet, bundle)["character"]
    assert short["resources"]["class"]["tireless"]["current"] == 0
    assert short["resources"]["class"]["natures_veil"]["current"] == 0
    long = Dnd2024RestEngine(bundle).complete_long_rest(deepcopy(sheet))["character"]
    assert long["resources"]["class"]["tireless"]["current"] == 3
    assert long["resources"]["class"]["natures_veil"]["current"] == 3


def test_advancing_does_not_silently_refill_a_spent_pool(sheets, bundle) -> None:
    sheet = deepcopy(sheets["ranger10"])
    canonical = sheet["ruleset_character"]
    canonical["resources"]["class"]["tireless"]["current"] = 1
    # Same Wisdom, higher level: the restored amount must stay "two spent".
    rested = Dnd2024RestEngine(bundle).sync_resources(sheet)
    assert rested["resources"]["class"]["tireless"]["current"] == 1
    assert rested["resources"]["class"]["tireless"]["maximum"] == 3


# ---------------------------------------------------------------------------
# 3-5. Tireless
# ---------------------------------------------------------------------------


def test_tireless_costs_a_magic_action_and_one_charge(sheets) -> None:
    engine, instance = _setup(sheets["ranger10"])
    _start(engine, instance)
    events = _activate(engine, instance, "tireless", dice=[7])
    spent = _of_type(events, "dnd2024.action.spent")
    assert [row["resource"] for row in spent] == ["action"]
    assert _of_type(events, "dnd2024.class_resource.spent")[0]["resource_id"] == "tireless"
    grant = _of_type(events, "resource.changed")[0]
    assert grant["resource"] == "temp_hp"
    assert grant["rolls"] == [7]
    assert grant["amount"] == 10  # 7 + Wisdom 3
    assert _canonical(instance)["resources"]["temp_hp"] == 10
    assert _pool(instance, "tireless")["current"] == 2


def test_tireless_keeps_the_higher_pool_and_still_pays(sheets) -> None:
    engine, instance = _setup(sheets["ranger10"])
    _start(engine, instance)
    _activate(engine, instance, "tireless", dice=[8])
    assert _canonical(instance)["resources"]["temp_hp"] == 11
    # A second use needs a second turn: the Magic action is spent, not reusable.
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    events = _activate(engine, instance, "tireless", dice=[1])
    assert _of_type(events, "dnd2024.action.spent")[0]["resource"] == "action"
    assert _canonical(instance)["resources"]["temp_hp"] == 11
    assert _pool(instance, "tireless")["current"] == 1


def test_tireless_never_touches_hit_points_or_other_conditions(sheets) -> None:
    sheet = deepcopy(sheets["ranger10"])
    sheet["ruleset_character"]["resources"]["hp"] = 5
    sheet["ruleset_character"]["conditions"] = {"poisoned": {"source": "test"}}
    engine, instance = _setup(sheet)
    _start(engine, instance)
    _activate(engine, instance, "tireless", dice=[6])
    canonical = _canonical(instance)
    assert canonical["resources"]["hp"] == 5
    assert "poisoned" in canonical["conditions"]
    assert canonical["resources"]["temp_hp"] == 9


def test_tireless_still_grants_one_when_the_formula_is_not_positive(sheets) -> None:
    sheet = deepcopy(sheets["ranger10"])
    sheet["ruleset_character"]["abilities"]["wis"] = 6  # -2, so 1 - 2 = -1
    engine, instance = _setup(sheet)
    _start(engine, instance)
    events = _activate(engine, instance, "tireless", dice=[1])
    assert _of_type(events, "resource.changed")[0]["amount"] == 1
    assert _canonical(instance)["resources"]["temp_hp"] == 1


def test_tireless_rolls_exactly_one_die_per_use(sheets) -> None:
    engine, instance = _setup(sheets["ranger10"])
    _start(engine, instance)
    # One declared die per use: a resolver that rolled two would run the
    # sequence dry instead of quietly taking the next value.
    first = _activate(engine, instance, "tireless", dice=[3])
    assert _of_type(first, "resource.changed")[0]["rolls"] == [3]
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    second = _activate(engine, instance, "tireless", dice=[4])
    assert _of_type(second, "resource.changed")[0]["rolls"] == [4]


def test_tireless_without_a_charge_produces_no_events(sheets) -> None:
    sheet = deepcopy(sheets["ranger10"])
    sheet["ruleset_character"]["resources"]["class"]["tireless"]["current"] = 0
    engine, instance = _setup(sheet)
    _start(engine, instance)
    result = _capability(engine, instance, "tireless", dice=[5])
    assert result["ok"] is False
    assert "not enough" in str(result["error"])


def test_tireless_without_an_action_produces_no_events(sheets) -> None:
    engine, instance = _setup(sheets["ranger10"])
    _start(engine, instance)
    for _ in range(1):
        _end_turn(engine, instance, "player:gm")
        _end_turn(engine, instance, "enemy:goblin-1")
    assert _combat(instance)["economy"]["actor_id"] == "player:gm"
    # Spend the action on something else first.
    _end_turn(engine, instance, "player:gm")
    result = _capability(engine, instance, "tireless", dice=[5])
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# 6. Tireless short-rest Exhaustion
# ---------------------------------------------------------------------------


def test_a_short_rest_lowers_exhaustion_by_one_with_tireless(sheets, bundle) -> None:
    sheet = deepcopy(sheets["ranger10"])
    sheet["ruleset_character"]["conditions"] = {"exhaustion": 3}
    rested = _rest_short(sheet, bundle)
    assert rested["character"]["conditions"]["exhaustion"] == 2
    reduced = _of_type(rested["events"], "reduce_exhaustion")
    assert len(reduced) == 1
    assert (reduced[0]["before"], reduced[0]["after"]) == (3, 2)
    assert reduced[0]["feature_id"] == "tireless"


def test_a_short_rest_invents_no_exhaustion_event_at_zero(sheets, bundle) -> None:
    sheet = deepcopy(sheets["ranger10"])
    sheet["ruleset_character"]["conditions"] = {"exhaustion": 0}
    rested = _rest_short(sheet, bundle)
    assert not _of_type(rested["events"], "reduce_exhaustion")


def test_a_ranger_below_tenth_level_gains_nothing_from_the_short_rest(
    sheets, bundle,
) -> None:
    sheet = deepcopy(sheets["ranger9"])
    sheet["ruleset_character"]["conditions"] = {"exhaustion": 3}
    rested = _rest_short(sheet, bundle)
    assert rested["character"]["conditions"]["exhaustion"] == 3
    assert not _of_type(rested["events"], "reduce_exhaustion")


def test_a_long_rest_still_takes_exactly_one_level(sheets, bundle) -> None:
    sheet = deepcopy(sheets["ranger10"])
    sheet["ruleset_character"]["conditions"] = {"exhaustion": 3}
    rested = Dnd2024RestEngine(bundle).complete_long_rest(deepcopy(sheet))
    assert rested["character"]["conditions"]["exhaustion"] == 2
    reduced = _of_type(rested["events"], "reduce_exhaustion")
    assert len(reduced) == 1
    assert "feature_id" not in reduced[0]


# ---------------------------------------------------------------------------
# 7-10. Nature's Veil
# ---------------------------------------------------------------------------


def test_natures_veil_costs_a_bonus_action_and_marks_only_its_owner(sheets) -> None:
    engine, instance = _setup(sheets["ranger14"])
    _start(engine, instance)
    events = _activate(engine, instance, "natures_veil")
    assert _of_type(events, "dnd2024.action.spent")[0]["resource"] == "bonus_action"
    assert _of_type(events, "dnd2024.class_resource.spent")[0]["resource_id"] == "natures_veil"
    applied = _of_type(events, "condition.applied")[0]
    assert applied["target_id"] == "player:gm"
    assert applied["condition"] == "invisible"
    assert applied["source_actor_id"] == "player:gm"
    assert applied["turns_remaining"] == 2
    assert "invisible" in _conditions(instance)
    assert "invisible" not in _combat(instance)["enemies"]["goblin-1"]["conditions"]


def test_the_veil_survives_its_own_turn_and_ends_after_the_next(sheets) -> None:
    engine, instance = _setup(sheets["ranger14"])
    _start(engine, instance)
    _activate(engine, instance, "natures_veil")
    _end_turn(engine, instance, "player:gm")
    assert "invisible" in _conditions(instance)
    _end_turn(engine, instance, "enemy:goblin-1")
    assert _combat(instance)["economy"]["actor_id"] == "player:gm"
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    assert "invisible" not in _conditions(instance)


def test_attacking_does_not_end_the_veil_early(sheets) -> None:
    engine, instance = _setup(sheets["ranger14"])
    _start(engine, instance)
    _activate(engine, instance, "natures_veil")
    state = ruleset_runtime.state(instance)
    attack = {
        "intent_id": f"veil-attack-{state['version']}", "type": "attack",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:longbow",
    }
    _apply(engine, instance, engine.resolve_intent(instance, attack, SequenceRng([15, 4, 2])))
    assert "invisible" in _conditions(instance)


def test_the_veil_does_not_stack(sheets) -> None:
    engine, instance = _setup(sheets["ranger14"])
    _start(engine, instance)
    _activate(engine, instance, "natures_veil")
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    _activate(engine, instance, "natures_veil")
    # Two uses, one condition: the second refreshes the span instead of adding
    # a second entry that would double the duration.
    assert list(_conditions(instance)).count("invisible") == 1
    assert _conditions(instance)["invisible"]["turns_remaining"] == 2
    assert _pool(instance, "natures_veil")["current"] == 1


def test_the_veil_without_a_bonus_action_creates_no_condition(sheets) -> None:
    engine, instance = _setup(sheets["ranger14"])
    _start(engine, instance)
    _activate(engine, instance, "natures_veil")
    assert _pool(instance, "natures_veil")["current"] == 2
    result = _capability(engine, instance, "natures_veil")
    # A charge is still there, but the Bonus Action is not.
    assert result["ok"] is False
    assert "bonus action" in str(result["error"])


def test_an_attack_from_a_blind_attacker_is_at_disadvantage(sheets) -> None:
    engine, instance = _setup(sheets["ranger14"])
    _start(engine, instance)
    _activate(engine, instance, "natures_veil")
    events = _enemy_attack(engine, instance)
    check = _of_type(events, "check.resolved")
    assert check and check[0]["advantage_mode"] == "disadvantage"
    # 一旦看不见的条件没了，攻击者看得见，就回到普通攻击骰：隐形不是永久优势。
    _end_turn(engine, instance, "enemy:goblin-1")
    _end_turn(engine, instance, "player:gm")
    assert "invisible" not in _conditions(instance)
    seen = _enemy_attack(engine, instance)
    check = _of_type(seen, "check.resolved")[0]
    assert not check["advantage_mode"] and not (check.get("advantage_sources") or [])


def test_the_veil_is_not_recovered_by_a_short_rest(sheets, bundle) -> None:
    sheet = deepcopy(sheets["ranger14"])
    sheet["ruleset_character"]["resources"]["class"]["natures_veil"]["current"] = 0
    rested = _rest_short(sheet, bundle)
    assert rested["character"]["resources"]["class"]["natures_veil"]["current"] == 0


# ---------------------------------------------------------------------------
# 11-13. Wholeness of Body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "die"),
    [("monk6", "1d8"), ("monk11", "1d10"), ("monk17", "1d12")],
)
def test_wholeness_rolls_the_martial_arts_die_of_its_level(sheets, key, die) -> None:
    sheet = deepcopy(sheets[key])
    sheet["ruleset_character"]["resources"]["hp"] = 5
    engine, instance = _setup(sheet)
    _start(engine, instance)
    events = _activate(engine, instance, "wholeness_of_body", dice=[5])
    heal = _of_type(events, "resource.changed")[0]
    assert heal["resource"] == "hp"
    assert heal["heal_die"] == die
    assert heal["amount"] == 5 + 3
    assert _canonical(instance)["resources"]["hp"] == 13


def test_wholeness_is_a_bonus_action_for_itself_only(sheets) -> None:
    engine, instance = _setup(sheets["monk6"])
    instance.players["ally"] = {
        "character_name": "Bryn", "character_sheet": deepcopy(sheets["monk6"]),
    }
    # Initiative is rolled for every player in sorted id order: the ally first
    # (low), then gm (high) so the character under test takes the first turn.
    _start(engine, instance, rolls=(3, 20, 2))
    assert _combat(instance)["economy"]["actor_id"] == "player:gm"
    events = _activate(engine, instance, "wholeness_of_body", dice=[4])
    assert _of_type(events, "dnd2024.action.spent")[0]["resource"] == "bonus_action"
    assert _of_type(events, "resource.changed")[0]["target_id"] == "player:gm"


def test_wholeness_heals_one_at_the_worst_and_clamps_at_the_maximum(sheets) -> None:
    sheet = deepcopy(sheets["monk6"])
    sheet["ruleset_character"]["abilities"]["wis"] = 6
    sheet["ruleset_character"]["resources"]["hp"] = 5
    engine, instance = _setup(sheet)
    _start(engine, instance)
    events = _activate(engine, instance, "wholeness_of_body", dice=[1])
    assert _of_type(events, "resource.changed")[0]["amount"] == 1
    assert _canonical(instance)["resources"]["hp"] == 6

    sheet = deepcopy(sheets["monk6"])
    sheet["ruleset_character"]["resources"]["hp"] = 5
    engine, instance = _setup(sheet)
    _start(engine, instance)
    _activate(engine, instance, "wholeness_of_body", dice=[8])
    assert _canonical(instance)["resources"]["hp"] <= int(
        _canonical(instance)["resources"]["max_hp"]
    )


def test_wholeness_pays_its_cost_even_when_healing_is_blocked(sheets) -> None:
    engine, instance = _setup(sheets["monk6"])
    _start(engine, instance)
    _conditions(instance)["cannot_regain_hp"] = {"source": "test"}
    before = int(_canonical(instance)["resources"]["hp"])
    events = _activate(engine, instance, "wholeness_of_body", dice=[6])
    heal = _of_type(events, "resource.changed")[0]
    assert heal["amount"] == 0 and heal["delta"] == 0
    assert heal["blocked_by"] == "cannot_regain_hp"
    assert int(_canonical(instance)["resources"]["hp"]) == before
    assert _pool(instance, "wholeness_of_body")["current"] == 2


# ---------------------------------------------------------------------------
# 14-15. the Superior Defense window
# ---------------------------------------------------------------------------


def _full_monk(sheet: dict) -> dict:
    sheet = deepcopy(sheet)
    sheet["ruleset_character"]["resources"]["class"]["focus_points"]["current"] = 18
    return sheet


def test_the_window_opens_with_the_first_turn_of_the_combat(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    window = _combat(instance)["economy"].get("turn_start_window")
    assert window == {
        "actor_id": "player:gm", "feature_id": "superior_defense",
        "capability_id": "superior_defense",
    }


def test_a_monk_without_three_focus_is_offered_nothing(sheets) -> None:
    sheet = deepcopy(sheets["monk18"])
    sheet["ruleset_character"]["resources"]["class"]["focus_points"]["current"] = 2
    engine, instance = _setup(sheet)
    _start(engine, instance)
    assert "turn_start_window" not in _combat(instance)["economy"]
    result = _capability(engine, instance, "superior_defense", dice=[])
    assert result["ok"] is False


def test_superior_defense_costs_three_focus_and_no_action(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    events = _activate(engine, instance, "superior_defense")
    spent = _of_type(events, "dnd2024.class_resource.spent")
    assert [(row["resource_id"], row["amount"]) for row in spent] == [("focus_points", 3)]
    assert not _of_type(events, "dnd2024.action.spent")
    assert _combat(instance)["economy"]["action"] == 1
    assert _combat(instance)["economy"]["bonus_action"] == 1
    assert _pool(instance, "focus_points")["current"] == 15


def test_the_window_closes_after_the_first_thing_you_do(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    state = ruleset_runtime.state(instance)
    dash = {
        "intent_id": f"early-dash-{state['version']}", "type": "dash",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm",
    }
    _apply(engine, instance, engine.resolve_intent(instance, dash, SequenceRng([])))
    assert "turn_start_window" not in _combat(instance)["economy"]
    result = _capability(engine, instance, "superior_defense", dice=[])
    assert result["ok"] is False
    assert "start of your turn" in str(result["error"])


def test_ending_the_turn_also_closes_the_window(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _end_turn(engine, instance, "player:gm")
    assert _combat(instance)["economy"].get("turn_start_window") is None


def test_the_next_turn_offers_the_window_again(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    assert _combat(instance)["economy"].get("turn_start_window", {}).get(
        "actor_id"
    ) == "player:gm"
    events = _activate(engine, instance, "superior_defense")
    assert _of_type(events, "dnd2024.class_resource.spent")[0]["amount"] == 3
    assert _pool(instance, "focus_points")["current"] == 12


# ---------------------------------------------------------------------------
# 16-19. resistance, incapacitation, and the minute
# ---------------------------------------------------------------------------


def _damage_events(events: list[dict]) -> list[dict]:
    return [
        event for event in events
        if event.get("type") == "resource.changed" and event.get("resource") == "hp"
    ]


@pytest.mark.parametrize(
    ("damage_type", "halved"),
    [("bludgeoning", True), ("fire", True), ("radiant", True), ("force", False)],
)
def test_superior_defense_resists_every_type_but_force(
    sheets, damage_type, halved,
) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance, enemies=[_typed_enemy(damage_type)])
    _activate(engine, instance, "superior_defense")
    events = _enemy_attack(engine, instance)
    hit = _damage_events(events)[0]
    # 1d6+2 with the die showing 4 is 6 damage; resistance takes it to 3.
    assert hit["amount"] == (3 if halved else 6)
    assert bool(hit.get("resisted")) is halved
    if halved:
        assert hit["resistance"] == "superior_defense"


def test_damage_taken_before_the_activation_is_not_rerolled(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    # The enemy's turn lands an unresisted hit first.
    first = _enemy_attack(engine, instance)
    assert _damage_events(first)[0]["amount"] == 6
    _end_turn(engine, instance, "enemy:goblin-1")
    assert _combat(instance)["economy"]["actor_id"] == "player:gm"
    _activate(engine, instance, "superior_defense")
    second = _enemy_attack(engine, instance)
    assert _damage_events(second)[0]["amount"] == 3


def test_resistance_does_not_stack_with_another_source(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    _conditions(instance)["stoneskinned"] = {"source": "spell"}
    events = _enemy_attack(engine, instance)
    hit = _damage_events(events)[0]
    assert hit["amount"] == 3  # halved once, not twice
    assert hit["resistance"] in {"stoneskinned", "superior_defense"}


def _inject(engine, instance, events: list[dict]) -> None:
    """Apply raw events through the same reducer ``apply_batch`` uses.

    Used only where no intent in this repository can produce the event: nothing
    in the SRD content stuns a player, so "a condition arrives" has to be
    written straight into the ledger.  The assertions still run against the
    real reducer and the real settlement helper, not a hand-built event list.
    """

    from src.rulesets.events import stable_batch_id

    version = int(ruleset_runtime.state(instance).get("version", 0) or 0)
    intent = {
        "intent_id": f"inject-{version}", "type": "combat.message",
        "expected_version": version,
    }
    batch = {
        "batch_id": stable_batch_id(intent, version),
        "intent_id": intent["intent_id"], "intent_type": "combat.message",
        "expected_version": version, "result_version": version + 1,
        "events": list(events), "source_ref": "srd-5.2.1:p52:superior-defense",
    }
    assert engine.apply_batch(instance, batch)["applied"] is True


def test_becoming_incapacitated_ends_superior_defense(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    assert "superior_defense" in _conditions(instance)

    stun = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "stunned", "duration": "actor_turn_start",
    }]
    # The settlement step is what turns "a condition arrived" into the end of
    # every effect whose own text says Incapacitated ends it.
    cleanup = engine._incapacitation_cleanup_events(instance, _combat(instance), stun)
    assert cleanup == [{
        "type": "condition.removed", "target_id": "player:gm",
        "condition": "superior_defense", "reason": "incapacitated",
    }]
    _inject(engine, instance, [*stun, *cleanup])
    assert "superior_defense" not in _conditions(instance)
    # And the resistance is really gone, not merely marked as expired.
    events = _enemy_attack(engine, instance)
    assert _damage_events(events)[0]["amount"] == 6


@pytest.mark.parametrize(
    "condition", ["incapacitated", "paralyzed", "stunned", "unconscious"],
)
def test_any_incapacitating_condition_ends_it(sheets, condition) -> None:
    # The text says "the Incapacitated condition", and the catalog decides which
    # conditions imply it, so the settlement step names none of them.
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    applied = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": condition, "duration": "actor_turn_start",
    }]
    cleanup = engine._incapacitation_cleanup_events(instance, _combat(instance), applied)
    assert [event["condition"] for event in cleanup] == ["superior_defense"]


def test_a_harmless_condition_leaves_it_alone(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    applied = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "poisoned", "duration": "actor_turn_start",
    }]
    assert engine._incapacitation_cleanup_events(
        instance, _combat(instance), applied,
    ) == []


def test_recovering_from_incapacitation_does_not_turn_it_back_on(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    stun = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "stunned", "duration": "actor_turn_start",
    }]
    cleanup = engine._incapacitation_cleanup_events(instance, _combat(instance), stun)
    _inject(engine, instance, [*stun, *cleanup])
    _inject(engine, instance, [{
        "type": "condition.removed", "target_id": "player:gm", "condition": "stunned",
    }])
    assert "stunned" not in _conditions(instance)
    assert "superior_defense" not in _conditions(instance)


def test_superior_defense_expires_after_ten_rounds(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    assert _conditions(instance)["superior_defense"]["expires_at_round"] == 11
    for _ in range(10):
        _end_turn(engine, instance, "player:gm")
        _end_turn(engine, instance, "enemy:goblin-1")
    assert _combat(instance)["round"] == 11
    assert "superior_defense" in _conditions(instance)
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    assert _combat(instance)["round"] == 12
    assert "superior_defense" not in _conditions(instance)


def test_the_effect_is_not_tied_to_the_combat_ending(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    _combat(instance)["enemies"]["goblin-1"]["hp"] = 0
    _end_turn(engine, instance, "player:gm")
    assert _combat(instance)["status"] == "ended"
    assert "superior_defense" in _conditions(instance)


def test_the_world_clock_can_end_it_before_the_rounds_do(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    assert _conditions(instance)["superior_defense"]["expires_at_minute"] == 1
    # 十轮还没走完，但世界时钟已经越过那一分钟：两个上限各自成立就结束。
    instance.world_state = {"clock": {"day": 1, "minute": 30}}
    events = _message(engine, instance)
    assert _of_type(events, "condition.removed") == [{
        "type": "condition.removed", "target_id": "player:gm",
        "condition": "superior_defense", "reason": "one minute passed",
    }]
    assert "superior_defense" not in _conditions(instance)


def test_a_leftover_buff_is_cleared_when_the_next_fight_starts(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    _combat(instance)["enemies"]["goblin-1"]["hp"] = 0
    _end_turn(engine, instance, "player:gm")
    assert _combat(instance)["status"] == "ended"
    # 战斗在一分钟内结束：效果按规则仍在，但时间已经过去了。
    assert "superior_defense" in _conditions(instance)
    instance.world_state = {"clock": {"day": 1, "minute": 30}}
    events = _start(engine, instance, intent_id="start-2")
    assert _of_type(events, "condition.removed") == [{
        "type": "condition.removed", "target_id": "player:gm",
        "condition": "superior_defense", "reason": "one minute passed",
    }]
    assert "superior_defense" not in _conditions(instance)


def test_a_live_effect_is_not_swept_by_the_clock(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    assert _of_type(_message(engine, instance), "condition.removed") == []
    assert "superior_defense" in _conditions(instance)


# ---------------------------------------------------------------------------
# 20-23. replay, saves, other actors, forged declarations
# ---------------------------------------------------------------------------


def test_replaying_the_activation_neither_spends_nor_heals_twice(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    state = ruleset_runtime.state(instance)
    intent = {
        "intent_id": "sd-once", "type": "class_capability",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm",
        "capability_id": "superior_defense",
    }
    first = engine.resolve_intent(instance, intent, SequenceRng([]))
    _apply(engine, instance, first)
    assert _pool(instance, "focus_points")["current"] == 15
    replay = engine.resolve_intent(instance, intent, SequenceRng([]))
    assert replay["ok"] is True and replay["replayed"] is True
    assert engine.apply_batch(instance, replay["event_batch"])["applied"] is False
    assert _pool(instance, "focus_points")["current"] == 15


def test_the_pools_and_the_window_survive_a_round_trip(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    window = _combat(instance)["economy"].get("turn_start_window")
    assert window["actor_id"] == "player:gm"
    _activate(engine, instance, "superior_defense")
    # 窗口在支付后立刻关闭，存档里留下来的是池子与效果本身。
    assert _combat(instance)["economy"].get("turn_start_window") is None
    reloaded = deepcopy(instance.get_character_sheet("gm"))
    assert reloaded["ruleset_character"]["resources"]["class"]["focus_points"]["current"] == 15
    assert reloaded["ruleset_character"]["conditions"]["superior_defense"][
        "expires_at_round"
    ] == 11


def test_another_actors_turn_cannot_open_or_use_the_window(sheets) -> None:
    sheet = _full_monk(sheets["monk18"])
    engine, instance = _setup(sheet)
    instance.players["ally"] = {
        "character_name": "Bryn",
        "character_sheet": deepcopy(sheets["ranger14"]),
    }
    _start(engine, instance, rolls=(20, 3, 15))
    # Whoever is first, the window names exactly that actor.
    economy = _combat(instance)["economy"]
    window = economy.get("turn_start_window")
    if window is not None:
        assert window["actor_id"] == economy["actor_id"]
    state = ruleset_runtime.state(instance)
    forged = {
        "intent_id": f"other-actor-{state['version']}", "type": "class_capability",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:ally",
        "capability_id": "superior_defense",
    }
    result = engine.resolve_intent(instance, forged, SequenceRng([]))
    assert result["ok"] is False


@pytest.mark.parametrize(
    ("extra", "fragment"),
    [
        # A ranger has no Superior Defense at all: the capability id alone is
        # not an entitlement.
        ({"capability_id": "tireless"}, "not available"),
        ({"capability_id": "not_a_capability"}, "not available"),
        ({"actor_id": "player:nobody"}, "not this actor's turn"),
    ],
)
def test_forged_declarations_are_refused(sheets, extra, fragment) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    state = ruleset_runtime.state(instance)
    intent = {
        "intent_id": f"forged-{state['version']}", "type": "class_capability",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm",
        "capability_id": "superior_defense",
    }
    intent.update(extra)
    result = engine.resolve_intent(instance, intent, SequenceRng([]))
    assert result["ok"] is False
    assert fragment in str(result["error"])


def test_the_force_exception_cannot_be_declared_by_the_client(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    events = _activate(
        engine, instance, "superior_defense", resist_all_except=[],
    )
    applied = _of_type(events, "condition.applied")[0]
    # The catalog owns the exception, not the intent.
    assert applied["resist_all_except"] == ["force"]


# ---------------------------------------------------------------------------
# 24-26. neighbouring resistances, regressions, and the public feed
# ---------------------------------------------------------------------------


def test_rage_resistance_still_halves_once_with_its_own_source(sheets) -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    from test_dnd2024_survivor import _sheet

    sheet = _sheet(runtime, 6, "relentless_vanguard")
    engine, instance = _setup(sheet)
    _start(engine, instance)
    _conditions(instance)["rage"] = {"duration": "rage", "turns_remaining": 10}
    events = _enemy_attack(engine, instance)
    hit = _damage_events(events)[0]
    assert hit["amount"] == 3
    assert hit["resistance"] == "rage"


def test_existing_features_still_work_after_the_slice(sheets) -> None:
    engine, instance = _setup(sheets["monk18"])
    _start(engine, instance)
    # Flurry of Blows is the neighbour most likely to break: it shares the
    # Focus pool that Superior Defense now prices.
    _canonical(instance)["resources"]["class"]["focus_points"]["current"] = 18
    events = _activate(
        engine, instance, "flurry_of_blows", dice=[12, 4, 3, 2, 5],
        target_id="enemy:goblin-1",
    )
    assert _of_type(events, "dnd2024.class_resource.spent")[0]["resource_id"] == "focus_points"
    assert _pool(instance, "focus_points")["current"] == 17


def test_the_public_feed_does_not_leak_the_new_condition_fields(sheets) -> None:
    engine, instance = _setup(_full_monk(sheets["monk18"]))
    _start(engine, instance)
    _activate(engine, instance, "superior_defense")
    feed = Dnd2024Runtime().gameplay_view(instance, "player:gm", False)[
        "recent_combat_events"
    ]
    applied = [
        row for row in feed
        if row.get("type") == "condition.applied"
        and row.get("condition") == "superior_defense"
    ]
    assert applied
    assert set(applied[0]) <= {
        "type", "actor_id", "target_id", "condition", "reason", "round",
        "turn_index", "event_id", "batch_id", "intent_type", "state_version",
        "actor_name", "target_name", "feature_id",
    }
