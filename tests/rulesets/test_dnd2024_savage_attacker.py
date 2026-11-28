"""FD10: Savage Attacker — roll the weapon's dice twice, keep the higher total.

SRD 5.2.1 p87: "Once per turn when you hit a target with a weapon, you can
roll the weapon's damage dice twice and use either roll against the target."

The offer must arrive before the damage is revealed: the alternate set is only
rolled on ``use``, so a player cannot read it and then decline.
"""

from __future__ import annotations

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from test_dnd2024_fighting_style import (
    enemy,
    events_of,
    fighter_sheet,
    new_combat,
    resolve,
    set_turn,
)

LONGSWORD = "item:longsword"
GREATSWORD = "item:greatsword"
LONGBOW = "item:longbow"
GWF = "feat:great_weapon_fighting"


def _combat(
    *, fighting_style: str = "feat:archery", only: tuple[str, ...] | None = None,
    without_feat: bool = False,
    package: str = "equipment_package:fighter_b",
):
    runtime = Dnd2024Runtime()
    sheet = fighter_sheet(runtime, fighting_style=fighting_style, package=package)
    refs = sheet["ruleset_character"]["equipment"]["item_refs"]
    if only is not None:
        refs[:] = list(only)
    if without_feat:
        feats = sheet["ruleset_character"]["features"]["feat_refs"]
        if "feat:savage_attacker" in feats:
            feats.remove("feat:savage_attacker")
    return new_combat(runtime, sheet, enemies=[enemy(position=5)])


def _intent(instance, kind: str, **extra) -> dict:
    return {
        "intent_id": f"{kind}:{ruleset_runtime.state(instance)['version']}",
        "type": kind,
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
        "actor_id": "player:gm",
        **extra,
    }


def _attack(engine, instance, *, weapon: str = LONGSWORD, rolls, **extra):
    set_turn(engine, instance, "player:gm")
    intent = _intent(
        instance, "attack",
        target_id="enemy:goblin-1", weapon_ref=weapon, **extra,
    )
    intent["intent_id"] = f"attack:{ruleset_runtime.state(instance)['version']}:{weapon}"
    return resolve(engine, instance, intent, rolls)


def _decision(result: dict) -> dict:
    return next(
        event for event in events_of(result)
        if event.get("type") == "dnd2024.decision.pending"
    )


def _damage(result: dict) -> dict:
    return next(
        event for event in events_of(result)
        if event.get("type") == "resource.changed"
        and event.get("resource") == "hp"
        and int(event.get("delta") or 0) < 0
    )


def _finish(engine, instance, decision: dict, option: str, rolls=()):
    intent = _intent(instance, "decision.resolve", **{
        "decision_id": decision["decision_id"], "option": option,
    })
    return resolve(engine, instance, intent, rolls)


def _hp(instance) -> int:
    return int(ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"])


# --------------------------------------------------------------------------
# the offer
# --------------------------------------------------------------------------


def test_a_weapon_hit_offers_the_decision_before_the_damage_lands() -> None:
    engine, instance = _combat(only=(LONGSWORD,))
    before = _hp(instance)

    result = _attack(engine, instance, rolls=[15])

    decision = _decision(result)
    assert decision["kind"] == "savage_attacker"
    assert decision["options"] == ["spend", "decline"]
    # 伤害还没落地：窗口就是把"揭示"这一步拦下来的。
    assert _hp(instance) == before
    # 公开投影里不能出现骰面与数值：否则玩家可以"这组低才重掷"。
    from src.rulesets.dnd2024.combat.visibility import public_pending_decision
    shown = public_pending_decision(
        decision, viewer_id=decision["assigned_to"], viewer_is_gm=False,
    )
    assert shown is not None
    assert "rolls" not in shown["damage_event"]
    assert "amount" not in shown["damage_event"]


def test_use_keeps_the_higher_set() -> None:
    engine, instance = _combat(only=(LONGSWORD,))
    before = _hp(instance)

    decision = _decision(_attack(engine, instance, rolls=[15, 1]))
    result = _finish(engine, instance, decision, "spend", rolls=[8])

    event = _damage(result)
    assert event["damage_rerolls"] == [[1], [8]]
    assert event["damage_reroll_picked"] == 1
    assert event["rolls"] == [8]
    assert before - _hp(instance) == event["amount"]


def test_use_keeps_the_original_when_it_is_already_higher() -> None:
    engine, instance = _combat(only=(LONGSWORD,))
    before = _hp(instance)

    decision = _decision(_attack(engine, instance, rolls=[15, 8]))
    result = _finish(engine, instance, decision, "spend", rolls=[1])

    event = _damage(result)
    assert event["damage_rerolls"] == [[8], [1]]
    assert event["damage_reroll_picked"] == 0
    assert event["rolls"] == [8]
    assert before - _hp(instance) == event["amount"]


def test_decline_leaves_the_event_untouched() -> None:
    engine, instance = _combat(only=(LONGSWORD,))

    decision = _decision(_attack(engine, instance, rolls=[15, 5]))
    result = _finish(engine, instance, decision, "decline")

    event = _damage(result)
    assert "damage_rerolls" not in event
    assert event["rolls"] == [5]


# --------------------------------------------------------------------------
# once per turn
# --------------------------------------------------------------------------


def _grant_extra_attack(instance) -> None:
    ruleset_runtime.state(instance)["combat"]["economy"]["attacks_remaining"] = 1


def test_the_budget_is_spent_only_when_used() -> None:
    engine, instance = _combat(only=(LONGSWORD,))

    decision = _decision(_attack(engine, instance, rolls=[15, 3]))
    _finish(engine, instance, decision, "spend", rolls=[2])
    _grant_extra_attack(instance)

    second = _attack(engine, instance, rolls=[15, 4])

    assert not any(
        event.get("type") == "dnd2024.decision.pending" for event in events_of(second)
    )


def test_a_declined_offer_is_still_available_on_the_next_hit() -> None:
    engine, instance = _combat(only=(LONGSWORD,))

    decision = _decision(_attack(engine, instance, rolls=[15, 3]))
    _finish(engine, instance, decision, "decline")
    _grant_extra_attack(instance)

    second = _attack(engine, instance, rolls=[15, 4])

    assert _decision(second)["kind"] == "savage_attacker"


# --------------------------------------------------------------------------
# scope: which attacks, which dice
# --------------------------------------------------------------------------


def test_a_critical_doubles_the_dice_in_both_sets() -> None:
    engine, instance = _combat(package="equipment_package:fighter_a")

    decision = _decision(
        _attack(engine, instance, weapon=GREATSWORD, rolls=[20, 1, 1]),
    )
    result = _finish(engine, instance, decision, "spend", rolls=[6, 6, 6, 6])

    event = _damage(result)
    assert event["critical"] is True
    assert event["damage_rerolls"] == [[1, 1, 1, 1], [6, 6, 6, 6]]
    assert event["damage_reroll_picked"] == 1


def test_great_weapon_fighting_applies_to_both_sets() -> None:
    engine, instance = _combat(
        fighting_style=GWF, package="equipment_package:fighter_a",
    )

    decision = _decision(
        _attack(engine, instance, weapon=GREATSWORD, rolls=[15, 1, 1]),
    )
    result = _finish(engine, instance, decision, "spend", rolls=[2, 2, 1, 1])

    event = _damage(result)
    # 两组都先各自 clamp，再比较：第一组 1,1 → 3,3；第二组 2,2 → 3,3。
    assert event["damage_rerolls"] == [[3, 3], [3, 3]]
    assert event["damage_reroll_picked"] == 0


def test_a_ranged_weapon_is_offered_too() -> None:
    engine, instance = _combat(only=(LONGBOW,))

    decision = _decision(_attack(engine, instance, weapon=LONGBOW, rolls=[15, 4]))

    assert decision["kind"] == "savage_attacker"


def test_a_character_without_the_feat_is_never_asked() -> None:
    # Criminal gives Alert, not Savage Attacker.
    engine, instance = _combat(only=(LONGSWORD,), without_feat=True)

    result = _attack(engine, instance, rolls=[15, 5])

    assert not any(
        event.get("type") == "dnd2024.decision.pending" for event in events_of(result)
    )
    assert _damage(result)["rolls"] == [5]
