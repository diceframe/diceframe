"""Weapon Mastery (Fighter 1) declaration chain and the four implemented properties.

The chain is three layers: the ``weapon_mastery`` feature grants access, every
weapon profile declares its property id, and the engine registry maps that id
to an effect.  Graze is a miss-face fixed amount; sap/vex/slow ride the existing
``next_attack_disadvantage`` / ``next_attack_advantage`` / ``slowed_10``
conditions and the existing consume-on-attack primitive.

Every state change goes through ``resolve_intent`` -> ``apply_batch``.  The two
same-side held-damage cases (Shield / Uncanny Dodge) build their pending
decision from the real attack events and apply it through ``apply_batch`` --
the same construction exception the sneak-attack tests use, because the
production offer deliberately never targets the attacker's own side.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatCatalog, Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.reaction_spells import HIT_REACTION_DECISION
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, advance, monk_instance, with_equipment


# §3.1 of the slice spec: every weapon profile carries one mastery property id.
MASTERY_BY_WEAPON = {
    "dagger": "nick",
    "flail": "sap",
    "greataxe": "cleave",
    "greatsword": "graze",
    "handaxe": "vex",
    "javelin": "slow",
    "longbow": "slow",
    "longsword": "sap",
    "mace": "sap",
    "quarterstaff": "topple",
    "scimitar": "nick",
    "shortbow": "vex",
    "shortsword": "vex",
    "sickle": "nick",
    "spear": "sap",
}

_WEAPON_CLASSES = (
    "class:fighter", "class:barbarian", "class:rogue",
    "class:paladin", "class:ranger",
)


def _preset_sheet(runtime: Dnd2024Runtime, preset_id: str, *, level: int = 1) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": preset_id},
    )
    return advance(runtime, sheet, level) if level > 1 else sheet


def _companion(
    sheet: dict, *, spellcasting: dict | None = None, hp: int = 40,
    conditions: dict | None = None,
) -> dict:
    canonical = deepcopy(sheet["ruleset_character"])
    canonical["resources"] = {
        **canonical.get("resources", {}), "hp": hp, "max_hp": hp,
    }
    if spellcasting is not None:
        canonical.setdefault("spellcasting", {})["class"] = spellcasting
    if conditions is not None:
        canonical.setdefault("conditions", {}).update(conditions)
    return {
        "id": "mira", "name": "Mira", "controller": "ai", "active": True,
        "ruleset_character": canonical,
    }


def _goblin(enemy_id: str = "goblin-1", *, position: int = 5, hp: int = 40) -> dict:
    return {
        "id": enemy_id, "name": "Goblin", "hp": hp, "armor_class": 12,
        "speed": 30, "position": position, "initiative_modifier": 2,
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": -1, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _instance(
    *, preset_id: str = "stalwart_guardian", level: int = 1,
    equipment: list[str] | None = None, abilities: dict | None = None,
    companion_level: int | None = None,
    companion_spellcasting: dict | None = None,
    companion_conditions: dict | None = None,
    enemies: list[dict] | None = None,
) -> tuple[Dnd2024Runtime, Dnd2024CombatEngine, object]:
    runtime = Dnd2024Runtime()
    sheet = _preset_sheet(runtime, preset_id, level=level)
    if abilities:
        sheet["ruleset_character"]["abilities"].update(abilities)
    if equipment:
        sheet = with_equipment(runtime, sheet, *equipment)
    engine, instance = monk_instance(runtime, sheet)
    if companion_level is not None:
        companion = _companion(
            _preset_sheet(runtime, "lucky_scout", level=companion_level),
            spellcasting=companion_spellcasting,
            conditions=companion_conditions,
        )
        party = ruleset_runtime.state(instance).setdefault("party", {"companions": {}})
        party.setdefault("companions", {})[companion["id"]] = companion
    _start(engine, instance, enemies or [_goblin()])
    return runtime, engine, instance


def _start(engine: Dnd2024CombatEngine, instance, enemies: list[dict]) -> dict:
    companions = ruleset_runtime.state(instance).get("party", {}).get("companions", {})
    rolls = [20] + [1] * (len(companions) + len(enemies))
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemies,
    }, SequenceRng(rolls))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["initiative"][0] == "player:gm"
    return combat


def _apply(engine: Dnd2024CombatEngine, instance, intent: dict, rolls=()) -> list[dict]:
    batch = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert batch["ok"] is True, batch
    assert engine.apply_batch(instance, batch["event_batch"])["applied"] is True
    return batch["event_batch"]["events"]


def _attack(
    engine: Dnd2024CombatEngine, instance, *, rolls, intent_id: str = "",
    actor_id: str = "player:gm", target_id: str = "enemy:goblin-1",
    weapon: str | None = None, attack_id: str | None = None,
) -> list[dict]:
    version = ruleset_runtime.state(instance)["version"]
    intent = {
        "intent_id": intent_id or f"attack-{version}", "type": "attack",
        "expected_version": version, "submitted_by": "gm",
        "actor_id": actor_id, "target_id": target_id,
    }
    if weapon is not None:
        intent["weapon_ref"] = weapon
    if attack_id is not None:
        intent["attack_id"] = attack_id
    return _apply(engine, instance, intent, rolls)


def _end_turn(engine: Dnd2024CombatEngine, instance, actor_id: str) -> list[dict]:
    version = ruleset_runtime.state(instance)["version"]
    return _apply(engine, instance, {
        "intent_id": f"end-{version}", "type": "end_turn",
        "expected_version": version, "submitted_by": "gm", "actor_id": actor_id,
    })


def _check(events: list[dict], *, kind: str = "attack") -> dict:
    return next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def _damage_to(events: list[dict], target_id: str) -> dict:
    return next(
        event for event in events
        if event["type"] == "resource.changed" and event.get("resource") == "hp"
        and event.get("target_id") == target_id
    )


def _applied(events: list[dict], condition: str) -> dict:
    return next(
        event for event in events
        if event["type"] == "condition.applied" and event["condition"] == condition
    )


def _actor_hp(instance, actor_id: str) -> int:
    kind, raw = actor_id.split(":", 1)
    if kind == "enemy":
        return int(ruleset_runtime.state(instance)["combat"]["enemies"][raw]["hp"])
    if kind == "player":
        canonical = instance.get_character_sheet(raw)["ruleset_character"]
    else:
        canonical = ruleset_runtime.state(instance)["party"]["companions"][raw][
            "ruleset_character"
        ]
    return int(canonical["resources"]["hp"])


def _canonical_conditions(instance, actor_id: str) -> dict:
    kind, raw = actor_id.split(":", 1)
    if kind == "enemy":
        return ruleset_runtime.state(instance)["combat"]["enemies"][raw].setdefault(
            "conditions", {},
        )
    if kind == "player":
        character = instance.get_character_sheet(raw)["ruleset_character"]
    else:
        character = ruleset_runtime.state(instance)["party"]["companions"][raw][
            "ruleset_character"
        ]
    return character.setdefault("conditions", {})


def _shield_spellcasting() -> dict:
    return {
        "ability": "int", "slots_current": {"1": 1},
        "prepared_spell_refs": ["spell:shield"], "cantrip_refs": [],
        "concentration": None,
    }


def _concentration() -> dict:
    return {"spell_ref": "spell:bless", "target_ids": ["player:gm"]}


# --------------------------------------------------------------------------
# M1-M4: Graze (greatsword) is a miss-face fixed amount with no dice.
# --------------------------------------------------------------------------


def test_graze_deals_the_ability_modifier_on_a_miss_with_no_damage_dice() -> None:
    _runtime, engine, instance = _instance(equipment=["item:greatsword"])

    events = _attack(engine, instance, weapon="item:greatsword", rolls=[1])

    assert _check(events)["success"] is False
    damage = _damage_to(events, "enemy:goblin-1")
    assert damage["amount"] == 3
    assert damage["delta"] == -3
    assert damage["damage_type"] == "slashing"
    assert damage["rolls"] == []
    assert _actor_hp(instance, "enemy:goblin-1") == 37
    assert not any("mastery_effects" in event for event in events)
    assert not any(event["type"] == "condition.applied" for event in events)


def test_graze_respects_resistance_and_triggers_a_concentration_save() -> None:
    _runtime, engine, instance = _instance(
        equipment=["item:greatsword"], companion_level=1,
        companion_spellcasting={"ability": "int", "slots_current": {},
                                "prepared_spell_refs": [], "cantrip_refs": [],
                                "concentration": _concentration()},
        companion_conditions={"stoneskinned": {}},
    )
    combat = ruleset_runtime.state(instance)["combat"]
    events = engine._attack_events(instance, combat, {
        "intent_id": "graze-resist", "actor_id": "player:gm",
        "target_id": "companion:mira", "weapon_ref": "item:greatsword",
    }, SequenceRng([1]))

    assert _check(events)["success"] is False
    damage = _damage_to(events, "companion:mira")
    assert damage["amount"] == 1
    assert damage["resisted"] is True
    assert damage["resistance"] == "stoneskinned"
    concentration = _check(events, kind="concentration")
    assert concentration["target"] == 10


def test_graze_can_finish_a_one_hit_point_enemy() -> None:
    _runtime, engine, instance = _instance(
        equipment=["item:greatsword"], enemies=[_goblin(hp=1)],
    )

    events = _attack(engine, instance, weapon="item:greatsword", rolls=[1])

    assert _check(events)["success"] is False
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 3
    ended = next(
        event for event in events if event["type"] == "dnd2024.combat.ended"
    )
    assert ended["reason"] == "victory"


def test_graze_floors_a_negative_modifier_at_zero() -> None:
    _runtime, engine, instance = _instance(
        equipment=["item:greatsword"], abilities={"str": 8},
    )

    events = _attack(engine, instance, weapon="item:greatsword", rolls=[1])

    assert _check(events)["success"] is False
    assert not any(
        event["type"] == "resource.changed"
        and event.get("target_id") == "enemy:goblin-1"
        for event in events
    )


# --------------------------------------------------------------------------
# M5-M8: Sap applies on any hit; Vex requires landed damage.
# --------------------------------------------------------------------------


def test_sap_applies_on_a_zero_damage_hit() -> None:
    _runtime, engine, instance = _instance(
        equipment=["item:longsword"], abilities={"str": 8},
    )

    events = _attack(engine, instance, weapon="item:longsword", rolls=[15, 1])

    assert _check(events)["success"] is True
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 0
    applied = _applied(events, "next_attack_disadvantage")
    assert applied["target_id"] == "enemy:goblin-1"
    assert applied["duration"] == "after_next_attack"
    assert applied["source_actor_id"] == "player:gm"


def test_sap_disadvantage_is_consumed_by_the_targets_next_attack() -> None:
    _runtime, engine, instance = _instance(equipment=["item:longsword"])

    events = _attack(engine, instance, weapon="item:longsword", rolls=[15, 4])
    assert _applied(events, "next_attack_disadvantage")["target_id"] == "enemy:goblin-1"

    _end_turn(engine, instance, "player:gm")
    events = _attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:gm",
        attack_id="scimitar", rolls=[15, 12, 3], intent_id="goblin-attack",
    )

    check = _check(events)
    assert check["advantage_mode"] == "disadvantage"
    assert {"code": "next_attack_disadvantage", "mode": "disadvantage"} in (
        check["advantage_sources"]
    )
    assert any(
        event["type"] == "condition.removed"
        and event.get("condition") == "next_attack_disadvantage"
        and event.get("target_id") == "enemy:goblin-1"
        for event in events
    )
    assert "next_attack_disadvantage" not in _canonical_conditions(
        instance, "enemy:goblin-1",
    )


def test_vex_grants_advantage_against_the_target_and_is_consumed() -> None:
    _runtime, engine, instance = _instance(equipment=["item:handaxe"])

    events = _attack(engine, instance, weapon="item:handaxe", rolls=[15, 4])
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 7
    applied = _applied(events, "next_attack_advantage")
    assert applied["target_id"] == "enemy:goblin-1"
    assert applied["duration"] == "after_next_attack"

    ruleset_runtime.state(instance)["combat"]["economy"]["attacks_remaining"] = 1
    events = _attack(
        engine, instance, weapon="item:handaxe", rolls=[15, 10, 4],
        intent_id="attack-vex-consume",
    )

    check = _check(events)
    assert check["advantage_mode"] == "advantage"
    assert {"code": "next_attack_advantage", "mode": "advantage"} in (
        check["advantage_sources"]
    )
    assert any(
        event["type"] == "condition.removed"
        and event.get("condition") == "next_attack_advantage"
        for event in events
    )
    assert "next_attack_advantage" not in _canonical_conditions(
        instance, "enemy:goblin-1",
    )


def test_vex_does_not_apply_when_the_hit_deals_no_damage() -> None:
    _runtime, engine, instance = _instance(
        equipment=["item:handaxe"], abilities={"str": 8},
    )

    events = _attack(engine, instance, weapon="item:handaxe", rolls=[15, 1])

    assert _check(events)["success"] is True
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 0
    assert not any(event["type"] == "condition.applied" for event in events)


# --------------------------------------------------------------------------
# M9: Slow lasts until the attacker's next turn starts.
# --------------------------------------------------------------------------


def test_slow_reduces_speed_until_the_attackers_next_turn_starts() -> None:
    _runtime, engine, instance = _instance(equipment=["item:javelin"])

    events = _attack(engine, instance, weapon="item:javelin", rolls=[15, 4])
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 7
    applied = _applied(events, "slowed_10")
    assert applied["target_id"] == "enemy:goblin-1"
    assert applied["duration"] == "attacker_next_turn_start"
    assert applied["source_actor_id"] == "player:gm"

    _end_turn(engine, instance, "player:gm")
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    assert economy["actor_id"] == "enemy:goblin-1"
    assert economy["speed"] == 20

    _end_turn(engine, instance, "enemy:goblin-1")
    assert ruleset_runtime.state(instance)["combat"]["economy"]["actor_id"] == "player:gm"
    assert "slowed_10" not in _canonical_conditions(instance, "enemy:goblin-1")


# --------------------------------------------------------------------------
# M10-M13: unlock gating, held damage correctness, and fail-closed entries.
# --------------------------------------------------------------------------


def test_a_class_without_weapon_mastery_gets_no_property_effect() -> None:
    _runtime, engine, instance = _instance(
        preset_id="curious_arcanist", equipment=["item:longsword"],
    )

    events = _attack(engine, instance, weapon="item:longsword", rolls=[15, 2])

    assert _check(events)["success"] is True
    assert not any(event["type"] == "condition.applied" for event in events)


def _held_attack(
    engine: Dnd2024CombatEngine, instance, *, target_id: str, weapon: str, rolls,
) -> tuple[list[dict], dict]:
    """Run the real attack resolution against a same-side target.

    The committed HP change and the deferred feature-use / mastery markers are
    pulled out of the batch and re-attached as the metadata production keeps
    inside one held damage event, so a later reaction can discard them.
    """

    combat = ruleset_runtime.state(instance)["combat"]
    events = engine._attack_events(instance, combat, {
        "intent_id": f"held:{target_id}", "actor_id": "player:gm",
        "target_id": target_id, "weapon_ref": weapon,
    }, SequenceRng(list(rolls)))
    kept: list[dict] = []
    damage_event: dict | None = None
    feature_used: dict | None = None
    mastery_effects: list[dict] = []
    for event in events:
        if event["type"] == "resource.changed" and event.get("resource") == "hp":
            damage_event = deepcopy(event)
        elif event["type"] == "dnd2024.feature.used":
            feature_used = deepcopy(event)
        elif event["type"] == "condition.applied":
            mastery_effects.append(deepcopy(event))
        else:
            kept.append(event)
    assert damage_event is not None
    if feature_used is not None:
        damage_event["feature_use"] = {
            key: value for key, value in feature_used.items() if key != "type"
        }
    if mastery_effects:
        damage_event["mastery_effects"] = [
            {
                key: value for key, value in effect.items()
                if key not in {"type", "target_id"}
            }
            for effect in mastery_effects
        ]
    return kept, damage_event


def _held_reaction_decision(
    engine: Dnd2024CombatEngine, instance, *, defender_id: str,
    check_event: dict, damage_event: dict,
) -> dict:
    combat = ruleset_runtime.state(instance)["combat"]
    defender = engine._actor_view(instance, combat, defender_id)
    spells = engine._reaction_spell_choices(combat, defender)
    features = engine._reaction_feature_choices(combat, defender)
    return {
        "decision_id": f"hit-reaction:held:{defender_id}",
        "kind": HIT_REACTION_DECISION,
        "actor_id": defender_id,
        "attacker_id": "player:gm",
        "assigned_to": "gm",
        "options": [
            *(["cast"] if spells else []),
            *(["feature"] if features else []),
            "decline",
        ],
        "attack_total": int(check_event["total"]),
        "armor_class": int(check_event["target"]),
        "critical": bool(check_event["critical"]),
        "damage_event": deepcopy(damage_event),
        "spells": spells,
        "features": features,
        "next_decision": None,
    }


def _apply_events(
    engine: Dnd2024CombatEngine, instance, events: list[dict], *, intent_id: str,
) -> None:
    version = int(ruleset_runtime.state(instance)["version"])
    batch = {
        "batch_id": f"batch_{intent_id}", "intent_id": intent_id,
        "intent_type": "attack", "expected_version": version,
        "result_version": version + 1, "events": events,
        "source_ref": "srd-5.2.1:p24-p27:playing-the-game",
    }
    assert engine.apply_batch(instance, batch)["applied"] is True


def test_shield_turning_the_hit_into_a_miss_discards_the_mastery_condition() -> None:
    _runtime, engine, instance = _instance(
        equipment=["item:longsword"], companion_level=1,
        companion_spellcasting=_shield_spellcasting(),
    )

    kept, damage_event = _held_attack(
        engine, instance, target_id="companion:mira",
        weapon="item:longsword", rolls=[9, 4],
    )
    check = _check(kept)
    assert check["total"] == 9 + 5 and check["target"] == 14
    assert damage_event["amount"] == 4 + 3
    assert damage_event["mastery_effects"] == [{
        "condition": "next_attack_disadvantage",
        "duration": "after_next_attack", "source_actor_id": "player:gm",
    }]
    decision = _held_reaction_decision(
        engine, instance, defender_id="companion:mira",
        check_event=check, damage_event=damage_event,
    )
    assert decision["options"] == ["cast", "decline"]
    _apply_events(
        engine, instance,
        [*kept, {"type": "dnd2024.decision.pending", **decision}],
        intent_id="held-shield",
    )

    hp_before = _actor_hp(instance, "companion:mira")
    events = _apply(engine, instance, {
        "intent_id": "shield-1", "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "cast", "spell_ref": "spell:shield", "slot_level": 1,
    })

    assert not any(
        event["type"] == "condition.applied"
        and event.get("condition") == "next_attack_disadvantage"
        for event in events
    )
    assert not any(
        event["type"] == "resource.changed" and event.get("target_id") == "companion:mira"
        for event in events
    )
    assert _actor_hp(instance, "companion:mira") == hp_before
    assert "next_attack_disadvantage" not in _canonical_conditions(
        instance, "companion:mira",
    )


def test_uncanny_dodge_halves_the_hit_but_keeps_the_mastery_condition() -> None:
    _runtime, engine, instance = _instance(
        equipment=["item:longsword"], companion_level=5,
    )

    kept, damage_event = _held_attack(
        engine, instance, target_id="companion:mira",
        weapon="item:longsword", rolls=[9, 4],
    )
    check = _check(kept)
    assert check["total"] == 9 + 5 and check["target"] == 14
    assert damage_event["amount"] == 4 + 3
    decision = _held_reaction_decision(
        engine, instance, defender_id="companion:mira",
        check_event=check, damage_event=damage_event,
    )
    assert [row["feature_id"] for row in decision["features"]] == ["uncanny_dodge"]
    assert decision["options"] == ["feature", "decline"]
    _apply_events(
        engine, instance,
        [*kept, {"type": "dnd2024.decision.pending", **decision}],
        intent_id="held-uncanny",
    )

    hp_before = _actor_hp(instance, "companion:mira")
    events = _apply(engine, instance, {
        "intent_id": "uncanny-1", "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "feature", "feature_id": "uncanny_dodge",
    })

    damage = _damage_to(events, "companion:mira")
    assert damage["amount"] == 3
    assert _actor_hp(instance, "companion:mira") == hp_before - 3
    applied = _applied(events, "next_attack_disadvantage")
    assert applied["target_id"] == "companion:mira"
    assert applied["source_actor_id"] == "player:gm"


@pytest.mark.parametrize(
    ("weapon", "mastery"),
    [("item:greataxe", "cleave"), ("item:quarterstaff", "topple")],
)
def test_unimplemented_properties_fail_closed(weapon: str, mastery: str) -> None:
    _runtime, engine, instance = _instance(equipment=[weapon])

    events = _attack(engine, instance, weapon=weapon, rolls=[15, 4])

    assert _check(events)["success"] is True
    assert _damage_to(events, "enemy:goblin-1")["amount"] > 0
    assert not any(event["type"] == "condition.applied" for event in events)
    assert [
        event.get("resource") for event in events
        if event["type"] == "resource.changed"
    ] == ["hp"]


# --------------------------------------------------------------------------
# M14 + declaration-chain data guards.
# --------------------------------------------------------------------------


def test_every_weapon_profile_declares_the_expected_mastery_property() -> None:
    runtime = Dnd2024Runtime()
    catalog = Dnd2024CombatCatalog.from_bundle(runtime.load_bundle("en"))

    assert {
        weapon_id: weapon.get("mastery")
        for weapon_id, weapon in catalog.weapons.items()
    } == MASTERY_BY_WEAPON
    assert "mastery" not in catalog.unarmed_strike


def test_the_five_weapon_classes_own_the_weapon_mastery_feature() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    for class_ref in _WEAPON_CLASSES:
        character = {
            "build": {"class_levels": [{"class_ref": class_ref, "level": 1}]},
        }
        assert resolver.has_feature(character, "weapon_mastery") is True

    wizard = {
        "build": {"class_levels": [{"class_ref": "class:wizard", "level": 1}]},
    }
    assert resolver.has_feature(wizard, "weapon_mastery") is False
