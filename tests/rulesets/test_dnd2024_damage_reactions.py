"""Deflect Attacks / Deflect Energy (Monk 3 / 13).

Every assertion goes through the authoritative chain the web layer uses:
``resolve_intent`` -> ``apply_batch`` for both the enemy attack and the monk's
``decision.resolve``.  Nothing here writes a reaction result, a damage total or
an AC by hand.

Slow Fall is deliberately absent: Falling has no model in this engine, so the
slice stops it and the gap is registered as GAP-50.
"""

from __future__ import annotations

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import (
    SequenceRng,
    focus_state,
    goblin,
    monk_instance,
    monk_sheet,
    start_combat,
)


def _instance(
    *, level: int = 3, focus: int | None = None, reactions: int | None = None,
    enemy: list[dict] | None = None,
) -> tuple[Dnd2024CombatEngine, GameInstance, dict]:
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=level)
    if focus is not None:
        sheet["ruleset_character"]["resources"]["class"]["focus_points"]["current"] = focus
    engine, instance = monk_instance(runtime, sheet)
    _start(engine, instance, enemy or [goblin()])
    if reactions is not None:
        ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] = reactions
    return engine, instance, sheet


def _start(engine: Dnd2024CombatEngine, instance: GameInstance, enemies: list[dict]) -> None:
    """``start_combat`` fixes the goblin's damage type; this file needs its own."""

    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemies,
    }, SequenceRng([20, 1]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True


def _apply(engine: Dnd2024CombatEngine, instance: GameInstance, intent: dict, rng=None):
    resolved = engine.resolve_intent(instance, intent, rng or SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _dex_modifier(sheet: dict) -> int:
    return ability_modifier(int(sheet["ruleset_character"]["abilities"]["dex"]))


def _enemy_attack(
    engine, instance, rolls: list[int], *, attack_id: str = "scimitar",
) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": "goblin-1", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1",
        "target_id": "player:gm", "attack_id": attack_id,
    }, SequenceRng(list(rolls)))


def _hit(engine, instance, rolls: list[int], **kwargs) -> list[dict]:
    """End the monk's turn, then let the goblin attack.

    The attack roll and its damage are the first two numbers in ``rolls``; any
    later numbers belong to the reaction.
    """

    _apply(engine, instance, {
        "intent_id": "end-1", "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    })
    return _enemy_attack(engine, instance, rolls, **kwargs)


def _pending(instance: GameInstance) -> dict | None:
    rows = ruleset_runtime.state(instance)["combat"]["pending_decisions"]
    return rows[0] if rows else None


def _resolve(engine, instance, intent: dict, rng=None) -> list[dict]:
    decision = _pending(instance)
    assert decision is not None, "no pending decision"
    return _apply(engine, instance, {
        "intent_id": f"resolve:{decision['decision_id']}",
        "decision_id": decision["decision_id"], "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", **intent,
    }, rng)


def _deflect_option(decision: dict) -> dict | None:
    return next(
        (row for row in decision.get("features") or []
         if row.get("feature_id") == "deflect_attacks"),
        None,
    )


def _event(events: list[dict], event_type: str) -> dict:
    return next(item for item in events if item.get("type") == event_type)


def _hp(engine: Dnd2024CombatEngine, instance: GameInstance) -> int:
    combat = ruleset_runtime.state(instance)["combat"]
    return int(engine._actor_view(instance, combat, "player:gm")["hp"])


# --- Ownership and gate ----------------------------------------------------


def test_a_third_level_monk_is_offered_deflect_against_a_slashing_hit() -> None:
    engine, instance, _sheet = _instance(level=3)

    events = _hit(engine, instance, [15, 4])

    decision = _pending(instance)
    assert decision is not None
    assert decision["kind"] == "hit_reaction_spell"
    choice = _deflect_option(decision)
    assert choice is not None, decision
    assert choice["effect_kind"] == "damage_reduction"
    assert choice["reduction_dice"] == "1d10"
    assert any(item["type"] == "dnd2024.decision.pending" for item in events)


def test_a_second_level_monk_is_not_offered_deflect() -> None:
    engine, instance, _sheet = _instance(level=2)

    _hit(engine, instance, [15, 4])

    decision = _pending(instance)
    # 2 级没有拨挡，也没有别的受击特性：窗口根本不该开。
    assert decision is None or _deflect_option(decision) is None


def test_a_pure_fire_hit_is_refused_until_deflect_energy() -> None:
    enemy = goblin()
    enemy["attacks"][0]["damage_type"] = "fire"
    engine, instance, _sheet = _instance(level=12, enemy=[enemy])

    _hit(engine, instance, [15, 4])
    decision = _pending(instance)
    assert decision is None or _deflect_option(decision) is None


def test_deflect_energy_opens_every_damage_type_at_thirteen() -> None:
    enemy = goblin()
    enemy["attacks"][0]["damage_type"] = "fire"
    engine, instance, _sheet = _instance(level=13, enemy=[enemy])

    _hit(engine, instance, [15, 4])
    decision = _pending(instance)
    assert decision is not None
    assert _deflect_option(decision) is not None


def test_no_reaction_means_no_deflect() -> None:
    engine, instance, _sheet = _instance(level=3, reactions=0)

    _hit(engine, instance, [15, 4])

    decision = _pending(instance)
    assert decision is None or _deflect_option(decision) is None


# --- The reduction itself --------------------------------------------------


def test_deflect_reduces_by_the_die_plus_dex_plus_monk_level() -> None:
    engine, instance, sheet = _instance(level=3)
    level = 3
    modifier = _dex_modifier(sheet)

    _hit(engine, instance, [15, 4])
    assert _pending(instance) is not None
    # 减伤骰在选择之后才掷：RNG 归 resolve 这一次调用所有。
    resolved = _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([4]))

    reduced = _event(resolved, "dnd2024.damage.reduced")
    assert reduced["rolls"] == [4]
    assert reduced["original_amount"] == 6
    assert reduced["reduction"] == 4 + modifier + level
    assert reduced["final_amount"] == max(0, 6 - (4 + modifier + level))


def test_damage_below_the_reduction_is_floored_at_zero_not_healed() -> None:
    engine, instance, sheet = _instance(level=3)
    before = _hp(engine, instance)

    _hit(engine, instance, [15, 4])
    resolved = _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([10]))

    reduced = _event(resolved, "dnd2024.damage.reduced")
    assert reduced["final_amount"] == 0
    assert _hp(engine, instance) == before
    assert not any(
        item.get("type") == "resource.changed" and item.get("resource") == "hp"
        for item in resolved
    )


def test_a_partial_reduction_lands_only_the_difference() -> None:
    engine, instance, sheet = _instance(level=3)
    modifier = _dex_modifier(sheet)
    before = _hp(engine, instance)
    level = 3

    # 1d6+2 掷出 6（总 8），1d10 掷 1：减伤 1 + dex + 3，必然还有一个正差额。
    _hit(engine, instance, [15, 6])
    resolved = _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([1]))

    reduced = _event(resolved, "dnd2024.damage.reduced")
    reduction = 1 + modifier + level
    landed = _event(resolved, "resource.changed")
    assert reduced["reduction"] == reduction
    assert landed["amount"] == 8 - reduction
    assert _hp(engine, instance) == before - landed["amount"]

    assert _pending(instance) is None


# --- Redirect --------------------------------------------------------------


def _second_goblin() -> dict:
    row = goblin()
    row.update({"id": "goblin-2", "name": "Goblin Two", "hp": 30, "position": 5})
    return row


def test_a_full_reduction_opens_the_focus_priced_redirect() -> None:
    engine, instance, _sheet = _instance(level=3, focus=3, enemy=[goblin(), _second_goblin()])

    _hit(engine, instance, [15, 4])
    events = _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([10]))

    reduced = _event(events, "dnd2024.damage.reduced")
    assert reduced["final_amount"] == 0
    window = _pending(instance)
    assert window is not None, "a full reduction with Focus left must open the redirect"
    assert window["options"] == ["redirect", "decline"]
    assert window["redirect"]["focus_cost"] == 1
    assert {row["actor_id"] for row in window["redirect"]["targets"]} >= {"enemy:goblin-2"}
    # 原伤害一分不落：0 点伤害不发 resource.changed。
    assert not any(
        item.get("type") == "resource.changed" and item.get("resource") == "hp"
        for item in events
    )


def test_redirect_spends_focus_and_punishes_a_failed_dexterity_save() -> None:
    engine, instance, _sheet = _instance(level=3, focus=3, enemy=[goblin(), _second_goblin()])
    focus_before = int(focus_state(instance).get("current", 0) or 0)

    _hit(engine, instance, [15, 4])
    _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([10]))
    resolved = _resolve(engine, instance, {
        "option": "redirect", "redirect_target_id": "enemy:goblin-2",
    }, SequenceRng([2, 5, 5]))

    spent = next(
        item for item in resolved
        if item.get("type") == "dnd2024.class_resource.spent"
        and item.get("resource_id") == "focus_points"
    )
    assert spent["amount"] == 1
    assert int(focus_state(instance).get("current", 0) or 0) == focus_before - 1
    check = _event(resolved, "check.resolved")
    assert (check["kind"], check["ability"], check["success"]) == ("saving_throw", "dex", False)
    landed = _event(resolved, "resource.changed")
    # 2 × 武艺骰（d6）掷 5、5，加敏捷调整值。
    assert landed["rolls"] == [5, 5]
    landed_modifier = ability_modifier(
        int(instance.get_character_sheet("gm")["ruleset_character"]["abilities"]["dex"])
    )
    assert landed["amount"] == 10 + landed_modifier


def test_a_successful_save_takes_no_redirect_damage() -> None:
    engine, instance, _sheet = _instance(level=3, focus=3, enemy=[goblin(), _second_goblin()])

    _hit(engine, instance, [15, 4])
    _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([10]))
    resolved = _resolve(engine, instance, {
        "option": "redirect", "redirect_target_id": "enemy:goblin-2",
    }, SequenceRng([20]))

    check = _event(resolved, "check.resolved")
    assert check["success"] is True
    assert not any(item.get("type") == "resource.changed" for item in resolved)


def test_no_focus_means_the_redirect_is_never_offered() -> None:
    engine, instance, _sheet = _instance(level=3, focus=0, enemy=[goblin(), _second_goblin()])

    _hit(engine, instance, [15, 4])
    events = _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([10]))

    assert _event(events, "dnd2024.damage.reduced")["final_amount"] == 0
    assert _pending(instance) is None


def test_declining_the_redirect_costs_nothing() -> None:
    engine, instance, _sheet = _instance(level=3, focus=3, enemy=[goblin(), _second_goblin()])
    focus_before = int(focus_state(instance).get("current", 0) or 0)
    hp_before = _hp(engine, instance)

    _hit(engine, instance, [15, 4])
    _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([10]))
    resolved = _resolve(engine, instance, {"option": "decline"})

    assert int(focus_state(instance).get("current", 0) or 0) == focus_before
    assert _pending(instance) is None
    # 放弃重定向不等于放弃拨挡：伤害已经在第一段减到 0，不能重新打一次。
    assert not any(item.get("type") == "resource.changed" for item in resolved)
    assert _hp(engine, instance) == hp_before


def test_the_owner_sees_the_redirect_targets_in_their_own_window() -> None:
    """The player projection is a whitelist; `redirect` has to be on it."""

    from src.rulesets.dnd2024.combat.visibility import public_pending_decision

    engine, instance, _sheet = _instance(level=3, focus=3, enemy=[goblin(), _second_goblin()])
    _hit(engine, instance, [15, 4])
    _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([10]))

    decision = _pending(instance)
    assert decision is not None
    projected = public_pending_decision(decision, viewer_id="gm")
    assert projected is not None, "the owner must receive their own window"
    redirect = projected["redirect"]
    # 任务书 §4.2.4：攻击者本人没有被规则排除，不能擅自加条件。
    assert {row["actor_id"] for row in redirect["targets"]} == {
        "enemy:goblin-1", "enemy:goblin-2",
    }
    assert redirect["save_dc"] > 0
    assert projected["options"] == ["redirect", "decline"]


@pytest.mark.parametrize("level", [5, 9, 13])
def test_the_redirect_rolls_the_monks_martial_arts_die(level: int) -> None:
    """Two rolls of *the monk's* Martial Arts die, which stops being a d6 at 5."""

    engine, instance, _sheet = _instance(
        level=level, focus=3, enemy=[goblin(), _second_goblin()],
    )
    combat = ruleset_runtime.state(instance)["combat"]
    die = str(engine._actor_view(instance, combat, "player:gm")["martial_arts_die"])
    assert die != "1d6"
    size = int(die.split("d")[-1])

    _hit(engine, instance, [15, 4])
    _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([10]))
    window = _pending(instance)
    assert window is not None
    # 骰式少了字母 d 就不是 NdM，结算时会静默回退成 d6。
    assert window["redirect"]["dice"] == f"2d{size}"
    resolved = _resolve(engine, instance, {
        "option": "redirect", "redirect_target_id": "enemy:goblin-2",
    }, SequenceRng([2, size, size]))

    landed = _event(resolved, "resource.changed")
    assert landed["rolls"] == [size, size]


@pytest.mark.parametrize(
    ("shape", "ranged"),
    [((5, 5), False), ((10, 10), False), ((80, 320), True), ((60, 60), True)],
)
def test_the_redirect_range_follows_whether_the_attack_was_ranged(
    shape: tuple[int, int], ranged: bool,
) -> None:
    """The SRD catalog stores melee as 5/5 (reach 10/10) and a bow as 80/320."""

    attacker = goblin()
    attacker["attacks"][0]["range"], attacker["attacks"][0]["long_range"] = shape
    engine, instance, _sheet = _instance(
        level=3, focus=3, enemy=[attacker, _second_goblin()],
    )

    _hit(engine, instance, [15, 4])
    first = _pending(instance)
    assert first is not None
    assert first["attack_ranged"] is ranged
    _resolve(engine, instance, {
        "option": "feature", "feature_id": "deflect_attacks",
    }, SequenceRng([10]))
    window = _pending(instance)
    assert window is not None
    assert window["redirect"]["range_ft"] == (60 if ranged else 5)
