"""Post-roll d20 check windows: Lucky, Cutting Words, and Dark One's Own Luck.

Every assertion goes through the same authoritative chain the web layer uses:
``available_intents`` -> ``validate_intent`` -> ``resolve_intent`` ->
``apply_batch``.  Window dice are rolled by ``decision.resolve``, never folded
into the attack's own dice, so the straight-through sequence stays byte
identical to the pre-window engine (asserted explicitly in the G1 tests).
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.engine.player_control import set_control
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
    monk_sheet,
)

LOCALE = "en"
MONK_ABILITIES = {"str": 12, "dex": 14, "con": 13, "int": 8, "wis": 15, "cha": 10}
BARD_ABILITIES = {"str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15}
WARLOCK_ABILITIES = {"str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15}


# --------------------------------------------------------------------------
# sheet construction (real builder + real advancement path)
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


def _class_sheet(
    runtime: Dnd2024Runtime, class_ref: str, skills: list[str], package: str,
    abilities: dict[str, int], level: int,
) -> dict:
    draft = monk_draft(runtime, LOCALE)
    draft.update(
        class_ref=class_ref, class_skill_refs=skills, class_tool_refs=[],
        equipment_package_ref=package, base_abilities=abilities,
    )
    choices = runtime.builder_choices(None, draft)
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
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


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def sheets(runtime: Dnd2024Runtime) -> dict[str, dict]:
    return {
        "monk": monk_sheet(runtime),
        "bard": _class_sheet(
            runtime, "class:bard",
            ["skill:performance", "skill:persuasion", "skill:stealth"],
            "equipment_package:bard_a", BARD_ABILITIES, 3,
        ),
        "warlock": _class_sheet(
            runtime, "class:warlock",
            ["skill:arcana", "skill:deception"],
            "equipment_package:warlock_a", WARLOCK_ABILITIES, 6,
        ),
    }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _state(instance) -> dict:
    return ruleset_runtime.state(instance)


def _combat(
    runtime: Dnd2024Runtime, primary: dict, secondary: dict | None = None,
    *, rolls: list[int], positions: dict[str, int] | None = None,
) -> tuple[Dnd2024CombatEngine, object]:
    """Start combat with the real ordering (players first, then the enemy)."""

    engine, instance = monk_instance(runtime, deepcopy(primary))
    if secondary is not None:
        instance.players["p2"] = {
            "character_name": "Bard", "character_sheet": deepcopy(secondary),
        }
    intent = {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin(position=5, hp=40)],
    }
    if positions is not None:
        intent["player_positions"] = positions
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return engine, instance


def _apply(engine: Dnd2024CombatEngine, instance, intent: dict, rng) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _pending(instance) -> list[dict]:
    return list(_state(instance)["combat"].get("pending_decisions") or [])


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _pool(instance, resource_id: str, uid: str = "gm") -> dict:
    return _canonical(instance, uid).get("resources", {}).get("class", {}).get(resource_id) or {}


def _hp(instance, uid: str = "gm") -> int:
    return int(_canonical(instance, uid)["resources"]["hp"])


def _grant_feat(runtime: Dnd2024Runtime, instance, uid: str = "gm", feat_id: str = "lucky") -> None:
    """Give an existing character the origin feat through the real sync path."""

    sheet = deepcopy(instance.get_character_sheet(uid))
    character = sheet["ruleset_character"]
    refs = character.setdefault("features", {}).setdefault("feat_refs", [])
    if f"feat:{feat_id}" not in refs:
        refs.append(f"feat:{feat_id}")
    sheet["ruleset_character"] = Dnd2024RestEngine(
        runtime.load_bundle(LOCALE),
    ).sync_resources(character)
    instance.set_character_sheet(uid, sheet)


def _set_condition(instance, uid: str, conditions: dict) -> None:
    _canonical(instance, uid)["conditions"] = dict(conditions)


def _set_enemy_condition(instance, conditions: dict) -> None:
    _state(instance)["combat"]["enemies"]["goblin-1"]["conditions"] = dict(conditions)


def _mutate_enemy_attack(instance, **fields) -> None:
    _state(instance)["combat"]["enemies"]["goblin-1"]["attacks"][0].update(fields)


def _attack_intent(
    instance, *, intent_id: str, actor_id: str, target_id: str,
    weapon_ref: str = "", attack_id: str = "", spend_luck: bool = False,
) -> dict:
    intent = {
        "intent_id": intent_id, "type": "attack",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": actor_id, "target_id": target_id,
    }
    if weapon_ref:
        intent["weapon_ref"] = weapon_ref
    if attack_id:
        intent["attack_id"] = attack_id
    if spend_luck:
        intent["spend_luck"] = True
    return intent


def _enemy_attack(engine, instance, rolls: list[int], *, intent_id: str = "g1", target_id: str = "player:gm"):
    return _apply(engine, instance, _attack_intent(
        instance, intent_id=intent_id, actor_id="enemy:goblin-1",
        target_id=target_id, attack_id="scimitar",
    ), SequenceRng(list(rolls)))


def _resolve(
    engine, instance, decision: dict, option: str, *, by: str | None = None,
    rng=None, intent_id: str = "decide-1",
) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": intent_id, "type": "decision.resolve",
        "expected_version": _state(instance)["version"],
        "submitted_by": by or decision["assigned_to"],
        "decision_id": decision["decision_id"], "option": option,
    }, rng or SequenceRng([]))


def _checked(events: list[dict], kind: str = "attack") -> dict:
    return next(event for event in events if event.get("type") == "check.resolved" and event.get("kind") == kind)


WINDOW_EVENTS = {
    "intent.submitted", "dnd2024.decision.pending", "dnd2024.decision.cleared",
}


# --------------------------------------------------------------------------
# L — Lucky
# --------------------------------------------------------------------------


def test_lucky_advantage_face_rolls_two_d20_before_consuming_a_point(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[20, 1])
    _grant_feat(runtime, instance)
    events = _apply(engine, instance, _attack_intent(
        instance, intent_id="atk-1", actor_id="player:gm",
        target_id="enemy:goblin-1", weapon_ref="unarmed_strike", spend_luck=True,
    ), SequenceRng([15, 3, 4]))

    assert [event["type"] for event in events[:3]] == [
        "intent.submitted", "dnd2024.attack.spent", "dnd2024.class_resource.spent",
    ]
    assert events[2]["resource_id"] == "lucky_points"
    check = _checked(events)
    assert check["rolls"] == [15, 3]
    assert check["advantage_mode"] == "advantage"
    assert check["advantage_sources"] == [{"code": "lucky", "mode": "advantage"}]
    assert _pool(instance, "lucky_points")["current"] == 1


def test_lucky_disadvantage_window_is_offered_after_the_attack_roll(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    events = _enemy_attack(engine, instance, [15])

    assert events[-1]["type"] == "dnd2024.decision.pending"
    decision = _pending(instance)[0]
    assert decision["kind"] == "check_window"
    assert decision["window_code"] == "lucky_disadvantage"
    assert decision["window_owner_id"] == "player:gm"
    assert decision["assigned_to"] == "gm"
    assert decision["options"] == ["spend", "decline"]
    assert decision["die_sides"] == 20
    assert decision["check"]["natural"] == 15
    assert decision["check"]["success"] is True

    events = _resolve(engine, instance, decision, "spend", rng=SequenceRng([2]))
    check = _checked(events)
    assert check["natural"] == 2
    assert check["total"] == 6
    assert check["success"] is False
    assert check["window"] == "lucky_disadvantage"
    assert check["window_roll"] == 2
    assert _pool(instance, "lucky_points")["current"] == 1


def test_declining_a_lucky_window_reproduces_the_straight_through_sequence(runtime, sheets) -> None:
    """L2 decline + G1: the pending/cleared pair aside, nothing changes."""

    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    first = _enemy_attack(engine, instance, [15])
    decision = _pending(instance)[0]
    rng = SequenceRng([4])
    second = _resolve(engine, instance, decision, "decline", rng=rng)
    assert rng.values == []
    combined = [
        event for event in [*first, *second]
        if event["type"] not in WINDOW_EVENTS
    ]

    baseline_engine, baseline = _combat(runtime, sheets["monk"], rolls=[1, 20])
    base_rng = SequenceRng([15, 4])
    baseline_events = _apply(
        baseline_engine, baseline,
        _attack_intent(
            baseline, intent_id="g1", actor_id="enemy:goblin-1",
            target_id="player:gm", attack_id="scimitar",
        ),
        base_rng,
    )
    assert base_rng.values == []
    expected = [
        event for event in baseline_events if event["type"] not in WINDOW_EVENTS
    ]

    assert combined == expected
    assert _hp(instance) == _hp(baseline)


def test_lucky_cancels_advantage_on_an_enemy_attack(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    _set_condition(instance, "gm", {"faerie_fire": {}})
    events = _enemy_attack(engine, instance, [15, 3])
    decision = _pending(instance)[0]
    assert decision["check"]["advantage_mode"] == "advantage"

    events = _resolve(engine, instance, decision, "spend", rng=SequenceRng([10]))
    check = _checked(events)
    assert check["advantage_mode"] == ""
    assert check["advantage_sources"] == [
        {"code": "faerie_fire", "mode": "advantage"},
        {"code": "lucky", "mode": "disadvantage"},
    ]
    assert check["natural"] == 10
    assert check["total"] == 14
    assert check["success"] is True


def test_an_attack_roll_that_is_already_at_disadvantage_offers_no_lucky_window(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    _set_condition(instance, "gm", {"dodging": {}})
    events = _enemy_attack(engine, instance, [15, 3])

    assert [event["type"] for event in events] == [
        "intent.submitted", "dnd2024.attack.spent", "check.resolved",
    ]
    assert _pending(instance) == []
    check = _checked(events)
    assert check["advantage_mode"] == "disadvantage"
    assert check["success"] is False
    assert _pool(instance, "lucky_points")["current"] == 2


def test_a_natural_twenty_is_offered_and_lucky_can_take_the_critical_away(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    events = _enemy_attack(engine, instance, [20])
    decision = _pending(instance)[0]
    assert decision["window_code"] == "lucky_disadvantage"
    assert decision["check"]["critical"] is True

    events = _resolve(engine, instance, decision, "spend", rng=SequenceRng([3]))
    check = _checked(events)
    assert check["natural"] == 3
    assert check["critical"] is False
    assert check["success"] is False


def test_declining_keeps_the_natural_twenty_critical(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    _enemy_attack(engine, instance, [20])
    decision = _pending(instance)[0]
    events = _resolve(engine, instance, decision, "decline")
    check = _checked(events)
    damage = next(event for event in events if event["type"] == "resource.changed")

    assert check["natural"] == 20
    assert check["critical"] is True
    assert damage["critical"] is True
    assert len(damage["rolls"]) == 2


def test_a_natural_one_never_opens_a_window(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    events = _enemy_attack(engine, instance, [1])

    assert _pending(instance) == []
    assert [event["type"] for event in events][-1] == "check.resolved"
    assert _checked(events)["success"] is False
    assert _pool(instance, "lucky_points")["current"] == 2


def test_an_empty_lucky_pool_offers_no_window_and_rejects_the_declaration(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    _pool(instance, "lucky_points")["current"] = 0

    events = _enemy_attack(engine, instance, [15])
    assert _pending(instance) == []
    assert [event["type"] for event in events].count("check.resolved") == 1

    bare_engine, bare = _combat(runtime, sheets["monk"], rolls=[20, 1])
    assert bare_engine.validate_intent(bare, _attack_intent(
        bare, intent_id="atk-2", actor_id="player:gm",
        target_id="enemy:goblin-1", weapon_ref="unarmed_strike", spend_luck=True,
    ))["error"] == "lucky pool is empty"


def test_lucky_advantage_face_also_covers_death_saves_and_spell_attacks(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[20, 1])
    _grant_feat(runtime, instance)
    _canonical(instance)["resources"]["hp"] = 0
    events = _apply(engine, instance, {
        "intent_id": "ds-1", "type": "death_save",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "spend_luck": True,
    }, SequenceRng([15, 3]))

    assert events[1]["type"] == "dnd2024.class_resource.spent"
    check = _checked(events, "death_save")
    assert check["rolls"] == [15, 3]
    assert check["advantage_sources"] == [{"code": "lucky", "mode": "advantage"}]
    assert check["success"] is True
    assert _pool(instance, "lucky_points")["current"] == 1

    engine, instance = _combat(runtime, sheets["monk"], rolls=[20, 1])
    _grant_feat(runtime, instance)
    _canonical(instance)["spellcasting"] = {"class": {
        "ability": "int", "slots_current": {}, "concentration": None,
        "prepared_spell_refs": [], "cantrip_refs": ["spell:fire_bolt"],
    }}
    events = _apply(engine, instance, {
        "intent_id": "cast-1", "type": "cast_spell",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:fire_bolt", "target_ids": ["enemy:goblin-1"],
        "slot_level": 0, "spend_luck": True,
    }, SequenceRng([15, 3, 4]))

    check = _checked(events, "spell_attack")
    assert check["rolls"] == [15, 3]
    assert check["advantage_sources"] == [{"code": "lucky", "mode": "advantage"}]
    assert _pool(instance, "lucky_points")["current"] == 1


def test_a_blessed_attackers_bonus_die_survives_the_lucky_window(runtime, sheets) -> None:
    """The window re-roll keeps the settled ``check_bonus`` (S-P3 #9 migration)."""

    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    _set_enemy_condition(instance, {"blessed": {"source": "test"}})
    events = _enemy_attack(engine, instance, [15, 4])

    decision = _pending(instance)[0]
    assert decision["window_code"] == "lucky_disadvantage"
    assert decision["check"]["check_bonus"] == 4
    assert decision["check"]["check_bonus_rolls"] == [
        {"condition": "blessed", "die": 4, "roll": 4},
    ]

    events = _resolve(engine, instance, decision, "spend", rng=SequenceRng([2]))
    check = _checked(events)
    assert check["natural"] == 2
    assert check["modifier"] == 4
    # The d4 is a settled bonus, not a window die: it is re-summed into the
    # total but never renamed into the window_bonus family.
    assert check["check_bonus"] == 4
    assert check["total"] == 10
    assert "window_bonus" not in check
    assert "window_bonus_rolls" not in check
    assert check["success"] is False


# --------------------------------------------------------------------------
# C — Cutting Words
# --------------------------------------------------------------------------


def test_cutting_words_check_face_can_turn_a_hit_into_a_miss(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    events = _enemy_attack(engine, instance, [10])

    decision = _pending(instance)[0]
    assert decision["window_code"] == "cutting_words_check"
    assert decision["window_owner_id"] == "player:p2"
    assert decision["assigned_to"] == "p2"
    assert decision["die_sides"] == 6
    assert decision["check"]["total"] == 14
    assert decision["check"]["success"] is True

    events = _resolve(engine, instance, decision, "spend", by="p2", rng=SequenceRng([6]))
    assert [event["type"] for event in events] == [
        "intent.submitted", "dnd2024.decision.cleared",
        "dnd2024.class_resource.spent", "dnd2024.reaction.spent", "check.resolved",
    ]
    assert events[2]["resource_id"] == "bardic_inspiration"
    check = _checked(events)
    assert check["total"] == 8
    assert check["check_penalty"] == 6
    assert check["check_penalty_rolls"] == [6]
    assert check["success"] is False
    assert _pending(instance) == []
    assert _pool(instance, "bardic_inspiration", "p2")["current"] == 1
    assert _state(instance)["combat"]["reactions"]["player:p2"] == 0


def test_the_bard_keeps_the_choice_even_when_the_cut_cannot_flip_the_hit(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    _enemy_attack(engine, instance, [18])
    decision = _pending(instance)[0]
    assert decision["check"]["total"] == 22

    events = _resolve(engine, instance, decision, "spend", by="p2", rng=SequenceRng([6]))
    check = _checked(events)
    assert check["total"] == 16
    assert check["success"] is True
    assert next(event for event in events if event["type"] == "resource.changed")["amount"] > 0


def test_cutting_words_needs_the_bard_within_sixty_feet(runtime, sheets) -> None:
    engine, instance = _combat(
        runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20],
        positions={"gm": 0, "p2": 100},
    )
    events = _enemy_attack(engine, instance, [10])

    assert _pending(instance) == []
    assert [event["type"] for event in events].count("resource.changed") == 1


def test_cutting_words_needs_a_pool_point_and_a_reaction(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    _pool(instance, "bardic_inspiration", "p2")["current"] = 0
    assert _pending(instance) == []
    _enemy_attack(engine, instance, [10])
    assert _pending(instance) == []

    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    _state(instance)["combat"]["reactions"]["player:p2"] = 0
    _enemy_attack(engine, instance, [10])
    assert _pending(instance) == []


def test_one_bard_can_only_cut_one_face_of_the_same_attack(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    _enemy_attack(engine, instance, [15])
    decision = _pending(instance)[0]
    assert decision["check"]["total"] == 19

    events = _resolve(engine, instance, decision, "spend", by="p2", rng=SequenceRng([1]))
    assert _checked(events)["success"] is True
    assert _pending(instance) == []
    assert [event["type"] for event in events].count("dnd2024.class_resource.spent") == 1


def test_a_bard_that_is_the_defender_spends_its_single_reaction_on_its_own_attack(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    events = _enemy_attack(engine, instance, [10], target_id="player:p2")
    decision = _pending(instance)[0]
    assert decision["window_owner_id"] == decision["target_id"] == "player:p2"

    events = _resolve(engine, instance, decision, "spend", by="p2", rng=SequenceRng([1]))
    check = _checked(events)
    assert check["total"] == 13
    assert check["success"] is True
    assert _pending(instance) == []
    assert _state(instance)["combat"]["reactions"]["player:p2"] == 0
    assert [event["type"] for event in events].count("dnd2024.class_resource.spent") == 1


def test_cutting_words_damage_face_reduces_committed_damage(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    before = _hp(instance)
    _enemy_attack(engine, instance, [15])
    check_decision = _pending(instance)[0]
    _resolve(engine, instance, check_decision, "decline", by="p2", intent_id="decide-1")

    damage_decision = _pending(instance)[0]
    assert damage_decision["kind"] == "damage_window"
    assert damage_decision["window_code"] == "cutting_words_damage"
    assert damage_decision["damage_event"]["amount"] == 3
    assert damage_decision["damage_sides"] == 6

    events = _resolve(engine, instance, damage_decision, "spend", by="p2",
                      rng=SequenceRng([4]), intent_id="decide-2")
    assert events[2]["type"] == "dnd2024.class_resource.spent"
    assert events[3]["type"] == "dnd2024.reaction.spent"
    window_check = _checked(events, "damage_window")
    assert window_check["penalty"] == 3
    damage = next(event for event in events if event["type"] == "resource.changed")
    assert damage["amount"] == 0
    assert damage["delta"] == 0
    assert damage["penalty"] == 4
    assert _hp(instance) == before
    assert _pending(instance) == []
    assert _pool(instance, "bardic_inspiration", "p2")["current"] == 1


def test_declining_the_damage_face_submits_the_original_damage(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    before = _hp(instance)
    _enemy_attack(engine, instance, [15])
    _resolve(engine, instance, _pending(instance)[0], "decline", by="p2", intent_id="decide-1")
    events = _resolve(engine, instance, _pending(instance)[0], "decline", by="p2", intent_id="decide-2")

    damage = next(event for event in events if event["type"] == "resource.changed")
    assert damage["amount"] == 3
    assert _hp(instance) == before - 3
    assert _pending(instance) == []


# --------------------------------------------------------------------------
# O — Dark One's Own Luck
# --------------------------------------------------------------------------


def _zero_hp(instance, uid: str = "gm") -> None:
    _canonical(instance, uid)["resources"]["hp"] = 0


def test_dark_ones_own_luck_adds_a_d10_to_a_failed_death_save(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["warlock"], rolls=[20, 1])
    _zero_hp(instance)
    events = _apply(engine, instance, {
        "intent_id": "ds-1", "type": "death_save",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([5]))

    assert events[-1]["type"] == "dnd2024.decision.pending"
    decision = _pending(instance)[0]
    assert decision["window_code"] == "dark_ones_own_luck"
    assert decision["surface"] == "death_save"
    assert decision["die_sides"] == 10
    assert decision["check"]["natural"] == 5
    assert decision["check"]["success"] is False

    events = _resolve(engine, instance, decision, "spend", rng=SequenceRng([8]))
    check = _checked(events, "death_save")
    assert check["window_bonus"] == 8
    assert check["total"] == 13
    assert check["success"] is True
    resolved = next(event for event in events if event["type"] == "dnd2024.death_save.resolved")
    assert (resolved["successes"], resolved["failures"], resolved["roll"]) == (1, 0, 13)
    assert _pool(instance, "dark_ones_own_luck")["current"] == 1


@pytest.mark.parametrize("roll", [1, 10, 20])
def test_dark_ones_own_luck_does_not_open_on_a_decisive_death_save(runtime, sheets, roll: int) -> None:
    engine, instance = _combat(runtime, sheets["warlock"], rolls=[20, 1])
    _zero_hp(instance)
    events = _apply(engine, instance, {
        "intent_id": "ds-1", "type": "death_save",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([roll]))

    assert _pending(instance) == []
    assert events[-1]["type"] in {
        "dnd2024.death_save.resolved", "dnd2024.turn.advanced",
    }
    assert _pool(instance, "dark_ones_own_luck")["current"] == 2


def test_bless_can_make_a_death_save_succeed_without_opening_dool(runtime, sheets) -> None:
    """A bless-boosted save that reaches 10 is decisive even though the d20 is low."""

    engine, instance = _combat(runtime, sheets["warlock"], rolls=[20, 1])
    _zero_hp(instance)
    _canonical(instance).setdefault("conditions", {})["blessed"] = {"source": "bless"}
    events = _apply(engine, instance, {
        "intent_id": "ds-1", "type": "death_save",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([8, 4]))

    check = _checked(events, "death_save")
    assert check["natural"] == 8
    assert check["check_bonus"] == 4
    assert check["total"] == 12
    assert check["success"] is True
    assert _pending(instance) == []
    assert _pool(instance, "dark_ones_own_luck")["current"] == 2


def test_dark_ones_own_luck_holds_off_the_concentration_failure(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["warlock"], rolls=[1, 20])
    _canonical(instance)["spellcasting"]["class"]["concentration"] = {"spell_ref": "spell:bless"}
    events = _enemy_attack(engine, instance, [15, 4, 5])

    decision = _pending(instance)[0]
    assert decision["surface"] == "concentration"
    assert decision["check"]["total"] == 6
    assert decision["check"]["target"] == 10

    events = _resolve(engine, instance, decision, "spend", rng=SequenceRng([8]))
    check = _checked(events, "concentration")
    assert check["window_bonus"] == 8
    assert check["total"] == 14
    assert check["success"] is True
    assert "dnd2024.concentration.ended" not in [event["type"] for event in events]


def test_declining_the_concentration_window_ends_concentration(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["warlock"], rolls=[1, 20])
    _canonical(instance)["spellcasting"]["class"]["concentration"] = {"spell_ref": "spell:bless"}
    _enemy_attack(engine, instance, [15, 4, 5])
    decision = _pending(instance)[0]
    events = _resolve(engine, instance, decision, "decline")

    assert _checked(events, "concentration")["success"] is False
    ended = next(event for event in events if event["type"] == "dnd2024.concentration.ended")
    assert ended["reason"] == "failed_concentration_save"
    assert _pool(instance, "dark_ones_own_luck")["current"] == 2


def test_dark_ones_own_luck_is_once_per_roll(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["warlock"], rolls=[20, 1])
    _zero_hp(instance)
    _apply(engine, instance, {
        "intent_id": "ds-1", "type": "death_save",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([5]))
    decision = _pending(instance)[0]
    events = _resolve(engine, instance, decision, "spend", rng=SequenceRng([8]))

    assert [event["type"] for event in events].count("dnd2024.class_resource.spent") == 1
    assert _pending(instance) == []
    replay = engine.validate_intent(instance, {
        "intent_id": "decide-2", "type": "decision.resolve",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"], "option": "spend",
    })
    assert replay["ok"] is False
    assert replay["error"] == "pending decision or option is invalid"


# --------------------------------------------------------------------------
# G — gating and the straight-through invariant
# --------------------------------------------------------------------------


def test_a_pending_check_window_gates_every_other_intent(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    _enemy_attack(engine, instance, [10])
    assert _pending(instance)[0]["kind"] == "check_window"

    version = _state(instance)["version"]
    for intent in (
        _attack_intent(instance, intent_id="atk-1", actor_id="player:gm",
                       target_id="enemy:goblin-1", weapon_ref="unarmed_strike"),
        {"intent_id": "end-1", "type": "end_turn", "expected_version": version,
         "submitted_by": "gm", "actor_id": "player:gm"},
        {"intent_id": "cast-1", "type": "cast_spell", "expected_version": version,
         "submitted_by": "gm", "actor_id": "player:gm",
         "spell_ref": "spell:fire_bolt", "target_ids": ["enemy:goblin-1"]},
    ):
        verdict = engine.validate_intent(instance, intent)
        assert verdict["ok"] is False, intent["type"]
        assert verdict["error"] == "resolve the pending check window first"

    message = engine.validate_intent(instance, {
        "intent_id": "msg-1", "type": "combat.message", "expected_version": version,
        "submitted_by": "gm", "text": "hold the line",
    })
    assert message["ok"] is True


def test_a_pending_damage_window_gates_every_other_intent(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    _enemy_attack(engine, instance, [15])
    _resolve(engine, instance, _pending(instance)[0], "decline", by="p2", intent_id="decide-1")
    assert _pending(instance)[0]["kind"] == "damage_window"

    verdict = engine.validate_intent(instance, _attack_intent(
        instance, intent_id="atk-1", actor_id="player:gm",
        target_id="enemy:goblin-1", weapon_ref="unarmed_strike",
    ))
    assert verdict["ok"] is False
    assert verdict["error"] == "resolve the pending check window first"


def test_only_the_window_owner_may_resolve_it(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    _enemy_attack(engine, instance, [10])
    decision = _pending(instance)[0]

    verdict = engine.validate_intent(instance, {
        "intent_id": "decide-1", "type": "decision.resolve",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"], "option": "spend",
    })
    assert verdict["ok"] is False
    assert verdict["error"] == "only the window owner can resolve this check window"


def test_straight_through_attacks_and_death_saves_keep_their_exact_dice(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[20, 1])
    rng = SequenceRng([15, 4])
    events = _apply(engine, instance, _attack_intent(
        instance, intent_id="atk-1", actor_id="player:gm",
        target_id="enemy:goblin-1", weapon_ref="unarmed_strike",
    ), rng)
    assert rng.values == []
    assert [event["type"] for event in events] == [
        "intent.submitted", "dnd2024.attack.spent", "check.resolved", "resource.changed",
    ]
    assert _pending(instance) == []

    engine, instance = _combat(runtime, sheets["monk"], rolls=[20, 1])
    _zero_hp(instance)
    rng = SequenceRng([7])
    events = _apply(engine, instance, {
        "intent_id": "ds-1", "type": "death_save",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, rng)
    assert rng.values == []
    assert [event["type"] for event in events] == [
        "intent.submitted", "check.resolved",
        "dnd2024.death_save.resolved", "dnd2024.turn.advanced",
    ]
    assert _pending(instance) == []


# --------------------------------------------------------------------------
# A — deterministic AI ladders
# --------------------------------------------------------------------------


def test_ai_lucky_spends_only_when_taking_the_lower_die_can_flip_the_hit(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    set_control(instance, "gm", "ai")
    _enemy_attack(engine, instance, [15])

    intent = engine.next_automatic_intent(instance)
    assert intent is not None
    assert intent["option"] == "spend"
    assert intent["type"] == "decision.resolve"

    engine, instance = _combat(runtime, sheets["monk"], rolls=[1, 20])
    _grant_feat(runtime, instance)
    set_control(instance, "gm", "ai")
    _mutate_enemy_attack(instance, attack_bonus=12)
    _enemy_attack(engine, instance, [10])

    intent = engine.next_automatic_intent(instance)
    assert intent is not None
    assert intent["option"] == "decline"


def test_ai_cutting_words_is_deterministic_on_both_faces(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    set_control(instance, "p2", "ai")
    _enemy_attack(engine, instance, [10])
    assert engine.next_automatic_intent(instance)["option"] == "spend"

    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    set_control(instance, "p2", "ai")
    _enemy_attack(engine, instance, [18])
    intent = engine.next_automatic_intent(instance)
    assert intent["option"] == "decline"
    _apply(engine, instance, intent, SequenceRng([]))

    damage_decision = _pending(instance)[0]
    assert damage_decision["kind"] == "damage_window"
    assert engine.next_automatic_intent(instance)["option"] == "spend"

    engine, instance = _combat(runtime, sheets["monk"], sheets["bard"], rolls=[1, 1, 20])
    set_control(instance, "p2", "ai")
    _mutate_enemy_attack(instance, damage="1d1")
    _enemy_attack(engine, instance, [18])
    _apply(engine, instance, engine.next_automatic_intent(instance), SequenceRng([]))
    damage_decision = _pending(instance)[0]
    assert damage_decision["damage_event"]["amount"] == 1
    assert engine.next_automatic_intent(instance)["option"] == "decline"


def test_ai_dark_ones_own_luck_spends_when_the_d10_reaches_the_dc(runtime, sheets) -> None:
    engine, instance = _combat(runtime, sheets["warlock"], rolls=[20, 1])
    _zero_hp(instance)
    set_control(instance, "gm", "ai")
    _apply(engine, instance, {
        "intent_id": "ds-1", "type": "death_save",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([5]))

    intent = engine.next_automatic_intent(instance)
    assert intent is not None
    assert intent["option"] == "spend"

    engine, instance = _combat(runtime, sheets["warlock"], rolls=[1, 20])
    character = _canonical(instance)
    character["spellcasting"]["class"]["concentration"] = {"spell_ref": "spell:bless"}
    character["resources"]["hp"] = 200
    character["resources"]["max_hp"] = 200
    set_control(instance, "gm", "ai")
    _mutate_enemy_attack(instance, damage="4d10+40")
    _enemy_attack(engine, instance, [15, 10, 10, 10, 10, 5])
    decision = _pending(instance)[0]
    assert decision["surface"] == "concentration"
    assert decision["check"]["target"] > decision["check"]["total"] + decision["die_sides"]

    intent = engine.next_automatic_intent(instance)
    assert intent is not None
    assert intent["option"] == "decline"
