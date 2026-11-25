"""S0-F10B: Hide is the exception, not the default.

The SRD gives three ways in -- Heavily Obscured, three-quarters cover, total
cover -- and treats everything else as open ground where you simply cannot try.
The engine used to list ``hide`` whenever an action remained and resolve it
without ever looking at the battlefield, so a rogue could vanish in an empty
room.  These tests drive the gate itself: one predicate, three consumers
(validation, the availability projection, and the resolver), plus the promise
that a refusal costs nothing.
"""

from __future__ import annotations

import pytest

from src.engine.modules import ruleset_runtime

from dnd2024_monk_common import SequenceRng
from test_dnd2024_channel_control import _apply, _capability, _inject
from test_dnd2024_combat import _instance, _start


def _state(instance):
    return ruleset_runtime.state(instance)


def _combat():
    engine, instance = _instance()
    _start(engine, instance)
    return engine, instance


def _declare_obscurement(engine, instance, level: str = "heavily",
                         target_id: str = "player:gm") -> None:
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"obs-{level}",
        "type": "combat.set_obscurement",
        "expected_version": int(_state(instance).get("version", 0) or 0),
        "submitted_by": "gm", "target_id": target_id, "level": level,
    }, SequenceRng([]))
    assert resolved["ok"] is True, resolved
    engine.apply_batch(instance, resolved["event_batch"])


def _declare_cover(engine, instance, level: str = "total",
                   target_id: str = "player:gm") -> None:
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"cover-{level}",
        "type": "combat.set_cover",
        "expected_version": int(_state(instance).get("version", 0) or 0),
        "submitted_by": "gm", "target_id": target_id, "level": level,
    }, SequenceRng([]))
    assert resolved["ok"] is True, resolved
    engine.apply_batch(instance, resolved["event_batch"])


def _hide(engine, instance, rolls=(13,)) -> dict:
    return engine.resolve_intent(instance, {
        "intent_id": "hide-1", "type": "hide",
        "expected_version": int(_state(instance).get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng(list(rolls)))


def _actions(engine, instance) -> list[dict]:
    # available_intents 的第二个参数是**席位 uid**（"gm"），不是 actor id。
    return engine.available_intents(instance, "gm")


def _hide_action(engine, instance) -> dict:
    return next(row for row in _actions(engine, instance) if row["type"] == "hide")


# ---------------------------------------------------------------------------
# 1–4: 默认拒绝与三条合法路径
# ---------------------------------------------------------------------------

def test_open_ground_refuses_with_a_readable_reason() -> None:
    engine, instance = _combat()
    resolved = _hide(engine, instance)
    assert resolved["ok"] is False
    assert "no heavy obscurement or three-quarters cover" in str(resolved.get("error"))


def test_a_refusal_costs_no_action() -> None:
    """拒绝发生在校验层：动作必须先留在账上，玩家才能再试别的。"""

    engine, instance = _combat()
    before = _state(instance)["combat"]["economy"]["action"]
    _hide(engine, instance)
    after = _state(instance)["combat"]["economy"]["action"]
    assert after == before


def test_heavy_obscurement_allows_the_attempt() -> None:
    engine, instance = _combat()
    _declare_obscurement(engine, instance, "heavily")
    # 投影要在花掉动作之前读：动作一花，hide 就从列表里消失了。
    assert _hide_action(engine, instance)["available"] is True
    resolved = _hide(engine, instance)
    assert resolved["ok"] is True, resolved


def test_three_quarters_and_total_cover_allow_it_but_half_does_not() -> None:
    engine, instance = _combat()
    _declare_cover(engine, instance, "three_quarters")
    assert _hide(engine, instance)["ok"] is True

    engine, instance = _combat()
    _declare_cover(engine, instance, "total")
    assert _hide(engine, instance)["ok"] is True

    engine, instance = _combat()
    _declare_cover(engine, instance, "half")
    assert _hide(engine, instance)["ok"] is False


def test_light_obscurement_is_not_enough() -> None:
    engine, instance = _combat()
    _declare_obscurement(engine, instance, "lightly")
    assert _hide(engine, instance)["ok"] is False


# ---------------------------------------------------------------------------
# 5: 可用性投影就是同一判定
# ---------------------------------------------------------------------------

def test_the_projection_and_the_refusal_agree() -> None:
    engine, instance = _combat()
    action = _hide_action(engine, instance)
    assert action["available"] is False
    assert action["unavailable_reason"]

    _declare_obscurement(engine, instance, "heavily")
    action = _hide_action(engine, instance)
    assert action["available"] is True
    assert action["unavailable_reason"] == ""


# ---------------------------------------------------------------------------
# 6–7: 能力不是后门
# ---------------------------------------------------------------------------

def test_cunning_hide_checks_the_same_gate_but_pays_a_bonus_action() -> None:
    from test_dnd2024_action_routing_a import _rogue_combat

    engine, instance = _rogue_combat()
    denied = _capability(engine, instance, "cunning_hide")
    assert denied["ok"] is False
    assert denied["error"] == "no heavy obscurement or three-quarters cover"
    assert _state(instance)["combat"]["economy"]["bonus_action"] == 1

    _declare_obscurement(engine, instance)
    events = _apply(engine, instance, _capability(engine, instance, "cunning_hide", [13]))
    assert [row["resource"] for row in events if row["type"] == "dnd2024.action.spent"] == [
        "bonus_action",
    ]
    check = next(row for row in events if row["type"] == "check.resolved")
    assert check["kind"] == "hide"
    assert check["natural"] == 13


def test_the_resolver_itself_refuses_regardless_of_entry_point() -> None:
    """能力路径不经过 ``hide`` 意图的校验分支，所以结算层必须再守一次。

    ``cunning_hide`` 走 ``_class_capability_events`` → ``_hide_events(...,
    spend_action=False)``，从不经过基本动作的 validation。直接调用结算函数
    是唯一能证明"后门不存在"的方式。
    """

    from src.rulesets.dnd2024.combat.engine import CombatIntentError

    engine, instance = _combat()
    combat = _state(instance)["combat"]
    with pytest.raises(CombatIntentError) as excinfo:
        engine._hide_events(
            instance, combat, {"actor_id": "player:gm"}, SequenceRng([13]),
            spend_action=False,
        )
    assert "no heavy obscurement or three-quarters cover" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 8: 成功仍然走原来的结算
# ---------------------------------------------------------------------------

def test_a_legal_hide_still_resolves_the_same_check() -> None:
    engine, instance = _combat()
    _declare_obscurement(engine, instance, "heavily")
    resolved = _hide(engine, instance, rolls=(13,))
    assert resolved["ok"] is True
    kinds = [event["type"] for event in resolved["event_batch"]["events"]]
    assert "dnd2024.action.spent" in kinds
    check = next(
        event for event in resolved["event_batch"]["events"]
        if event.get("type") == "check.resolved"
    )
    assert check["kind"] == "hide"
    assert check["total"] == check["natural"] + check["modifier"]
