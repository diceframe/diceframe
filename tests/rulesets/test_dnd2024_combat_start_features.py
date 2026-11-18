"""S0-F20: combat start, Initiative choices, and the first-round schedule.

Two things are under test and they never share a mechanism.  The first is the
one-time choice made *when Initiative is rolled* (Uncanny Metabolism, Perfect
Focus, Persistent Rage) plus the always-on Initiative Advantage of Feral
Instinct.  The second is the first-round turn schedule: a Thief takes two
turns, which this slice models as two slots of one actor rather than as two
copies of a character.

Every combat assertion goes through ``resolve_intent`` -> ``apply_batch``, the
same two calls the web layer makes, so a rule that only holds inside the
resolver cannot pass here.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, monk_sheet, with_equipment
from test_dnd2024_survivor import _goblin, _instance, _sheet


@pytest.fixture(scope="module")
def sheets() -> dict[str, dict]:
    """Every level this slice needs, built through the real advancement path."""

    runtime = Dnd2024Runtime()
    return {
        "barbarian6": _sheet(runtime, 6, "relentless_vanguard"),
        "barbarian7": _sheet(runtime, 7, "relentless_vanguard"),
        "barbarian15": _sheet(runtime, 15, "relentless_vanguard"),
        "champion9": _sheet(runtime, 9, "stalwart_guardian"),
        "champion10": _sheet(runtime, 10, "stalwart_guardian"),
        "thief16": _sheet(runtime, 16, "lucky_scout"),
        "thief17": _sheet(runtime, 17, "lucky_scout"),
        "monk1": monk_sheet(runtime, level=1),
        "monk2": monk_sheet(runtime, level=2),
        "monk14": monk_sheet(runtime, level=14),
        "monk15": monk_sheet(runtime, level=15),
    }


def _setup(sheet: dict) -> tuple[object, object]:
    return _instance(Dnd2024Runtime().load_bundle("en"), deepcopy(sheet))


def _with_ally(sheet: dict, ally: dict) -> tuple[object, object]:
    """The same combat with a second player, so losing one is not a wipe."""

    engine, instance = _setup(sheet)
    instance.players["ally"] = {
        "character_name": "Bryn", "character_sheet": deepcopy(ally),
    }
    return engine, instance


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _start(engine, instance, rolls, **extra) -> list[dict]:
    intent = {
        "intent_id": extra.pop("intent_id", "start-1"),
        "type": "combat.start",
        "expected_version": extra.pop(
            "expected_version",
            int(ruleset_runtime.state(instance).get("version", 0) or 0),
        ),
        "submitted_by": "gm", "enemies": [_goblin()],
    }
    intent.update(extra)
    rng = rolls if isinstance(rolls, SequenceRng) else SequenceRng(list(rolls))
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _try_start(engine, instance, rolls, **extra) -> dict:
    intent = {
        "intent_id": extra.pop("intent_id", "start-1"),
        "type": "combat.start",
        "expected_version": extra.pop(
            "expected_version",
            int(ruleset_runtime.state(instance).get("version", 0) or 0),
        ),
        "submitted_by": "gm", "enemies": [_goblin()],
    }
    intent.update(extra)
    rng = rolls if isinstance(rolls, SequenceRng) else SequenceRng(list(rolls))
    return engine.resolve_intent(instance, intent, rng)


def _conditions(instance, uid: str = "gm") -> dict:
    return _canonical(instance, uid).setdefault("conditions", {})


def _combat(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _end_turn(
    engine, instance, actor_id: str, rolls: list[int] | None = None,
    submitted_by: str = "gm",
) -> list[dict]:
    intent = {
        "intent_id": f"end:{actor_id}:{ruleset_runtime.state(instance)['version']}",
        "type": "end_turn", "actor_id": actor_id,
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": submitted_by,
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls or [])))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _resolve_choice(engine, instance, decision_id: str, payload: dict | None = None) -> dict:
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"resolve:{decision_id}",
        "type": "decision.resolve", "decision_id": decision_id,
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", **(payload or {}),
    }, SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]


def _end_combat(engine, instance) -> list[dict]:
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"end-combat:{ruleset_runtime.state(instance)['version']}",
        "type": "combat.end",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
    }, SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _of_type(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event["type"] == kind]


def _current_actor(instance) -> str:
    combat = _combat(instance)
    return str(combat["turn_slots"][int(combat["turn_index"])]["actor_id"])


def _slot_actors(instance) -> list[str]:
    return [str(slot["actor_id"]) for slot in _combat(instance)["turn_slots"]]


def _walk_first_round(engine, instance, limit: int = 12) -> list[list[dict]]:
    """End every turn of the first round, returning each batch in order."""

    batches: list[list[dict]] = []
    for _ in range(limit):
        combat = _combat(instance)
        if int(combat["round"]) > 1 or combat["status"] != "active":
            break
        actor_id = str(combat["turn_slots"][int(combat["turn_index"])]["actor_id"])
        # 真人席位只能由本人提交；GM 只能代 AI 托管席位或敌人提交。
        submitted_by = (
            actor_id.removeprefix("player:") if actor_id.startswith("player:") else "gm"
        )
        batches.append(_end_turn(engine, instance, actor_id, submitted_by=submitted_by))
    return batches


# ---------------------------------------------------------------------------
# 1. the six declarations arrive at their own level
# ---------------------------------------------------------------------------

def test_the_six_features_arrive_at_their_own_level(sheets) -> None:
    resolver = Dnd2024ClassFeatureResolver(Dnd2024Runtime().load_bundle("en"))

    def owns(key: str, feature_id: str) -> bool:
        return resolver.has_feature(sheets[key]["ruleset_character"], feature_id)

    assert owns("barbarian7", "feral_instinct") is True
    assert owns("barbarian6", "feral_instinct") is False
    assert owns("barbarian15", "persistent_rage") is True
    assert owns("barbarian7", "persistent_rage") is False
    assert owns("monk2", "uncanny_metabolism") is True
    assert owns("monk1", "uncanny_metabolism") is False
    assert owns("monk15", "perfect_focus") is True
    assert owns("monk14", "perfect_focus") is False
    assert owns("champion10", "heroic_warrior") is True
    assert owns("champion9", "heroic_warrior") is False
    assert owns("thief17", "thiefs_reflexes") is True
    assert owns("thief16", "thiefs_reflexes") is False


# ---------------------------------------------------------------------------
# 2-3. Feral Instinct: Initiative Advantage, once, from declarations
# ---------------------------------------------------------------------------

def test_feral_instinct_rolls_two_dice_and_keeps_the_higher(sheets) -> None:
    engine, instance = _setup(sheets["barbarian7"])
    # Advantage: the player rolls [7, 18] and keeps 18; the goblin rolls one die.
    events = _start(engine, instance, [7, 18, 5])
    started = _of_type(events, "dnd2024.combat.started")[0]
    row = next(
        item for item in started["initiative_rolls"] if item["actor_id"] == "player:gm"
    )
    assert row["rolls"] == [7, 18]
    assert row["roll"] == 18
    assert row["advantage_mode"] == "advantage"
    assert [source["code"] for source in row["advantage_sources"]] == ["feral_instinct"]
    goblin = next(
        item for item in started["initiative_rolls"] if item["actor_id"] == "enemy:goblin-1"
    )
    # Nothing declared Advantage for the goblin, so it never got a second die.
    assert "rolls" not in goblin


def test_a_second_advantage_source_still_rolls_two_dice(sheets) -> None:
    engine, instance = _setup(sheets["barbarian7"])
    # The Invisible condition also grants Initiative Advantage; two sources must
    # collapse into one mode, never into a third die.
    _conditions(instance)["invisible"] = {"duration": "combat"}
    events = _start(engine, instance, [3, 19, 4])
    started = _of_type(events, "dnd2024.combat.started")[0]
    row = next(
        item for item in started["initiative_rolls"] if item["actor_id"] == "player:gm"
    )
    assert row["rolls"] == [3, 19]
    assert row["advantage_mode"] == "advantage"
    assert sorted(source["code"] for source in row["advantage_sources"]) == [
        "feral_instinct", "invisible",
    ]


def test_a_barbarian_below_seven_rolls_a_single_die(sheets) -> None:
    engine, instance = _setup(sheets["barbarian6"])
    events = _start(engine, instance, [11, 4])
    started = _of_type(events, "dnd2024.combat.started")[0]
    row = next(
        item for item in started["initiative_rolls"] if item["actor_id"] == "player:gm"
    )
    assert "rolls" not in row
    assert row["roll"] == 11


# ---------------------------------------------------------------------------
# 4-6. Uncanny Metabolism
# ---------------------------------------------------------------------------

def test_uncanny_metabolism_refills_focus_heals_and_spends_its_charge(sheets) -> None:
    engine, instance = _setup(sheets["monk2"])
    canonical = _canonical(instance)
    canonical["resources"]["class"]["focus_points"]["current"] = 0
    canonical["resources"]["hp"] = 5
    before_hp = int(canonical["resources"]["hp"])

    events = _start(
        engine, instance, [12, 4, 6],
        initiative_choices={"player:gm": {"uncanny_metabolism": True}},
    )
    started_at = next(
        index for index, event in enumerate(events)
        if event["type"] == "dnd2024.combat.started"
    )
    opening = events[started_at + 1:]

    refill = _of_type(opening, "dnd2024.class_resource.refilled")
    assert [(row["resource_id"], row["target"]) for row in refill] == [("focus_points", 2)]
    healing = next(
        event for event in _of_type(opening, "resource.changed")
        if event.get("feature_id") == "uncanny_metabolism"
    )
    assert healing["healing"] is True
    assert healing["martial_arts_die"] == "1d6"
    assert healing["roll"] == 6
    assert healing["monk_level"] == 2
    assert healing["amount"] == 8
    assert int(_canonical(instance)["resources"]["hp"]) == before_hp + 8
    spent = _of_type(opening, "dnd2024.class_resource.spent")
    assert [(row["resource_id"], row["amount"]) for row in spent] == [
        ("uncanny_metabolism", 1),
    ]
    assert int(
        _canonical(instance)["resources"]["class"]["uncanny_metabolism"]["current"]
    ) == 0


def test_uncanny_metabolism_at_full_focus_still_spends_and_rolls(sheets) -> None:
    engine, instance = _setup(sheets["monk2"])
    canonical = _canonical(instance)
    assert int(canonical["resources"]["class"]["focus_points"]["current"]) == 2
    max_hp = int(canonical["resources"]["max_hp"])
    canonical["resources"]["hp"] = max_hp

    events = _start(
        engine, instance, [12, 4, 3],
        initiative_choices={"player:gm": {"uncanny_metabolism": True}},
    )
    opening = events[
        next(
            index for index, event in enumerate(events)
            if event["type"] == "dnd2024.combat.started"
        ) + 1:
    ]
    healing = next(
        event for event in _of_type(opening, "resource.changed")
        if event.get("feature_id") == "uncanny_metabolism"
    )
    # The roll happened and the charge is gone even though HP could not rise.
    assert healing["roll"] == 3
    assert healing["amount"] == 5
    assert int(_canonical(instance)["resources"]["hp"]) == max_hp
    assert int(
        _canonical(instance)["resources"]["class"]["uncanny_metabolism"]["current"]
    ) == 0


def test_uncanny_metabolism_cannot_regain_hp_still_refills_focus(sheets) -> None:
    engine, instance = _setup(sheets["monk2"])
    canonical = _canonical(instance)
    canonical["resources"]["class"]["focus_points"]["current"] = 0
    _conditions(instance)["cannot_regain_hp"] = {}
    before_hp = int(canonical["resources"]["hp"])

    events = _start(
        engine, instance, [12, 4, 5],
        initiative_choices={"player:gm": {"uncanny_metabolism": True}},
    )
    opening = events[
        next(
            index for index, event in enumerate(events)
            if event["type"] == "dnd2024.combat.started"
        ) + 1:
    ]
    healing = next(
        event for event in _of_type(opening, "resource.changed")
        if event.get("feature_id") == "uncanny_metabolism"
    )
    assert healing["amount"] == 0
    assert healing["blocked_by"] == "cannot_regain_hp"
    assert int(_canonical(instance)["resources"]["hp"]) == before_hp
    assert int(_canonical(instance)["resources"]["class"]["focus_points"]["current"]) == 2
    assert int(
        _canonical(instance)["resources"]["class"]["uncanny_metabolism"]["current"]
    ) == 0


def test_uncanny_metabolism_cannot_be_declared_again_before_a_long_rest(sheets) -> None:
    engine, instance = _setup(sheets["monk2"])
    _start(
        engine, instance, [12, 4, 5],
        initiative_choices={"player:gm": {"uncanny_metabolism": True}},
    )
    # The charge is spent for the rest of the day: the next combat's declaration
    # is a refused intent, not a silently ignored one.  This is also what makes
    # a replay safe -- the second roll cannot regain the Focus a third time.
    _end_combat(engine, instance)
    second = _try_start(
        engine, instance, [10, 3],
        intent_id="start-2",
        initiative_choices={"player:gm": {"uncanny_metabolism": True}},
    )
    assert second["ok"] is False
    assert "already spent" in str(second["error"])


# ---------------------------------------------------------------------------
# 7-8. Perfect Focus
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("current", "expected"), [(0, 4), (3, 4), (4, 0), (5, 0)])
def test_perfect_focus_fills_to_four_only_from_below(sheets, current: int, expected: int) -> None:
    engine, instance = _setup(sheets["monk15"])
    _canonical(instance)["resources"]["class"]["focus_points"]["current"] = current
    rng = SequenceRng([12, 4])
    events = _start(engine, instance, rng)
    opening = events[
        next(
            index for index, event in enumerate(events)
            if event["type"] == "dnd2024.combat.started"
        ) + 1:
    ]
    refills = _of_type(opening, "dnd2024.class_resource.refilled")
    if expected:
        assert [(row["resource_id"], row["target"]) for row in refills] == [
            ("focus_points", expected),
        ]
        assert int(_canonical(instance)["resources"]["class"]["focus_points"]["current"]) == expected
    else:
        assert refills == []
        assert int(_canonical(instance)["resources"]["class"]["focus_points"]["current"]) == current
    # Perfect Focus never rolls and never spends: the Initiative dice are all
    # the RNG this combat consumed.
    assert rng.values == []


def test_perfect_focus_does_not_fire_when_uncanny_was_used(sheets) -> None:
    engine, instance = _setup(sheets["monk15"])
    canonical = _canonical(instance)
    canonical["resources"]["class"]["focus_points"]["current"] = 0
    events = _start(
        engine, instance, [12, 4, 7],
        initiative_choices={"player:gm": {"uncanny_metabolism": True}},
    )
    opening = events[
        next(
            index for index, event in enumerate(events)
            if event["type"] == "dnd2024.combat.started"
        ) + 1:
    ]
    assert [(row["resource_id"], row["target"]) for row in _of_type(
        opening, "dnd2024.class_resource.refilled",
    )] == [("focus_points", 15)]
    assert int(
        _canonical(instance)["resources"]["class"]["uncanny_metabolism"]["current"]
    ) == 0


# ---------------------------------------------------------------------------
# 9. Persistent Rage opening choice
# ---------------------------------------------------------------------------

def test_persistent_rage_refills_every_use_and_spends_its_charge(sheets) -> None:
    engine, instance = _setup(sheets["barbarian15"])
    canonical = _canonical(instance)
    maximum = int(canonical["resources"]["class"]["rages"]["maximum"])
    canonical["resources"]["class"]["rages"]["current"] = 0

    events = _start(
        engine, instance, [15, 4],
        initiative_choices={"player:gm": {"persistent_rage": True}},
    )
    opening = events[
        next(
            index for index, event in enumerate(events)
            if event["type"] == "dnd2024.combat.started"
        ) + 1:
    ]
    assert [(row["resource_id"], row["target"]) for row in _of_type(
        opening, "dnd2024.class_resource.refilled",
    )] == [("rages", maximum)]
    assert [(row["resource_id"], row["amount"]) for row in _of_type(
        opening, "dnd2024.class_resource.spent",
    )] == [("persistent_rage", 1)]
    assert int(_canonical(instance)["resources"]["class"]["rages"]["current"]) == maximum
    assert int(
        _canonical(instance)["resources"]["class"]["persistent_rage"]["current"]
    ) == 0


def test_persistent_rage_untouched_when_not_declared(sheets) -> None:
    engine, instance = _setup(sheets["barbarian15"])
    _canonical(instance)["resources"]["class"]["rages"]["current"] = 0
    _start(engine, instance, [15, 4])
    assert int(_canonical(instance)["resources"]["class"]["rages"]["current"]) == 0
    assert int(
        _canonical(instance)["resources"]["class"]["persistent_rage"]["current"]
    ) == 1


def test_persistent_rage_charge_follows_the_rest_catalogue(sheets) -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    declaration = resolver.resources.declaration("barbarian", "persistent_rage")
    assert declaration is not None
    # A Short Rest never gives it back; only a Long Rest does.
    assert declaration.short_rest == "none"
    assert declaration.long_rest == "all"


# ---------------------------------------------------------------------------
# 10-11. Persistent Rage lifecycle
# ---------------------------------------------------------------------------

def _enter_rage(engine, instance) -> list[dict]:
    resolved = engine.resolve_intent(instance, {
        "intent_id": "rage-1", "type": "class_capability",
        "capability_id": "rage", "actor_id": "player:gm",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
    }, SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def test_level_fifteen_rage_is_recorded_as_persistent(sheets) -> None:
    engine, instance = _setup(sheets["barbarian15"])
    _start(engine, instance, [15, 4])
    applied = next(
        event for event in _enter_rage(engine, instance)
        if event["type"] == "condition.applied"
    )
    assert applied["duration"] == "persistent_rage"
    assert applied["turns_remaining"] == 100


def test_level_seven_rage_keeps_the_old_duration(sheets) -> None:
    engine, instance = _setup(sheets["barbarian7"])
    _start(engine, instance, [7, 18, 4])
    applied = next(
        event for event in _enter_rage(engine, instance)
        if event["type"] == "condition.applied"
    )
    assert applied["duration"] == "rage"


def test_persistent_rage_survives_incapacitation_but_not_unconsciousness(sheets) -> None:
    engine, instance = _setup(sheets["barbarian15"])
    _start(engine, instance, [15, 4])
    _enter_rage(engine, instance)
    canonical = _canonical(instance)
    canonical["conditions"]["incapacitated"] = {"duration": "until_save"}
    _end_turn(engine, instance, "player:gm")
    assert "rage" in _conditions(instance)

    # Unconscious is the condition the 2024 rule names, so it ends the Rage.
    engine2, instance2 = _setup(sheets["barbarian15"])
    _start(engine2, instance2, [15, 4])
    _enter_rage(engine2, instance2)
    _conditions(instance2)["unconscious"] = {"duration": "until_save"}
    _end_turn(engine2, instance2, "player:gm")
    assert "rage" not in _conditions(instance2)


def test_persistent_rage_ends_on_real_heavy_armor(sheets) -> None:
    runtime = Dnd2024Runtime()
    armored = with_equipment(runtime, sheets["barbarian15"], "item:chain_mail")
    engine, instance = _setup(armored)
    _start(engine, instance, [15, 4])
    _enter_rage(engine, instance)
    _end_turn(engine, instance, "player:gm")
    assert "rage" not in _conditions(instance)


def test_persistent_rage_lasts_ninety_nine_rounds_and_expires_on_the_hundredth(sheets) -> None:
    engine, instance = _setup(sheets["barbarian15"])
    _start(engine, instance, [15, 4])
    _enter_rage(engine, instance)
    assert int(_conditions(instance)["rage"]["turns_remaining"]) == 100

    def finish_round() -> None:
        target = int(_combat(instance)["round"]) + 1
        for _ in range(6):
            if int(_combat(instance)["round"]) >= target:
                return
            _end_turn(engine, instance, _current_actor(instance))

    for _ in range(99):
        finish_round()
    assert int(_combat(instance)["round"]) == 100
    # 100 轮是从进入那一刻起算的总时长，不是每回合重置的预算：第 99 轮结束
    # 时只剩 1 轮，第 100 轮结束即到期。
    assert int(_conditions(instance)["rage"]["turns_remaining"]) == 1
    finish_round()
    assert int(_combat(instance)["round"]) == 101
    assert "rage" not in _conditions(instance)


def test_persistent_rage_does_not_end_with_the_combat(sheets) -> None:
    engine, instance = _setup(sheets["barbarian15"])
    _start(engine, instance, [15, 4])
    _enter_rage(engine, instance)
    # Drop the goblin outside the damage pipeline: this test is about the
    # ``combat.ended`` cleanup, not about landing an attack.
    _combat(instance)["enemies"]["goblin-1"]["hp"] = 0
    _end_turn(engine, instance, _current_actor(instance))
    assert _combat(instance)["status"] == "ended"
    assert "rage" in _conditions(instance)


def _heroic_events(events: list[dict]) -> list[dict]:
    return _of_type(events, "dnd2024.heroic_inspiration.changed")


def test_heroic_warrior_grants_at_turn_start_then_stops(sheets) -> None:
    engine, instance = _setup(sheets["champion10"])
    events = _start(engine, instance, [20, 1, 4])
    grants = _heroic_events(events)
    assert [(row["actor_id"], row["granted"], row["feature_id"]) for row in grants] == [
        ("player:gm", True, "heroic_warrior"),
    ]
    assert _canonical(instance)["heroic_inspiration"]["source"] == "heroic_warrior"

    # Walk back around to the same actor: it already holds one, so no second
    # event and no overwrite.
    while _combat(instance)["turn_index"] != 0 or int(_combat(instance)["round"]) == 1:
        batch = _end_turn(
            engine, instance,
            str(_combat(instance)["turn_slots"][int(_combat(instance)["turn_index"])]["actor_id"]),
        )
        if int(_combat(instance)["round"]) > 1 and int(_combat(instance)["turn_index"]) == 0:
            assert _heroic_events(batch) == []
            break


def test_heroic_warrior_is_silent_before_level_ten(sheets) -> None:
    engine, instance = _setup(sheets["champion9"])
    events = _start(engine, instance, [20, 1, 4])
    assert _heroic_events(events) == []


def test_heroic_inspiration_has_no_spending_entry_point(sheets) -> None:
    """The known integration gap, asserted instead of papered over.

    Sp spending Heroic Inspiration rerolls *any* die.  The d20 windows this
    repository owns cannot express that, and inventing an Advantage instead
    would be a different rule, so the grant exists and has no consumer yet.
    """

    engine, instance = _setup(sheets["champion10"])
    _start(engine, instance, [20, 1, 4])
    assert _canonical(instance)["heroic_inspiration"]["granted"] is True
    intents = engine.available_intents(instance, "gm")
    assert not any(
        "heroic" in str(item.get("type") or "") + str(item.get("capability_id") or "")
        for item in intents
    )


# ---------------------------------------------------------------------------
# 15-20. Thief's Reflexes and the first-round schedule
# ---------------------------------------------------------------------------

def test_thief_gets_two_slots_with_the_second_ten_lower(sheets) -> None:
    engine, instance = _setup(sheets["thief17"])
    events = _start(engine, instance, [15, 12])
    started = _of_type(events, "dnd2024.combat.started")[0]
    slots = started["turn_slots"]
    mine = [slot for slot in slots if slot["actor_id"] == "player:gm"]
    assert [slot["kind"] for slot in mine] == ["normal", "thief_reflexes"]
    assert mine[1]["initiative_total"] == mine[0]["initiative_total"] - 10
    assert mine[1]["rounds"] == [1]
    assert mine[0]["slot_id"] != mine[1]["slot_id"]
    # Slots are ordered by their own total, not appended after the first turn.
    totals = [int(slot["initiative_total"]) for slot in slots]
    assert totals == sorted(totals, reverse=True)
    # ``initiative`` stays deduplicated: it answers "who is in this combat".
    assert started["initiative"].count("player:gm") == 1


def test_both_thief_slots_refresh_economy_and_reactions(sheets) -> None:
    engine, instance = _setup(sheets["thief17"])
    # Thief 25 / goblin 16 / Thief's second turn 15: the enemy acts *between* the
    # two slots, so the second turn is a real position in the order rather than
    # something appended to the first.
    _start(engine, instance, [15, 14])
    assert _current_actor(instance) == "player:gm"
    events = _end_turn(engine, instance, "player:gm")
    assert _current_actor(instance) == "enemy:goblin-1"
    events += _end_turn(engine, instance, "enemy:goblin-1")
    second = [
        row for row in _of_type(events, "dnd2024.turn.advanced")
        if row["actor_id"] == "player:gm"
    ]
    assert second
    # Same actor identity, different slot: the second turn refreshes its own
    # action, bonus action, reaction and movement without a second character.
    assert second[0]["slot_id"] == "player:gm#2"
    assert second[0]["economy"]["action"] == 1
    assert second[0]["economy"]["reaction"] == 1
    assert second[0]["round"] == 1

def test_second_round_keeps_only_the_normal_slot(sheets) -> None:
    engine, instance = _setup(sheets["thief17"])
    _start(engine, instance, [15, 12])
    batches = _walk_first_round(engine, instance)
    assert batches
    combat = _combat(instance)
    assert int(combat["round"]) == 2
    # Round two onward: one Thief turn and no Initiative-10 insertion.
    second_round: list[str] = []
    for _ in range(4):
        combat = _combat(instance)
        if int(combat["round"]) > 2 or combat["status"] != "active":
            break
        actor_id = str(combat["turn_slots"][int(combat["turn_index"])]["actor_id"])
        second_round.append(actor_id)
        _end_turn(engine, instance, actor_id)
    assert second_round.count("player:gm") == 1


def test_a_thief_slot_tied_with_an_enemy_orders_deterministically(sheets) -> None:
    engine, instance = _setup(sheets["thief17"])
    # The goblin's total lands exactly on the second slot's total: the tie is
    # broken by the same key Initiative uses, so a replay cannot reorder it.
    events = _start(engine, instance, [15, 6])
    slots = _of_type(events, "dnd2024.combat.started")[0]["turn_slots"]
    totals = [int(slot["initiative_total"]) for slot in slots]
    assert totals == sorted(totals, reverse=True)

    engine2, instance2 = _setup(sheets["thief17"])
    events2 = _start(engine2, instance2, [15, 6])
    assert _of_type(events2, "dnd2024.combat.started")[0]["turn_slots"] == slots


def test_combat_started_precedes_every_turn_start_grant(sheets) -> None:
    engine, instance = _setup(sheets["champion10"])
    events = _start(engine, instance, [20, 1, 4])
    kinds = [event["type"] for event in events]
    assert kinds.index("dnd2024.combat.started") < kinds.index(
        "dnd2024.heroic_inspiration.changed"
    )

def test_the_first_round_wraps_only_after_every_slot(sheets) -> None:
    engine, instance = _setup(sheets["thief17"])
    _start(engine, instance, [15, 12])
    batches = _walk_first_round(engine, instance)
    rounds = [
        int(_of_type(batch, "dnd2024.turn.advanced")[0]["round"])
        for batch in batches if _of_type(batch, "dnd2024.turn.advanced")
    ]
    # The Thief's second turn is still round one; the wrap comes afterwards.
    assert rounds == sorted(rounds)
    assert rounds.count(1) >= 2


def test_a_dead_thief_loses_both_slots(sheets) -> None:
    # A second player keeps the party alive: otherwise losing the only player
    # ends the combat before the schedule can be observed at all.
    engine, instance = _with_ally(sheets["thief17"], sheets["barbarian6"])
    _start(engine, instance, [3, 15, 12])
    assert _current_actor(instance) == "player:gm"
    # Only the condition is set: the skip rules key on ``dead``/``stable``, and
    # the HP pipeline already has its own tests.
    _conditions(instance)["dead"] = {"source": "test"}
    batches = _walk_first_round(engine, instance)
    advanced = [
        _of_type(batch, "dnd2024.turn.advanced")[0]
        for batch in batches if _of_type(batch, "dnd2024.turn.advanced")
    ]
    assert advanced
    assert all(row["actor_id"] != "player:gm" for row in advanced)
    # The second slot is skipped on its own merits: no turn is invented for a
    # dead actor, and the ally still gets a turn in the same round.
    assert "player:gm#2" not in [row["slot_id"] for row in advanced]
    assert any(row["actor_id"] == "player:ally" for row in advanced)


# ---------------------------------------------------------------------------
# 22. replaying the opening batch changes nothing
# ---------------------------------------------------------------------------

def test_replaying_combat_start_neither_rerolls_nor_regrants(sheets) -> None:
    engine, instance = _setup(sheets["monk2"])
    canonical = _canonical(instance)
    canonical["resources"]["class"]["focus_points"]["current"] = 0
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin()],
        "initiative_choices": {"player:gm": {"uncanny_metabolism": True}},
    }
    first = engine.resolve_intent(instance, deepcopy(intent), SequenceRng([12, 4, 5]))
    assert first["ok"] is True, first
    assert engine.apply_batch(instance, first["event_batch"])["applied"] is True
    hp = int(_canonical(instance)["resources"]["hp"])
    focus = int(_canonical(instance)["resources"]["class"]["focus_points"]["current"])

    # 同一个 intent 再次提交：交回原批次，不重掷、不再恢复、不再扣长休资源。
    again = engine.resolve_intent(instance, deepcopy(intent), SequenceRng([1, 1, 1]))
    assert again["ok"] is True
    assert again.get("replayed") is True
    assert again["event_batch"]["batch_id"] == first["event_batch"]["batch_id"]
    assert engine.apply_batch(instance, again["event_batch"])["applied"] is False
    assert int(_canonical(instance)["resources"]["hp"]) == hp
    assert int(_canonical(instance)["resources"]["class"]["focus_points"]["current"]) == focus
    assert int(
        _canonical(instance)["resources"]["class"]["uncanny_metabolism"]["current"]
    ) == 0


# ---------------------------------------------------------------------------
# 24. joining mid-combat adds a slot, never a second round
# ---------------------------------------------------------------------------

def test_a_late_joiner_gets_one_slot_and_keeps_the_schedule(sheets) -> None:
    engine, instance = _setup(sheets["thief17"])
    _start(engine, instance, [15, 12])
    before = [slot["slot_id"] for slot in _combat(instance)["turn_slots"]]

    # 换人发生在 combat.start 之后：只往 initiative 里插人会让人永远轮不到。
    instance.players["late"] = {
        "character_name": "Wren", "character_sheet": deepcopy(sheets["barbarian6"]),
    }
    result = engine.add_player_to_active_combat(instance, "late")
    assert result["ok"] is True
    assert result["changed"] is True

    combat = _combat(instance)
    slots = [slot["slot_id"] for slot in combat["turn_slots"]]
    assert "player:late#1" in slots
    # 已结算的先攻顺序不重排，当前 slot 的下标也不动。
    assert [slot_id for slot_id in slots if slot_id in before] == before
    assert slots.index("player:late#1") == int(combat["turn_index"]) + 1
    assert _current_actor(instance) == "player:gm"
    # 迟到的玩家没有 Thief's Reflexes 式的额外回合。
    assert slots.count("player:late#1") == 1
    assert not [
        slot for slot in combat["turn_slots"]
        if slot["actor_id"] == "player:late" and slot["kind"] != "normal"
    ]


# ---------------------------------------------------------------------------
# 23. forged declarations are refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("choices", [
    {"someone-else": {"uncanny_metabolism": True}},
    {"player:gm": {"uncanny_metabolism": 40}},
    {"player:gm": {"uncanny_metabolism": "yes"}},
    {"player:gm": {"focus_refill": True}},
])
def test_illegal_initiative_choices_are_refused(sheets, choices: dict) -> None:
    engine, instance = _setup(sheets["monk2"])
    resolved = _try_start(engine, instance, [12, 4], initiative_choices=choices)
    assert resolved["ok"] is False


def test_a_character_without_the_feature_cannot_declare_it(sheets) -> None:
    engine, instance = _setup(sheets["barbarian7"])
    resolved = _try_start(
        engine, instance, [7, 18, 4],
        initiative_choices={"player:gm": {"persistent_rage": True}},
    )
    assert resolved["ok"] is False
    assert "cannot use persistent_rage" in str(resolved["error"])


# ---------------------------------------------------------------------------
# 25. the public feed shows the grant and nothing private
# ---------------------------------------------------------------------------

def test_the_public_feed_keeps_heroic_inspiration_to_its_public_fields(sheets) -> None:
    engine, instance = _setup(sheets["champion10"])
    _start(engine, instance, [20, 1, 4])
    feed = Dnd2024Runtime().gameplay_view(instance, "player:gm", False)[
        "recent_combat_events"
    ]
    grants = [
        row for row in feed
        if row.get("type") == "dnd2024.heroic_inspiration.changed"
    ]
    assert grants
    assert grants[0]["actor_id"] == "player:gm"
    assert grants[0]["granted"] is True
    assert grants[0]["feature_id"] == "heroic_warrior"
    # 投影只走白名单：整张角色表、HP 与资源不会搭着事件一起出门。
    leaked = {"hp", "max_hp", "resources", "character", "sheet", "ruleset_character"}
    assert not leaked & set(grants[0])
