"""S0-F19 (part 6): the Hunter's Defensive Tactics options actually bite.

Both options were declared, selectable and projected -- and had no reader:
`escape_the_horde` and `multiattack_defense` appeared only in the feature
catalog and in the option list, so choosing either changed nothing.  Neither is
a condition ("this creature is frightened"); the first is a fact about *which
roll* is being made, the second a fact about *who already hit whom this turn*.
So both are synthesised as attack sources instead of being written onto either
creature, and the second rides the shared ``attack_marks`` store.
"""

from __future__ import annotations

from dnd2024_monk_common import SequenceRng, goblin

from test_dnd2024_hunters_mark import (
    _apply, _combat, _combat_state, _to_actor, ranger,
)

HUNTER = "subclass:hunter"


def _hunter(level: int, option: str) -> dict:
    sheet = ranger(level)
    features = sheet["ruleset_character"].setdefault("features", {})
    features["subclass_ref"] = HUNTER
    features["feature_choices"] = [{
        "owner": HUNTER, "feature_id": "defensive_tactics",
        "choice_id": "defensive_tactics_option",
        "selected_refs": [f"feature:{option}"],
    }]
    return sheet


def _fight(option: str, level: int = 7):
    return _combat(
        {"gm": _hunter(level, option)}, {"gm": 1},
        enemies=[goblin(position=5, hp=40)],
    )


def _mode(engine, instance, *, opportunity: bool = False) -> dict:
    combat = _combat_state(instance)
    hunter = engine._actor_view(instance, combat, "player:gm")
    attacker = engine._actor_view(instance, combat, "enemy:goblin-1")
    return engine._attack_check_mode(
        instance, combat, attacker, hunter, False,
        ability="dex", declared_reckless=False, opportunity=opportunity,
    )


def _codes(mode: dict) -> set[str]:
    return {str(row["code"]) for row in mode["sources"]}


# --------------------------------------------------------------------------
# Escape the Horde：只有借机攻击吃劣势
# --------------------------------------------------------------------------


def test_escape_the_horde_disadvantages_opportunity_attacks() -> None:
    engine, instance = _fight("escape_the_horde")
    _to_actor(engine, instance, "enemy:goblin-1")

    mode = _mode(engine, instance, opportunity=True)

    assert "escape_the_horde" in _codes(mode)
    assert mode["mode"] == "disadvantage"


def test_escape_the_horde_leaves_ordinary_attacks_alone() -> None:
    engine, instance = _fight("escape_the_horde")
    _to_actor(engine, instance, "enemy:goblin-1")

    mode = _mode(engine, instance)

    assert "escape_the_horde" not in _codes(mode)
    assert mode["mode"] == ""


def test_the_other_option_does_not_disadvantage_opportunity_attacks() -> None:
    engine, instance = _fight("multiattack_defense")
    _to_actor(engine, instance, "enemy:goblin-1")

    mode = _mode(engine, instance, opportunity=True)

    assert "escape_the_horde" not in _codes(mode)


def test_an_opportunity_attack_against_the_holder_rolls_with_disadvantage() -> None:
    """End-to-end: the move → decision → reaction attack chain carries the flag."""

    engine, instance = _fight("escape_the_horde")
    _to_actor(engine, instance, "player:gm")

    moved = _apply(engine, instance, {
        "intent_id": "move-1", "type": "move", "submitted_by": "gm",
        "actor_id": "player:gm", "distance": -10,
    })
    decision = next(
        row for row in moved if row["type"] == "dnd2024.decision.pending"
    )
    decided = _apply(engine, instance, {
        "intent_id": "decision-1", "type": "decision.resolve", "submitted_by": "gm",
        "decision_id": decision["decision_id"], "option": "resolve",
    }, rolls=(15, 4))

    check = next(row for row in decided if row["type"] == "check.resolved")
    assert check["advantage_mode"] == "disadvantage"
    assert "escape_the_horde" in {
        str(row["code"]) for row in check["advantage_sources"]
    }


# --------------------------------------------------------------------------
# Multiattack Defense：命中之后，同一攻击者本回合后续攻击吃劣势
# --------------------------------------------------------------------------


def _goblin_attack(engine, instance, *, rolls=(15, 4)) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": "goblin-attack", "type": "attack", "submitted_by": "gm",
        "actor_id": "enemy:goblin-1", "target_id": "player:gm",
        "attack_id": "scimitar",
    }, rolls)


def test_the_first_hit_is_not_retroactively_disadvantaged() -> None:
    engine, instance = _fight("multiattack_defense")
    _to_actor(engine, instance, "enemy:goblin-1")

    events = _goblin_attack(engine, instance)
    check = next(row for row in events if row["type"] == "check.resolved")

    assert check["success"] is True
    assert "multiattack_defense" not in {
        str(row["code"]) for row in check["advantage_sources"]
    }


def test_the_same_attacker_gets_disadvantage_after_hitting() -> None:
    engine, instance = _fight("multiattack_defense")
    _to_actor(engine, instance, "enemy:goblin-1")
    _goblin_attack(engine, instance)

    mode = _mode(engine, instance)

    assert "multiattack_defense" in _codes(mode)
    assert mode["mode"] == "disadvantage"


def test_the_mark_does_not_survive_the_turn() -> None:
    engine, instance = _fight("multiattack_defense")
    _to_actor(engine, instance, "enemy:goblin-1")
    _goblin_attack(engine, instance)

    _to_actor(engine, instance, "player:gm")

    assert "multiattack_defense" not in _codes(_mode(engine, instance))


def test_a_hunter_who_chose_the_other_option_leaves_no_mark() -> None:
    engine, instance = _fight("escape_the_horde")
    _to_actor(engine, instance, "enemy:goblin-1")
    _goblin_attack(engine, instance)

    mode = _mode(engine, instance)

    assert "multiattack_defense" not in _codes(mode)


def test_a_hunter_below_seven_carries_neither_option() -> None:
    engine, instance = _fight("multiattack_defense", level=6)
    _to_actor(engine, instance, "enemy:goblin-1")
    _goblin_attack(engine, instance)

    assert "multiattack_defense" not in _codes(_mode(engine, instance))
    assert "escape_the_horde" not in _codes(_mode(engine, instance, opportunity=True))
