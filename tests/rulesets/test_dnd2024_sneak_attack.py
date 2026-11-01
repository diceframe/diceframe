"""Sneak Attack (Rogue 1): predicate, per-turn ledger, and held-damage chains.

Every state change goes through the same authoritative chain the web layer
uses: ``resolve_intent`` -> ``apply_batch``.  The two same-side held-damage
cases (Shield / Uncanny Dodge) build their pending decision from the real
attack events and apply it through ``apply_batch``; that is the construction
exception the task spec allows for decision-chain tests, because the
production offer deliberately never targets the attacker's own side.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.reaction_spells import HIT_REACTION_DECISION
from src.rulesets.dnd2024.combat.triggered_spells import HIT_SPELL_DECISION
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, advance, monk_instance, with_equipment


def _rogue_sheet(runtime: Dnd2024Runtime, *, level: int = 1) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "lucky_scout")
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Rogue"},
    )
    return advance(runtime, sheet, level) if level > 1 else sheet


def _companion(sheet: dict, *, spellcasting: dict | None = None, hp: int = 40) -> dict:
    canonical = deepcopy(sheet["ruleset_character"])
    canonical["resources"] = {
        **canonical.get("resources", {}), "hp": hp, "max_hp": hp,
    }
    if spellcasting is not None:
        canonical.setdefault("spellcasting", {})["class"] = spellcasting
    return {
        "id": "mira", "name": "Mira", "controller": "ai", "active": True,
        "ruleset_character": canonical,
    }


def _shield_spellcasting() -> dict:
    return {
        "ability": "int", "slots_current": {"1": 1},
        "prepared_spell_refs": ["spell:shield"], "cantrip_refs": [],
        "concentration": None,
    }


def _goblin(enemy_id: str = "goblin-1", *, position: int = 5, hp: int = 60) -> dict:
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
    *, level: int = 1, equipment: list[str] | None = None,
    spellcasting: dict | None = None, companion_level: int | None = None,
    companion_spellcasting: dict | None = None,
    enemies: list[dict] | None = None,
) -> tuple[Dnd2024Runtime, Dnd2024CombatEngine, object]:
    runtime = Dnd2024Runtime()
    sheet = _rogue_sheet(runtime, level=level)
    if equipment:
        sheet = with_equipment(runtime, sheet, *equipment)
    if spellcasting is not None:
        sheet["ruleset_character"].setdefault("spellcasting", {})["class"] = spellcasting
    engine, instance = monk_instance(runtime, sheet)
    if companion_level is not None:
        companion = _companion(
            _rogue_sheet(runtime, level=companion_level),
            spellcasting=companion_spellcasting,
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
    engine: Dnd2024CombatEngine, instance, *, rolls, intent_id: str = "attack-1",
    weapon: str = "item:dagger", target_id: str = "enemy:goblin-1",
) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": intent_id, "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": target_id, "weapon_ref": weapon,
    }, rolls)


def _end_turn(engine: Dnd2024CombatEngine, instance, actor_id: str) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": f"end-{ruleset_runtime.state(instance)['version']}",
        "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": actor_id,
    })


def _canonical_conditions(instance, actor_id: str) -> dict:
    kind, raw = actor_id.split(":", 1)
    if kind == "player":
        character = instance.get_character_sheet(raw)["ruleset_character"]
    else:
        character = ruleset_runtime.state(instance)["party"]["companions"][raw][
            "ruleset_character"
        ]
    return character.setdefault("conditions", {})


def _grant_advantage(instance, target_id: str) -> None:
    kind, raw = target_id.split(":", 1)
    if kind == "enemy":
        ruleset_runtime.state(instance)["combat"]["enemies"][raw].setdefault(
            "conditions", {},
        )["next_attack_advantage"] = {}
        return
    _canonical_conditions(instance, target_id)["next_attack_advantage"] = {}


def _grant_disadvantage(instance, actor_id: str = "player:gm") -> None:
    _canonical_conditions(instance, actor_id)["next_attack_disadvantage"] = {}


def _feature_usage(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"].get("feature_usage") or {}


def _damage_to(events: list[dict], target_id: str) -> dict:
    return next(
        event for event in events
        if event["type"] == "resource.changed" and event.get("resource") == "hp"
        and event.get("target_id") == target_id
    )


def _check(events: list[dict]) -> dict:
    return next(event for event in events if event["type"] == "check.resolved")


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


def _held_attack(
    engine: Dnd2024CombatEngine, instance, *, target_id: str, rolls,
) -> tuple[list[dict], dict]:
    """Run the real attack resolution against a same-side target.

    Returns the events that stay in the batch plus the damage event that the
    pending reaction decision would hold.  The committed HP change and its
    deferred feature-use marker are removed here and re-attached as the
    ``feature_use`` metadata production keeps inside the held damage event.
    """

    combat = ruleset_runtime.state(instance)["combat"]
    events = engine._attack_events(instance, combat, {
        "intent_id": f"held:{target_id}", "actor_id": "player:gm",
        "target_id": target_id, "weapon_ref": "item:dagger",
    }, SequenceRng(list(rolls)))
    kept: list[dict] = []
    damage_event: dict | None = None
    feature_used: dict | None = None
    for event in events:
        if event["type"] == "resource.changed" and event.get("resource") == "hp":
            damage_event = deepcopy(event)
        elif event["type"] == "dnd2024.feature.used":
            feature_used = deepcopy(event)
        else:
            kept.append(event)
    assert damage_event is not None
    if feature_used is not None:
        damage_event["feature_use"] = {
            key: value for key, value in feature_used.items() if key != "type"
        }
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


def _apply_events(engine: Dnd2024CombatEngine, instance, events: list[dict], *, intent_id: str) -> None:
    version = int(ruleset_runtime.state(instance)["version"])
    batch = {
        "batch_id": f"batch_{intent_id}",
        "intent_id": intent_id,
        "intent_type": "attack",
        "expected_version": version,
        "result_version": version + 1,
        "events": events,
        "source_ref": "srd-5.2.1:p24-p27:playing-the-game",
    }
    assert engine.apply_batch(instance, batch)["applied"] is True


def test_sneak_attack_appends_one_die_with_advantage_and_finesse() -> None:
    runtime, engine, instance = _instance()
    _grant_advantage(instance, "enemy:goblin-1")

    events = _attack(engine, instance, rolls=[15, 3, 4, 5])

    check = _check(events)
    assert check["advantage_mode"] == "advantage"
    assert check["success"] is True
    damage = _damage_to(events, "enemy:goblin-1")
    assert damage["damage_type"] == "piercing"
    assert damage["amount"] == 4 + 3 + 5
    assert damage["rolls"] == [4, 5]
    assert [event["type"] for event in events].count("dnd2024.feature.used") == 1
    assert _feature_usage(instance)["player:gm"] == {
        "round": 1, "turn_index": 0, "feature_ids": ["sneak_attack"],
    }


def test_a_ranged_weapon_qualifies_even_at_range() -> None:
    runtime, engine, instance = _instance(enemies=[_goblin(position=30)])
    _grant_advantage(instance, "enemy:goblin-1")

    events = _attack(engine, instance, weapon="item:shortbow", rolls=[15, 3, 4, 5])

    assert _check(events)["success"] is True
    damage = _damage_to(events, "enemy:goblin-1")
    assert damage["amount"] == 4 + 3 + 5
    assert damage["rolls"] == [4, 5]
    assert _feature_usage(instance)["player:gm"]["feature_ids"] == ["sneak_attack"]


def test_a_non_finesse_melee_weapon_never_qualifies() -> None:
    runtime, engine, instance = _instance(equipment=["item:mace"])
    _grant_advantage(instance, "enemy:goblin-1")

    events = _attack(engine, instance, weapon="item:mace", rolls=[15, 3, 4])

    assert _check(events)["success"] is True
    damage = _damage_to(events, "enemy:goblin-1")
    assert damage["amount"] == 4 + 1
    assert damage["rolls"] == [4]
    assert not any(event["type"] == "dnd2024.feature.used" for event in events)
    assert _feature_usage(instance) == {}


def test_a_thrown_non_finesse_weapon_never_qualifies_even_with_advantage() -> None:
    runtime, engine, instance = _instance(equipment=["item:handaxe"], enemies=[_goblin(position=10)])
    _grant_advantage(instance, "enemy:goblin-1")

    events = _attack(engine, instance, weapon="item:handaxe", rolls=[15, 3, 4])

    assert _check(events)["success"] is True
    damage = _damage_to(events, "enemy:goblin-1")
    # 投掷的近战武器沿用近战属性（力量），也不带偷袭骰。
    strength = instance.get_character_sheet("gm")["ruleset_character"]["abilities"]["str"]
    assert damage["amount"] == 4 + (int(strength) - 10) // 2
    assert damage["rolls"] == [4]
    assert _feature_usage(instance) == {}


def test_a_miss_records_nothing_and_a_later_hit_in_the_same_turn_qualifies() -> None:
    runtime, engine, instance = _instance()

    events = _attack(engine, instance, rolls=[1], intent_id="attack-miss")
    assert _check(events)["success"] is False
    assert _feature_usage(instance) == {}

    ruleset_runtime.state(instance)["combat"]["economy"]["attacks_remaining"] = 1
    _grant_advantage(instance, "enemy:goblin-1")
    events = _attack(engine, instance, rolls=[15, 3, 4, 5], intent_id="attack-hit")

    damage = _damage_to(events, "enemy:goblin-1")
    assert damage["amount"] == 4 + 3 + 5
    assert _feature_usage(instance)["player:gm"]["feature_ids"] == ["sneak_attack"]


def test_an_ally_within_five_feet_qualifies_but_hostile_helpers_do_not() -> None:
    runtime, engine, instance = _instance(companion_level=1)
    events = _attack(engine, instance, rolls=[15, 4, 5])
    assert _check(events)["advantage_mode"] == ""
    damage = _damage_to(events, "enemy:goblin-1")
    assert damage["amount"] == 4 + 3 + 5
    assert _feature_usage(instance)["player:gm"]["feature_ids"] == ["sneak_attack"]

    # No ally at all: the same hit deals only weapon damage.
    runtime, engine, instance = _instance()
    events = _attack(engine, instance, rolls=[15, 4])
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 4 + 3
    assert _feature_usage(instance) == {}

    # An adjacent enemy helper never counts as the attacker's ally.
    runtime, engine, instance = _instance(enemies=[
        _goblin("goblin-1"), _goblin("goblin-2", position=5),
    ])
    events = _attack(engine, instance, rolls=[15, 4])
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 4 + 3
    assert _feature_usage(instance) == {}


@pytest.mark.parametrize(
    "condition",
    ["incapacitated", "paralyzed", "petrified", "stunned", "unconscious"],
)
def test_an_incapacitated_ally_does_not_qualify(condition: str) -> None:
    runtime, engine, instance = _instance(companion_level=1)
    _canonical_conditions(instance, "companion:mira")[condition] = {}

    events = _attack(engine, instance, rolls=[15, 4])

    assert _damage_to(events, "enemy:goblin-1")["amount"] == 4 + 3
    assert _feature_usage(instance) == {}


def test_a_prone_ally_still_qualifies() -> None:
    runtime, engine, instance = _instance(companion_level=1)
    _canonical_conditions(instance, "companion:mira")["prone"] = {}

    events = _attack(engine, instance, rolls=[15, 4, 5])

    assert _damage_to(events, "enemy:goblin-1")["amount"] == 4 + 3 + 5
    assert _feature_usage(instance)["player:gm"]["feature_ids"] == ["sneak_attack"]


def test_disadvantage_blocks_sneak_attack_even_with_a_nearby_ally() -> None:
    runtime, engine, instance = _instance(companion_level=1)
    _grant_disadvantage(instance, "player:gm")

    events = _attack(engine, instance, rolls=[15, 12, 4])

    check = _check(events)
    assert check["advantage_mode"] == "disadvantage"
    assert check["success"] is True
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 4 + 3
    assert _feature_usage(instance) == {}


def test_cancelling_advantage_and_disadvantage_falls_back_to_the_ally_path() -> None:
    runtime, engine, instance = _instance(companion_level=1)
    _grant_advantage(instance, "enemy:goblin-1")
    _grant_disadvantage(instance, "player:gm")

    events = _attack(engine, instance, rolls=[15, 4, 5])

    assert _check(events)["advantage_mode"] == ""
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 4 + 3 + 5
    assert _feature_usage(instance)["player:gm"]["feature_ids"] == ["sneak_attack"]


def test_a_critical_hit_doubles_weapon_hex_and_sneak_dice() -> None:
    runtime, engine, instance = _instance()
    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"].setdefault(
        "conditions", {},
    )["hexed"] = {"source_actor_id": "player:gm"}
    _grant_advantage(instance, "enemy:goblin-1")

    events = _attack(engine, instance, rolls=[20, 3, 4, 3, 5, 2, 6, 1])

    check = _check(events)
    assert check["critical"] is True
    damage = _damage_to(events, "enemy:goblin-1")
    # 2d4 weapon + 2d6 hex + 2d6 sneak + DEX 3, rolls keep dice order.
    assert damage["rolls"] == [4, 3, 5, 2, 6, 1]
    assert damage["amount"] == (4 + 3) + (5 + 2) + (6 + 1) + 3
    assert _feature_usage(instance)["player:gm"]["feature_ids"] == ["sneak_attack"]


def test_a_second_qualifying_hit_in_the_same_turn_gets_no_dice_and_recovers_next_turn() -> None:
    runtime, engine, instance = _instance()
    _grant_advantage(instance, "enemy:goblin-1")
    events = _attack(engine, instance, rolls=[15, 3, 4, 5], intent_id="attack-first")
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 4 + 3 + 5
    assert _feature_usage(instance)["player:gm"]["round"] == 1

    ruleset_runtime.state(instance)["combat"]["economy"]["attacks_remaining"] = 1
    _grant_advantage(instance, "enemy:goblin-1")
    events = _attack(engine, instance, rolls=[15, 3, 4], intent_id="attack-second")

    damage = _damage_to(events, "enemy:goblin-1")
    assert damage["amount"] == 4 + 3
    assert damage["rolls"] == [4]
    assert not any(event["type"] == "dnd2024.feature.used" for event in events)
    assert _feature_usage(instance)["player:gm"]["feature_ids"] == ["sneak_attack"]

    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["round"] == 2 and combat["turn_index"] == 0

    _grant_advantage(instance, "enemy:goblin-1")
    events = _attack(engine, instance, rolls=[15, 3, 4, 5], intent_id="attack-third")

    assert _damage_to(events, "enemy:goblin-1")["amount"] == 4 + 3 + 5
    assert _feature_usage(instance)["player:gm"] == {
        "round": 2, "turn_index": 0, "feature_ids": ["sneak_attack"],
    }


def test_an_opportunity_attack_on_the_enemys_turn_can_sneak_attack_again() -> None:
    runtime, engine, instance = _instance()
    _grant_advantage(instance, "enemy:goblin-1")
    events = _attack(engine, instance, rolls=[15, 3, 4, 5], intent_id="attack-turn")
    assert _damage_to(events, "enemy:goblin-1")["amount"] == 4 + 3 + 5

    _end_turn(engine, instance, "player:gm")
    _grant_advantage(instance, "enemy:goblin-1")
    events = _apply(engine, instance, {
        "intent_id": "move-goblin",
        "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1", "distance": 10,
    })
    pending = next(
        event for event in events if event["type"] == "dnd2024.decision.pending"
    )
    assert pending["kind"] == "opportunity_attack"
    assert pending["threat_actor_ids"] == ["player:gm"]

    events = _apply(engine, instance, {
        "intent_id": "opportunity-1",
        "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": pending["decision_id"],
        "option": "resolve",
    }, rolls=[15, 3, 4, 5])

    damage = _damage_to(events, "enemy:goblin-1")
    assert damage["amount"] == 4 + 3 + 5
    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["reactions"]["player:gm"] == 0
    assert combat["feature_usage"]["player:gm"] == {
        "round": 1, "turn_index": 1, "feature_ids": ["sneak_attack"],
    }


def test_shield_that_turns_the_hit_into_a_miss_records_no_sneak_attack() -> None:
    runtime, engine, instance = _instance(
        companion_level=1, companion_spellcasting=_shield_spellcasting(),
    )
    _grant_advantage(instance, "companion:mira")

    kept, damage_event = _held_attack(
        engine, instance, target_id="companion:mira", rolls=[9, 3, 4, 5],
    )
    check = _check(kept)
    assert check["total"] == 9 + 5 and check["target"] == 14
    assert damage_event["amount"] == 4 + 3 + 5
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
        "intent_id": "shield-1",
        "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "cast", "spell_ref": "spell:shield", "slot_level": 1,
    })

    assert not any(
        event["type"] == "resource.changed" and event.get("target_id") == "companion:mira"
        for event in events
    )
    assert not any(event["type"] == "dnd2024.feature.used" for event in events)
    assert _actor_hp(instance, "companion:mira") == hp_before
    assert _feature_usage(instance) == {}

    # The held-then-missed hit used no slot of the ledger: a later legal hit
    # still qualifies for Sneak Attack.
    ruleset_runtime.state(instance)["combat"]["economy"]["attacks_remaining"] = 1
    _grant_advantage(instance, "enemy:goblin-1")
    events = _attack(engine, instance, rolls=[15, 3, 4, 5], intent_id="attack-after-shield")

    assert _damage_to(events, "enemy:goblin-1")["amount"] == 4 + 3 + 5
    assert _feature_usage(instance)["player:gm"]["feature_ids"] == ["sneak_attack"]


def test_uncanny_dodge_halves_the_whole_hit_and_records_sneak_attack_once() -> None:
    runtime, engine, instance = _instance(companion_level=5)
    _grant_advantage(instance, "companion:mira")

    kept, damage_event = _held_attack(
        engine, instance, target_id="companion:mira", rolls=[9, 3, 4, 5],
    )
    check = _check(kept)
    assert check["total"] == 9 + 5 and check["target"] == 14
    assert damage_event["amount"] == 4 + 3 + 5
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
        "intent_id": "uncanny-1",
        "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "feature", "feature_id": "uncanny_dodge",
    })

    damage = _damage_to(events, "companion:mira")
    assert damage["amount"] == 6
    assert damage["delta"] == -6
    assert damage["rolls"] == [4, 5]
    assert [event["type"] for event in events].count("dnd2024.feature.used") == 1
    assert _actor_hp(instance, "companion:mira") == hp_before - 6
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["companion:mira"] == 0
    assert _feature_usage(instance)["player:gm"]["feature_ids"] == ["sneak_attack"]


def _smite_spellcasting() -> dict:
    return {
        "ability": "int", "slots_current": {"1": 1},
        "prepared_spell_refs": ["spell:divine_smite"], "cantrip_refs": [],
        "concentration": None,
    }


@pytest.mark.parametrize("mode", ["decline", "cast"])
def test_divine_smite_decline_and_cast_both_keep_sneak_attack_once(mode: str) -> None:
    runtime, engine, instance = _instance(spellcasting=_smite_spellcasting())
    _grant_advantage(instance, "enemy:goblin-1")

    events = _attack(engine, instance, rolls=[15, 3, 4, 5], intent_id=f"attack-{mode}")
    pending = next(
        event for event in events if event["type"] == "dnd2024.decision.pending"
    )
    assert pending["kind"] == HIT_SPELL_DECISION
    assert pending["damage_event"]["amount"] == 4 + 3 + 5
    hp_before = _actor_hp(instance, "enemy:goblin-1")

    if mode == "decline":
        events = _apply(engine, instance, {
            "intent_id": "smite-decline",
            "type": "decision.resolve",
            "expected_version": ruleset_runtime.state(instance)["version"],
            "submitted_by": "gm", "decision_id": pending["decision_id"],
            "option": "decline",
        })
        assert _actor_hp(instance, "enemy:goblin-1") == hp_before - (4 + 3 + 5)
    else:
        events = _apply(engine, instance, {
            "intent_id": "smite-cast",
            "type": "cast_spell",
            "expected_version": ruleset_runtime.state(instance)["version"],
            "submitted_by": "gm", "actor_id": "player:gm",
            "spell_ref": "spell:divine_smite", "target_id": "enemy:goblin-1",
            "slot_level": 1,
        }, rolls=[3, 4])
        radiant = next(
            event for event in events if event.get("damage_type") == "radiant"
        )
        assert radiant["amount"] == 3 + 4
        assert _actor_hp(instance, "enemy:goblin-1") == hp_before - (4 + 3 + 5) - 7

    assert [event["type"] for event in events].count("dnd2024.feature.used") == 1
    assert _feature_usage(instance)["player:gm"]["feature_ids"] == ["sneak_attack"]
