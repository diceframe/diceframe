"""S0-F3: what a failed (or seen) d20 opens -- six declared windows.

Every assertion runs the authoritative chain the web layer uses:
``available_intents`` -> ``resolve_intent`` -> ``apply_batch`` ->
``decision.resolve``.  Nothing here writes a die, a total or a resource: the
window decides, the engine rolls, the event batch records.

The six features split by shape, not by class:

* a **bonus die** added after the roll (Tactical Mind, Dark One's Own Luck);
* a **reroll** that must be used (Countercharm, Indomitable, Disciplined
  Survivor);
* a **face rewrite** into a natural 20 (Stroke of Luck).

The item numbers in the docstrings are the task spec's matrix.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
)

LOCALE = "en"
FIGHTER_ABILITIES = {"str": 15, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 8}
ROGUE_ABILITIES = {"str": 8, "dex": 15, "con": 14, "int": 10, "wis": 12, "cha": 13}
MONK_ABILITIES = {"str": 12, "dex": 14, "con": 13, "int": 8, "wis": 15, "cha": 10}
BARD_ABILITIES = {"str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15}
WARLOCK_ABILITIES = {"str": 8, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 15}


# --------------------------------------------------------------------------
# sheets: real builder + real advancement path
# --------------------------------------------------------------------------


def _spell_choices(runtime: Dnd2024Runtime, class_ref: str, level: int) -> dict:
    options = Dnd2024SpellSelection(runtime.load_bundle(LOCALE)).options(class_ref, level)
    requirements = options["requirements"]
    cantrips = [row["ref"] for row in options["cantrips"]]
    leveled = [row["ref"] for row in options["leveled_spells"]]
    prepared = leveled[: int(requirements["prepared_spell_count"])]
    spellbook: list[str] = []
    minimum = int(requirements.get("spellbook_minimum") or 0)
    if minimum:
        spellbook = leveled[:minimum]
        prepared = spellbook[: int(requirements["prepared_spell_count"])]
    return {
        "cantrip_refs": cantrips[: int(requirements["cantrip_count"])],
        "prepared_spell_refs": prepared,
        "spellbook_refs": spellbook,
    }


def _picks(preview: dict, character: dict) -> dict:
    """First legal answer per requirement, keeping ability scores under 20.

    ``advancement_choices`` always raises the first allowed ability, which
    overflows a level-17 Fighter's Strength.  The lowest score is equally
    legal and keeps a long-lived fixture valid.
    """

    picks = advancement_choices(preview)
    for requirement in preview.get("requirements") or []:
        if requirement.get("id") != "ability_score_increases":
            continue
        allowed = requirement.get("allowed", "any")
        if allowed == "any":
            abilities = character.get("abilities") or {}
            picks["ability_score_increases"] = {
                min(abilities, key=lambda key: int(abilities[key])): int(
                    requirement.get("total", 2)
                )
            }
    return picks


def _sheet(
    runtime: Dnd2024Runtime,
    class_ref: str,
    skills: list[str],
    package: str,
    abilities: dict[str, int],
    level: int,
) -> dict:
    draft = monk_draft(runtime, LOCALE)
    draft.update(
        class_ref=class_ref,
        class_skill_refs=list(skills),
        class_tool_refs=[],
        equipment_package_ref=package,
        base_abilities=dict(abilities),
    )
    choices = runtime.builder_choices(None, draft)
    draft["class_tool_refs"] = [item["ref"] for item in choices["class_tools"]][
        : choices["class_tool_count"]
    ]
    draft["class_spell_choices"] = _spell_choices(runtime, class_ref, 1)
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        picks = _picks(preview, sheet["ruleset_character"])
        picks["class_spell_choices"] = _spell_choices(runtime, class_ref, next_level)
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _fighter(runtime: Dnd2024Runtime, level: int = 2) -> dict:
    return _sheet(
        runtime, "class:fighter", ["skill:athletics", "skill:perception"],
        "equipment_package:fighter_a", FIGHTER_ABILITIES, level,
    )


def _rogue(runtime: Dnd2024Runtime, level: int = 1) -> dict:
    return _sheet(
        runtime, "class:rogue",
        ["skill:stealth", "skill:sleight_of_hand", "skill:perception", "skill:acrobatics"],
        "equipment_package:rogue_a", ROGUE_ABILITIES, level,
    )


def _monk(runtime: Dnd2024Runtime, level: int = 2) -> dict:
    return _sheet(
        runtime, "class:monk", ["skill:acrobatics", "skill:stealth"],
        "equipment_package:monk_b", MONK_ABILITIES, level,
    )


def _bard(runtime: Dnd2024Runtime, level: int = 7) -> dict:
    return _sheet(
        runtime, "class:bard", ["skill:performance", "skill:persuasion", "skill:stealth"],
        "equipment_package:bard_a", BARD_ABILITIES, level,
    )


def _warlock(runtime: Dnd2024Runtime, level: int = 6) -> dict:
    return _sheet(
        runtime, "class:warlock",
        ["skill:arcana", "skill:deception"], "equipment_package:warlock_a",
        WARLOCK_ABILITIES, level,
    )


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def sheets(runtime: Dnd2024Runtime) -> dict[str, dict]:
    return {
        "fighter1": _fighter(runtime, 1),
        "fighter2": _fighter(runtime, 2),
        "fighter9": _fighter(runtime, 9),
        "fighter13": _fighter(runtime, 13),
        "fighter17": _fighter(runtime, 17),
        "rogue19": _rogue(runtime, 19),
        "rogue20": _rogue(runtime, 20),
        "monk13": _monk(runtime, 13),
        "monk14": _monk(runtime, 14),
        "bard6": _bard(runtime, 6),
        "bard7": _bard(runtime, 7),
        "warlock5": _warlock(runtime, 5),
        "warlock6": _warlock(runtime, 6),
    }


# --------------------------------------------------------------------------
# combat plumbing
# --------------------------------------------------------------------------


def _state(instance) -> dict:
    from src.engine.modules import ruleset_runtime

    return ruleset_runtime.state(instance)


def _apply(engine, instance, intent: dict, rng) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _try(engine, instance, intent: dict, rng) -> dict:
    return engine.resolve_intent(instance, intent, rng)


def _combat(
    runtime: Dnd2024Runtime, players: dict[str, dict], *, rolls: list[int],
    positions: dict[str, int] | None = None, hp: int = 60,
    enemies: list[dict] | None = None,
) -> tuple[Dnd2024CombatEngine, object]:
    first_uid = next(iter(players))
    engine, instance = monk_instance(runtime, deepcopy(players[first_uid]), uid=first_uid)
    for uid, sheet in list(players.items())[1:]:
        instance.players[uid] = {
            "character_name": str(sheet.get("character_name") or "Hero"),
            "character_sheet": deepcopy(sheet),
        }
    intent = {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": first_uid,
        "enemies": list(enemies) if enemies is not None else [goblin(position=5, hp=hp)],
    }
    if positions:
        intent["player_positions"] = positions
    _apply(engine, instance, intent, SequenceRng(list(rolls)))
    return engine, instance


def _current(instance) -> str:
    combat = _state(instance)["combat"]
    return str(combat["initiative"][combat["turn_index"]])


def _end_turn(engine, instance, actor_id: str) -> list[dict]:
    state = _state(instance)
    uid = "gm" if actor_id.startswith("enemy:") else actor_id.split(":", 1)[1]
    return _apply(engine, instance, {
        "intent_id": f"end-{state['version']}-{actor_id}", "type": "end_turn",
        "expected_version": state["version"],
        "submitted_by": uid, "actor_id": actor_id,
    }, SequenceRng([]))


def _to_actor(engine, instance, actor_id: str) -> None:
    for _ in range(12):
        current = _current(instance)
        if current == actor_id:
            return
        _end_turn(engine, instance, current)
    raise AssertionError("the requested actor never got a turn")


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _pool(instance, resource_id: str, uid: str = "gm") -> dict:
    return dict(_canonical(instance, uid)["resources"]["class"].get(resource_id) or {})


def _set_pool(instance, resource_id: str, current: int, uid: str = "gm") -> None:
    _canonical(instance, uid)["resources"]["class"][resource_id]["current"] = current


def _pending(instance, index: int = -1) -> dict | None:
    rows = _state(instance)["combat"].get("pending_decisions") or []
    return rows[index] if rows else None


def _decide(engine, instance, decision: dict, option: str, rolls: list[int] | None = None,
            uid: str = "gm") -> dict:
    return engine.resolve_intent(instance, {
        "intent_id": f"decide-{decision['decision_id']}-{option}",
        "type": "decision.resolve",
        "expected_version": _state(instance)["version"],
        "submitted_by": uid,
        "decision_id": decision["decision_id"],
        "option": option,
    }, SequenceRng(list(rolls or [])))


def _hide(engine, instance, *, uid: str = "gm", rolls: list[int]) -> dict:
    result = _try(engine, instance, {
        "intent_id": f"hide-{uid}-{_state(instance)['version']}", "type": "hide",
        "expected_version": _state(instance)["version"],
        "submitted_by": uid, "actor_id": f"player:{uid}",
    }, SequenceRng(list(rolls)))
    assert result["ok"] is True, result
    engine.apply_batch(instance, result["event_batch"])
    return result


def _attack(engine, instance, *, uid: str = "gm", rolls: list[int]) -> dict:
    available = [item for item in engine.available_intents(instance, uid)
                 if item["type"] == "attack"]
    assert available, "no attack intent"
    result = _try(engine, instance, {
        "intent_id": f"atk-{uid}-{_state(instance)['version']}", "type": "attack",
        "expected_version": _state(instance)["version"],
        "submitted_by": uid, "actor_id": f"player:{uid}",
        "target_id": available[0]["targets"][0]["actor_id"],
        "weapon_ref": available[0]["weapons"][0]["weapon_ref"],
    }, SequenceRng(list(rolls)))
    assert result["ok"] is True, result
    engine.apply_batch(instance, result["event_batch"])
    return result


def _events(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def _checked(events: list[dict], kind: str) -> dict:
    return next(
        event for event in reversed(events)
        if event.get("type") == "check.resolved" and event.get("kind") == kind
    )


def _has_feature(runtime: Dnd2024Runtime, sheet: dict, feature_id: str) -> bool:
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))
    return resolver.has_feature(sheet["ruleset_character"], feature_id)


# --------------------------------------------------------------------------
# matrix 1: the declaration itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("sheet_key", "feature_id", "expected"), [
    ("fighter1", "tactical_mind", False),
    ("fighter2", "tactical_mind", True),
    ("fighter9", "indomitable", True),
    ("fighter2", "indomitable", False),
    ("monk13", "disciplined_survivor", False),
    ("monk14", "disciplined_survivor", True),
    ("rogue19", "stroke_of_luck", False),
    ("rogue20", "stroke_of_luck", True),
    ("bard6", "countercharm", False),
    ("bard7", "countercharm", True),
    ("warlock5", "dark_ones_own_luck", False),
    ("warlock6", "dark_ones_own_luck", True),
])
def test_each_feature_belongs_to_its_level(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
    sheet_key: str, feature_id: str, expected: bool,
) -> None:
    """Matrix 1: the six features are owned only from their own level on."""

    assert _has_feature(runtime, sheets[sheet_key], feature_id) is expected


def test_stroke_of_luck_gets_its_own_single_use_pool(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 1/18: one use, restored by a Short or Long Rest."""

    rogue = sheets["rogue20"]["ruleset_character"]["resources"]["class"]["stroke_of_luck"]
    assert (rogue["current"], rogue["maximum"]) == (1, 1)


# --------------------------------------------------------------------------
# matrix 2-4: Tactical Mind
# --------------------------------------------------------------------------


def test_tactical_mind_opens_only_on_a_failed_ability_check(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 2: a failed hide opens it; a successful one does not."""

    engine, instance = _combat(
        runtime, {"gm": sheets["fighter2"]}, rolls=[20, 1],
    )
    _to_actor(engine, instance, "player:gm")
    failed = _hide(engine, instance, rolls=[2])
    assert failed["ok"] is True, failed
    window = _pending(instance)
    assert window is not None
    assert window["window_code"] == "tactical_mind"
    assert window["die_sides"] == 10
    assert window["check"]["success"] is False

    engine2, instance2 = _combat(
        runtime, {"gm": sheets["fighter2"]}, rolls=[20, 1],
    )
    _to_actor(engine2, instance2, "player:gm")
    # A Fighter's starting armour gives Stealth disadvantage, so a clean pass
    # needs both d20s high rather than one lucky one.
    passed = _hide(engine2, instance2, rolls=[20, 20])
    assert passed["ok"] is True, passed
    assert _pending(instance2) is None


def test_tactical_mind_is_not_offered_on_an_attack_or_a_saving_throw(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 2: the face is an ability check, not "any failed roll"."""

    engine, instance = _combat(runtime, {"gm": sheets["fighter2"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    miss = _attack(engine, instance, rolls=[2])
    assert miss["ok"] is True, miss
    codes = [event.get("window_code") for event in _events(miss)]
    assert "tactical_mind" not in codes


def test_tactical_mind_rolls_a_real_d10_and_spends_only_on_a_rescue(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 3: a rescue costs a Second Wind use; a still-failing one does not."""

    engine, instance = _combat(runtime, {"gm": sheets["fighter2"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _hide(engine, instance, rolls=[2])
    window = _pending(instance)
    target = int(window["check"]["target"])
    assert window["check"]["total"] + 10 >= target, "the fixture must be rescuable"
    before = _pool(instance, "second_wind")["current"]

    resolved = _decide(engine, instance, window, "spend", [9])
    assert resolved["ok"] is True, resolved
    check = _checked(_events(resolved), "hide")
    assert check["window_roll"] == 9
    assert check["total"] == int(window["check"]["total"]) + 9
    types = [event["type"] for event in _events(resolved)]
    assert "dnd2024.class_resource.spent" in types
    engine.apply_batch(instance, resolved["event_batch"])
    assert _pool(instance, "second_wind")["current"] == before - 1
    assert "invisible" in (_canonical(instance).get("conditions") or {})

    engine2, instance2 = _combat(runtime, {"gm": sheets["fighter2"]}, rolls=[20, 1])
    _to_actor(engine2, instance2, "player:gm")
    _hide(engine2, instance2, rolls=[2])
    failing = _pending(instance2)
    start = _pool(instance2, "second_wind")["current"]
    spent = _decide(engine2, instance2, failing, "spend", [1])
    types = [event["type"] for event in _events(spent)]
    assert "dnd2024.class_resource.spent" in types
    assert "dnd2024.class_resource.refunded" in types
    engine2.apply_batch(instance2, spent["event_batch"])
    assert _pool(instance2, "second_wind")["current"] == start
    assert "invisible" not in (_canonical(instance2).get("conditions") or {})


def test_tactical_mind_with_no_second_wind_declines_without_a_trace(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 4: an empty pool, a decline and a replay all move no state."""

    engine, instance = _combat(runtime, {"gm": sheets["fighter2"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _set_pool(instance, "second_wind", 0)
    assert _hide(engine, instance, rolls=[2])["ok"] is True
    assert _pending(instance) is None

    engine2, instance2 = _combat(runtime, {"gm": sheets["fighter2"]}, rolls=[20, 1])
    _to_actor(engine2, instance2, "player:gm")
    _hide(engine2, instance2, rolls=[2])
    window = _pending(instance2)
    start = _pool(instance2, "second_wind")["current"]
    declined = _decide(engine2, instance2, window, "decline")
    assert declined["ok"] is True, declined
    engine2.apply_batch(instance2, declined["event_batch"])
    assert _pool(instance2, "second_wind")["current"] == start

    replay = engine2.resolve_intent(instance2, {
        "intent_id": "replay-tactical-mind", "type": "decision.resolve",
        "expected_version": _state(instance2)["version"],
        "submitted_by": "gm", "decision_id": window["decision_id"], "option": "spend",
    }, SequenceRng([5]))
    assert replay["ok"] is False
    assert _pool(instance2, "second_wind")["current"] == start


# --------------------------------------------------------------------------
# matrix 5-8: Dark One's Own Luck on an ability check
# --------------------------------------------------------------------------


def test_dark_luck_is_offered_on_a_successful_and_a_failed_ability_check(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 5: "after seeing the roll" is not a failure-only wording."""

    engine, instance = _combat(runtime, {"gm": sheets["warlock6"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    passed = _hide(engine, instance, rolls=[20])
    window = _pending(instance)
    assert window is not None, "a successful check still shows the die"
    assert window["window_code"] == "dark_ones_own_luck_check"
    assert window["check"]["success"] is True
    start = _pool(instance, "dark_ones_own_luck")["current"]
    spent = _decide(engine, instance, window, "spend", [7])
    check = _checked(_events(spent), "hide")
    assert check["window_bonus"] == 7
    assert check["success"] is True
    engine.apply_batch(instance, spent["event_batch"])
    assert _pool(instance, "dark_ones_own_luck")["current"] == start - 1

    engine2, instance2 = _combat(runtime, {"gm": sheets["warlock6"]}, rolls=[20, 1])
    _to_actor(engine2, instance2, "player:gm")
    _hide(engine2, instance2, rolls=[2])
    failed = _pending(instance2)
    assert failed is not None
    assert failed["check"]["success"] is False
    spent2 = _decide(engine2, instance2, failed, "spend", [8])
    assert _checked(_events(spent2), "hide")["total"] == int(failed["check"]["total"]) + 8


def test_dark_luck_never_opens_on_an_attack_and_only_once_per_roll(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 6: no attack face, and one use per roll."""

    engine, instance = _combat(runtime, {"gm": sheets["warlock6"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    miss = _attack(engine, instance, rolls=[2])
    assert "dark_ones_own_luck" not in [
        event.get("window_code") for event in _events(miss)
    ]
    assert _pending(instance) is None

    engine2, instance2 = _combat(runtime, {"gm": sheets["warlock6"]}, rolls=[20, 1])
    _to_actor(engine2, instance2, "player:gm")
    _hide(engine2, instance2, rolls=[2])
    first = _pending(instance2)
    spent = _decide(engine2, instance2, first, "spend", [1])
    engine2.apply_batch(instance2, spent["event_batch"])
    # Still a failure, and the same roll is closed to a second die.
    assert _pending(instance2) is None
    replay = engine2.resolve_intent(instance2, {
        "intent_id": "second-die-same-roll", "type": "decision.resolve",
        "expected_version": _state(instance2)["version"],
        "submitted_by": "gm", "decision_id": first["decision_id"], "option": "spend",
    }, SequenceRng([9]))
    assert replay["ok"] is False


def test_dark_luck_pool_is_charisma_with_a_floor_and_a_long_rest_refill(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 6: ``max(1, Cha)`` uses, all back on a Long Rest."""

    from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine

    warlock = sheets["warlock6"]
    pool = warlock["ruleset_character"]["resources"]["class"]["dark_ones_own_luck"]
    charisma = warlock["ruleset_character"]["abilities"]["cha"]
    assert pool["maximum"] == max(1, (charisma - 10) // 2)
    spent = deepcopy(warlock)
    spent["ruleset_character"]["resources"]["class"]["dark_ones_own_luck"]["current"] = 0
    rest = Dnd2024RestEngine(runtime.load_bundle(LOCALE))
    long_rested = rest.complete_long_rest(spent)["character"]
    assert (
        long_rested["resources"]["class"]["dark_ones_own_luck"]["current"]
        == pool["maximum"]
    )
    short_rested = rest.complete_short_rest(deepcopy(spent))["character"]
    assert short_rested["resources"]["class"]["dark_ones_own_luck"]["current"] == 0


def test_dark_luck_can_rescue_a_death_save_without_double_counting(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 7: the death-save face still lands on the tally exactly once."""

    engine, instance = _combat(runtime, {"gm": sheets["warlock6"]}, rolls=[20, 1])
    _canonical(instance)["resources"]["hp"] = 0
    _to_actor(engine, instance, "player:gm")
    events = _apply(engine, instance, {
        "intent_id": "death-save", "type": "death_save",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([5]))
    assert events[-1]["type"] == "dnd2024.decision.pending"
    window = _pending(instance)
    assert window["surface"] == "death_save"
    resolved = _decide(engine, instance, window, "spend", [8])
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    death_saves = _canonical(instance)["conditions"]["death_saves"]
    assert (death_saves["successes"], death_saves["failures"]) == (1, 0)
    assert _pool(instance, "dark_ones_own_luck")["current"] == 1


def _failed_death_save(engine, instance, rolls: list[int]) -> dict:
    """Roll one failing death save and return the window it opened."""

    _canonical(instance)["resources"]["hp"] = 0
    _to_actor(engine, instance, "player:gm")
    events = _apply(engine, instance, {
        "intent_id": "death-save", "type": "death_save",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng(rolls))
    assert events[-1]["type"] == "dnd2024.decision.pending", events
    window = _pending(instance)
    assert window["surface"] == "death_save"
    return window


def test_indomitable_rerolls_a_failed_death_save_and_lands_once(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Ruling: a death save offers the reroll faces, not just the die faces.

    A reroll discards the old die rather than adding to it, so it can rescue a
    death save even when a bonus die could not -- and the tally still lands
    exactly once, after every legal face has had its turn.
    """

    engine, instance = _combat(runtime, {"gm": sheets["fighter9"]}, rolls=[20, 1])
    window = _failed_death_save(engine, instance, [4])
    assert window["window_code"] == "indomitable"
    assert window["reroll_bonus"] == 9

    resolved = _decide(engine, instance, window, "spend", [15])
    check = _checked(_events(resolved), "death_save")
    assert check["natural"] == 15
    assert check["success"] is True
    # The old face is kept for the audit trail, not as a second result.
    assert check["rerolled_natural"] == 4
    engine.apply_batch(instance, resolved["event_batch"])
    death_saves = _canonical(instance)["conditions"]["death_saves"]
    assert (death_saves["successes"], death_saves["failures"]) == (1, 0)
    assert int(_canonical(instance)["resources"]["hp"]) == 0
    assert _pool(instance, "indomitable")["current"] == 0


def test_a_death_save_reroll_discards_the_natural_one(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """A natural 1 costs two failures only while that face still exists."""

    engine, instance = _combat(runtime, {"gm": sheets["fighter9"]}, rolls=[20, 1])
    window = _failed_death_save(engine, instance, [1])
    assert window["window_code"] == "indomitable"

    resolved = _decide(engine, instance, window, "spend", [12])
    engine.apply_batch(instance, resolved["event_batch"])
    death_saves = _canonical(instance)["conditions"]["death_saves"]
    assert (death_saves["successes"], death_saves["failures"]) == (1, 0)


# --------------------------------------------------------------------------
# matrix 9-11: Countercharm
# --------------------------------------------------------------------------


def _persist_repeat_save(
    instance, uid: str, condition: str, *, ability: str = "wis", dc: int = 20,
) -> None:
    """Persist a condition that repeats a save, as a landed spell would.

    ``sleep`` and ``hold_person`` are the two carriers the bundle declares
    today, and neither applies Charmed or Frightened.  Countercharm's trigger
    facts -- "the effect applies Charmed/Frightened" and "the save repeats" --
    are therefore persisted here as data, exactly as a future charm spell would
    write them, and every mechanic under test still runs the real
    ``_save_repeat_events`` -> window -> finish chain.
    """

    character = instance.get_character_sheet(uid)["ruleset_character"]
    character.setdefault("conditions", {})[condition] = {
        "source": "test",
        "source_actor_id": f"player:{uid}",
        "save_repeat": {"ability": ability, "dc": dc},
    }


def test_countercharm_reacts_to_an_ally_charmed_save(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 9: the ally's failed charm save opens the Bard's Reaction."""

    engine, instance = _combat(
        runtime, {"gm": sheets["fighter2"], "ally": sheets["bard7"]},
        rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "frightened")
    _end_turn(engine, instance, "player:gm")
    window = _pending(instance)
    assert window is not None
    assert window["window_code"] == "countercharm"
    assert window["surface"] == "save"
    assert window["window_owner_id"] == "player:ally"
    assert window["reroll_mode"] == "advantage"
    assert window["check"]["success"] is False

    resolved = _decide(engine, instance, window, "spend", [3, 17], uid="ally")
    assert resolved["ok"] is True, resolved
    check = _checked(_events(resolved), "saving_throw")
    assert check["natural"] == 17          # the advantage pair kept the high die
    assert check["rolls"] == [3, 17]
    assert check["rerolled_total"] == window["check"]["total"]
    engine.apply_batch(instance, resolved["event_batch"])
    assert _state(instance)["combat"]["reactions"]["player:ally"] == 0
    assert _pool(instance, "bardic_inspiration", "ally")["current"] == 2


def test_countercharm_does_not_react_out_of_reach_or_without_a_reaction(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 10: 31 feet or a spent Reaction closes the window with no cost."""

    engine, instance = _combat(
        runtime, {"gm": sheets["fighter2"], "ally": sheets["bard7"]},
        rolls=[20, 15, 1], positions={"gm": 0, "ally": 40},
    )
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "frightened")
    _end_turn(engine, instance, "player:gm")
    assert _pending(instance) is None
    assert _state(instance)["combat"]["reactions"]["player:ally"] == 1


def test_countercharm_ignores_other_conditions_and_other_check_kinds(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 11: only Charmed/Frightened, only a save, and no sight clause."""

    engine, instance = _combat(
        runtime, {"gm": sheets["fighter2"], "ally": sheets["bard7"]},
        rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "restrained")
    _end_turn(engine, instance, "player:gm")
    assert _pending(instance) is None

    engine2, instance2 = _combat(
        runtime, {"gm": sheets["fighter2"], "ally": sheets["bard7"]},
        rolls=[20, 15, 1],
    )
    _to_actor(engine2, instance2, "player:gm")
    _persist_repeat_save(instance2, "gm", "frightened")
    # A blinded Bard still qualifies: 2024 Countercharm has no sight clause.
    instance2.get_character_sheet("ally")["ruleset_character"].setdefault(
        "conditions", {}
    )["blinded"] = {"source": "test"}
    _end_turn(engine2, instance2, "player:gm")
    window = _pending(instance2)
    assert window is not None and window["window_code"] == "countercharm"


# --------------------------------------------------------------------------
# matrix 12-13: Indomitable
# --------------------------------------------------------------------------


def _wise_goblin(*, wisdom: int = 10) -> dict:
    """A goblin whose passive Wisdom makes the Hide DC unreachable."""

    enemy = goblin(position=5, hp=60)
    enemy["saving_throws"]["wis"] = wisdom
    enemy["abilities"]["wis"] = wisdom
    return enemy


def test_indomitable_adds_the_fighter_level_and_forces_the_new_roll(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 12: a worse new roll is still the roll you use."""

    engine, instance = _combat(runtime, {"gm": sheets["fighter9"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "restrained")
    _end_turn(engine, instance, "player:gm")
    window = _pending(instance)
    assert window["window_code"] == "indomitable"
    assert window["reroll_bonus"] == 9
    first_total = int(window["check"]["total"])

    resolved = _decide(engine, instance, window, "spend", [2])
    check = _checked(_events(resolved), "saving_throw")
    assert check["natural"] == 2
    # Every non-d20 component is unchanged; only the face and the +level move.
    assert check["total"] - check["natural"] == first_total - int(window["check"]["natural"]) + 9
    assert check["success"] is False          # the better original is gone
    assert check["rerolled_total"] == first_total
    engine.apply_batch(instance, resolved["event_batch"])
    assert _pool(instance, "indomitable")["current"] == 0
    assert "restrained" in (_canonical(instance).get("conditions") or {})


@pytest.mark.parametrize(("sheet_key", "expected"), [
    ("fighter9", 1), ("fighter13", 2), ("fighter17", 3),
])
def test_indomitable_uses_scale_with_fighter_level(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
    sheet_key: str, expected: int,
) -> None:
    """Matrix 13: 1/2/3 uses, and a Short Rest restores none of them."""

    from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine

    sheet = sheets[sheet_key]
    pool = sheet["ruleset_character"]["resources"]["class"]["indomitable"]
    assert pool["maximum"] == expected

    drained = deepcopy(sheet)
    drained["ruleset_character"]["resources"]["class"]["indomitable"]["current"] = 0
    rest = Dnd2024RestEngine(runtime.load_bundle(LOCALE))
    assert rest.complete_short_rest(deepcopy(drained))["character"][
        "resources"
    ]["class"]["indomitable"]["current"] == 0
    assert rest.complete_long_rest(deepcopy(drained))["character"][
        "resources"
    ]["class"]["indomitable"]["current"] == expected


def test_indomitable_closes_when_the_pool_is_empty(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 13: an exhausted pool is not a window at all."""

    engine, instance = _combat(runtime, {"gm": sheets["fighter9"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _set_pool(instance, "indomitable", 0)
    _persist_repeat_save(instance, "gm", "restrained")
    _end_turn(engine, instance, "player:gm")
    assert _pending(instance) is None


# --------------------------------------------------------------------------
# matrix 14-15: Disciplined Survivor
# --------------------------------------------------------------------------


def test_disciplined_survivor_grants_every_saving_throw_proficiency(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 14: all six saves carry one Proficiency Bonus, exactly once."""

    from src.rulesets.dnd2024.character.builder import ability_modifier

    monk = sheets["monk14"]["ruleset_character"]
    proficiency = 2 + (14 - 1) // 4
    refs = set(monk["proficiencies"]["saving_throw_refs"])
    assert refs == {f"ability:{ability}" for ability in ("str", "dex", "con", "int", "wis", "cha")}
    for ability, value in monk["derived"]["saving_throws"].items():
        assert value == ability_modifier(monk["abilities"][ability]) + proficiency

    # The same sheet rebuilt through the real advancement path does not stack
    # a second proficiency bonus on top.
    rebuilt = _monk(runtime, 14)
    assert (
        rebuilt["ruleset_character"]["derived"]["saving_throws"]
        == monk["derived"]["saving_throws"]
    )
    low = sheets["monk13"]["ruleset_character"]["proficiencies"]["saving_throw_refs"]
    assert "ability:cha" not in low


def test_disciplined_survivor_reroll_costs_one_focus(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 15: 1 Focus, forced new roll, nothing at all on an ability check."""

    engine, instance = _combat(runtime, {"gm": sheets["monk14"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "restrained")
    _end_turn(engine, instance, "player:gm")
    window = _pending(instance)
    assert window["window_code"] == "disciplined_survivor"
    assert window["resource_id"] == "focus_points"
    focus = _pool(instance, "focus_points")["current"]
    resolved = _decide(engine, instance, window, "spend", [11])
    check = _checked(_events(resolved), "saving_throw")
    assert check["natural"] == 11
    engine.apply_batch(instance, resolved["event_batch"])
    assert _pool(instance, "focus_points")["current"] == focus - 1

    engine2, instance2 = _combat(runtime, {"gm": sheets["monk14"]}, rolls=[20, 1])
    _to_actor(engine2, instance2, "player:gm")
    _set_pool(instance2, "focus_points", 0)
    _persist_repeat_save(instance2, "gm", "restrained")
    _end_turn(engine2, instance2, "player:gm")
    assert _pending(instance2) is None

    engine3, instance3 = _combat(runtime, {"gm": sheets["monk14"]}, rolls=[20, 1])
    _to_actor(engine3, instance3, "player:gm")
    _hide(engine3, instance3, rolls=[2])
    codes = [item.get("window_code") for item in _state(instance3)["combat"].get(
        "pending_decisions") or []]
    assert "disciplined_survivor" not in codes


# --------------------------------------------------------------------------
# matrix 16-18: Stroke of Luck
# --------------------------------------------------------------------------


def test_stroke_of_luck_turns_a_failed_attack_into_a_natural_twenty(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 16/17: the d20 face becomes 20, so the hit is a Critical Hit."""

    engine, instance = _combat(runtime, {"gm": sheets["rogue20"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _attack(engine, instance, rolls=[2])
    window = _pending(instance)
    assert window["window_code"] == "stroke_of_luck_attack"
    assert window["die_sides"] == 0        # no die is bought
    resolved = _decide(engine, instance, window, "spend")
    events = _events(resolved)
    check = _checked(events, "attack")
    assert check["natural"] == 20
    assert check["success"] is True
    assert check["critical"] is True
    assert check["window_roll"] is None
    damage = next(event for event in events if event.get("type") == "resource.changed")
    assert damage["critical"] is True
    engine.apply_batch(instance, resolved["event_batch"])
    assert _pool(instance, "stroke_of_luck")["current"] == 0
    assert _state(instance)["combat"]["enemies"]["goblin-1"]["hp"] < 60


def test_stroke_of_luck_is_offered_on_an_ability_check_and_a_save(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 16: the face is "a failed D20 Test", not "a failed attack"."""

    engine, instance = _combat(
        runtime, {"gm": sheets["rogue20"]}, rolls=[20, 1],
        enemies=[_wise_goblin(wisdom=15)],
    )
    _to_actor(engine, instance, "player:gm")
    _hide(engine, instance, rolls=[2])
    window = _pending(instance)
    assert window["window_code"] == "stroke_of_luck_check"
    resolved = _decide(engine, instance, window, "spend")
    check = _checked(_events(resolved), "hide")
    assert (check["natural"], check["success"]) == (20, True)
    engine.apply_batch(instance, resolved["event_batch"])
    assert "invisible" in (_canonical(instance).get("conditions") or {})
    assert _pool(instance, "stroke_of_luck")["current"] == 0

    engine2, instance2 = _combat(runtime, {"gm": sheets["rogue20"]}, rolls=[20, 1])
    _to_actor(engine2, instance2, "player:gm")
    _persist_repeat_save(instance2, "gm", "restrained")
    _end_turn(engine2, instance2, "player:gm")
    save_window = _pending(instance2)
    assert save_window["window_code"] == "stroke_of_luck_save"
    resolved2 = _decide(engine2, instance2, save_window, "spend")
    check2 = _checked(_events(resolved2), "saving_throw")
    assert (check2["natural"], check2["success"]) == (20, True)
    engine2.apply_batch(instance2, resolved2["event_batch"])
    assert "restrained" not in (_canonical(instance2).get("conditions") or {})


def test_stroke_of_luck_is_not_offered_on_a_hit_or_a_second_time(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 17/18: a success never opens it, and one use is all you have."""

    engine, instance = _combat(runtime, {"gm": sheets["rogue20"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _attack(engine, instance, rolls=[20])
    assert _pending(instance) is None

    engine2, instance2 = _combat(runtime, {"gm": sheets["rogue20"]}, rolls=[20, 1])
    _to_actor(engine2, instance2, "player:gm")
    _set_pool(instance2, "stroke_of_luck", 0)
    _attack(engine2, instance2, rolls=[2])
    assert _pending(instance2) is None


def test_stroke_of_luck_returns_on_a_short_or_long_rest(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 18: a Short Rest is enough, unlike Indomitable."""

    from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine

    drained = deepcopy(sheets["rogue20"])
    drained["ruleset_character"]["resources"]["class"]["stroke_of_luck"]["current"] = 0
    rest = Dnd2024RestEngine(runtime.load_bundle(LOCALE))
    assert rest.complete_short_rest(deepcopy(drained))["character"][
        "resources"
    ]["class"]["stroke_of_luck"]["current"] == 1
    assert rest.complete_long_rest(deepcopy(drained))["character"][
        "resources"
    ]["class"]["stroke_of_luck"]["current"] == 1


# --------------------------------------------------------------------------
# matrix 19-21: roll identity, forced results, ordered faces
# --------------------------------------------------------------------------


def test_a_reroll_gets_its_own_roll_id_and_retires_the_old_one(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 19: the new roll is a new roll, not the old one re-used."""

    engine, instance = _combat(runtime, {"gm": sheets["fighter9"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "restrained")
    _end_turn(engine, instance, "player:gm")
    window = _pending(instance)
    original_id = window["check"]["roll_id"]
    assert original_id

    resolved = _decide(engine, instance, window, "spend", [15])
    check = _checked(_events(resolved), "saving_throw")
    assert check["rerolled_from"] == original_id
    assert check["roll_id"] != original_id
    engine.apply_batch(instance, resolved["event_batch"])

    again = engine.resolve_intent(instance, {
        "intent_id": "resolve-the-old-roll-again", "type": "decision.resolve",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "decision_id": window["decision_id"], "option": "spend",
    }, SequenceRng([20]))
    assert again["ok"] is False
    assert _pool(instance, "indomitable")["current"] == 0


def test_a_worse_reroll_is_still_the_result_and_both_trails_stay_readable(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 20: the old roll is kept as a record, never as a result."""

    engine, instance = _combat(runtime, {"gm": sheets["fighter9"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "restrained", dc=21)
    _end_turn(engine, instance, "player:gm")
    window = _pending(instance)
    assert window["check"]["natural"] == 1
    resolved = _decide(engine, instance, window, "spend", [3])
    check = _checked(_events(resolved), "saving_throw")
    assert check["natural"] == 3
    assert check["rerolled_natural"] == 1
    assert check["rerolled_rolls"] == window["check"]["rolls"]
    assert check["success"] is False


def test_the_roller_own_face_comes_before_the_allies_reaction(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 21: one decision at a time, in a fixed order."""

    engine, instance = _combat(
        runtime, {"gm": sheets["fighter9"], "ally": sheets["bard7"]},
        rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "frightened")
    _end_turn(engine, instance, "player:gm")
    first = _pending(instance)
    assert first["window_code"] == "indomitable"
    assert len(_state(instance)["combat"]["pending_decisions"]) == 1

    declined = _decide(engine, instance, first, "decline")
    assert declined["ok"] is True, declined
    engine.apply_batch(instance, declined["event_batch"])
    second = _pending(instance)
    assert second is not None, "declining one face must not drop the next"
    assert second["window_code"] == "countercharm"
    assert second["window_owner_id"] == "player:ally"
    assert _pool(instance, "indomitable")["current"] == 1


# --------------------------------------------------------------------------
# matrix 22-23: fail closed, save/load and replay
# --------------------------------------------------------------------------


def test_a_forged_or_stale_window_moves_no_state(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 22: unknown ids, wrong owners, empty pools and stale rows all fail."""

    engine, instance = _combat(runtime, {"gm": sheets["fighter9"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "restrained")
    _end_turn(engine, instance, "player:gm")
    window = _pending(instance)
    version = _state(instance)["version"]
    start_pool = _pool(instance, "indomitable")["current"]

    forged = engine.resolve_intent(instance, {
        "intent_id": "forged", "type": "decision.resolve", "expected_version": version,
        "submitted_by": "gm", "decision_id": "check-window:indomitable:forged:player:gm",
        "option": "spend",
    }, SequenceRng([20]))
    assert forged["ok"] is False

    engine.apply_batch(instance, _decide(engine, instance, window, "decline")["event_batch"])
    stale = engine.resolve_intent(instance, {
        "intent_id": "stale", "type": "decision.resolve",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "decision_id": window["decision_id"], "option": "spend",
    }, SequenceRng([20]))
    assert stale["ok"] is False
    assert _pool(instance, "indomitable")["current"] == start_pool

    engine2, instance2 = _combat(runtime, {"gm": sheets["fighter9"]}, rolls=[20, 1])
    _to_actor(engine2, instance2, "player:gm")
    _persist_repeat_save(instance2, "gm", "restrained")
    _end_turn(engine2, instance2, "player:gm")
    live = _pending(instance2)
    _set_pool(instance2, "indomitable", 0)
    broke = _decide(engine2, instance2, live, "spend", [20])
    assert broke["ok"] is False
    assert _pool(instance2, "indomitable")["current"] == 0


def test_an_allies_window_cannot_be_resolved_by_the_gm(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 22: ownership is re-checked at resolve time, not just at offer."""

    engine, instance = _combat(
        runtime, {"gm": sheets["fighter2"], "ally": sheets["bard7"]},
        rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "frightened")
    _end_turn(engine, instance, "player:gm")
    window = _pending(instance)
    stolen = _decide(engine, instance, window, "spend", [20, 20], uid="gm")
    assert stolen["ok"] is False
    assert _state(instance)["combat"]["reactions"]["player:ally"] == 1
    assert "frightened" in (_canonical(instance).get("conditions") or {})


def test_state_and_ledger_survive_a_round_trip_and_a_replay(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 23: uses, reactions, ids and the final result all replay alike."""

    import json

    engine, instance = _combat(runtime, {"gm": sheets["fighter9"]}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "restrained")
    _end_turn(engine, instance, "player:gm")
    window = _pending(instance)
    intent = {
        "intent_id": "replay-me", "type": "decision.resolve",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "decision_id": window["decision_id"], "option": "spend",
    }
    first = engine.resolve_intent(instance, intent, SequenceRng([18]))
    assert first["ok"] is True
    engine.apply_batch(instance, first["event_batch"])
    state = _state(instance)

    restored = json.loads(json.dumps(state))
    # The whole authoritative state is JSON-safe and round-trips identically:
    # uses, reactions, roll ids and the pending rows are all plain data.
    assert restored == state
    assert restored["combat"]["reactions"] == state["combat"]["reactions"]

    # The same intent at the same version is a replay: the original batch comes
    # back and applying it again is a duplicate, so nothing is spent twice.
    replay = engine.resolve_intent(instance, intent, SequenceRng([18]))
    assert replay["ok"] is True and replay.get("replayed") is True, replay
    applied = engine.apply_batch(instance, replay["event_batch"])
    assert applied["duplicate"] is True
    assert _pool(instance, "indomitable")["current"] == 0


def test_dark_luck_leaves_the_next_failure_face_open(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 8: a die that does not rescue the save hands over to the next face."""

    engine, instance = _combat(
        runtime, {"gm": sheets["warlock6"], "ally": sheets["bard7"]},
        rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "frightened", dc=25)
    _end_turn(engine, instance, "player:gm")
    first = _pending(instance)
    assert first["window_code"] == "dark_ones_own_luck"

    spent = _decide(engine, instance, first, "spend", [1])
    assert spent["ok"] is True, spent
    engine.apply_batch(instance, spent["event_batch"])
    second = _pending(instance)
    assert second is not None, "a still-failing save must keep its other faces"
    assert second["window_code"] == "countercharm"
    assert second["window_owner_id"] == "player:ally"


def test_a_blessed_repeat_save_keeps_its_bonus_die_under_the_window(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 7: an aura/bless bonus is still its own field, not folded away."""

    engine, instance = _combat(runtime, {"gm": sheets["warlock6"]}, rolls=[20, 1])
    _canonical(instance).setdefault("conditions", {})["blessed"] = {"source": "bless"}
    _to_actor(engine, instance, "player:gm")
    _persist_repeat_save(instance, "gm", "restrained", dc=30)
    _end_turn(engine, instance, "player:gm")
    window = _pending(instance)
    assert window["check"]["check_bonus"] >= 1
    spent = _decide(engine, instance, window, "spend", [10])
    check = _checked(_events(spent), "saving_throw")
    assert check["check_bonus"] == window["check"]["check_bonus"]
    assert check["window_bonus"] == 10
    assert (
        check["total"]
        == check["natural"]
        + int(check["modifier"])
        + int(check["check_bonus"])
        + int(check["aura_bonus"])
        + 10
    )
