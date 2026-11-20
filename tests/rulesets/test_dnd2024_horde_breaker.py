"""S0-F19 (part 4b): Horde Breaker's second attack, and the history that makes it legal.

The SRD sentence has four conditions in it -- another attack with the *same*
weapon, against a *different* creature, within 5 feet of the original, one this
Hunter has not attacked this turn.  Three of the four are questions about the
past, so the slice starts with a small piece of state: ``attack_history`` records
``(actor, target, round, turn_index)`` for every attack roll the reducer writes.

That record is keyed by the *turn*, not by the attacker's own turn, because an
Opportunity Attack happens on somebody else's turn and must not consume the
Hunter's own budget.
"""

from __future__ import annotations

import json

import pytest

from src.engine.game_instance import GameInstance

from dnd2024_monk_common import SequenceRng, goblin

from test_dnd2024_hunters_mark import _apply, _attack_intent, _combat, _combat_state, ranger

HUNTER = "subclass:hunter"


def _reloaded(instance):
    """Round-trip the persisted projection through plain JSON."""

    return GameInstance.from_dict(json.loads(json.dumps(instance.to_dict())))


def _hunter(option: str = "horde_breaker", level: int = 3) -> dict:
    sheet = ranger(level)
    features = sheet["ruleset_character"].setdefault("features", {})
    features["subclass_ref"] = HUNTER
    features["feature_choices"] = [{
        "owner": HUNTER, "feature_id": "hunters_prey",
        "choice_id": "hunters_prey_option", "selected_refs": [f"feature:{option}"],
    }]
    return sheet


def _two_goblins(gap: int = 5) -> tuple[object, object]:
    first = goblin(position=20, hp=40)
    second = {**goblin(position=20 + gap, hp=40), "id": "goblin-2", "name": "Goblin 2"}
    return _combat({"gm": _hunter()}, {"gm": 20}, enemies=[first, second])


def _second(engine, instance, *, original: str = "enemy:goblin-1",
            target: str = "enemy:goblin-2") -> dict:
    from src.engine.modules import ruleset_runtime

    intent = _attack_intent(instance, target_id=target)
    intent["second_target_of"] = original
    intent["expected_version"] = int(ruleset_runtime.state(instance).get("version", 0))
    return intent


def test_a_second_attack_needs_the_original_attack_this_turn() -> None:
    engine, instance = _two_goblins()
    resolved = engine.resolve_intent(instance, _second(engine, instance), SequenceRng([15, 7]))
    assert resolved["ok"] is False
    assert "original target this turn" in str(resolved.get("error"))


def test_the_record_lists_only_this_turn_and_this_actor() -> None:
    engine, instance = _two_goblins()
    _apply(engine, instance, _attack_intent(instance), [15, 7, 5])
    combat = _combat_state(instance)
    assert engine.attacked_this_turn(combat, "player:gm") == {"enemy:goblin-1"}
    assert engine.attacked_this_turn(combat, "player:nobody") == set()


def test_a_legal_second_attack_does_not_spend_the_action_again() -> None:
    engine, instance = _two_goblins()
    _apply(engine, instance, _attack_intent(instance), [15, 7, 5])
    before = dict(_combat_state(instance)["economy"])
    events = _apply(engine, instance, _second(engine, instance), [15, 7, 5])
    after = _combat_state(instance)["economy"]
    assert after.get("attacks_remaining") == before.get("attacks_remaining")
    assert after.get("action") == before.get("action")
    used = [e for e in events if e["type"] == "dnd2024.feature.used"]
    assert [e["feature_id"] for e in used] == ["horde_breaker"]


def test_the_same_target_twice_is_rejected() -> None:
    engine, instance = _two_goblins()
    _apply(engine, instance, _attack_intent(instance), [15, 7, 5])
    resolved = engine.resolve_intent(
        instance, _second(engine, instance, target="enemy:goblin-1"), SequenceRng([15, 7]),
    )
    assert resolved["ok"] is False
    assert "different target" in str(resolved.get("error"))


def test_a_target_too_far_from_the_original_is_rejected() -> None:
    engine, instance = _two_goblins(gap=10)
    _apply(engine, instance, _attack_intent(instance), [15, 7, 5])
    resolved = engine.resolve_intent(instance, _second(engine, instance), SequenceRng([15, 7]))
    assert resolved["ok"] is False
    assert "5 feet of the original" in str(resolved.get("error"))


def test_a_creature_already_attacked_this_turn_is_rejected() -> None:
    """第二目标本回合已经被打过：拒绝，而不是悄悄换一个最近的敌人。"""

    from test_dnd2024_channel_control import _inject

    engine, instance = _two_goblins()
    _apply(engine, instance, _attack_intent(instance), [15, 7, 5])
    # 把"goblin-2 本回合也被打过"写进账本：一次正常攻击付不起第二次动作，
    # 所以直接记一笔攻击记录，被测的仍是 reducer 记账后的校验。
    _inject(engine, instance, [{
        "type": "check.resolved", "kind": "attack",
        "actor_id": "player:gm", "target_id": "enemy:goblin-2",
    }])
    resolved = engine.resolve_intent(instance, _second(engine, instance), SequenceRng([15, 7]))
    assert resolved["ok"] is False
    assert "attacked this turn" in str(resolved.get("error"))


def test_the_option_belongs_to_one_hunter_only() -> None:
    sheet = _hunter("colossus_slayer")
    engine, instance = _combat(
        {"gm": sheet}, {"gm": 20},
        enemies=[goblin(position=20, hp=40),
                 {**goblin(position=25, hp=40), "id": "goblin-2", "name": "Goblin 2"}],
    )
    _apply(engine, instance, _attack_intent(instance), [15, 7, 5])
    resolved = engine.resolve_intent(instance, _second(engine, instance), SequenceRng([15, 7]))
    assert resolved["ok"] is False
    assert "no horde breaker" in str(resolved.get("error"))


def test_the_attack_record_survives_a_save_and_load() -> None:
    """Horde Breaker 的前提是"本回合打过原目标"，它在存档往返后必须还在。

    这两份账本（``attack_history`` / ``mark_damage_history``）是按回合号记账的，
    读档后如果丢了，"第二击"就会莫名其妙地非法——所以这里验的是读档之后
    同一回合还能打，而下一回合不行。
    """

    engine, instance = _two_goblins()
    _apply(engine, instance, _attack_intent(instance), [15, 7, 5])
    assert engine.attacked_this_turn(_combat_state(instance), "player:gm") == {
        "enemy:goblin-1",
    }

    restored = _reloaded(instance)
    combat = _combat_state(restored)
    assert engine.attacked_this_turn(combat, "player:gm") == {"enemy:goblin-1"}

    # 读档后仍然合法：记录随存档走了。
    events = _apply(engine, restored, _second(engine, restored), [15, 7, 5])
    assert [e["feature_id"] for e in events if e["type"] == "dnd2024.feature.used"] == [
        "horde_breaker",
    ]


def test_the_mark_damage_record_survives_a_save_and_load() -> None:
    """Superior Prey 同样读账本，读档后不能把"已经打过的标记伤害"忘掉。"""

    from test_dnd2024_superior_prey import _hunter as _prey_hunter, _marked_and_hit
    from test_dnd2024_hunters_mark import _mark_intent  # noqa: F401  (夹具同源)

    engine, instance = _combat(
        {"gm": _prey_hunter()}, {"gm": 20},
        enemies=[goblin(position=20, hp=60),
                 {**goblin(position=25, hp=60), "id": "goblin-2", "name": "Goblin 2"}],
    )
    _marked_and_hit(engine, instance)
    assert engine.mark_damage_this_turn(
        _combat_state(instance), "player:gm", "enemy:goblin-1",
    )

    restored = _reloaded(instance)
    assert engine.mark_damage_this_turn(
        _combat_state(restored), "player:gm", "enemy:goblin-1",
    )
