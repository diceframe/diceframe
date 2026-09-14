from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pytest

from src.engine.game_instance import GameInstance
from src.commands.round_effects import apply_revive_commands
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


@dataclass
class SequenceRng:
    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum
        return value


def _character(runtime: Dnd2024Runtime, preset_id: str, name: str) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": name},
    )


def _instance(*, wizard: bool = False) -> tuple[Dnd2024CombatEngine, GameInstance]:
    return _preset_instance("curious_arcanist" if wizard else "stalwart_guardian")


def _preset_instance(preset_id: str) -> tuple[Dnd2024CombatEngine, GameInstance]:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-combat", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    first = _character(runtime, preset_id, "Arden")
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": first}
    assert instance.bind_ruleset_runtime(first["rule_binding"])
    return Dnd2024CombatEngine(
        runtime.load_bundle("en"), EncounterAccess.sandbox(),
    ), instance


def _goblin(*, position: int = 5) -> dict:
    return {
        "id": "goblin-1", "name": "Goblin", "hp": 18, "armor_class": 12,
        "speed": 30, "position": position, "initiative_modifier": 2,
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": -1, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _start(engine: Dnd2024CombatEngine, instance: GameInstance, *, position: int = 5) -> dict:
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin(position=position)],
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng([20, 1]))
    assert resolved["ok"] is True
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    assert instance.ruleset_state["combat"]["initiative"][0] == "player:gm"
    return resolved["event_batch"]


def test_weapon_attack_is_server_rolled_atomic_and_idempotent() -> None:
    engine, instance = _instance()
    _start(engine, instance)
    intent = {
        "intent_id": "attack-1", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:greatsword",
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 4, 5]))
    applied = engine.apply_batch(instance, resolved["event_batch"])
    hp_after = instance.ruleset_state["combat"]["enemies"]["goblin-1"]["hp"]
    replayed = engine.apply_batch(instance, resolved["event_batch"])

    assert applied["state_version"] == 2
    assert hp_after == 6
    assert replayed["duplicate"] is True
    assert instance.ruleset_state["combat"]["enemies"]["goblin-1"]["hp"] == hp_after
    assert instance.ruleset_state["combat"]["economy"]["action"] == 0


def test_catalog_preset_replaces_client_enemy_payload() -> None:
    engine, instance = _instance()
    intent = {
        "intent_id": "catalog-start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "encounter_preset_id": "goblin_patrol",
        "enemies": [{"id": "forged", "hp": 9999, "armor_class": 1, "attacks": []}],
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng([20, 1]))
    assert resolved["ok"] is True
    started = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "dnd2024.combat.started"
    )
    assert set(started["enemies"]) == {"goblin-warrior-1"}
    assert started["enemies"]["goblin-warrior-1"]["hp"] == 10


def test_player_joining_active_combat_is_added_to_next_turn() -> None:
    engine, instance = _instance()
    _start(engine, instance)
    runtime = Dnd2024Runtime()
    late = _character(runtime, "stalwart_guardian", "Latecomer")
    instance.players["late"] = {
        "character_name": "Latecomer",
        "character_sheet": late,
    }

    runtime.on_player_join(instance, "late")
    combat = instance.ruleset_state["combat"]
    assert combat["initiative"][combat["turn_index"] + 1] == "player:late"
    assert combat["reactions"]["player:late"] == 1
    assert combat["positions"]["player:late"] == 0
    ended = engine.resolve_intent(
        instance,
        {
            "intent_id": "end-gm-after-join",
            "type": "end_turn",
            "expected_version": instance.ruleset_state["version"],
            "submitted_by": "gm",
            "actor_id": "player:gm",
        },
        SequenceRng([]),
    )
    assert ended["ok"] is True
    engine.apply_batch(instance, ended["event_batch"])
    actions = engine.available_intents(instance, "late")
    assert any(action["type"] == "attack" for action in actions)


def test_sandbox_hides_tutorial_presets_but_story_binding_can_use_them() -> None:
    runtime = Dnd2024Runtime()
    catalog = runtime.load_bundle("en").get(
        "encounter_catalog", "srd_training_encounters",
    )
    assert catalog is not None
    training = deepcopy(catalog["presets"][0])
    training["id"] = "training_only"
    training["difficulty"] = "tutorial"
    catalog["presets"].append(training)

    sandbox = Dnd2024CombatEngine(
        runtime.load_bundle("en"), EncounterAccess.sandbox(), catalog,
    )
    assert "training_only" not in {item["id"] for item in sandbox.encounter_presets()}

    story = Dnd2024CombatEngine(
        runtime.load_bundle("en"),
        EncounterAccess(
            mode="story", status="pending", encounter_instance_id="story:test:step",
            encounter_preset_id="training_only", origin_step_id="step",
        ),
        catalog,
    )
    assert "training_only" in {item["id"] for item in story.encounter_presets()}


def test_available_weapons_use_the_materialized_bundle_locale_name() -> None:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-combat-zh", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="zh-CN",
    )
    choices = runtime.builder_choices(None, {"locale": "zh-CN"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian")
    character = runtime.finalize_character(
        None, {**preset["draft"], "locale": "zh-CN", "name": "守卫"},
    )
    instance.players["gm"] = {"character_name": "守卫", "character_sheet": character}
    assert instance.bind_ruleset_runtime(character["rule_binding"])
    engine = Dnd2024CombatEngine(runtime.load_bundle("zh-CN"), EncounterAccess.sandbox())
    _start(engine, instance)

    attack = next(
        item for item in engine.available_intents(instance, "gm")
        if item["type"] == "attack"
    )
    greatsword = next(item for item in attack["weapons"] if item["weapon_ref"] == "item:greatsword")
    assert greatsword["name"] == "巨剑"
    assert greatsword["weapon_ref"] == "item:greatsword"


def test_prepared_spell_consumes_slot_and_cannot_trust_client_damage() -> None:
    engine, instance = _instance(wizard=True)
    _start(engine, instance, position=30)
    intent = {
        "intent_id": "spell-1", "type": "cast_spell", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "spell_ref": "spell:magic_missile", "slot_level": 1, "damage": 9999,
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([1, 2, 3]))
    engine.apply_batch(instance, resolved["event_batch"])

    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    assert canonical["spellcasting"]["class"]["slots_current"] == {"1": 1}
    assert instance.ruleset_state["combat"]["enemies"]["goblin-1"]["hp"] == 9
    assert any(
        event["type"] == "dnd2024.spell.cast"
        for event in resolved["event_batch"]["events"]
    )


def test_move_out_of_reach_creates_a_pending_reaction_decision() -> None:
    engine, instance = _instance()
    _start(engine, instance)
    intent = {
        "intent_id": "move-1", "type": "move", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "distance": -10,
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([]))
    engine.apply_batch(instance, resolved["event_batch"])

    assert resolved["pending_decision"]["kind"] == "opportunity_attack"
    assert instance.ruleset_state["combat"]["positions"]["player:gm"] == 0
    assert instance.ruleset_state["combat"]["pending_decisions"][0]["movement"]["distance"] == -10

    decision_intent = {
        "intent_id": "decision-1", "type": "decision.resolve", "expected_version": 2,
        "submitted_by": "gm", "decision_id": "decision:move-1", "option": "resolve",
    }
    decided = engine.resolve_intent(instance, decision_intent, SequenceRng([15, 4]))
    engine.apply_batch(instance, decided["event_batch"])

    assert instance.ruleset_state["combat"]["positions"]["player:gm"] == -10
    assert instance.ruleset_state["combat"]["pending_decisions"] == []
    assert instance.ruleset_state["combat"]["reactions"]["enemy:goblin-1"] == 0
    assert instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] < 12


def test_natural_twenty_death_save_restores_one_hp() -> None:
    engine, instance = _instance()
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 0
    canonical["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 1},
    }
    _start(engine, instance)
    intent = {
        "intent_id": "death-1", "type": "death_save", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm",
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([20]))
    engine.apply_batch(instance, resolved["event_batch"])

    updated = instance.get_character_sheet("gm")["ruleset_character"]
    assert updated["resources"]["hp"] == 1
    assert "unconscious" not in updated["conditions"]
    assert updated["conditions"]["death_saves"] == {"successes": 0, "failures": 0}


def test_revival_clears_canonical_death_save_state_and_invalidates_combat_actions() -> None:
    engine, instance = _instance()
    runtime = Dnd2024Runtime()
    _start(engine, instance)
    sheet = instance.get_character_sheet("gm")
    canonical = sheet["ruleset_character"]
    canonical["resources"]["hp"] = 0
    canonical["conditions"] = {
        "dead": {"source": "death_saves"},
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 3},
    }
    sheet["hp"] = 0
    sheet["deceased"] = True
    assert engine.available_intents(instance, "gm")[0]["type"] == "combat.message"
    assert any(item["type"] == "death_save" for item in engine.available_intents(instance, "gm"))

    apply_revive_commands(
        instance, {"revive_commands": [{"uid": "gm", "method": "法术"}]}, runtime,
    )

    updated = instance.get_character_sheet("gm")
    assert updated["ruleset_character"]["resources"]["hp"] == updated["hp"]
    assert updated["ruleset_character"]["conditions"] == {}
    assert updated["deceased"] is False
    assert instance.ruleset_state["version"] == 2
    actions = engine.available_intents(instance, "gm")
    assert "death_save" not in [item["type"] for item in actions]
    assert any(item["type"] == "attack" for item in actions)


def test_spell_targets_respect_hostile_and_allied_relationships() -> None:
    engine, instance = _instance(wizard=True)
    _start(engine, instance, position=30)
    invalid = engine.validate_intent(instance, {
        "intent_id": "friendly-fire-1", "type": "cast_spell", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "player:gm",
        "spell_ref": "spell:magic_missile", "slot_level": 1,
    })

    assert invalid["ok"] is False
    assert "hostile" in invalid["error"]


def test_failed_save_applies_condition_but_successful_save_does_not() -> None:
    failed_engine, failed_instance = _instance(wizard=True)
    _start(failed_engine, failed_instance, position=30)
    failed = failed_engine.resolve_intent(failed_instance, {
        "intent_id": "sleep-fail", "type": "cast_spell", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "spell_ref": "spell:sleep", "slot_level": 1,
    }, SequenceRng([1]))
    failed_engine.apply_batch(failed_instance, failed["event_batch"])

    success_engine, success_instance = _instance(wizard=True)
    _start(success_engine, success_instance, position=30)
    succeeded = success_engine.resolve_intent(success_instance, {
        "intent_id": "sleep-success", "type": "cast_spell", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "spell_ref": "spell:sleep", "slot_level": 1,
    }, SequenceRng([20]))
    success_engine.apply_batch(success_instance, succeeded["event_batch"])

    assert "incapacitated" in failed_instance.ruleset_state["combat"]["enemies"]["goblin-1"]["conditions"]
    assert "incapacitated" not in success_instance.ruleset_state["combat"]["enemies"]["goblin-1"]["conditions"]


def test_failed_concentration_save_removes_owned_conditions() -> None:
    engine, instance = _preset_instance("kindly_bulwark")
    _start(engine, instance)
    cast = engine.resolve_intent(instance, {
        "intent_id": "bless-1", "type": "cast_spell", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "player:gm",
        "spell_ref": "spell:bless", "slot_level": 1,
    }, SequenceRng([]))
    engine.apply_batch(instance, cast["event_batch"])
    ended = engine.resolve_intent(instance, {
        "intent_id": "end-1", "type": "end_turn", "expected_version": 2,
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([]))
    engine.apply_batch(instance, ended["event_batch"])
    attacked = engine.resolve_intent(instance, {
        "intent_id": "enemy-attack-1", "type": "attack", "expected_version": 3,
        "submitted_by": "gm", "actor_id": "enemy:goblin-1", "target_id": "player:gm",
        "attack_id": "scimitar",
    }, SequenceRng([20, 4, 4, 1]))
    engine.apply_batch(instance, attacked["event_batch"])

    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    assert canonical["spellcasting"]["class"]["concentration"] is None
    assert "blessed" not in canonical["conditions"]


def test_only_one_slot_spell_can_be_cast_on_a_turn() -> None:
    engine, instance = _preset_instance("kindly_bulwark")
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] -= 1
    canonical["spellcasting"]["class"]["prepared_spell_refs"].append("spell:healing_word")
    _start(engine, instance, position=30)
    healed = engine.resolve_intent(instance, {
        "intent_id": "healing-word-1", "type": "cast_spell", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "player:gm",
        "spell_ref": "spell:healing_word", "slot_level": 1,
    }, SequenceRng([2, 2]))
    engine.apply_batch(instance, healed["event_batch"])
    second = engine.validate_intent(instance, {
        "intent_id": "guiding-bolt-1", "type": "cast_spell", "expected_version": 2,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "spell_ref": "spell:guiding_bolt", "slot_level": 1,
    })

    assert second["ok"] is False
    assert "only one spell slot" in second["error"]


def test_normal_healing_cannot_revive_a_dead_character() -> None:
    engine, instance = _preset_instance("kindly_bulwark")
    caster = instance.get_character_sheet("gm")["ruleset_character"]
    caster["spellcasting"]["class"]["prepared_spell_refs"].append("spell:healing_word")
    fallen = _character(Dnd2024Runtime(), "stalwart_guardian", "Fallen Ally")
    fallen_canonical = fallen["ruleset_character"]
    fallen_canonical["resources"]["hp"] = 0
    fallen_canonical.setdefault("conditions", {})["dead"] = {"source": "test"}
    instance.players["ally"] = {
        "character_name": "Fallen Ally", "character_sheet": fallen,
    }
    started = engine.resolve_intent(instance, {
        "intent_id": "start-dead-target", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin(position=30)],
    }, SequenceRng([1, 20, 1]))
    engine.apply_batch(instance, started["event_batch"])
    assert instance.ruleset_state["combat"]["initiative"][0] == "player:gm"

    result = engine.validate_intent(instance, {
        "intent_id": "healing-dead-1", "type": "cast_spell", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "player:ally",
        "spell_ref": "spell:healing_word", "slot_level": 1,
    })

    assert result["ok"] is False
    assert "resurrection" in result["error"]


def test_cantrip_scales_at_level_five_and_spell_can_end_combat() -> None:
    engine, instance = _instance(wizard=True)
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["build"]["level"] = 5
    _start(engine, instance, position=30)
    ray = engine.resolve_intent(instance, {
        "intent_id": "ray-1", "type": "cast_spell", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "spell_ref": "spell:ray_of_frost", "slot_level": 0,
    }, SequenceRng([20, 1, 1, 1, 1]))
    damage_event = next(
        event for event in ray["event_batch"]["events"]
        if event["type"] == "resource.changed"
    )
    assert len(damage_event["rolls"]) == 4  # critical doubles the level-5 2d8 cantrip

    victory_engine, victory_instance = _instance(wizard=True)
    _start(victory_engine, victory_instance, position=30)
    victory_instance.ruleset_state["combat"]["enemies"]["goblin-1"]["hp"] = 3
    missile = victory_engine.resolve_intent(victory_instance, {
        "intent_id": "missile-win", "type": "cast_spell", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "spell_ref": "spell:magic_missile", "slot_level": 1,
    }, SequenceRng([1, 1, 1]))
    victory_engine.apply_batch(victory_instance, missile["event_batch"])
    assert victory_instance.ruleset_state["combat"]["status"] == "ended"


def test_stable_actor_remains_unconscious_and_skips_death_save() -> None:
    engine, instance = _instance()
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 0
    canonical["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "stable": {"duration": "until_healed"},
        "death_saves": {"successes": 3, "failures": 0},
    }
    _start(engine, instance)
    actions = engine.available_intents(instance, "gm")

    assert "end_turn" in [action["type"] for action in actions]
    assert "death_save" not in [action["type"] for action in actions]
    assert "unconscious" in canonical["conditions"]


def test_last_stable_player_ends_combat_instead_of_looping_enemy_turns() -> None:
    engine, instance = _instance()
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 0
    canonical["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "stable": {"duration": "until_healed"},
        "death_saves": {"successes": 3, "failures": 0},
    }
    _start(engine, instance)

    ended = engine.resolve_intent(instance, {
        "intent_id": "stable-player-end", "type": "end_turn", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([]))
    assert ended["ok"] is True
    assert ended["event_batch"]["events"][-1] == {
        "type": "dnd2024.combat.ended", "reason": "party_incapacitated",
    }

    engine.apply_batch(instance, ended["event_batch"])
    assert instance.ruleset_state["combat"]["status"] == "ended"
    assert instance.ruleset_state["combat"]["outcome"] == "party_incapacitated"
    assert engine.next_automatic_intent(instance) is None


def test_stable_player_does_not_end_combat_while_a_teammate_can_act() -> None:
    engine, instance = _instance()
    runtime = Dnd2024Runtime()
    ally = _character(runtime, "stalwart_guardian", "Mira")
    instance.players["zz-ally"] = {"character_name": "Mira", "character_sheet": ally}
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 0
    canonical["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "stable": {"duration": "until_healed"},
        "death_saves": {"successes": 3, "failures": 0},
    }
    _start(engine, instance)

    ended = engine.resolve_intent(instance, {
        "intent_id": "stable-player-pass", "type": "end_turn", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([]))

    assert ended["ok"] is True
    assert ended["event_batch"]["events"][-1]["type"] == "dnd2024.turn.advanced"
    assert ended["event_batch"]["events"][-1]["actor_id"] == "player:zz-ally"


def test_enemy_turn_is_declared_by_server_and_returns_control_to_player() -> None:
    engine, instance = _instance()
    _start(engine, instance, position=5)
    ended = engine.resolve_intent(instance, {
        "intent_id": "player-end", "type": "end_turn", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([]))
    engine.apply_batch(instance, ended["event_batch"])

    attack = engine.next_automatic_intent(instance)
    assert attack is not None
    assert attack["type"] == "attack"
    assert attack["actor_id"] == "enemy:goblin-1"
    assert attack["target_id"] == "player:gm"
    resolved = engine.resolve_intent(instance, attack, SequenceRng([15, 3]))
    engine.apply_batch(instance, resolved["event_batch"])
    assert instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] < 12

    finish = engine.next_automatic_intent(instance)
    assert finish is not None and finish["type"] == "end_turn"
    resolved_finish = engine.resolve_intent(instance, finish, SequenceRng([]))
    engine.apply_batch(instance, resolved_finish["event_batch"])
    assert instance.ruleset_state["combat"]["initiative"][
        instance.ruleset_state["combat"]["turn_index"]
    ] == "player:gm"


def test_llm_view_uses_canonical_state_and_resolved_event_batch() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = _instance()
    instance.get_character_sheet("gm")["hp"] = 9999
    batch = _start(engine, instance)

    view = runtime.build_llm_view(instance)

    assert view["players"]["gm"]["character_sheet"]["hp"] == 12
    authority = view["ruleset_authority"]
    assert authority["runtime_id"] == "core:dnd2024"
    assert authority["latest_event_batch"]["batch_id"] == batch["batch_id"]
    assert "combat_enemies" not in view


def _companion(
    companion_id: str, name: str, *, hp: int = 20, max_hp: int | None = None,
    str_value: int = 18,
    slots: dict[int, int] | None = None, prepared: list[str] | None = None,
) -> dict:
    return {
        "id": companion_id, "name": name, "controller": "ai", "active": True,
        "ruleset_character": {
            "resources": {"hp": hp, "max_hp": max_hp if max_hp is not None else hp},
            "conditions": {},
            "abilities": {
                "str": str_value, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 10,
            },
            "derived": {
                "armor_class": 16, "speed": 30, "initiative": 1,
                "proficiency_bonus": 2, "saving_throws": {"str": 4, "con": 2},
            },
            "proficiencies": {
                "skill_values": {"athletics": 6, "medicine": 4},
                "weapon_category_refs": ["weapon_category:martial"],
            },
            "equipment": {"item_refs": ["item:greatsword"]},
            "spellcasting": {"class": {
                "ability": "wis", "slots_current": {str(k): v for k, v in (slots or {}).items()},
                "concentration": None, "prepared_spell_refs": prepared or [],
                "cantrip_refs": [],
            }},
            "build": {"class_levels": [{"class_ref": "class:fighter", "level": 5}]},
        },
    }


def _seed_companion(instance: GameInstance, companion: dict) -> None:
    party = instance.ruleset_state.setdefault("party", {"companions": {}})
    party.setdefault("companions", {})[companion["id"]] = companion


def _set_turn(engine: Dnd2024CombatEngine, instance: GameInstance, actor_id: str) -> None:
    combat = instance.ruleset_state["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    actor = engine._actor_view(instance, combat, actor_id)
    combat["economy"] = engine._fresh_economy(actor)


def test_companion_actor_identity_and_view() -> None:
    from src.rulesets.dnd2024.combat.primitives import (
        CombatIntentError, actor_kind, actor_side, companion_actor,
    )

    assert actor_kind("companion:mira") == ("companion", "mira")
    assert actor_side("companion") == "party"
    assert actor_side("player") == "party"
    assert actor_side("enemy") == "enemy"

    engine, instance = _instance()
    _seed_companion(instance, _companion("mira", "Mira"))
    view = engine._actor_view(instance, {"enemies": {}}, companion_actor("mira"))
    assert view["kind"] == "companion"
    assert view["side"] == "party"
    assert view["actor_id"] == "companion:mira"
    assert view["hp"] == 20
    assert view["armor_class"] == 16

    with pytest.raises(CombatIntentError):
        engine._actor_view(instance, {"enemies": {}}, "companion:ghost")


def test_companion_joins_initiative_with_party_side() -> None:
    engine, instance = _instance()
    _seed_companion(instance, _companion("mira", "Mira"))
    batch = _start(engine, instance)
    combat = instance.ruleset_state["combat"]

    assert "player:gm" in combat["initiative"]
    assert "companion:mira" in combat["initiative"]
    assert "enemy:goblin-1" in combat["initiative"]
    kinds = {row["actor_id"]: row["kind"] for row in combat["initiative_rolls"]}
    assert kinds["companion:mira"] == "companion"

    companion_view = engine._actor_view(instance, combat, "companion:mira")
    assert companion_view["side"] == "party"
    hostile = engine._hostile_targets(instance, combat, "companion:mira")
    assert {item["actor_id"] for item in hostile} == {"enemy:goblin-1"}


def test_companion_cannot_attack_party_members() -> None:
    engine, instance = _instance()
    _seed_companion(instance, _companion("mira", "Mira"))
    _start(engine, instance)
    _set_turn(engine, instance, "companion:mira")
    version = instance.ruleset_state["version"]

    friendly = engine.validate_intent(instance, {
        "intent_id": "c-attack-ally", "type": "attack", "expected_version": version,
        "submitted_by": "gm", "actor_id": "companion:mira",
        "target_id": "player:gm", "weapon_ref": "item:greatsword",
    })
    assert friendly["ok"] is False

    hostile = engine.validate_intent(instance, {
        "intent_id": "c-attack-enemy", "type": "attack", "expected_version": version,
        "submitted_by": "gm", "actor_id": "companion:mira",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:greatsword",
    })
    assert hostile["ok"] is True


def test_enemy_can_attack_companion_and_hp_syncs() -> None:
    engine, instance = _instance()
    _seed_companion(instance, _companion("mira", "Mira", hp=10))
    _start(engine, instance)
    _set_turn(engine, instance, "enemy:goblin-1")

    intent = engine.next_automatic_intent(instance)
    # 同距离下 HP 更低的目标优先：mira(10) 会被敌人在自动回合选中。
    assert intent is not None and intent["type"] == "attack"
    assert intent["target_id"] == "companion:mira"

    resolved = engine.resolve_intent(instance, intent, SequenceRng([20, 1]))
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    mira = instance.ruleset_state["party"]["companions"]["mira"]["ruleset_character"]
    assert mira["resources"]["hp"] < 10


def test_companion_automatic_turn_attacks_through_authoritative_chain() -> None:
    engine, instance = _instance()
    _seed_companion(instance, _companion("mira", "Mira"))
    _start(engine, instance)
    _set_turn(engine, instance, "companion:mira")

    intent = engine.next_automatic_intent(instance)
    assert intent is not None
    assert intent["type"] in {"attack", "cast_spell", "move", "dodge", "end_turn"}
    assert intent["submitted_by"] == "gm"

    if intent["type"] == "attack":
        resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 4, 5]))
        applied = engine.apply_batch(instance, resolved["event_batch"])
        assert applied["applied"] is True


def test_player_can_heal_companion() -> None:
    engine, instance = _instance(wizard=True)
    _seed_companion(instance, _companion("mira", "Mira", hp=6, max_hp=20))
    _start(engine, instance)
    sheet = instance.players["gm"]["character_sheet"]
    prepared = sheet["ruleset_character"]["spellcasting"]["class"].setdefault(
        "prepared_spell_refs", [],
    )
    prepared.append("spell:cure_wounds")
    _set_turn(engine, instance, "player:gm")

    resolved = engine.resolve_intent(instance, {
        "intent_id": "heal-companion", "type": "cast_spell", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:cure_wounds", "slot_level": 1,
        "target_ids": ["companion:mira"],
    }, SequenceRng([3, 4]))
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    mira = instance.ruleset_state["party"]["companions"]["mira"]["ruleset_character"]
    assert mira["resources"]["hp"] > 6
    assert mira["resources"]["hp"] <= mira["resources"]["max_hp"]


def test_companion_spell_slot_consumed() -> None:
    engine, instance = _instance()
    _seed_companion(instance, _companion(
        "mira", "Mira", slots={1: 2}, prepared=["spell:cure_wounds"],
    ))
    _start(engine, instance)
    _set_turn(engine, instance, "companion:mira")
    version = instance.ruleset_state["version"]

    slots_before = (
        instance.ruleset_state["party"]["companions"]["mira"]["ruleset_character"]
        ["spellcasting"]["class"]["slots_current"]["1"]
    )
    resolved = engine.resolve_intent(instance, {
        "intent_id": "companion-heal", "type": "cast_spell", "expected_version": version,
        "submitted_by": "gm", "actor_id": "companion:mira",
        "spell_ref": "spell:cure_wounds", "slot_level": 1,
        "target_ids": ["player:gm"],
    }, SequenceRng([2, 3]))
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    slots_after = (
        instance.ruleset_state["party"]["companions"]["mira"]["ruleset_character"]
        ["spellcasting"]["class"]["slots_current"]["1"]
    )
    assert slots_after == slots_before - 1
    assert any(
        event["type"] == "dnd2024.spell.cast" and event["actor_id"] == "companion:mira"
        for event in resolved["event_batch"]["events"]
    )
