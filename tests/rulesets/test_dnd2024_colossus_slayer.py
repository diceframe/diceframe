"""S0-F19 (part 4a): Colossus Slayer adds its die once per turn, only to a wounded target.

The SRD states the predicate in one sentence -- "if it's missing any of its Hit
Points" -- and adds a budget ("only once per turn").  Both are checked here
against the events the engine actually emits, not against a helper's return
value, because the interesting failures are ordering ones: a hit that a
reaction turns into a miss must not spend the budget, and a full-HP target must
not get the die even though the same weapon hit it.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from dnd2024_monk_common import goblin

from test_dnd2024_hunters_mark import _apply, _attack, _combat, ranger

HUNTER = "subclass:hunter"


def _hunter(level: int = 3, option: str = "colossus_slayer") -> dict:
    sheet = ranger(level)
    features = sheet["ruleset_character"].setdefault("features", {})
    features["subclass_ref"] = HUNTER
    features["feature_choices"] = [{
        "owner": HUNTER, "feature_id": "hunters_prey",
        "choice_id": "hunters_prey_option", "selected_refs": [f"feature:{option}"],
    }]
    return sheet


def _fight(option: str = "colossus_slayer", *, wounded: bool = True) -> tuple[object, object]:
    from test_dnd2024_hunters_mark import _combat_state

    engine, instance = _combat(
        {"gm": _hunter(3, option)}, {"gm": 20},
        enemies=[goblin(position=20, hp=40)],
    )
    enemy = _combat_state(instance)["enemies"]["goblin-1"]
    # "missing any of its Hit Points" 是命中前的状态，所以这里直接构造两种局面：
    # 已受伤（max 高于当前）与满血（两者相等）。
    enemy["max_hp"] = 40 if not wounded else 60
    enemy["hp"] = 40
    return engine, instance


def _hp_events(events: list[dict]) -> list[dict]:
    return [
        event for event in events
        if event.get("type") == "resource.changed" and event.get("resource") == "hp"
        and int(event.get("delta", 0) or 0) < 0
    ]


def _dice_count(events: list[dict]) -> int:
    count = 0
    for event in _hp_events(events):
        for roll in event.get("rolls") or []:
            count += 1
    return count


def test_a_wounded_target_takes_the_extra_die() -> None:
    engine, instance = _fight()
    from dnd2024_monk_common import SequenceRng
    from test_dnd2024_hunters_mark import _combat_state

    actor = engine._actor_view(instance, _combat_state(instance), "player:gm")
    assert "colossus_slayer" in {
        feature.id for feature in engine.features.features_for(actor)
    }, "the option is not owned, so nothing below would be testing the rider"

    events = _attack(engine, instance, rolls=(15, 7, 6))
    # longbow 1d8 + 巨兽杀手 1d8 = 两枚骰
    assert _dice_count(events) == 2
    used = [event for event in events if event.get("type") == "dnd2024.feature.used"]
    assert [event["feature_id"] for event in used] == ["colossus_slayer"]


def test_a_full_health_target_takes_only_the_weapon_die() -> None:
    engine, instance = _fight(wounded=False)
    events = _attack(engine, instance, rolls=(15, 7))
    assert _dice_count(events) == 1
    assert not [e for e in events if e.get("feature_id") == "colossus_slayer"]


def test_the_die_is_spent_once_per_turn() -> None:
    """本回合已经用过一次，同一回合的第二击不再加骰。

    这里预先记账而不是连打两下：一个 3 级游侠每回合只有一次攻击动作，
    "第二次攻击"要靠加速/多重攻击才存在，而那条路径与本条断言无关。
    """

    from test_dnd2024_channel_control import _inject

    from test_dnd2024_hunters_mark import _combat_state

    # 对照组：同一个局面下不预记账就应该是两枚骰。缺了它，这条用例在
    # 特性被整个关掉时也会"通过"——那正是 #807 被指出的安慰剂测试。
    control_engine, control_instance = _fight()
    assert _dice_count(_attack(control_engine, control_instance, rolls=(15, 7, 6))) == 2

    engine, instance = _fight()
    combat = _combat_state(instance)
    _inject(engine, instance, [{
        "type": "dnd2024.feature.used", "actor_id": "player:gm",
        "feature_id": "colossus_slayer",
        "round": int(combat.get("round", 1)), "turn_index": int(combat.get("turn_index", 0)),
    }])
    events = _attack(engine, instance, rolls=(15, 7))
    assert _dice_count(events) == 1
    assert not [
        event for event in events
        if event["type"] == "dnd2024.feature.used"
        and event.get("feature_id") == "colossus_slayer"
    ]


def test_a_hunter_who_chose_horde_breaker_gets_no_die() -> None:
    engine, instance = _fight(option="horde_breaker")
    events = _attack(engine, instance, rolls=(15, 7))
    assert _dice_count(events) == 1
