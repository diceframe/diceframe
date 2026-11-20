"""S0-F19 (part 5a): Superior Hunter's Prey moves the mark's die to a second creature.

"Once per turn when you deal damage to a creature marked by your Hunter's Mark,
you can also deal that spell's extra damage to a different creature you can see
within 30 feet of the first."

Three things follow from that sentence and are pinned below: what qualifies as
"dealt damage" (the log, not the caller's claim), what the second creature
takes (one die of the *mark*, resisted on its own), and what it does not take
(the weapon's dice, the ability modifier, or the first target's total).
"""

from __future__ import annotations

import pytest

from dnd2024_monk_common import SequenceRng, goblin

from test_dnd2024_hunters_mark import (
    _apply,
    _attack_intent,
    _combat,
    _combat_state,
    _mark_intent,
    ranger,
)

HUNTER = "subclass:hunter"


def _hunter(level: int = 11, option: str = "colossus_slayer") -> dict:
    sheet = ranger(level)
    features = sheet["ruleset_character"].setdefault("features", {})
    features["subclass_ref"] = HUNTER
    features["feature_choices"] = [{
        "owner": HUNTER, "feature_id": "hunters_prey",
        "choice_id": "hunters_prey_option", "selected_refs": [f"feature:{option}"],
    }]
    return sheet


def _pair(gap: int = 20) -> tuple[object, object]:
    first = goblin(position=20, hp=60)
    second = {**goblin(position=20 + gap, hp=60), "id": "goblin-2", "name": "Goblin 2"}
    return _combat({"gm": _hunter()}, {"gm": 20}, enemies=[first, second])


def _prey(engine, instance, *, original: str = "enemy:goblin-1",
          second: str = "enemy:goblin-2") -> dict:
    from src.engine.modules import ruleset_runtime

    return {
        "intent_id": f"prey-{ruleset_runtime.state(instance)['version']}",
        "type": "superior_hunters_prey",
        "expected_version": int(ruleset_runtime.state(instance).get("version", 0)),
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": original, "second_target_id": second,
    }


def _marked_and_hit(engine, instance) -> None:
    _apply(engine, instance, _mark_intent())
    _apply(engine, instance, _attack_intent(instance), [15, 7, 5, 4])


def test_it_needs_mark_damage_dealt_this_turn() -> None:
    engine, instance = _pair()
    _apply(engine, instance, _mark_intent())
    resolved = engine.resolve_intent(instance, _prey(engine, instance), SequenceRng([]))
    assert resolved["ok"] is False
    assert "mark damage" in str(resolved.get("error"))


def test_the_second_creature_takes_one_mark_die() -> None:
    engine, instance = _pair()
    _marked_and_hit(engine, instance)
    before = _combat_state(instance)["enemies"]["goblin-2"]["hp"]
    events = _apply(engine, instance, _prey(engine, instance), [6])
    after = _combat_state(instance)["enemies"]["goblin-2"]["hp"]
    assert before - after == 6
    hit = next(
        event for event in events
        if event.get("feature_id") == "superior_hunters_prey"
        and event["type"] == "resource.changed"
    )
    assert hit["damage_type"] == "force"
    assert hit["rolls"] == [6]
    assert hit["second_target_of"] == "enemy:goblin-1"


def test_it_is_spent_once_per_turn() -> None:
    engine, instance = _pair()
    _marked_and_hit(engine, instance)
    _apply(engine, instance, _prey(engine, instance), [6])
    resolved = engine.resolve_intent(instance, _prey(engine, instance), SequenceRng([6]))
    assert resolved["ok"] is False
    assert "already used this turn" in str(resolved.get("error"))


def test_the_second_target_cannot_be_the_marked_creature() -> None:
    engine, instance = _pair()
    _marked_and_hit(engine, instance)
    resolved = engine.resolve_intent(
        instance, _prey(engine, instance, second="enemy:goblin-1"), SequenceRng([6]),
    )
    assert resolved["ok"] is False
    assert "different target" in str(resolved.get("error"))


def test_a_target_beyond_thirty_feet_is_rejected() -> None:
    engine, instance = _pair(gap=35)
    _marked_and_hit(engine, instance)
    resolved = engine.resolve_intent(instance, _prey(engine, instance), SequenceRng([6]))
    assert resolved["ok"] is False
    assert "30 feet" in str(resolved.get("error"))


def test_the_marked_creature_must_be_the_current_mark() -> None:
    engine, instance = _pair()
    _marked_and_hit(engine, instance)
    resolved = engine.resolve_intent(
        instance,
        _prey(engine, instance, original="enemy:goblin-2", second="enemy:goblin-1"),
        SequenceRng([6]),
    )
    assert resolved["ok"] is False
    assert "marked by your Hunter's Mark" in str(resolved.get("error"))


def test_a_ranger_below_eleven_does_not_have_it() -> None:
    engine, instance = _combat(
        {"gm": _hunter(level=9)}, {"gm": 20},
        enemies=[goblin(position=20, hp=60),
                 {**goblin(position=40, hp=60), "id": "goblin-2", "name": "Goblin 2"}],
    )
    _marked_and_hit(engine, instance)
    resolved = engine.resolve_intent(instance, _prey(engine, instance), SequenceRng([6]))
    assert resolved["ok"] is False
    assert "no superior hunter's prey" in str(resolved.get("error"))
