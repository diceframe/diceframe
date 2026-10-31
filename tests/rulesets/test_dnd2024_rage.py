"""Rage (Barbarian 1): sustained condition, rage damage, Strength save advantage.

Every assertion goes through the same authoritative chain the web layer uses:
``available_intents`` -> ``validate_intent`` -> ``resolve_intent`` ->
``apply_batch``.  The only exception is the non-hostile-target renewal check,
which calls ``_attack_events`` directly because validation refuses same-side
attacks before they can be resolved.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    capability_action,
    capability_intent,
    monk_instance,
    start_combat,
    with_equipment,
)


def _advance(runtime: Dnd2024Runtime, sheet: dict, target_level: int) -> dict:
    """Advance to ``target_level``, spending ASIs on Constitution.

    预设把力量堆到 19；若 ASI 继续加力量会在 4/8 级越过 20 上限。
    本测试只关心狂暴加值随等级表的取值，因此把属性提升改投体质。
    """

    while int(sheet["ruleset_character"]["build"]["level"]) < target_level:
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        for requirement in preview.get("requirements") or []:
            if requirement["id"] != "ability_score_increases":
                continue
            allowed = requirement.get("allowed", "any")
            if allowed == "any" or "con" in allowed:
                choices["ability_score_increases"] = {
                    "con": int(requirement.get("total", 2)),
                }
        if choices:
            preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def _barbarian_sheet(runtime: Dnd2024Runtime, *, level: int = 5) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "relentless_vanguard"
    )
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Barbarian"},
    )
    return _advance(runtime, sheet, level) if level > 1 else sheet


def _instance(
    *, level: int = 5, rages: int | None = None, conditions: dict | None = None,
    hp: int | None = None,
):
    runtime = Dnd2024Runtime()
    sheet = _barbarian_sheet(runtime, level=level)
    canonical = sheet["ruleset_character"]
    if rages is not None:
        canonical["resources"]["class"]["rages"]["current"] = rages
    if conditions is not None:
        canonical["conditions"] = conditions
    if hp is not None:
        canonical["resources"]["hp"] = hp
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance)
    return engine, instance


def _player(instance: GameInstance) -> dict:
    return instance.get_character_sheet("gm")["ruleset_character"]


def _conditions(instance: GameInstance) -> dict:
    return _player(instance).get("conditions") or {}


def _apply(engine: Dnd2024CombatEngine, instance: GameInstance, intent: dict, rng=None):
    resolved = engine.resolve_intent(instance, intent, rng or SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _enter_rage(engine: Dnd2024CombatEngine, instance: GameInstance) -> list[dict]:
    return _apply(
        engine, instance,
        capability_intent(engine, instance, "rage", intent_id="rage-1", target_id=""),
    )


def _attack_intent(
    engine, instance, weapon_ref: str, *, intent_id: str = "atk-1",
    actor_id: str = "player:gm", target_id: str = "enemy:goblin-1",
    attack_id: str = "",
) -> dict:
    intent = {
        "intent_id": intent_id, "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": actor_id, "target_id": target_id,
    }
    if weapon_ref:
        intent["weapon_ref"] = weapon_ref
    if attack_id:
        intent["attack_id"] = attack_id
    return intent


def _end_turn(engine, instance, *, actor_id: str = "player:gm", intent_id: str = "end-1"):
    return _apply(engine, instance, {
        "intent_id": intent_id, "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": actor_id,
    })


def _damage_event(events: list[dict]) -> dict:
    return next(
        event for event in events
        if event["type"] == "resource.changed" and event.get("target_id") == "enemy:goblin-1"
    )


def test_entering_rage_spends_a_use_and_applies_the_condition() -> None:
    engine, instance = _instance(level=5)

    events = _enter_rage(engine, instance)

    applied = [event for event in events if event["type"] != "intent.submitted"]
    assert [event["type"] for event in applied] == [
        "dnd2024.action.spent", "dnd2024.class_resource.spent", "condition.applied",
    ]
    assert (applied[0]["resource"], applied[0]["amount"]) == ("bonus_action", 1)
    assert (applied[1]["resource_id"], applied[1]["amount"]) == ("rages", 1)
    assert applied[2]["condition"] == "rage"
    assert applied[2]["duration"] == "rage"
    assert applied[2]["turns_remaining"] == 100
    assert applied[2]["target_id"] == "player:gm"

    rage = _conditions(instance)["rage"]
    assert rage["turns_remaining"] == 100
    assert rage["sustained_this_turn"] is False
    assert _player(instance)["resources"]["class"]["rages"]["current"] == 2
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0


def test_reentering_rage_is_rejected_without_spending_anything() -> None:
    engine, instance = _instance(level=5)
    _enter_rage(engine, instance)
    before_player = deepcopy(_player(instance))
    before_combat = deepcopy(ruleset_runtime.state(instance)["combat"])

    forged = engine.validate_intent(
        instance,
        capability_intent(engine, instance, "rage", intent_id="rage-2", target_id=""),
    )

    assert forged["ok"] is False
    assert "Rage" in forged["error"]
    assert capability_action(engine, instance, "rage") is None
    assert _player(instance) == before_player
    assert ruleset_runtime.state(instance)["combat"] == before_combat


def test_sustain_requires_an_active_rage() -> None:
    engine, instance = _instance(level=5)

    assert capability_action(engine, instance, "rage_sustain") is None
    forged = engine.validate_intent(
        instance,
        capability_intent(
            engine, instance, "rage_sustain", intent_id="sustain-1", target_id="",
        ),
    )

    assert forged["ok"] is False
    assert "Sustain Rage" in forged["error"]
    assert "Rage" in forged["error"]


@pytest.mark.parametrize("level,bonus", [(5, 2), (9, 3)])
def test_rage_adds_strength_melee_damage_only(level: int, bonus: int) -> None:
    engine, instance = _instance(level=level)
    _enter_rage(engine, instance)
    strength = (int(_player(instance)["abilities"]["str"]) - 10) // 2

    events = _apply(
        engine, instance, _attack_intent(engine, instance, "item:greataxe"),
        SequenceRng([15, 7]),
    )
    damage = _damage_event(events)

    assert damage["rolls"] == [7]
    assert damage["amount"] == 7 + strength + bonus
    assert damage["delta"] == -(7 + strength + bonus)


def test_dexterity_and_ranged_attacks_gain_no_rage_damage() -> None:
    engine, instance = _instance(level=5)
    _enter_rage(engine, instance)
    dexterity = (int(_player(instance)["abilities"]["dex"]) - 10) // 2

    events = _apply(
        engine, instance, _attack_intent(engine, instance, "item:shortbow"),
        SequenceRng([15, 5]),
    )
    damage = _damage_event(events)

    assert damage["rolls"] == [5]
    assert damage["amount"] == 5 + dexterity


def test_attacking_an_enemy_renews_the_rage_even_on_a_miss() -> None:
    engine, instance = _instance(level=5)
    _enter_rage(engine, instance)

    # natural 1 必然落空；规则只要求"对敌人进行一次攻击检定"。
    events = _apply(
        engine, instance, _attack_intent(engine, instance, "item:greataxe"),
        SequenceRng([1]),
    )
    sustained = [event for event in events if event["type"] == "dnd2024.condition.sustained"]

    assert len(sustained) == 1
    assert sustained[0]["condition"] == "rage"
    assert sustained[0]["target_id"] == "player:gm"
    assert _conditions(instance)["rage"]["sustained_this_turn"] is True


def test_rage_keeps_ticking_only_while_it_is_renewed() -> None:
    engine, instance = _instance(level=5)
    _enter_rage(engine, instance)
    _apply(
        engine, instance, _attack_intent(engine, instance, "item:greataxe"),
        SequenceRng([15, 7]),
    )

    # 进入回合结束：规则保证第一轮存续（established），本轮攻击不消耗轮数。
    _end_turn(engine, instance)
    rage = _conditions(instance)["rage"]
    assert rage["established"] is True
    assert rage["turns_remaining"] == 100
    assert rage["sustained_this_turn"] is False

    _end_turn(engine, instance, actor_id="enemy:goblin-1", intent_id="end-2")
    _apply(
        engine, instance, _attack_intent(
            engine, instance, "item:greataxe", intent_id="atk-2",
        ),
        SequenceRng([15, 7]),
    )

    # 下个回合攻击续期：该回合结束时消耗一轮。
    _end_turn(engine, instance, intent_id="end-3")
    rage = _conditions(instance)["rage"]
    assert rage["turns_remaining"] == 99
    assert rage["sustained_this_turn"] is False


def test_rage_ends_at_turn_end_without_renewal() -> None:
    engine, instance = _instance(level=5)
    _enter_rage(engine, instance)

    # 规则保证第一轮：进入回合结束时狂暴仍存活。
    _end_turn(engine, instance)
    assert "rage" in _conditions(instance)

    # 下个回合不攻击、不延长：该回合结束时狂暴结束。
    _end_turn(engine, instance, actor_id="enemy:goblin-1", intent_id="end-2")
    _end_turn(engine, instance, intent_id="end-3")

    assert "rage" not in _conditions(instance)


def test_bonus_action_renewal_keeps_the_rage_alive() -> None:
    engine, instance = _instance(level=5, conditions={
        "rage": {
            "duration": "rage", "turns_remaining": 100, "source_actor_id": "player:gm",
            "established": True,
        },
    })

    _apply(
        engine, instance,
        capability_intent(engine, instance, "rage_sustain", intent_id="sustain-1", target_id=""),
    )
    _end_turn(engine, instance)

    rage = _conditions(instance)["rage"]
    assert rage["turns_remaining"] == 99
    assert rage["sustained_this_turn"] is False


def test_rage_ends_when_the_renewed_turns_run_out() -> None:
    engine, instance = _instance(level=5, conditions={
        "rage": {
            "duration": "rage", "turns_remaining": 1, "source_actor_id": "player:gm",
            "established": True, "sustained_this_turn": True,
        },
    })

    _end_turn(engine, instance)

    assert "rage" not in _conditions(instance)


def test_rage_ends_when_the_barbarian_falls_unconscious() -> None:
    engine, instance = _instance(level=5, hp=1)
    _enter_rage(engine, instance)
    _end_turn(engine, instance)

    assert ruleset_runtime.state(instance)["combat"]["turn_index"] != 0
    _apply(engine, instance, _attack_intent(
        engine, instance, "", intent_id="goblin-1", actor_id="enemy:goblin-1",
        target_id="player:gm", attack_id="scimitar",
    ), SequenceRng([15, 6]))

    conditions = _conditions(instance)
    assert _player(instance)["resources"]["hp"] == 0
    assert "unconscious" in conditions
    assert "rage" not in conditions


# --------------------------------------------------------------------------
# Strength saving throw advantage: the catalog has no Strength-save spell, so
# the test swaps one real effect's saving throw onto Strength instead of
# inventing a spell.  The same engine keeps a Constitution-save spell for the
# negative (single-roll) half.
# --------------------------------------------------------------------------


def _swap_sacred_flame_to_strength(combat_catalog: dict) -> None:
    combat_catalog["spell_effects"]["sacred_flame"]["save"] = "str"


def _caster_companion() -> dict:
    return {
        "id": "mira", "name": "Mira", "controller": "ai", "active": True,
        "ruleset_character": {
            "resources": {"hp": 20, "max_hp": 20},
            "conditions": {},
            "abilities": {
                "str": 14, "dex": 12, "con": 14, "int": 10, "wis": 16, "cha": 10,
            },
            "derived": {
                "armor_class": 15, "speed": 30, "proficiency_bonus": 2,
                "saving_throws": {"wis": 5}, "spell_save_dc": 14,
                "spell_attack_bonus": 5,
            },
            "proficiencies": {"skill_values": {}, "weapon_category_refs": []},
            "equipment": {"item_refs": []},
            "spellcasting": {"class": {
                "ability": "wis", "slots_current": {"1": 2}, "concentration": None,
                "prepared_spell_refs": ["spell:sacred_flame", "spell:thunderwave"],
                "cantrip_refs": ["spell:sacred_flame"],
            }},
            "build": {"class_levels": [{"class_ref": "class:fighter", "level": 1}]},
        },
    }


def _saving_throw_setup():
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    _swap_sacred_flame_to_strength(entities["combat_catalog"]["srd_combat_core"])
    sheet = _barbarian_sheet(runtime, level=5)
    canonical = sheet["ruleset_character"]
    canonical["conditions"] = {
        "rage": {"duration": "rage", "turns_remaining": 10, "source_actor_id": "player:gm"},
    }
    instance = GameInstance(
        game_key=("test", "dnd2024-rage", "bot"), rule_id="dnd2024_srd",
        gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Barbarian", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(canonical["rule_binding"])
    engine = Dnd2024CombatEngine(replace(bundle, entities=entities), EncounterAccess.sandbox())
    start_combat(engine, instance)
    party = ruleset_runtime.state(instance).setdefault("party", {"companions": {}})
    party.setdefault("companions", {})["mira"] = _caster_companion()
    return engine, instance


def _cast_at_barbarian(
    engine, instance, spell_ref: str, rolls: list[int], *, slot_level: int = 0,
) -> dict:
    combat = ruleset_runtime.state(instance)["combat"]
    events = engine._spell_events(instance, combat, {
        "actor_id": "companion:mira", "spell_ref": spell_ref,
        "target_id": "player:gm", "slot_level": slot_level,
    }, SequenceRng(list(rolls)))
    return next(
        event for event in events
        if event["type"] == "check.resolved" and event["kind"] == "saving_throw"
    )


def test_raged_barbarian_rolls_strength_saves_with_advantage() -> None:
    engine, instance = _saving_throw_setup()

    check = _cast_at_barbarian(engine, instance, "spell:sacred_flame", [7, 18, 5])

    assert check["ability"] == "str"
    assert check["rolls"] == [7, 18]
    assert check["natural"] == 18
    assert check["advantage_mode"] == "advantage"
    assert check["advantage_sources"] == [{"code": "rage", "mode": "advantage"}]


def test_other_saving_throws_stay_single_roll_while_raging() -> None:
    engine, instance = _saving_throw_setup()

    check = _cast_at_barbarian(
        engine, instance, "spell:thunderwave", [7, 3, 4], slot_level=1,
    )

    assert check["ability"] == "con"
    assert check["rolls"] == [7]
    assert "advantage_mode" not in check


def test_attacking_a_non_enemy_does_not_renew_the_rage() -> None:
    engine, instance = _instance(level=5, conditions={
        "rage": {"duration": "rage", "turns_remaining": 10, "source_actor_id": "player:gm"},
    })
    party = ruleset_runtime.state(instance).setdefault("party", {"companions": {}})
    party.setdefault("companions", {})["mira"] = _caster_companion()
    combat = ruleset_runtime.state(instance)["combat"]

    events = engine._attack_events(instance, combat, {
        "actor_id": "player:gm", "target_id": "companion:mira",
        "weapon_ref": "item:greataxe",
    }, SequenceRng([15, 7]))

    assert not any(
        event["type"] == "dnd2024.condition.sustained" for event in events
    )


# --------------------------------------------------------------------------
# 2024 Rage resilience: bludgeoning / piercing / slashing damage taken while
# raging is halved (rounding down, like every other damage multiplier).
# --------------------------------------------------------------------------


def _damage_to_player(events: list[dict]) -> dict:
    return next(
        event for event in events
        if event["type"] == "resource.changed" and event.get("target_id") == "player:gm"
    )


def _goblin_attacks_the_barbarian(engine, instance, rolls: list[int]) -> list[dict]:
    return _apply(engine, instance, _attack_intent(
        engine, instance, "", intent_id="goblin-1", actor_id="enemy:goblin-1",
        target_id="player:gm", attack_id="scimitar",
    ), SequenceRng(list(rolls)))


def test_rage_halves_slashing_damage_taken() -> None:
    engine, instance = _instance(level=5, hp=30, conditions={
        "rage": {"duration": "rage", "turns_remaining": 10, "source_actor_id": "player:gm"},
    })
    _end_turn(engine, instance)

    events = _goblin_attacks_the_barbarian(engine, instance, [15, 5])

    damage = _damage_to_player(events)
    assert damage["damage_type"] == "slashing"
    assert damage["amount"] == 3  # (5 + 2) = 7, halved rounds down
    assert damage["resistance"] == "rage"
    assert _player(instance)["resources"]["hp"] == 30 - 3


def test_rage_leaves_nonphysical_damage_alone() -> None:
    engine, instance = _instance(level=5, hp=30, conditions={
        "rage": {"duration": "rage", "turns_remaining": 10, "source_actor_id": "player:gm"},
    })
    _end_turn(engine, instance)
    combat = ruleset_runtime.state(instance)["combat"]
    combat["enemies"]["goblin-1"]["attacks"][0]["damage_type"] = "fire"

    events = _goblin_attacks_the_barbarian(engine, instance, [15, 5])

    damage = _damage_to_player(events)
    assert damage["damage_type"] == "fire"
    assert damage["amount"] == 7
    assert "resistance" not in damage


@pytest.mark.parametrize(
    "condition",
    ["incapacitated", "paralyzed", "stunned", "petrified"],
)
def test_incapacitating_conditions_end_the_rage(condition: str) -> None:
    engine, instance = _instance(level=5, conditions={
        "rage": {"duration": "rage", "turns_remaining": 10, "source_actor_id": "player:gm"},
        condition: {},
    })

    _end_turn(engine, instance)

    assert condition in _conditions(instance)
    assert "rage" not in _conditions(instance)


def test_an_opportunity_attack_on_another_actors_turn_does_not_renew_rage() -> None:
    engine, instance = _instance(level=5, conditions={
        "rage": {"duration": "rage", "turns_remaining": 10, "source_actor_id": "player:gm"},
    })
    _end_turn(engine, instance)

    moved = _apply(engine, instance, {
        "intent_id": "move-1", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1", "distance": 10,
    })

    pending = next(
        event for event in moved if event["type"] == "dnd2024.decision.pending"
    )
    assert pending["kind"] == "opportunity_attack"
    assert pending["threat_actor_ids"] == ["player:gm"]

    decided = _apply(engine, instance, {
        "intent_id": "decision-1", "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": pending["decision_id"], "option": "resolve",
    }, SequenceRng([15, 7]))

    # 借机攻击确实结算了，但它发生在别人的回合，因此不延长狂暴。
    assert any(
        event["type"] == "resource.changed" and event.get("target_id") == "enemy:goblin-1"
        for event in decided
    )
    assert not any(
        event["type"] == "dnd2024.condition.sustained" for event in decided
    )


# ---- No Concentration or Spells ----------------------------------------------


def _initiate_barbarian(runtime: Dnd2024Runtime, feat_ref: str, answers: dict) -> dict:
    """Barbarian whose background grants Magic Initiate (spells without a casting class)."""

    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "relentless_vanguard"
    )
    background = "sage" if feat_ref.endswith("wizard") else "acolyte"
    draft = {
        **preset["draft"], "locale": "en", "name": "Barbarian",
        "background_ref": f"background:{background}",
        "background_equipment_package_ref": f"equipment_package:{background}_b",
    }
    choices = runtime.builder_choices(None, draft)
    refs = choices["background_ability_refs"]
    draft["background_ability_bonuses"] = {
        refs[0].split(":", 1)[1]: 2, refs[1].split(":", 1)[1]: 1,
    }
    draft["feat_choice_answers"] = {
        feat["feat_ref"]: {
            spec["id"]: [option["value"] for option in spec["options"]][: int(spec["count"])]
            for spec in feat["specs"]
        }
        for feat in choices["feat_choices"]
    }
    draft["feat_choice_answers"][feat_ref] = answers
    assert runtime.validate_character(None, draft) == []
    return runtime.finalize_character(None, draft)


def _initiate_instance(feat_ref: str, answers: dict):
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, _initiate_barbarian(runtime, feat_ref, answers))
    start_combat(engine, instance, hp=100)
    return engine, instance


def _resolve_and_apply(engine, instance, intent, rolls=()):
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _cast_intent(instance, spell_ref: str, target_ids: list[str]) -> dict:
    version = ruleset_runtime.state(instance)["version"]
    return {
        "intent_id": f"cast:{version}", "type": "cast_spell", "expected_version": version,
        "submitted_by": "gm", "actor_id": "player:gm", "spell_ref": spell_ref,
        "target_ids": target_ids, "slot_level": 0,
    }


def test_raging_barbarian_cannot_cast_or_be_offered_spells() -> None:
    engine, instance = _initiate_instance("feat:magic_initiate_wizard", {
        "spellcasting_ability": ["int"],
        "cantrips": ["fire_bolt", "chill_touch"],
        "level_1_spell": ["shield"],
    })
    assert any(row["type"] == "cast_spell" for row in engine.available_intents(instance, "gm"))

    _resolve_and_apply(engine, instance, capability_intent(engine, instance, "rage", intent_id="rage-1"))

    assert not any(row["type"] == "cast_spell" for row in engine.available_intents(instance, "gm"))
    actor = next(
        row for row in engine.gameplay_view(instance)["combat"]["actors"]
        if row["actor_id"] == "player:gm"
    )
    assert {row["blocked_reason"] for row in actor["spells"]} == {"raging"}
    rejected = engine.resolve_intent(
        instance, _cast_intent(instance, "spell:fire_bolt", ["enemy:goblin-1"]), SequenceRng([15, 5]),
    )
    assert rejected["ok"] is False
    assert "raging" in str(rejected)


def test_entering_rage_ends_concentration() -> None:
    engine, instance = _initiate_instance("feat:magic_initiate_cleric", {
        "spellcasting_ability": ["wis"],
        "cantrips": ["sacred_flame", "spare_the_dying"],
        "level_1_spell": ["bless"],
    })
    _resolve_and_apply(engine, instance, _cast_intent(instance, "spell:bless", ["player:gm"]))
    assert _player(instance)["spellcasting"]["concentration"]["spell_ref"] == "spell:bless"
    # Bless costs the Action; Rage still has the Bonus Action this turn.
    events = _resolve_and_apply(
        engine, instance, capability_intent(engine, instance, "rage", intent_id="rage-1"),
    )

    assert {"type": "dnd2024.concentration.ended", "actor_id": "player:gm", "reason": "rage"} in events
    assert _player(instance)["spellcasting"]["concentration"] is None
    assert "rage" in _player(instance)["conditions"]


def _end_combat_by_gm(engine, instance) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": "end-combat", "type": "combat.end",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
    })


def _end_combat_by_victory(engine, instance) -> list[dict]:
    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] = 1
    events = _apply(
        engine, instance, _attack_intent(engine, instance, "item:greataxe", intent_id="atk-kill"),
        SequenceRng([15, 7]),
    )
    assert {"type": "dnd2024.combat.ended", "reason": "victory"} in events
    return events


@pytest.mark.parametrize("end_combat", [_end_combat_by_gm, _end_combat_by_victory])
def test_rage_ends_with_the_combat(end_combat) -> None:
    engine, instance = _instance(level=5)
    _enter_rage(engine, instance)
    assert "rage" in _conditions(instance)

    end_combat(engine, instance)

    assert ruleset_runtime.state(instance)["combat"]["status"] == "ended"
    assert "rage" not in _conditions(instance)


def test_a_thrown_strength_weapon_keeps_strength_and_rage_damage() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _barbarian_sheet(runtime, level=5), "item:handaxe")
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance, position=15)  # 投掷距离内，但不在近战范围
    _enter_rage(engine, instance)
    strength = (int(_player(instance)["abilities"]["str"]) - 10) // 2

    events = _apply(
        engine, instance, _attack_intent(engine, instance, "item:handaxe"),
        SequenceRng([15, 4]),
    )
    damage = _damage_event(events)

    assert damage["rolls"] == [4]
    assert damage["amount"] == 4 + strength + 2
