"""S0-F19 (part 3): Hunter's Lore hands the marker the target's real defences.

SRD p61: while a creature is marked, "you know whether that creature has any
Immunities, Resistances, or Vulnerabilities, and if the creature has any, you
know what they are".  Two consequences drive the shape of this test file:

* the answer is *specific lists*, never a boolean -- "has resistances" is not
  something a player can act on;
* it belongs to the marking character alone, so it is projected onto their own
  view instead of being emitted into the shared combat feed.

The data comes from the enemy stat block (S0-MM1), which is the only reason
this part could be written at all.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from dnd2024_monk_common import goblin

from test_dnd2024_hunters_mark import (
    _apply,
    _combat,
    _combat_state,
    _mark_intent,
    ranger,
)

HUNTER = "subclass:hunter"

DEFENCED_GOBLIN = {
    **goblin(position=20, hp=40),
    "resist_damage_types": ["fire", "cold"],
    "immune_damage_types": ["poison"],
    "vulnerable_damage_types": ["radiant"],
    "condition_immunities": ["charmed", "frightened"],
}


def _hunter_ranger(level: int = 3) -> dict:
    sheet = ranger(level)
    sheet["ruleset_character"].setdefault("features", {})["subclass_ref"] = HUNTER
    return sheet


def _start() -> tuple[object, object]:
    return _combat({"gm": _hunter_ranger()}, {"gm": 20}, enemies=[deepcopy(DEFENCED_GOBLIN)])


def _view(engine, instance, actor_id: str = "player:gm") -> dict:
    return engine._actor_view(instance, _combat_state(instance), actor_id)


def test_a_marked_target_reveals_its_three_lists() -> None:
    engine, instance = _start()
    _apply(engine, instance, _mark_intent())
    lore = _view(engine, instance)["hunter_lore"]
    assert lore["target_id"] == "enemy:goblin-1"
    assert lore["immunities"] == {"damage": ["poison"], "condition": ["charmed", "frightened"]}
    assert lore["resistances"] == {"damage": ["fire", "cold"], "condition": []}
    assert lore["vulnerabilities"] == {"damage": ["radiant"], "condition": []}


def test_without_a_mark_there_is_no_lore() -> None:
    engine, instance = _start()
    assert "hunter_lore" not in _view(engine, instance)


def test_a_ranger_without_the_subclass_learns_nothing() -> None:
    sheet = ranger(3)
    sheet["ruleset_character"].setdefault("features", {})["subclass_ref"] = "subclass:beast_master"
    engine, instance = _combat({"gm": sheet}, {"gm": 20}, enemies=[deepcopy(DEFENCED_GOBLIN)])
    _apply(engine, instance, _mark_intent())
    assert "hunter_lore" not in _view(engine, instance)


def test_the_lists_are_specific_and_never_booleans() -> None:
    engine, instance = _start()
    _apply(engine, instance, _mark_intent())
    lore = _view(engine, instance)["hunter_lore"]
    for group in ("immunities", "resistances", "vulnerabilities"):
        assert isinstance(lore[group], dict)
        assert isinstance(lore[group]["damage"], list)
        assert isinstance(lore[group]["condition"], list)
        assert all(isinstance(entry, str) for entry in lore[group]["damage"])


def test_an_enemy_with_no_defences_reports_empty_lists_not_silence() -> None:
    """清白的敌人也要有答案："没有"和"不知道"必须能区分。"""

    engine, instance = _combat(
        {"gm": _hunter_ranger()}, {"gm": 20},
        enemies=[goblin(position=20, hp=40)],
    )
    _apply(engine, instance, _mark_intent())
    lore = _view(engine, instance)["hunter_lore"]
    assert lore["immunities"]["damage"] == []
    assert lore["resistances"]["damage"] == []
    assert lore["vulnerabilities"]["damage"] == []


def test_the_defence_lists_never_enter_the_shared_feed() -> None:
    """情报是投影，不是事件：标记的结算批里不该出现任何一项防御列表。"""

    engine, instance = _start()
    intent = {"intent_id": "mark", "type": "cast_spell", "submitted_by": "gm",
              **_mark_intent()}
    events = _apply(engine, instance, _mark_intent())
    payload = str(events)
    for leaked in ("poison", "radiant", "charmed"):
        assert leaked not in payload
    # 敌方自己的视图也不带 Lore：它属于标记持有者。
    assert "hunter_lore" not in _view(engine, instance, "enemy:goblin-1")


def test_lore_follows_the_current_mark_only() -> None:
    engine, instance = _start()
    _apply(engine, instance, _mark_intent())
    first = _view(engine, instance)["hunter_lore"]["target_id"]
    assert first == "enemy:goblin-1"
    # 投影是每次读取时按当前专注现算的：没有缓存，也就不存在"上一次的答案"。
    _apply(engine, instance, {
        "intent_id": "turn", "type": "end_turn", "submitted_by": "gm",
        "actor_id": "player:gm",
    })
    still = _view(engine, instance).get("hunter_lore")
    assert still is None or still["target_id"] == first
