"""S0-F19 (part 5b): Superior Hunter's Defense buys resistance before the damage lands.

"当 you take damage, you can take a Reaction to give yourself Resistance to that
damage and any other damage of the same type until the end of the current turn."

Two consequences shape this test file:

* the decision has to be made *before* the damage is committed -- the same edge
  the Bard's Cutting Words damage window sits on -- because resistance has to
  halve the pending number, not refund half of it afterwards;
* resistance never stacks, so a defender who is already resistant does not get
  a quarter, and the condition is still worth applying for the rest of the turn.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from dnd2024_monk_common import SequenceRng, goblin

from test_dnd2024_hunters_mark import _apply, _combat, _combat_state, ranger

HUNTER = "subclass:hunter"


def _hunter(level: int = 15) -> dict:
    sheet = ranger(level)
    features = sheet["ruleset_character"].setdefault("features", {})
    features["subclass_ref"] = HUNTER
    features["feature_choices"] = [{
        "owner": HUNTER, "feature_id": "hunters_prey",
        "choice_id": "hunters_prey_option", "selected_refs": ["feature:colossus_slayer"],
    }]
    return sheet


def _fight(level: int = 15) -> tuple[object, object]:
    return _combat({"gm": _hunter(level)}, {"gm": 1}, enemies=[goblin(position=5, hp=40)])


def _pending(instance) -> list[dict]:
    return list(_combat_state(instance).get("pending_decisions") or [])


def _let_goblin_attack(engine, instance, rolls=(15, 4)) -> list[dict]:
    """Advance turns until the goblin acts, then have it swing at the Hunter."""

    from test_dnd2024_hunters_mark import _to_actor

    _to_actor(engine, instance, "enemy:goblin-1")
    return _apply(engine, instance, {
        "intent_id": "goblin-attack", "type": "attack", "submitted_by": "gm",
        "actor_id": "enemy:goblin-1", "target_id": "player:gm",
        "attack_id": "scimitar",
    }, rolls)


def _reaction_decision(instance) -> dict | None:
    """The hit-reaction decision offering this Hunter's reaction feature."""

    for row in _pending(instance):
        for feature in row.get("features") or []:
            if feature.get("feature_id") == "superior_hunters_defense":
                return row
    return None


def test_a_hunter_defender_is_offered_it_as_a_reaction_feature() -> None:
    engine, instance = _fight()
    _let_goblin_attack(engine, instance)
    decision = _reaction_decision(instance)
    assert decision is not None, "the reaction feature was not offered"
    assert decision["kind"] == "hit_reaction_spell"
    offer = next(
        row for row in decision["features"]
        if row["feature_id"] == "superior_hunters_defense"
    )
    assert offer["damage_multiplier"] == 0.5


def test_a_ranger_below_fifteen_is_not_offered_it() -> None:
    engine, instance = _fight(level=11)
    _let_goblin_attack(engine, instance)
    assert _reaction_decision(instance) is None


def _resolve(engine, instance, option: str) -> list[dict]:
    decision = _reaction_decision(instance)
    assert decision is not None
    intent: dict = {
        "intent_id": f"resolve-{option}", "type": "decision.resolve",
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": option,
    }
    if option == "feature":
        intent["feature_id"] = "superior_hunters_defense"
    return _apply(engine, instance, intent)


def _damage_taken(events: list[dict]) -> int:
    return sum(
        -int(event.get("delta", 0) or 0)
        for event in events
        if event.get("type") == "resource.changed" and event.get("resource") == "hp"
        and event.get("target_id") == "player:gm"
        and int(event.get("delta", 0) or 0) < 0
    )


def test_spending_halves_the_pending_damage_and_costs_the_reaction() -> None:
    engine, instance = _fight()
    _let_goblin_attack(engine, instance)
    halved = _resolve(engine, instance, "feature")
    taken = _damage_taken(halved)
    # 弯刀 1d6+2，骰面 4 → 6 点；抗性减半为 3。
    assert taken == 3
    # 反应花掉后同一回合不能再花一次。
    assert _combat_state(instance)["reactions"].get("player:gm") == 0
    # 倍率在提交前就作用在待结算的伤害上，所以事件里直接是减半后的数字；
    # 它不是"抗性来源"，不该写成 resistance —— 那是被动抗性的字段。
    hit = next(
        event for event in halved
        if event.get("type") == "resource.changed"
        and event.get("target_id") == "player:gm"
        and int(event.get("delta", 0) or 0) < 0
    )
    assert hit["delta"] == -3


def test_declining_leaves_the_damage_untouched() -> None:
    engine, instance = _fight()
    _let_goblin_attack(engine, instance)
    events = _resolve(engine, instance, "decline")
    assert _damage_taken(events) == 6
    assert not [
        event for event in events
        if event.get("condition") == "hunters_defense"
    ]


def test_the_condition_is_applied_for_the_rest_of_the_turn() -> None:
    engine, instance = _fight()
    _let_goblin_attack(engine, instance)
    events = _resolve(engine, instance, "feature")
    applied = [
        event for event in events
        if event.get("type") == "condition.applied"
        and event.get("condition") == "hunters_defense"
    ]
    assert applied, "the sustained half of the feature was not applied"
    assert applied[0]["duration"] == "turn"
    assert applied[0]["resist_damage_types"]
