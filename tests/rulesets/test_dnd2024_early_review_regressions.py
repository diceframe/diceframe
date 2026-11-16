"""Cross-feature regressions from the early D&D stack review (F3--F5)."""
from copy import deepcopy

import pytest

import test_dnd2024_failed_d20_windows as f
import test_dnd2024_paladin_auras as pa
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


def test_successful_hide_decline_settles_and_unblocks_next_intent():
    runtime = Dnd2024Runtime()
    engine, instance = f._combat(runtime, {"gm": f._warlock(runtime, 6)}, rolls=[20, 1])
    f._hide(engine, instance, rolls=[20, 20])
    decision = deepcopy(f._pending(instance))
    assert decision["window_code"] == "dark_ones_own_luck_check"
    before = f._pool(instance, "dark_ones_own_luck")
    result = f._decide(engine, instance, decision, "decline")
    assert result["ok"], result
    engine.apply_batch(instance, result["event_batch"])
    assert f._pending(instance) is None
    assert "invisible" in f._canonical(instance)["conditions"]
    assert f._pool(instance, "dark_ones_own_luck") == before
    f._end_turn(engine, instance, "player:gm")


def test_last_stroke_decline_does_not_regenerate_candidates():
    runtime = Dnd2024Runtime()
    engine, instance = f._combat(runtime, {"gm": f._rogue(runtime, 20)}, rolls=[20, 1])
    combat = f._state(instance)["combat"]
    actor = engine._actor_view(instance, combat, "player:gm")
    check = {"type": "check.resolved", "kind": "hide", "actor_id": "player:gm",
             "rolls": [10], "natural": 10, "modifier": 5, "total": 15,
             "target": 25, "success": False}
    decision = engine._ability_check_window_offer(
        instance, combat, actor, check, {"kind": "hide", "intent_id": "high-dc"},
    )
    assert decision["window_code"] == "stroke_of_luck_check"
    assert decision["remaining_windows"] == []
    # A pre-fix persisted pending window must terminate too.
    decision["settlement"].pop("ability_offered", None)
    events = engine._finish_check_window(
        instance, combat, decision, {"option": "decline"}, f.SequenceRng([]),
    )
    assert not any(e["type"] == "dnd2024.decision.pending" for e in events)


def test_failed_refunded_rescue_is_not_offered_again():
    runtime = Dnd2024Runtime()
    engine, instance = f._combat(runtime, {"gm": f._fighter(runtime, 2)}, rolls=[20, 1])
    before = f._pool(instance, "second_wind")
    f._hide(engine, instance, rolls=[2, 2])
    result = f._decide(engine, instance, f._pending(instance), "spend", [1])
    assert result["ok"], result
    engine.apply_batch(instance, result["event_batch"])
    assert f._pending(instance) is None
    assert f._pool(instance, "second_wind") == before
    assert "invisible" not in f._canonical(instance).get("conditions", {})


@pytest.mark.parametrize("option", ["decline", "spend"])
def test_natural_one_with_bless_and_aura_still_opens_stroke(option):
    runtime = Dnd2024Runtime()
    paladin = pa.paladin_sheet(runtime, level=6)
    paladin["ruleset_character"]["abilities"]["cha"] = 20
    engine, instance = f._combat(
        runtime, {"gm": f._rogue(runtime, 20), "paladin": paladin}, rolls=[20, 1, 1],
    )
    character = f._canonical(instance)
    character["resources"]["hp"] = 0
    character.setdefault("conditions", {}).update({
        "unconscious": {"source": "zero_hp"}, "blessed": {"duration": "combat"},
    })
    result = engine.resolve_intent(instance, {
        "intent_id": "death", "type": "death_save", "submitted_by": "gm",
        "actor_id": "player:gm", "expected_version": f._state(instance)["version"],
    }, f.SequenceRng([1, 4]))
    assert result["ok"], result
    engine.apply_batch(instance, result["event_batch"])
    decision = f._pending(instance)
    assert decision["window_code"] == "stroke_of_luck_save"
    assert decision["check"]["total"] == 10
    assert not decision["check"]["success"]
    result = f._decide(engine, instance, decision, option)
    assert result["ok"], result
    engine.apply_batch(instance, result["event_batch"])
    assert f._pending(instance) is None
    event = next(e for e in f._events(result) if e["type"] == "dnd2024.death_save.resolved")
    assert event["failures"] == (2 if option == "decline" else 0)
    assert event["hp"] == (0 if option == "decline" else 1)


@pytest.mark.parametrize(("level", "modifier", "total", "success"), [
    (13, 0, 5, False), (14, 5, 10, True),
])
def test_only_all_save_proficiency_adds_pb_to_death_saves(level, modifier, total, success):
    runtime = Dnd2024Runtime()
    engine, instance = f._combat(runtime, {"gm": f._monk(runtime, level)}, rolls=[20, 1])
    character = f._canonical(instance)
    character["resources"]["hp"] = 0
    character.setdefault("conditions", {})["unconscious"] = {"source": "zero_hp"}
    f._set_pool(instance, "focus_points", 0)
    result = engine.resolve_intent(instance, {
        "intent_id": "death", "type": "death_save", "submitted_by": "gm",
        "actor_id": "player:gm", "expected_version": f._state(instance)["version"],
    }, f.SequenceRng([5]))
    assert result["ok"], result
    engine.apply_batch(instance, result["event_batch"])
    check = f._checked(f._events(result), "death_save")
    assert (check["modifier"], check["total"], check["success"]) == (modifier, total, success)
    tally = f._canonical(instance)["conditions"]["death_saves"]
    assert (tally["successes"], tally["failures"]) == ((1, 0) if success else (0, 1))
