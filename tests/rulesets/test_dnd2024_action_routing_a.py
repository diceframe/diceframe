"""S0-F10A: the Bonus Action riders that move you, and the bookkeeping behind them.

Instinctive Pounce and Tactical Shift both say "as part of the Bonus Action, move
up to half your Speed".  That is not extra movement to spend later -- it happens
inside the bonus action, and the engine has to express it as a *free* shift so it
never eats the normal allowance, while still refusing anything past half speed.
"""

from __future__ import annotations

import pytest

from src.engine.modules import ruleset_runtime

from dnd2024_monk_common import SequenceRng, advance
from test_dnd2024_combat import _instance, _start


def _state(instance):
    return ruleset_runtime.state(instance)


def _capability(engine, instance, capability_id: str, **extra) -> dict:
    state = _state(instance)
    return engine.resolve_intent(instance, {
        "intent_id": f"cap-{capability_id}-{state['version']}",
        "type": "class_capability",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm",
        "capability_id": capability_id, **extra,
    }, SequenceRng([1, 1, 1, 1]))


def _apply(engine, instance, resolved: dict) -> list[dict]:
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _barbarian_combat(level: int = 7):
    """真实职业链构造的野蛮人（不是手写特性）。"""

    from test_dnd2024_rage import _instance as barbarian_instance

    return barbarian_instance(level=level)


def test_granted_movement_does_not_touch_the_normal_allowance() -> None:
    engine, instance = _barbarian_combat()
    before = int(_state(instance)["combat"]["economy"]["movement"])

    resolved = _capability(engine, instance, "rage", rider_move=5)
    events = _apply(engine, instance, resolved)

    shift = next(event for event in events if event.get("granted_by"))
    assert shift["type"] == "dnd2024.position.changed"
    assert shift["movement_cost"] == 0
    # 7 级野蛮人有 Fast Movement：速度 40，半速 20。
    assert shift["allowance_ft"] == 20
    assert shift["provokes_opportunity"] is False
    assert int(_state(instance)["combat"]["economy"]["movement"]) == before


def test_granted_movement_beyond_half_speed_is_rejected() -> None:
    engine, instance = _barbarian_combat()
    resolved = _capability(engine, instance, "rage", rider_move=25)
    assert resolved["ok"] is False
    assert "exceeds half your speed" in str(resolved.get("error"))


def test_granted_movement_is_only_allowed_on_the_declared_capabilities() -> None:
    engine, instance = _instance()
    _start(engine, instance)
    resolved = _capability(engine, instance, "flurry_of_blows", rider_move=5)
    assert resolved["ok"] is False
    assert "not available for this capability" in str(resolved.get("error"))


# ---------------------------------------------------------------------------
# Tactical Shift / Steady Aim
# ---------------------------------------------------------------------------

def _fighter_combat(level: int = 5):
    from test_dnd2024_failed_d20_windows import _combat as fighter_combat, _fighter

    from src.rulesets.dnd2024 import Dnd2024Runtime

    runtime = Dnd2024Runtime()
    return fighter_combat(runtime, {"gm": _fighter(runtime, level)}, rolls=[1, 1, 1, 1])


def _rogue_combat(level: int = 3):
    """Steady Aim 是盗贼特性，测试必须用盗贼，不能用战士凑。"""

    from test_dnd2024_d20_skills import _combat as rogue_combat, _rogue

    from src.rulesets.dnd2024 import Dnd2024Runtime

    runtime = Dnd2024Runtime()
    return rogue_combat(runtime, _rogue(runtime, level))


def test_tactical_shift_moves_with_second_wind_and_promises_no_reaction() -> None:
    engine, instance = _fighter_combat(5)
    resolved = _capability(engine, instance, "second_wind", rider_move=5)
    events = _apply(engine, instance, resolved)
    shift = next(event for event in events if event.get("granted_by"))
    assert shift["granted_by"] == "tactical_shift"
    assert shift["provokes_opportunity"] is False
    assert shift["movement_cost"] == 0


def test_steady_aim_really_refuses_after_moving() -> None:
    """不是看账本，而是真的提交一次：移动过的回合必须被拒绝。"""

    engine, instance = _rogue_combat(3)
    moved = engine.resolve_intent(instance, {
        "intent_id": "mv-1", "type": "move",
        "expected_version": int(_state(instance).get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm", "distance": 5,
    }, SequenceRng([1]))
    assert moved["ok"] is True, moved
    engine.apply_batch(instance, moved["event_batch"])

    resolved = _capability(engine, instance, "steady_aim")
    assert resolved["ok"] is False
    assert "already moved this turn" in str(resolved.get("error"))


def test_steady_aim_grants_one_advantage_and_zeroes_speed() -> None:
    engine, instance = _rogue_combat(3)
    events = _apply(engine, instance, _capability(engine, instance, "steady_aim"))
    applied = [
        event["condition"] for event in events
        if event.get("type") == "condition.applied"
    ]
    assert applied == ["next_attack_advantage", "steady_aim_slow"]

    combat = _state(instance)["combat"]
    from src.rulesets.dnd2024.combat.check_modes import effective_speed

    actor = engine._actor_view(instance, combat, "player:gm")
    assert effective_speed(
        engine.catalog.conditions, actor["conditions"], int(actor["speed"]),
    ) == 0


# ---------------------------------------------------------------------------
# Heightened Focus：Flurry 三次
# ---------------------------------------------------------------------------

def test_heightened_focus_turns_flurry_into_three_strikes() -> None:
    """10 级 Monk 的付费 Flurry 是三次徒手攻击，基线是两次。"""

    from test_dnd2024_monk_combat import _apply, _setup, capability_intent

    base_engine, base_instance = _setup(level=9)
    resolved, _ = _apply(
        base_engine, base_instance,
        capability_intent(base_engine, base_instance, "flurry_of_blows",
                          intent_id="flurry-base"),
        SequenceRng([15, 4, 15, 5]),
    )
    base_checks = [
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "check.resolved" and event["kind"] == "attack"
    ]

    engine, instance = _setup(level=10)
    resolved, _ = _apply(
        engine, instance,
        capability_intent(engine, instance, "flurry_of_blows",
                          intent_id="flurry-upgraded"),
        SequenceRng([15, 4, 15, 5, 15, 6]),
    )
    checks = [
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "check.resolved" and event["kind"] == "attack"
    ]

    assert len(base_checks) == 2
    assert len(checks) == 3


# ---------------------------------------------------------------------------
# Fleet Step
# ---------------------------------------------------------------------------

def _monk11():
    from dnd2024_monk_common import monk_sheet
    from src.rulesets.dnd2024 import Dnd2024Runtime

    from test_dnd2024_monk_combat import _setup

    return _setup(level=11)


def test_fleet_step_offers_a_free_step_after_another_bonus_action() -> None:
    engine, instance = _monk11()
    combat = _state(instance)["combat"]
    character = instance.get_character_sheet("gm")["ruleset_character"]
    assert engine.features.has_feature(character, "fleet_step") is True

    # 先花掉一个非 Step 的附赠动作：Flurry。
    from test_dnd2024_monk_combat import _apply as monk_apply, capability_intent

    resolved, _ = monk_apply(
        engine, instance,
        capability_intent(engine, instance, "flurry_of_blows", intent_id="flurry-fleet"),
        SequenceRng([15, 4, 15, 5, 15, 6]),
    )
    combat = _state(instance)["combat"]
    assert combat["economy"]["bonus_action"] == 0
    assert combat["economy"].get("fleet_step_available") is True

    # 附赠动作已经用光，但免费的 Step 仍然可用。
    views = {
        view.id: view
        for view in engine._available_class_capabilities(
            engine._actor_view(instance, combat, "player:gm"), combat["economy"],
        )
    }
    assert "step_of_the_wind" in views


def test_a_bonus_action_move_still_counts_as_having_moved() -> None:
    """Pounce 的附带移动不花普通移动力，但它确实让角色动过。

    Steady Aim 的前提是"本回合还没移动"，所以附带移动必须把 ``moved`` 置位，
    否则"先 Pounce 再瞄准"会绕过前提——这条是评审时发现的缺陷。
    """

    engine, instance = _barbarian_combat()
    events = _apply(
        engine, instance, _capability(engine, instance, "rage", rider_move=5),
    )
    assert any(event.get("granted_by") for event in events)

    combat = _state(instance)["combat"]
    assert combat["economy"]["moved"] is True
    assert engine.has_moved_this_turn(combat, "player:gm") is True
    # 普通移动额度没有被这次附带移动扣掉。
    assert combat["economy"]["movement"] == 40
