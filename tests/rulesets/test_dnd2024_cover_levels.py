"""C2A: cover is a level the table declares, not a bonus someone computes.

"Half cover" is a judgement made at the table -- a GM says the goblin is
behind the crate -- and every consumer downstream (AC, Dexterity saves, "did
you end your turn behind three-quarters cover") has to agree on it.  These
tests drive the declaration path and the consumers that read it.
"""

from __future__ import annotations

from src.rulesets.dnd2024.combat.cover import (
    COVER_LEVELS,
    best_cover,
    cover_axis,
    cover_bonus,
    cover_rank,
    cover_row,
)
from src.engine.modules import ruleset_runtime

from dnd2024_monk_common import SequenceRng
from test_dnd2024_combat import _instance, _start


def _state(instance):
    return ruleset_runtime.state(instance)


def _combat():
    engine, instance = _instance()
    _start(engine, instance)
    return engine, instance


def _apply(engine, instance, resolved: dict) -> list[dict]:
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _declare(engine, instance, level: str, target_id: str = "enemy:goblin-1",
             submitted_by: str = "gm") -> dict:
    state = _state(instance)
    return engine.resolve_intent(instance, {
        "intent_id": f"cover-{level}-{state['version']}-{submitted_by}",
        "type": "combat.set_cover",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": submitted_by,
        "target_id": target_id,
        "level": level,
    }, SequenceRng([]))


def _attack(engine, instance):
    state = _state(instance)
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"attack-{state['version']}",
        "type": "attack",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm",
        "actor_id": "player:gm",
        "target_id": "enemy:goblin-1",
        "weapon_ref": "item:greatsword",
    }, SequenceRng([15, 5]))
    assert resolved["ok"] is True, resolved
    engine.apply_batch(instance, resolved["event_batch"])
    return resolved["event_batch"]["events"]


# ---------------------------------------------------------------------------
# 1-3. the ladder, and it never stacks
# ---------------------------------------------------------------------------

def test_the_ladder_and_its_bonuses() -> None:
    assert COVER_LEVELS == ("none", "half", "three_quarters", "total")
    assert [cover_bonus(level) for level in COVER_LEVELS] == [0, 2, 5, 5]
    assert cover_rank("three_quarters") > cover_rank("half") > cover_rank("none")
    assert cover_rank("almost") == 0  # 未知等级按 none，不炸
    assert cover_row("half", ["gm"]) == {
        "level": "half", "armor_class_bonus": 2, "dex_save_bonus": 2,
        "sources": ["gm"],
    }


def test_cover_never_stacks() -> None:
    assert best_cover(["half", "three_quarters", "none"]) == "three_quarters"
    assert best_cover([]) == "none"
    assert best_cover(["total", "three_quarters"]) == "total"


def test_the_effect_axis_has_three_ways_to_say_ignored() -> None:
    assert cover_axis({}) == ("horizontal", "")
    assert cover_axis({"cover_interaction": "horizontal"}) == ("horizontal", "")
    for reason in ("above", "ground", "explicit"):
        assert cover_axis({
            "cover_interaction": {"mode": "ignored", "reason": reason},
        }) == ("ignored", reason)
    assert cover_axis({"cover_interaction": "sideways"}) == ("horizontal", "")


# ---------------------------------------------------------------------------
# 4-6. declaration: state, view, GM-only
# ---------------------------------------------------------------------------

def test_declaration_round_trips_into_state_and_view() -> None:
    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, "three_quarters"))
    combat = _state(instance)["combat"]
    assert combat["cover"]["enemy:goblin-1"]["level"] == "three_quarters"
    assert engine.cover_level(instance, combat, "enemy:goblin-1") == "three_quarters"
    assert engine._cover_record(
        instance, combat, {"actor_id": "enemy:goblin-1", "side": "enemy"},
    ) == {
        "level": "three_quarters", "armor_class_bonus": 5,
        "dex_save_bonus": 5, "sources": ["gm:gm"],
    }


def test_cancelling_a_declaration_removes_the_key() -> None:
    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, "half"))
    _apply(engine, instance, _declare(engine, instance, "none"))
    combat = _state(instance)["combat"]
    assert "enemy:goblin-1" not in combat["cover"]
    assert engine.cover_level(instance, combat, "enemy:goblin-1") == "none"


def test_only_the_gm_may_declare_and_only_real_levels() -> None:
    engine, instance = _combat()
    assert _declare(engine, instance, "almost")["ok"] is False
    assert _declare(engine, instance, "half", target_id="enemy:nope")["ok"] is False
    instance.players["someone"] = instance.players["gm"]
    assert _declare(engine, instance, "half", submitted_by="someone")["ok"] is False
    assert _declare(engine, instance, "half")["ok"] is True


# ---------------------------------------------------------------------------
# 7-9. consumers
# ---------------------------------------------------------------------------

def test_cover_raises_the_armor_class_of_an_attack() -> None:
    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, "three_quarters"))
    attack = next(
        event for event in _attack(engine, instance)
        if event["type"] == "check.resolved" and event.get("kind") == "attack"
    )
    assert attack["target"] == 12 + 5  # 默认敌人 AC 12


def test_an_undeclared_target_keeps_todays_numbers() -> None:
    engine, instance = _combat()
    attack = next(
        event for event in _attack(engine, instance)
        if event["type"] == "check.resolved" and event.get("kind") == "attack"
    )
    assert attack["target"] == 12
    assert engine.cover_level(instance, _state(instance)["combat"],
                              "enemy:goblin-1") == "none"


def test_the_aurora_path_still_answers_its_own_level() -> None:
    """没有声明时，光环那一路仍按加值反推等级（+2 → half）。"""

    engine, instance = _combat()
    combat = _state(instance)["combat"]
    row = engine._cover_record(
        instance, combat, {"actor_id": "player:gm", "side": "party"},
    )
    assert row["level"] in COVER_LEVELS
    assert row["armor_class_bonus"] == 0  # 本局没有圣武士灵光


# ---------------------------------------------------------------------------
# 10-12. lifecycle and recording
# ---------------------------------------------------------------------------

def test_a_new_combat_starts_without_cover() -> None:
    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, "half"))
    state = _state(instance)
    _apply(engine, instance, engine.resolve_intent(instance, {
        "intent_id": "end-1", "type": "combat.end",
        "expected_version": int(state["version"]),
        "submitted_by": "gm",
    }, SequenceRng([])))
    from test_dnd2024_combat import _goblin

    state = _state(instance)
    _apply(engine, instance, engine.resolve_intent(instance, {
        "intent_id": "start-again", "type": "combat.start",
        "expected_version": int(state["version"]),
        "submitted_by": "gm", "enemies": [_goblin()],
    }, SequenceRng([20, 1])))
    assert _state(instance)["combat"].get("cover") == {}


def test_the_declaration_is_a_recorded_event_not_hidden_state() -> None:
    engine, instance = _combat()
    events = _apply(engine, instance, _declare(engine, instance, "half"))
    declared = [event for event in events if event["type"] == "dnd2024.cover.declared"]
    assert len(declared) == 1
    assert declared[0]["target_id"] == "enemy:goblin-1"
    assert declared[0]["level"] == "half"
    assert declared[0]["source"] == "gm"


def test_a_declaration_survives_until_it_is_changed() -> None:
    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, "half"))
    _apply(engine, instance, _declare(engine, instance, "three_quarters"))
    assert engine.cover_level(
        instance, _state(instance)["combat"], "enemy:goblin-1",
    ) == "three_quarters"
