from __future__ import annotations
from src.engine.modules import ruleset_runtime

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.resting import Dnd2024RestEngine
from src.rulesets.events import EventBatchError

from dnd2024_monk_common import SequenceRng, monk_draft, monk_instance, start_combat


def paladin_sheet(
    runtime: Dnd2024Runtime, *, level: int = 2, prepared_smite: bool = False
) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref="class:paladin",
        class_skill_refs=["skill:athletics", "skill:intimidation"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:paladin_a",
        class_spell_choices={
            "cantrip_refs": [],
            "spellbook_refs": [],
            "prepared_spell_refs": [
                "spell:heroism",
                "spell:divine_smite" if prepared_smite else "spell:cure_wounds",
            ],
        },
    )
    sheet = runtime.finalize_character(None, draft)
    if level == 2:
        choices = {
            "class_spell_choices": {
                "cantrip_refs": [],
                "spellbook_refs": [],
                "prepared_spell_refs": [
                    "spell:heroism",
                    "spell:cure_wounds",
                    "spell:bless",
                ],
            }
        }
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def smite_instance(*, level: int = 2, prepared_smite: bool = False):
    runtime = Dnd2024Runtime()
    sheet = paladin_sheet(runtime, level=level, prepared_smite=prepared_smite)
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance, hp=100)
    return runtime, engine, instance


def intent(instance, kind: str, **extra) -> dict:
    return {
        "intent_id": f"{kind}:{ruleset_runtime.state(instance)['version']}",
        "type": kind,
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
        "actor_id": "player:gm",
        **extra,
    }


def attack(engine, instance, *, roll: int = 15, weapon: str = "item:longsword"):
    resolved = engine.resolve_intent(
        instance,
        intent(instance, "attack", target_id="enemy:goblin-1", weapon_ref=weapon),
        SequenceRng([roll, 1, 1, 1, 1]),
    )
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return resolved


def smite_option(engine, instance):
    actions = engine.available_intents(instance, "gm")
    return next(
        (
            spell
            for action in actions
            if action["type"] == "cast_spell"
            for spell in action["spells"]
            if spell["spell_ref"] == "spell:divine_smite"
        ),
        None,
    )


@pytest.mark.parametrize("weapon", ["item:longsword", "unarmed_strike"])
def test_prepared_smite_is_offered_after_melee_hit(weapon) -> None:
    _runtime, engine, instance = smite_instance(level=1, prepared_smite=True)
    assert smite_option(engine, instance) is None
    attack(engine, instance, weapon=weapon)
    option = smite_option(engine, instance)
    assert option is not None
    assert option["available_slot_levels"] == [1]
    assert option["trigger_target_id"] == "enemy:goblin-1"


def cast(engine, instance, slot=1, **extra):
    request = intent(
        instance,
        "cast_spell",
        spell_ref="spell:divine_smite",
        target_id="enemy:goblin-1",
        slot_level=slot,
        **extra,
    )
    return engine.resolve_intent(
        instance, request, SequenceRng([3, 4, 5, 6, 7, 8, 1, 2])
    )


def test_default_paladin_spellbook_stays_visible_before_a_hit() -> None:
    _runtime, engine, instance = smite_instance()
    player = next(
        row
        for row in engine.gameplay_view(instance)["combat"]["actors"]
        if row["actor_id"] == "player:gm"
    )
    assert (
        next(
            row for row in player["spells"] if row["spell_ref"] == "spell:divine_smite"
        )["blocked_reason"]
        == "requires_melee_hit"
    )
    actor = engine._player_view(
        "gm", instance.get_character_sheet("gm")["ruleset_character"]
    )
    actor["spell_refs"] = ["spell:searing_smite"]
    assert (
        engine._spell_statuses(
            instance, actor, ruleset_runtime.state(instance)["combat"]["economy"],
        )[0]["blocked_reason"]
        == "not_automated"
    )


@pytest.mark.parametrize(
    "roll,position,weapon", [(1, 5, "item:longsword"), (15, 30, "item:javelin")]
)
def test_a_miss_or_thrown_attack_does_not_offer_smite(roll, position, weapon) -> None:
    _runtime, engine, instance = smite_instance(level=1, prepared_smite=True)
    ruleset_runtime.state(instance)["combat"]["positions"]["enemy:goblin-1"] = position
    attack(engine, instance, roll=roll, weapon=weapon)
    assert smite_option(engine, instance) is None
    assert ruleset_runtime.state(instance)["combat"]["pending_decisions"] == []
    assert not cast(engine, instance)["ok"]


def test_free_cast_uses_class_resource_and_long_rest_recovers_it() -> None:
    runtime, engine, instance = smite_instance()
    attack(engine, instance)
    result = cast(engine, instance, 0)
    assert result["ok"], result
    engine.apply_batch(instance, result["event_batch"])
    character = instance.get_character_sheet("gm")["ruleset_character"]
    assert character["resources"]["class"]["paladins_smite"]["current"] == 0
    assert character["spellcasting"]["class"]["slots_current"]["1"] == 2
    assert not ruleset_runtime.state(instance)["combat"]["economy"].get("slot_spell_cast")
    resting = Dnd2024RestEngine(runtime.load_bundle("en"))
    assert (
        resting.sync_resources(character)["resources"]["class"]["paladins_smite"][
            "current"
        ]
        == 0
    )
    short = resting.complete_short_rest(character)["character"]
    assert short["resources"]["class"]["paladins_smite"]["current"] == 0
    long = resting.complete_long_rest(character)["character"]
    assert long["resources"]["class"]["paladins_smite"]["current"] == 1


def test_no_slot_free_cast_requires_the_granted_level_two_resource() -> None:
    _runtime, engine, instance = smite_instance(level=1, prepared_smite=True)
    attack(engine, instance)
    assert not cast(engine, instance, 0)["ok"]
    assert ruleset_runtime.state(instance)["combat"]["pending_decisions"]


def test_hit_decision_blocks_other_actions_and_decline_applies_damage_once() -> None:
    _runtime, engine, instance = smite_instance()
    attack(engine, instance)
    decision = ruleset_runtime.state(instance)["combat"]["pending_decisions"][0]
    assert engine.next_automatic_intent(instance) is None
    assert not engine.validate_intent(instance, intent(instance, "end_turn"))["ok"]
    assert not engine.validate_intent(instance, intent(instance, "move", distance=5))[
        "ok"
    ]
    request = intent(
        instance,
        "decision.resolve",
        decision_id=decision["decision_id"],
        option="decline",
    )
    result = engine.resolve_intent(instance, request, SequenceRng([]))
    assert result["ok"], result
    engine.apply_batch(instance, result["event_batch"])
    assert (
        ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"]
        == 100 - decision["damage_event"]["amount"]
    )
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 1
    assert ruleset_runtime.state(instance)["combat"]["pending_decisions"] == []
    assert not cast(engine, instance)["ok"]


@pytest.mark.parametrize(
    "critical,creature_type,slot,expected",
    [
        (False, "", 1, 7),
        (True, "", 1, 18),
        (False, "undead", 1, 12),
        (True, "fiend", 2, 36),
    ],
)
def test_smite_dice_follow_critical_upcasting_and_canonical_creature_type(
    critical, creature_type, slot, expected
) -> None:
    _runtime, engine, instance = smite_instance()
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character["spellcasting"]["class"]["slots_current"]["2"] = 1
    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["creature_type"] = (
        creature_type
    )
    attack(engine, instance, roll=20 if critical else 15)
    result = cast(engine, instance, slot)
    assert result["ok"], result
    radiant = next(
        row
        for row in result["event_batch"]["events"]
        if row.get("damage_type") == "radiant"
    )
    assert radiant["amount"] == expected
    assert radiant["critical"] is critical


def test_other_actors_targets_stale_requests_and_forged_critical_cannot_bypass_the_hit() -> (
    None
):
    _runtime, engine, instance = smite_instance()
    attack(engine, instance)
    before = deepcopy(ruleset_runtime.state(instance))
    request = intent(
        instance,
        "cast_spell",
        spell_ref="spell:divine_smite",
        target_id="player:gm",
        slot_level=1,
    )
    assert not engine.validate_intent(instance, request)["ok"]
    request.update(target_id="enemy:goblin-1", submitted_by="other")
    assert not engine.validate_intent(instance, request)["ok"]
    decision = ruleset_runtime.state(instance)["combat"]["pending_decisions"][0]
    request = intent(
        instance,
        "decision.resolve",
        decision_id=decision["decision_id"],
        option="decline",
    )
    request["submitted_by"] = "other"
    assert not engine.validate_intent(instance, request)["ok"]
    result = cast(engine, instance, critical=True)
    assert (
        next(
            row
            for row in result["event_batch"]["events"]
            if row.get("damage_type") == "radiant"
        )["critical"]
        is False
    )
    assert ruleset_runtime.state(instance) == before
    request = intent(
        instance,
        "cast_spell",
        spell_ref="spell:divine_smite",
        target_id="enemy:goblin-1",
        slot_level=1,
    )
    request["expected_version"] -= 1
    assert not engine.validate_intent(instance, request)["ok"]


def test_reducer_failure_rolls_back_slot_hp_and_pending_decision() -> None:
    _runtime, engine, instance = smite_instance()
    attack(engine, instance)
    result = cast(engine, instance)
    before = (
        deepcopy(ruleset_runtime.state(instance)),
        deepcopy(instance.get_character_sheet("gm")),
        deepcopy(ruleset_runtime.event_ledger(instance)),
    )
    result["event_batch"]["events"].append({"type": "unsupported.event"})
    with pytest.raises(EventBatchError):
        engine.apply_batch(instance, result["event_batch"])
    assert before == (
        ruleset_runtime.state(instance),
        instance.get_character_sheet("gm"),
        ruleset_runtime.event_ledger(instance),
    )


def test_level_two_paladin_always_has_smite_without_using_preparation_capacity() -> (
    None
):
    _runtime, engine, instance = smite_instance()
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    assert len(canonical["spellcasting"]["class"]["prepared_spell_refs"]) == 3
    assert (
        "spell:divine_smite"
        not in canonical["spellcasting"]["class"]["prepared_spell_refs"]
    )
    attack(engine, instance)
    option = smite_option(engine, instance)
    assert option is not None
    assert option["available_slot_levels"] == [0, 1]


def test_smite_spends_bonus_and_slot_once_and_ignores_client_damage() -> None:
    _runtime, engine, instance = smite_instance(level=1, prepared_smite=True)
    attack(engine, instance)
    before = deepcopy(instance.get_character_sheet("gm")["ruleset_character"])
    target_hp = ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"]
    base_damage = ruleset_runtime.state(instance)["combat"]["pending_decisions"][0][
        "damage_event"
    ]["amount"]
    request = intent(
        instance,
        "cast_spell",
        spell_ref="spell:divine_smite",
        target_id="enemy:goblin-1",
        slot_level=1,
        damage=9999,
    )
    result = engine.resolve_intent(instance, request, SequenceRng([3, 4]))
    assert result["ok"], result
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    assert (
        canonical["spellcasting"]["class"]["slots_current"]["1"]
        == before["spellcasting"]["class"]["slots_current"]["1"] - 1
    )
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0
    assert (
        ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"]
        == target_hp - base_damage - 7
    )
    assert engine.apply_batch(instance, result["event_batch"])["duplicate"]
    assert smite_option(engine, instance) is None
