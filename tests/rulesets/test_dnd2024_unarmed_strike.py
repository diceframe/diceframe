"""Issue #301: action availability for weaponless D&D 2024 actors.

Covers the two halves of the issue:

* every player-like actor (player / companion) always owns a canonical
  Unarmed Strike, resolved through the same authoritative attack chain as an
  equipped weapon;
* bonus-action entries appear only for bonus-action capabilities the runtime
  can actually settle, instead of an empty "Bonus Action" button.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.engine.game_instance import GameInstance
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.catalog import (
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.engine.modules import ruleset_runtime


@dataclass
class SequenceRng:
    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum
        return value


def _monk_draft(runtime: Dnd2024Runtime, locale: str = "en") -> dict:
    """A level 1 monk who takes the weaponless equipment package (monk_b).

    This is the exact shape reported in #301: ``equipment.item_refs`` is empty,
    so no equipped weapon exists to derive an attack from.
    """

    draft = {
        "locale": locale,
        "name": "Monk",
        "level": 1,
        "ability_method": "standard_array",
        "base_abilities": {
            "str": 12, "dex": 14, "con": 13, "int": 8, "wis": 15, "cha": 10,
        },
        "class_ref": "class:monk",
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "class_skill_refs": ["skill:acrobatics", "skill:stealth"],
        "equipment_package_ref": "equipment_package:monk_b",
        "background_equipment_package_ref": "equipment_package:acolyte_b",
        "alignment": "neutral",
    }
    # Guided choices stay data-driven so the fixture keeps following the bundle
    # instead of hard-coding content ids.
    choices = runtime.builder_choices(None, draft)
    ability_refs = choices["background_ability_refs"]
    draft["background_ability_bonuses"] = {
        ability_refs[0].split(":", 1)[1]: 2,
        ability_refs[1].split(":", 1)[1]: 1,
    }
    draft["species_size"] = (choices["species_sizes"] or [""])[0]
    draft["species_skill_refs"] = [
        item["ref"] for item in choices["species_skills"]
    ][: choices["species_skill_count"]]
    draft["species_feat_refs"] = [
        item["ref"] for item in choices["species_feats"]
    ][: choices["species_feat_count"]]
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
    draft["language_refs"] = ["language:common", "language:dwarvish", "language:elvish"]
    draft["feat_choice_answers"] = {
        feat["feat_ref"]: {
            spec["id"]: [option["value"] for option in spec["options"]][: int(spec["count"])]
            for spec in feat["specs"]
        }
        for feat in choices["feat_choices"]
    }
    assert runtime.validate_character(None, draft) == []
    return draft


def _instance(
    runtime: Dnd2024Runtime, character: dict, *, locale: str = "en",
) -> tuple[Dnd2024CombatEngine, GameInstance]:
    instance = GameInstance(
        game_key=("test", "dnd2024-unarmed", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language=locale,
    )
    instance.players["gm"] = {
        "character_name": str(character.get("character_name") or "Hero"),
        "character_sheet": character,
    }
    assert instance.bind_ruleset_runtime(character["rule_binding"])
    return Dnd2024CombatEngine(runtime.load_bundle(locale), EncounterAccess.sandbox()), instance


def _monk_instance(
    locale: str = "en",
) -> tuple[Dnd2024CombatEngine, GameInstance, dict]:
    runtime = Dnd2024Runtime()
    character = runtime.finalize_character(None, _monk_draft(runtime, locale))
    engine, instance = _instance(runtime, character, locale=locale)
    return engine, instance, character


def _preset_instance(
    preset_id: str, *, locale: str = "en",
) -> tuple[Dnd2024CombatEngine, GameInstance]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": locale})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    character = runtime.finalize_character(
        None, {**preset["draft"], "locale": locale, "name": preset_id},
    )
    # Soldier grants Savage Attacker; its hit window would hold the damage
    # this file asserts on directly.
    feats = character["ruleset_character"]["features"].setdefault("feat_refs", [])
    if "feat:savage_attacker" in feats:
        feats.remove("feat:savage_attacker")
    return _instance(runtime, character, locale=locale)


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


def _start(
    engine: Dnd2024CombatEngine, instance: GameInstance, *, position: int = 5,
) -> None:
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin(position=position)],
    }, SequenceRng([20, 1]))
    assert resolved["ok"] is True
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"


def _attack_intent(engine: Dnd2024CombatEngine, instance: GameInstance) -> dict:
    return next(
        item for item in engine.available_intents(instance, "gm")
        if item["type"] == "attack"
    )


def _bonus_action_consumers(actions: list[dict]) -> list[dict]:
    """Available intents that really spend the bonus action resource."""

    return [
        item for item in actions
        if item["type"] == "cast_spell"
        and any(
            str(spell["casting_time"]).startswith("Bonus Action")
            for spell in item["spells"]
        )
    ]


def test_weaponless_player_gets_a_canonical_unarmed_attack() -> None:
    engine, instance, character = _monk_instance()
    canonical = character["ruleset_character"]
    assert canonical["equipment"]["item_refs"] == []
    _start(engine, instance)

    attack = _attack_intent(engine, instance)

    assert [item["weapon_ref"] for item in attack["weapons"]] == ["unarmed_strike"]
    unarmed = attack["weapons"][0]
    assert unarmed["name"] == "Unarmed Strike"
    # 武僧的武艺特性把徒手打击的基础伤害换成 Martial Arts Die（此处 1d6）；
    # 攻击身份仍然是同一个 canonical unarmed_strike。
    assert unarmed["damage"] == "1d6"
    assert unarmed["martial_arts"] is True
    assert unarmed["damage_type"] == "bludgeoning"
    assert unarmed["range"] == 5
    assert unarmed["unarmed"] is True
    assert {item["type"] for item in engine.available_intents(instance, "gm")} >= {
        "attack", "dash", "dodge", "disengage", "move", "end_turn",
    }


def test_non_monk_unarmed_strike_keeps_the_base_damage() -> None:
    # 非武僧（此处为战士）不获得武艺：徒手打击仍然是基础 1 点钝击。
    engine, instance = _preset_instance("stalwart_guardian")
    _start(engine, instance)

    unarmed = _attack_intent(engine, instance)["weapons"][-1]

    assert unarmed["weapon_ref"] == "unarmed_strike"
    assert unarmed["damage"] == "1"
    assert "martial_arts" not in unarmed


def test_unarmed_strike_resolves_through_the_authoritative_chain() -> None:
    engine, instance, _character = _monk_instance()
    _start(engine, instance)
    intent = {
        "intent_id": "unarmed-1", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "unarmed_strike",
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 3]))
    assert resolved["ok"] is True
    applied = engine.apply_batch(instance, resolved["event_batch"])
    replayed = engine.apply_batch(instance, resolved["event_batch"])

    events = resolved["event_batch"]["events"]
    check = next(event for event in events if event["type"] == "check.resolved")
    damage = next(event for event in events if event["type"] == "resource.changed")
    canonical = instance.get_character_sheet("gm")["ruleset_character"]

    assert applied["applied"] is True and applied["state_version"] == 2
    # 武艺允许力量/敏捷中修正值较高者：DEX 14(+2) 高于 STR 12(+1)，取 DEX，
    # 再加熟练加值 (+2)；伤害为 1d6 加 DEX 修正（骰点 3 + 2）。
    assert check["modifier"] == 4 and check["success"] is True
    assert damage["amount"] == 5 and damage["damage_type"] == "bludgeoning"
    assert damage["rolls"] == [3]
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 13
    # 徒手打击是天然能力：不写入 inventory / equipment。
    assert canonical["equipment"]["item_refs"] == []
    assert replayed["duplicate"] is True
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 13


def test_unarmed_strike_shares_the_attack_action_economy() -> None:
    engine, instance, _character = _monk_instance()
    _start(engine, instance)
    version = ruleset_runtime.state(instance)["version"]

    first = engine.resolve_intent(instance, {
        "intent_id": "unarmed-1", "type": "attack", "expected_version": version,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "unarmed_strike",
    }, SequenceRng([15, 3]))
    engine.apply_batch(instance, first["event_batch"])
    economy = ruleset_runtime.state(instance)["combat"]["economy"]

    assert economy["action"] == 0 and economy["attacks_remaining"] == 0
    assert not any(
        item["type"] == "attack" for item in engine.available_intents(instance, "gm")
    )
    second = engine.validate_intent(instance, {
        "intent_id": "unarmed-2", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "unarmed_strike",
    })
    assert second["ok"] is False
    assert "Attack action" in second["error"]


def test_equipped_weapons_keep_their_profiles_and_are_offered_first() -> None:
    engine, instance = _preset_instance("stalwart_guardian")
    _start(engine, instance)

    weapons = _attack_intent(engine, instance)["weapons"]
    greatsword = weapons[0]

    assert greatsword["weapon_ref"] == "item:greatsword"
    assert greatsword["damage"] == "2d6" and greatsword["range"] == 5
    assert weapons[-1]["weapon_ref"] == "unarmed_strike"

    resolved = engine.resolve_intent(instance, {
        "intent_id": "greatsword-1", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:greatsword",
    }, SequenceRng([15, 4, 5]))

    events = resolved["event_batch"]["events"]
    check = next(event for event in events if event["type"] == "check.resolved")
    damage = next(event for event in events if event["type"] == "resource.changed")
    # 原武器攻击加值/伤害不受徒手打击影响：STR 17(+3) + 熟练 2 = +5，2d6+3。
    assert check["modifier"] == 5
    assert damage["amount"] == 12 and damage["damage_type"] == "slashing"


def test_unknown_weapon_ref_is_still_rejected() -> None:
    engine, instance, _character = _monk_instance()
    _start(engine, instance)

    result = engine.validate_intent(instance, {
        "intent_id": "forged-1", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:greatsword",
    })

    assert result["ok"] is False
    assert "attack profile is not available" in result["error"]


def test_unarmed_strike_name_follows_the_bundle_locale() -> None:
    engine, instance, _character = _monk_instance(locale="zh-CN")
    _start(engine, instance)

    unarmed = _attack_intent(engine, instance)["weapons"][0]

    assert unarmed["weapon_ref"] == "unarmed_strike"
    assert unarmed["name"] == "徒手打击"


def _weaponless_companion() -> dict:
    return {
        "id": "mira", "name": "Mira", "controller": "ai", "active": True,
        "ruleset_character": {
            "resources": {"hp": 20, "max_hp": 20},
            "conditions": {},
            "abilities": {
                "str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 10,
            },
            "derived": {
                "armor_class": 16, "speed": 30, "initiative": 1,
                "proficiency_bonus": 2, "saving_throws": {"str": 4, "con": 2},
            },
            "proficiencies": {
                "skill_values": {"athletics": 6},
                "weapon_category_refs": ["weapon_category:martial"],
            },
            "equipment": {"item_refs": []},
            "spellcasting": {"class": {
                "ability": "wis", "slots_current": {}, "concentration": None,
                "prepared_spell_refs": [], "cantrip_refs": [],
            }},
            "build": {"class_levels": [{"class_ref": "class:fighter", "level": 1}]},
        },
    }


def test_weaponless_companion_attacks_with_unarmed_strike() -> None:
    engine, instance, _character = _monk_instance()
    party = ruleset_runtime.state(instance).setdefault("party", {"companions": {}})
    party.setdefault("companions", {})["mira"] = _weaponless_companion()
    _start(engine, instance)
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index("companion:mira")
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, "companion:mira"),
    )

    intent = engine.next_automatic_intent(instance)

    assert intent is not None and intent["type"] == "attack"
    assert intent["weapon_ref"] == "unarmed_strike"
    resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 4]))
    assert resolved["ok"] is True
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] < 18


def test_bonus_action_entries_only_exist_for_real_capabilities() -> None:
    # 无合法 bonus-action 能力：不注入空的「附赠动作」入口。
    fighter, fighter_instance = _preset_instance("stalwart_guardian")
    _start(fighter, fighter_instance)
    actions = fighter.available_intents(fighter_instance, "gm")

    assert _bonus_action_consumers(actions) == []
    assert ruleset_runtime.state(fighter_instance)["combat"]["economy"]["bonus_action"] == 1
    assert not any(item["type"] == "bonus_action" for item in actions)

    # 有真实 bonus-action 法术：暴露并被权威结算（施法消耗 bonus_action，不消耗 action）。
    cleric, cleric_instance = _preset_instance("kindly_bulwark")
    _start(cleric, cleric_instance, position=25)
    spell_action = next(
        item for item in cleric.available_intents(cleric_instance, "gm")
        if item["type"] == "cast_spell"
    )
    bonus_spells = {
        spell["spell_ref"] for spell in _bonus_action_consumers([spell_action])[0]["spells"]
    }
    assert "spell:shield_of_faith" in bonus_spells

    resolved = cleric.resolve_intent(cleric_instance, {
        "intent_id": "bonus-1", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(cleric_instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:shield_of_faith", "slot_level": 1,
        "target_ids": ["player:gm"],
    }, SequenceRng([]))
    assert resolved["ok"] is True
    assert cleric.apply_batch(cleric_instance, resolved["event_batch"])["applied"] is True

    economy = ruleset_runtime.state(cleric_instance)["combat"]["economy"]
    assert economy["bonus_action"] == 0 and economy["action"] == 1
    spent = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "dnd2024.action.spent"
    )
    assert spent["resource"] == "bonus_action"

    second = cleric.validate_intent(cleric_instance, {
        "intent_id": "bonus-2", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(cleric_instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:shield_of_faith", "slot_level": 1,
        "target_ids": ["player:gm"],
    })
    assert second["ok"] is False
    assert "bonus action" in second["error"]


@pytest.mark.parametrize(
    "profile",
    [
        {"damage": "not-dice", "damage_type": "bludgeoning", "range": 5},
        {"damage": "1", "damage_type": "", "range": 5},
        {"damage": "1", "damage_type": "bludgeoning", "range": 0},
        "not-an-object",
    ],
)
def test_catalog_rejects_malformed_unarmed_profiles(profile: object) -> None:
    with pytest.raises(CombatCatalogError):
        Dnd2024CombatCatalog._validated_unarmed_strike(profile)


def test_catalog_without_unarmed_profile_exposes_no_unarmed_strike() -> None:
    assert Dnd2024CombatCatalog._validated_unarmed_strike(None) is None
