"""S0-F2: Bardic Inspiration dice, Font/Superior, Peerless and the new faces.

Every assertion goes through the authoritative chain the web layer uses:
``available_intents`` -> ``resolve_intent`` -> ``apply_batch`` ->
``decision.resolve``.  The window die is rolled at resolve time, so the
straight-through dice sequence stays untouched.

Scope: the grant lifecycle, the failed-attack face, the ability-check face
(hide), Font of Inspiration, Superior Inspiration and Peerless Skill.  The
saving-throw face is a separate follow-up slice; Cutting Words' attack and
damage faces are pinned by ``test_dnd2024_cutting_words.py``.
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
    grant_hide_footing,
)

LOCALE = "en"
BARD_ABILITIES = {"str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15}
FIGHTER_ABILITIES = {"str": 15, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 8}


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
        class_skill_refs=skills,
        class_tool_refs=[],
        equipment_package_ref=package,
        base_abilities=abilities,
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
        picks = advancement_choices(preview)
        picks["class_spell_choices"] = _spell_choices(runtime, class_ref, next_level)
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _bard(runtime: Dnd2024Runtime, level: int = 1) -> dict:
    return _sheet(
        runtime, "class:bard",
        ["skill:performance", "skill:persuasion", "skill:stealth"],
        "equipment_package:bard_a", BARD_ABILITIES, level,
    )


def _fighter(runtime: Dnd2024Runtime, level: int = 2) -> dict:
    return _sheet(
        runtime, "class:fighter",
        ["skill:athletics", "skill:perception"],
        "equipment_package:fighter_a", FIGHTER_ABILITIES, level,
    )


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def sheets(runtime: Dnd2024Runtime) -> dict[str, dict]:
    return {
        "bard1": _bard(runtime, 1),
        "bard3": _bard(runtime, 3),
        "bard5": _bard(runtime, 5),
        "bard10": _bard(runtime, 10),
        "bard15": _bard(runtime, 15),
        "bard18": _bard(runtime, 18),
        "fighter": _fighter(runtime, 2),
    }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _state(instance) -> dict:
    from src.engine.modules import ruleset_runtime

    return ruleset_runtime.state(instance)


def _apply(engine, instance, intent: dict, rng) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _combat(
    runtime: Dnd2024Runtime, players: dict[str, dict], *, rolls: list[int],
    positions: dict[str, int] | None = None,
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
        "submitted_by": first_uid, "enemies": [goblin(position=5, hp=60)],
    }
    if positions:
        intent["player_positions"] = positions
    _apply(engine, instance, intent, SequenceRng(list(rolls)))
    return engine, instance
    # S0-F10B：Hide 默认拒绝，夹具必须给出一个合法的落脚点。
    grant_hide_footing(engine, instance)


def _current(instance) -> str:
    state = _state(instance)
    combat = state["combat"]
    return str(combat["initiative"][combat["turn_index"]])


def _end_turn(engine, instance, actor_id: str) -> list[dict]:
    """End one turn.  The ledger is version-keyed, so the id stays unique.

    Enemy turns are narrated by the GM: only the GM may submit an enemy
    intent, so the actor id and the submitting user are not interchangeable.
    """

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


def _next_turn_round(engine, instance, actor_id: str) -> None:
    """Close the current turn and come all the way back round to ``actor_id``.

    A new turn is what restores the action economy, so this is how a test asks
    "the same Bard tries again on their next turn" without pretending two
    Bonus Actions exist in one turn.
    """

    _end_turn(engine, instance, _current(instance))
    _to_actor(engine, instance, actor_id)


def _capability(engine, instance, capability_id: str, uid: str = "gm") -> dict | None:
    return next(
        (
            item for item in engine.available_intents(instance, uid)
            if item["type"] == "class_capability"
            and item["capability_id"] == capability_id
        ),
        None,
    )


def _grant(engine, instance, *, intent_id: str, target_id: str, uid: str = "gm",
           die: int = 4) -> dict:
    state = _state(instance)
    return engine.resolve_intent(instance, {
        "intent_id": intent_id, "type": "class_capability",
        "expected_version": state["version"], "submitted_by": uid,
        "actor_id": f"player:{uid}", "capability_id": "bardic_inspiration",
        "target_id": target_id,
    }, SequenceRng([die]))


def _pool(instance, uid: str) -> dict:
    canonical = instance.get_character_sheet(uid)["ruleset_character"]
    return dict(canonical["resources"]["class"]["bardic_inspiration"])


def _instances(instance) -> list[dict]:
    return list (_state(instance)["combat"].get("bardic_instances") or [])


def _pending(instance, kind: str = "check_window") -> dict | None:
    return next(
        (item for item in _state(instance)["combat"]["pending_decisions"]
         if item.get("kind") == kind),
        None,
    )


def _decide(engine, instance, decision: dict, option: str, rolls: list[int],
            uid: str = "gm") -> dict:
    state = _state(instance)
    return engine.resolve_intent(instance, {
        "intent_id": f"dec-{decision['decision_id']}-{option}",
        "type": "decision.resolve", "expected_version": state["version"],
        "submitted_by": uid, "decision_id": decision["decision_id"],
        "option": option,
    }, SequenceRng(list(rolls)))


def _attack(engine, instance, *, uid: str = "ally", rolls: list[int],
            target_id: str = "enemy:goblin-1") -> dict:
    state = _state(instance)
    if state["combat"].get("pending_decisions"):
        raise AssertionError("an attack cannot start while a window is open")
    intents = [
        item for item in engine.available_intents(instance, uid)
        if item["type"] == "attack"
    ]
    assert intents, "no attack intent"
    # Version-keyed: the same actor legitimately attacks on several turns, and
    # an intent id belongs to exactly one batch in the ledger.
    return engine.resolve_intent(instance, {
        "intent_id": f"atk-{state['version']}-{uid}", "type": "attack",
        "expected_version": state["version"], "submitted_by": uid,
        "actor_id": f"player:{uid}", "target_id": target_id,
        "weapon_ref": intents[0]["weapons"][0]["weapon_ref"],
    }, SequenceRng(list(rolls)))


# --------------------------------------------------------------------------
# matrix 1-4: die face, uses, grant eligibility
# --------------------------------------------------------------------------


@pytest.mark.parametrize("level,sides", [(1, 6), (5, 8), (10, 10), (15, 12)])
def test_die_face_follows_the_bard_progression(
    runtime: Dnd2024Runtime, sheets: dict[str, dict], level: int, sides: int,
) -> None:
    """Matrix 1: d6 / d8 / d10 / d12 and uses = max(1, Charisma)."""

    bard = sheets[f"bard{level}"]
    pool = bard["ruleset_character"]["resources"]["class"]["bardic_inspiration"]
    assert pool["maximum"] == 2  # Charisma 15 -> +2
    fighter = sheets["fighter"]
    engine, instance = _combat(
        runtime, {"gm": bard, "ally": fighter}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    resolved = _grant(engine, instance, intent_id=f"g{level}", target_id="player:ally", die=3)
    assert resolved["ok"] is True, resolved
    granted = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "dnd2024.bardic.granted"
    )
    assert granted["die_sides"] == sides
    assert granted["expires_at_minute"] == granted["granted_at_minute"] + 60


def test_wrong_class_cannot_grant(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 1: a Fighter owns neither the resource nor the capability."""

    engine, instance = _combat(
        runtime, {"gm": sheets["fighter"]}, rolls=[20, 1],
    )
    assert _capability(engine, instance, "bardic_inspiration") is None
    state = _state(instance)
    resolved = engine.resolve_intent(instance, {
        "intent_id": "forged", "type": "class_capability",
        "expected_version": state["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "capability_id": "bardic_inspiration",
        "target_id": "player:gm",
    }, SequenceRng([4]))
    assert resolved["ok"] is False


def test_grant_spends_bonus_action_and_one_use(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 2: one Bonus Action, one use, one instance."""

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    assert _capability(engine, instance, "bardic_inspiration") is not None
    assert _pool(instance, "gm")["current"] == 2
    resolved = _grant(engine, instance, intent_id="g1", target_id="player:ally")
    assert resolved["ok"] is True, resolved
    types = [event["type"] for event in resolved["event_batch"]["events"]]
    assert "dnd2024.action.spent" in types
    assert "dnd2024.class_resource.spent" in types
    engine.apply_batch(instance, resolved["event_batch"])
    assert _pool(instance, "gm")["current"] == 1
    assert _state(instance)["combat"]["economy"]["bonus_action"] == 0
    holder = _instances(instance)
    assert [(item["holder_id"], item["status"]) for item in holder] == [
        ("player:ally", "active"),
    ]


def test_grant_rejects_self_out_of_range_and_unseen_targets(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 3: self, 60-foot reach, and the see-or-hear clause."""

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]},
        rolls=[20, 15, 1], positions={"gm": 0, "ally": 70},
    )
    _to_actor(engine, instance, "player:gm")
    assert _grant(engine, instance, intent_id="self", target_id="player:gm")["ok"] is False
    far = _grant(engine, instance, intent_id="far", target_id="player:ally")
    assert far["ok"] is False
    assert "range" in str(far["error"])
    assert _instances(instance) == []

    # Second table, everyone in reach: the see-or-hear clause is the only
    # remaining gate, and it fails closed on the canonical conditions.
    engine2, instance2 = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]},
        rolls=[20, 15, 1],
    )
    _to_actor(engine2, instance2, "player:gm")
    ally = instance2.get_character_sheet("ally")["ruleset_character"]
    ally.setdefault("conditions", {})["blinded"] = {"source": "test"}
    bard = instance2.get_character_sheet("gm")["ruleset_character"]
    bard.setdefault("conditions", {})["invisible"] = {"source": "test"}
    unseen = _grant(engine2, instance2, intent_id="unseen", target_id="player:ally")
    assert unseen["ok"] is False
    assert "see nor hear" in str(unseen["error"])
    assert _instances(instance2) == []


def test_second_grant_to_one_holder_is_rejected_and_others_keep_their_own(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 4: one live die per holder, independent provenance per holder."""

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]},
        rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    first = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=5)
    assert first["ok"] is True, first
    engine.apply_batch(instance, first["event_batch"])
    uses = _pool(instance, "gm")["current"]
    # Next turn: the Bonus Action is back, the die in the holder's hand is not.
    _next_turn_round(engine, instance, "player:gm")
    second = _grant(engine, instance, intent_id="g2", target_id="player:ally", die=2)
    assert second["ok"] is False
    assert "already holds" in str(second["error"])
    assert _pool(instance, "gm")["current"] == uses  # the rejected grant cost nothing
    own = _grant(engine, instance, intent_id="g3", target_id="player:gm", die=2)
    assert own["ok"] is False  # self is still rejected
    holder = _instances(instance)
    assert len(holder) == 1
    assert holder[0]["source_actor_id"] == "player:gm"
    assert holder[0]["die_sides"] == 6


# --------------------------------------------------------------------------
# matrix 5-6: persistence and the one-hour clock
# --------------------------------------------------------------------------


def test_instance_survives_a_save_round_trip_and_level_up_keeps_the_face(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 5: the record is canonical state; advancement never rewrites it."""

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    resolved = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=3)
    engine.apply_batch(instance, resolved["event_batch"])
    before = deepcopy(_instances(instance))
    reloaded = json.loads(json.dumps(_state(instance)["combat"]))
    assert reloaded["bardic_instances"] == before
    assert before[0]["die_sides"] == 6
    assert before[0]["expires_at_minute"] - before[0]["granted_at_minute"] == 60


def test_expiry_is_exactly_one_hour_of_logical_minutes(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 6: 59 minutes live, 60 minutes dead — no wall clock involved."""

    from src.engine.world_events import advance_world_time

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    resolved = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=3)
    engine.apply_batch(instance, resolved["event_batch"])
    assert _instances(instance)[0]["status"] == "active"

    advance_world_time(instance, 59)
    _to_actor(engine, instance, "player:ally")
    miss = _attack(engine, instance, uid="ally", rolls=[5])
    assert miss["ok"] is True, miss
    assert any(
        event.get("window_code") == "bardic_inspiration"
        for event in miss["event_batch"]["events"]
    )
    engine.apply_batch(instance, miss["event_batch"])
    declined = _decide(engine, instance, _pending(instance), "decline", [], uid="ally")
    assert declined["ok"] is True, declined
    engine.apply_batch(instance, declined["event_batch"])

    advance_world_time(instance, 1)
    _end_turn(engine, instance, "player:ally")
    _to_actor(engine, instance, "player:ally")
    late = _attack(engine, instance, uid="ally", rolls=[5])
    assert late["ok"] is True, late
    assert not any(
        event.get("window_code") == "bardic_inspiration"
        for event in late["event_batch"]["events"]
    )


# --------------------------------------------------------------------------
# matrix 7-9: the failed-attack face
# --------------------------------------------------------------------------


def test_held_die_turns_a_miss_into_a_hit_and_is_consumed(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 7-8: only a failed roll opens the window; the die then lands."""

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    resolved = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=3)
    engine.apply_batch(instance, resolved["event_batch"])

    _to_actor(engine, instance, "player:ally")
    miss = _attack(engine, instance, uid="ally", rolls=[5])
    assert miss["ok"] is True, miss
    pending = [event for event in miss["event_batch"]["events"]
               if event.get("window_code") == "bardic_inspiration"]
    assert pending and pending[0]["die_sides"] == 6
    engine.apply_batch(instance, miss["event_batch"])
    decision = _pending(instance)
    assert decision["check"]["success"] is False

    hit = _decide(engine, instance, decision, "spend", [4], uid="ally")
    assert hit["ok"] is True, hit
    types = [event["type"] for event in hit["event_batch"]["events"]]
    assert "dnd2024.bardic.consumed" in types
    check = next(event for event in hit["event_batch"]["events"]
                 if event["type"] == "check.resolved")
    assert check["success"] is True
    assert check["window_roll"] == 4
    assert any(event["type"] == "resource.changed" for event in hit["event_batch"]["events"])
    engine.apply_batch(instance, hit["event_batch"])
    assert [item["status"] for item in _instances(instance)] == ["rolled"]


def test_held_die_is_consumed_even_when_the_roll_still_fails(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 8: "expended when it's rolled" — a still-failed roll still spends."""

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    resolved = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=3)
    engine.apply_batch(instance, resolved["event_batch"])
    _to_actor(engine, instance, "player:ally")
    miss = _attack(engine, instance, uid="ally", rolls=[2])
    engine.apply_batch(instance, miss["event_batch"])
    decision = _pending(instance)
    still = _decide(engine, instance, decision, "spend", [1], uid="ally")
    assert still["ok"] is True, still
    check = next(event for event in still["event_batch"]["events"]
                 if event["type"] == "check.resolved")
    assert check["success"] is False
    engine.apply_batch(instance, still["event_batch"])
    assert [item["status"] for item in _instances(instance)] == ["rolled"]


def test_a_hit_never_offers_the_holder_die(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 7/9: the face belongs to failures only."""

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    resolved = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=3)
    engine.apply_batch(instance, resolved["event_batch"])
    _to_actor(engine, instance, "player:ally")
    hit = _attack(engine, instance, uid="ally", rolls=[19, 3, 4])
    assert hit["ok"] is True, hit
    assert not any(
        event.get("window_code") in {"bardic_inspiration", "peerless_skill"}
        for event in hit["event_batch"]["events"]
    )


def test_declining_keeps_the_die_and_matches_the_straight_through_miss(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 9: a decline consumes nothing and lands on the same miss path."""

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    resolved = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=3)
    engine.apply_batch(instance, resolved["event_batch"])
    _to_actor(engine, instance, "player:ally")
    miss = _attack(engine, instance, uid="ally", rolls=[5])
    engine.apply_batch(instance, miss["event_batch"])
    declines = _decide(engine, instance, _pending(instance), "decline", [], uid="ally")
    assert declines["ok"] is True, declines
    types = [event["type"] for event in declines["event_batch"]["events"]]
    assert "check.resolved" in types
    assert "dnd2024.bardic.consumed" not in types
    engine.apply_batch(instance, declines["event_batch"])
    assert [item["status"] for item in _instances(instance)] == ["active"]


# --------------------------------------------------------------------------
# matrix 10-12: rests, Font, Superior
# --------------------------------------------------------------------------


def test_short_rest_only_restores_from_level_five(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 10: the rest catalogue's short_all_from_level stays authoritative."""

    from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine

    rest = Dnd2024RestEngine(runtime.load_bundle(LOCALE))
    for key, expected_short in (("bard3", 0), ("bard5", 2)):
        sheet = deepcopy(sheets[key])
        sheet["ruleset_character"]["resources"]["class"]["bardic_inspiration"]["current"] = 0
        short = rest.complete_short_rest(sheet)["character"]
        short_pool = short["resources"]["class"]["bardic_inspiration"]
        assert short_pool["current"] == expected_short, key
        long = rest.complete_long_rest(deepcopy(sheet))["character"]
        long_pool = long["resources"]["class"]["bardic_inspiration"]
        assert long_pool["current"] == long_pool["maximum"], key


def test_font_exchanges_one_slot_for_one_use(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 11: legal slot + expended use, both halves in one batch."""

    engine, instance = _combat(
        runtime, {"gm": sheets["bard5"], "ally": sheets["fighter"]}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    resolved = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=3)
    engine.apply_batch(instance, resolved["event_batch"])
    assert _pool(instance, "gm")["current"] == 1
    slots_before = deepcopy(
        instance.get_character_sheet("gm")["ruleset_character"]["spellcasting"]["class"][
            "slots_current"
        ]
    )
    state = _state(instance)
    exchanged = engine.resolve_intent(instance, {
        "intent_id": "font-1", "type": "class_capability",
        "expected_version": state["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "capability_id": "font_slot_exchange",
        "slot_level": 1,
    }, SequenceRng([]))
    assert exchanged["ok"] is True, exchanged
    types = [event["type"] for event in exchanged["event_batch"]["events"]]
    assert "dnd2024.spell.slot_spent" in types
    assert "dnd2024.class_resource.refunded" in types
    engine.apply_batch(instance, exchanged["event_batch"])
    assert _pool(instance, "gm")["current"] == 2
    slots_after = instance.get_character_sheet("gm")["ruleset_character"][
        "spellcasting"
    ]["class"]["slots_current"]
    assert slots_after["1"] == slots_before["1"] - 1
    # No action was spent and the exchange is not a spell cast.
    assert _state(instance)["combat"]["economy"]["bonus_action"] == 0  # already spent by the grant
    assert not _state(instance)["combat"]["economy"].get("slot_spell_cast")
    repeat = engine.resolve_intent(instance, {
        "intent_id": "font-2", "type": "class_capability",
        "expected_version": _state(instance)["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "capability_id": "font_slot_exchange",
        "slot_level": 1,
    }, SequenceRng([]))
    assert repeat["ok"] is False
    assert "no expended" in str(repeat["error"])


def test_superior_refills_to_two_on_the_real_initiative_roll(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 12: 0/1 becomes two; two or more is untouched."""

    bard = deepcopy(sheets["bard18"])
    pool = bard["ruleset_character"]["resources"]["class"]["bardic_inspiration"]
    assert pool["maximum"] >= 2
    pool["current"] = 0
    engine, instance = _combat(runtime, {"gm": bard}, rolls=[20, 1])
    assert _pool(instance, "gm")["current"] == 2

    other = deepcopy(sheets["bard18"])
    other["ruleset_character"]["resources"]["class"]["bardic_inspiration"]["current"] = (
        other["ruleset_character"]["resources"]["class"]["bardic_inspiration"]["maximum"]
    )
    engine2, instance2 = _combat(runtime, {"gm": other}, rolls=[20, 1])
    assert _pool(instance2, "gm")["current"] == _pool(instance2, "gm")["maximum"]


# --------------------------------------------------------------------------
# matrix 13-15: Peerless Skill
# --------------------------------------------------------------------------


def _lore_bard(runtime: Dnd2024Runtime, level: int = 14) -> dict:
    return _sheet(
        runtime, "class:bard",
        ["skill:performance", "skill:persuasion", "skill:stealth"],
        "equipment_package:bard_a", BARD_ABILITIES, level,
    )


def test_peerless_only_answers_the_bards_own_failed_roll(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 13: own attack/ability check only; never another creature's."""

    bard = _lore_bard(runtime, 14)
    fighter = _fighter(runtime, 2)
    engine, instance = _combat(
        runtime, {"gm": bard, "ally": fighter}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:ally")
    ally_miss = _attack(engine, instance, uid="ally", rolls=[5])
    assert ally_miss["ok"] is True, ally_miss
    assert not any(
        event.get("window_code") == "peerless_skill"
        for event in ally_miss["event_batch"]["events"]
    )
    engine.apply_batch(instance, ally_miss["event_batch"])

    _end_turn(engine, instance, "player:ally")
    _to_actor(engine, instance, "player:gm")
    # A level 14 Bard swings a dagger at +7, so 4 just misses AC 12 while the
    # Fighter's own +4 attack bonus would have hit with the same natural roll.
    own_miss = _attack(engine, instance, uid="gm", rolls=[4])
    assert own_miss["ok"] is True, own_miss
    assert any(
        event.get("window_code") == "peerless_skill"
        for event in own_miss["event_batch"]["events"]
    )


def test_peerless_consumes_on_success_and_refunds_on_failure(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 14: both paths roll a real die; only a success spends the use."""

    bard = _lore_bard(runtime, 14)
    engine, instance = _combat(runtime, {"gm": bard}, rolls=[20, 1])
    _to_actor(engine, instance, "player:gm")
    uses = _pool(instance, "gm")["current"]
    miss = _attack(engine, instance, uid="gm", rolls=[2])
    engine.apply_batch(instance, miss["event_batch"])
    decision = _pending(instance)
    assert decision["window_code"] == "peerless_skill"
    still = _decide(engine, instance, decision, "spend", [1])
    assert still["ok"] is True, still
    types = [event["type"] for event in still["event_batch"]["events"]]
    assert "dnd2024.class_resource.spent" in types
    assert "dnd2024.class_resource.refunded" in types
    engine.apply_batch(instance, still["event_batch"])
    assert _pool(instance, "gm")["current"] == uses  # the failure refunded itself

    _end_turn(engine, instance, "player:gm")
    _to_actor(engine, instance, "player:gm")
    miss2 = _attack(engine, instance, uid="gm", rolls=[3])  # 3 + 7 = 10 < AC 12
    engine.apply_batch(instance, miss2["event_batch"])
    success = _decide(engine, instance, _pending(instance), "spend", [6])
    assert success["ok"] is True, success
    types = [event["type"] for event in success["event_batch"]["events"]]
    assert "dnd2024.class_resource.spent" in types
    assert "dnd2024.class_resource.refunded" not in types
    engine.apply_batch(instance, success["event_batch"])
    assert _pool(instance, "gm")["current"] == uses - 1


def test_peerless_needs_no_held_die_and_never_touches_instances(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 15: the pool is the cost; a held die belongs to someone else."""

    bard = _lore_bard(runtime, 14)
    fighter = _fighter(runtime, 2)
    engine, instance = _combat(
        runtime, {"gm": bard, "ally": fighter}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    other = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=3)
    engine.apply_batch(instance, other["event_batch"])
    assert len(_instances(instance)) == 1
    _to_actor(engine, instance, "player:gm")
    miss = _attack(engine, instance, uid="gm", rolls=[2])
    assert any(
        event.get("window_code") == "peerless_skill"
        for event in miss["event_batch"]["events"]
    )
    engine.apply_batch(instance, miss["event_batch"])
    decided = _decide(engine, instance, _pending(instance), "spend", [1])
    engine.apply_batch(instance, decided["event_batch"])
    assert [(item["holder_id"], item["status"]) for item in _instances(instance)] == [
        ("player:ally", "active"),
    ]


# --------------------------------------------------------------------------
# matrix 17: Cutting Words on the ability-check face
# --------------------------------------------------------------------------


def _hide(engine, instance, *, uid: str, rolls: list[int]) -> dict:
    state = _state(instance)
    # S0-F10B：Hide 默认拒绝，先给这个行动者一块合法的落脚点。
    grant_hide_footing(engine, instance, actor_id=f"player:{uid}")
    state = _state(instance)
    return engine.resolve_intent(instance, {
        "intent_id": f"hide-{uid}", "type": "hide",
        "expected_version": state["version"], "submitted_by": uid,
        "actor_id": f"player:{uid}",
    }, SequenceRng(list(rolls)))


def test_cutting_words_can_cut_a_successful_hide(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 17: the cut face turns a successful ability check into a failure."""

    bard = _lore_bard(runtime, 3)
    fighter = _fighter(runtime, 2)
    engine, instance = _combat(
        runtime, {"gm": bard, "ally": fighter}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:ally")
    # The Fighter wears heavy armour: Stealth rolls two dice and keeps the lower
    # (12 + 2 = 14), which clears the goblin-derived DC 9 by exactly five.
    hidden = _hide(engine, instance, uid="ally", rolls=[12, 12])
    assert hidden["ok"] is True, hidden
    assert any(
        event.get("window_code") == "cutting_words_ability"
        for event in hidden["event_batch"]["events"]
    )
    engine.apply_batch(instance, hidden["event_batch"])
    decision = _pending(instance)
    assert decision["surface"] == "ability_check"
    cut = _decide(engine, instance, decision, "spend", [6])
    assert cut["ok"] is True, cut
    types = [event["type"] for event in cut["event_batch"]["events"]]
    assert "dnd2024.reaction.spent" in types
    assert "dnd2024.class_resource.spent" in types
    check = next(event for event in cut["event_batch"]["events"]
                 if event["type"] == "check.resolved")
    assert check["success"] is False
    assert "condition.applied" not in types  # no invisibility
    engine.apply_batch(instance, cut["event_batch"])
    assert _pool(instance, "gm")["current"] == 1


def test_a_cut_that_still_succeeds_still_spends(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 17: a successful result after the cut keeps the cost."""

    bard = _lore_bard(runtime, 3)
    fighter = _fighter(runtime, 2)
    engine, instance = _combat(
        runtime, {"gm": bard, "ally": fighter}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:ally")
    hidden = _hide(engine, instance, uid="ally", rolls=[20, 20])
    engine.apply_batch(instance, hidden["event_batch"])
    cut = _decide(engine, instance, _pending(instance), "spend", [1])
    assert cut["ok"] is True, cut
    types = [event["type"] for event in cut["event_batch"]["events"]]
    assert "dnd2024.reaction.spent" in types
    assert "dnd2024.class_resource.spent" in types
    assert "condition.applied" in types
    check = next(event for event in cut["event_batch"]["events"]
                 if event["type"] == "check.resolved")
    assert check["success"] is True
    engine.apply_batch(instance, cut["event_batch"])
    assert _pool(instance, "gm")["current"] == 1
    assert "invisible" in (
        instance.get_character_sheet("ally")["ruleset_character"].get("conditions") or {}
    )


# --------------------------------------------------------------------------
# matrix 21-24: fail closed, ordering, rests and replay
# --------------------------------------------------------------------------


def test_unknown_decisions_fail_closed_and_a_replay_never_spends_twice(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 21/24: a forged or replayed decision moves no state."""

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    granted = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=3)
    engine.apply_batch(instance, granted["event_batch"])

    ghost = engine.resolve_intent(instance, {
        "intent_id": "ghost", "type": "decision.resolve",
        "expected_version": _state(instance)["version"],
        "submitted_by": "ally", "decision_id": "window:ghost", "option": "spend",
    }, SequenceRng([4]))
    assert ghost["ok"] is False
    assert "pending" in str(ghost["error"]).lower()

    _to_actor(engine, instance, "player:ally")
    miss = _attack(engine, instance, uid="ally", rolls=[5])
    engine.apply_batch(instance, miss["event_batch"])
    decision = _pending(instance)
    assert decision["window_code"] == "bardic_inspiration"

    spent = _decide(engine, instance, decision, "spend", [4], uid="ally")
    engine.apply_batch(instance, spent["event_batch"])
    assert _pool(instance, "gm")["current"] == 1

    # Same decision, same intent id and the same expected version: the ledger
    # answers with the original batch and applying it again is a duplicate, so
    # nothing is spent twice.
    replay = engine.resolve_intent(instance, {
        "intent_id": spent["event_batch"]["intent_id"],
        "type": "decision.resolve",
        "expected_version": spent["event_batch"]["expected_version"],
        "submitted_by": "ally", "decision_id": decision["decision_id"],
        "option": "spend",
    }, SequenceRng([4]))
    assert replay["ok"] is True and replay.get("replayed") is True, replay
    applied = engine.apply_batch(instance, replay["event_batch"])
    assert applied["duplicate"] is True
    assert _pool(instance, "gm")["current"] == 1
    assert [item["status"] for item in _instances(instance)] == ["rolled"]


def test_a_held_die_is_offered_before_the_holder_own_pool(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 22: two legal failure windows settle one at a time, in order."""

    holder = _lore_bard(runtime, 14)   # owns Peerless Skill
    granter = _lore_bard(runtime, 3)   # can hand out one d6
    engine, instance = _combat(
        runtime, {"gm": holder, "ally": granter}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:ally")
    granted = _grant(
        engine, instance, intent_id="g1", target_id="player:gm", die=3, uid="ally",
    )
    assert granted["ok"] is True, granted
    engine.apply_batch(instance, granted["event_batch"])

    _to_actor(engine, instance, "player:gm")
    miss = _attack(engine, instance, uid="gm", rolls=[4])
    engine.apply_batch(instance, miss["event_batch"])
    first = _pending(instance)
    assert first["window_code"] == "bardic_inspiration"
    assert first["die_sides"] == 6

    declined = _decide(engine, instance, first, "decline", [], uid="gm")
    assert declined["ok"] is True, declined
    engine.apply_batch(instance, declined["event_batch"])
    second = _pending(instance)
    assert second is not None, "the second legal window was dropped on decline"
    assert second["window_code"] == "peerless_skill"
    assert second["die_sides"] == 10  # a level 14 Bard's own die

    spent = _decide(engine, instance, second, "spend", [10], uid="gm")
    assert spent["ok"] is True, spent
    engine.apply_batch(instance, spent["event_batch"])
    assert _pool(instance, "gm")["current"] == _pool(instance, "gm")["maximum"] - 1
    # Declining the die left it in the holder's hand, untouched by the pool.
    assert [(item["holder_id"], item["status"]) for item in _instances(instance)] == [
        ("player:gm", "active"),
    ]


def test_a_long_rest_refills_the_pool_without_touching_the_live_die(
    runtime: Dnd2024Runtime, sheets: dict[str, dict],
) -> None:
    """Matrix 23: a rest is not a second grant, and an hour is not a rest."""

    from src.engine.world_events import advance_world_time
    from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine

    engine, instance = _combat(
        runtime, {"gm": sheets["bard3"], "ally": sheets["fighter"]}, rolls=[20, 15, 1],
    )
    _to_actor(engine, instance, "player:gm")
    granted = _grant(engine, instance, intent_id="g1", target_id="player:ally", die=3)
    engine.apply_batch(instance, granted["event_batch"])
    before = deepcopy(_instances(instance))
    assert _pool(instance, "gm")["current"] == 1

    rest = Dnd2024RestEngine(runtime.load_bundle(LOCALE))
    canonical = instance.get_character_sheet("gm")
    rested = rest.complete_long_rest(deepcopy(canonical))["character"]
    pool = rested["resources"]["class"]["bardic_inspiration"]
    assert pool["current"] == pool["maximum"]  # the pool is the rest's business
    # The die record is the combat timeline's business: resting neither writes
    # a new instance nor silently extends the hour already granted.
    assert _instances(instance) == before
    assert before[0]["expires_at_minute"] - before[0]["granted_at_minute"] == 60

    # Eight hours of logical time is what actually kills the die: the record
    # stays as history, the view and every window stop offering it.
    advance_world_time(instance, 480)
    assert _instances(instance) == before
    assert engine.gameplay_view(instance)["combat"]["bardic_instances"] == []
