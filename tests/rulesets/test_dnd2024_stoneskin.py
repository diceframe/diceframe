"""Stoneskin (P5): data-driven damage resistance and choice-driven resistance."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.catalog import (
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.combat.reaction_spells import HIT_REACTION_DECISION
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    advance,
    goblin,
    monk_draft,
    monk_instance,
)


CANTRIPS = ["spell:light", "spell:mage_hand", "spell:ray_of_frost", "spell:fire_bolt"]
SPELLBOOK = [
    # Ordered by spell level so every level's minimum spellbook prefix stays
    # inside that level's maximum spell level.
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:shield",
    "spell:thunderwave",
    "spell:charm_person",
    "spell:sleep",
    "spell:burning_hands",
    "spell:invisibility",
    "spell:blur",
    "spell:misty_step",
    "spell:mirror_image",
    "spell:fireball",
    "spell:counterspell",
    "spell:fly",
    "spell:haste",
    "spell:stoneskin",
    "spell:fire_shield",
]
PREPARED = [
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:shield",
    "spell:thunderwave",
    "spell:charm_person",
    "spell:sleep",
    "spell:burning_hands",
    "spell:invisibility",
    "spell:blur",
    "spell:fireball",
]


def wizard_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        "class:wizard", level
    )
    prepared = PREPARED[: requirements["prepared_spell_count"]]
    spellbook = SPELLBOOK[: requirements["spellbook_minimum"]]
    assert set(prepared).issubset(spellbook)
    return {
        "cantrip_refs": CANTRIPS[: requirements["cantrip_count"]],
        "prepared_spell_refs": prepared,
        "spellbook_refs": spellbook,
    }


def wizard_sheet(runtime: Dnd2024Runtime, *, level: int = 7) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref="class:wizard",
        class_skill_refs=["skill:arcana", "skill:history"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:wizard_a",
        base_abilities={
            "str": 8, "dex": 12, "con": 13, "int": 15, "wis": 14, "cha": 10,
        },
        class_spell_choices=wizard_choices(runtime, 1),
    )
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        choices["class_spell_choices"] = wizard_choices(runtime, next_level)
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def preset_sheet(runtime: Dnd2024Runtime, preset_id: str, *, level: int) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": preset_id},
    )
    return advance(runtime, sheet, level) if level > 1 else sheet


def party_instance(gm_sheet: dict, ally_sheet: dict | None = None):
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, gm_sheet)
    start(engine, instance, allies=[("ally", ally_sheet)] if ally_sheet else [])
    return runtime, engine, instance


def wizard_instance(*, level: int = 7):
    runtime = Dnd2024Runtime()
    return party_instance(wizard_sheet(runtime, level=level))


def wizard_and_ally(*, wizard_level: int = 7):
    runtime = Dnd2024Runtime()
    return party_instance(
        wizard_sheet(runtime, level=wizard_level),
        wizard_sheet(runtime, level=wizard_level),
    )


def start(engine, instance, *, allies) -> None:
    for uid, sheet in allies:
        instance.players[uid] = {"character_name": uid.title(), "character_sheet": sheet}
    # sorted(players) is ["ally", "gm"]: low roll for the ally, high roll for the GM.
    rolls = [1, 20, 1] if allies else [20, 1]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin(position=5, hp=100)],
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def grant(instance, uid: str, *refs: str) -> None:
    magic = canonical(instance, uid)["spellcasting"]["class"]
    for ref in refs:
        if ref not in magic["prepared_spell_refs"]:
            magic["prepared_spell_refs"].append(ref)


def grant_slot(instance, uid: str, level: int, count: int = 1) -> None:
    magic = canonical(instance, uid)["spellcasting"]["class"]
    magic["slots_current"][str(level)] = count
    magic["slots_max"][str(level)] = count


def add_player_condition(instance, uid: str, condition: str, meta: dict | None = None) -> None:
    canonical(instance, uid).setdefault("conditions", {})[condition] = meta or {"source": "test"}


def add_enemy_condition(instance, condition: str, meta: dict | None = None) -> None:
    enemies = ruleset_runtime.state(instance)["combat"]["enemies"]
    enemies["goblin-1"].setdefault("conditions", {})[condition] = meta or {"source": "test"}


def set_enemy_attack(instance, *, damage: str | None = None, damage_type: str | None = None) -> None:
    profile = ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["attacks"][0]
    if damage is not None:
        profile["damage"] = damage
    if damage_type is not None:
        profile["damage_type"] = damage_type


def set_turn(engine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def resolve(engine, instance, request: dict, rolls=()):
    request.setdefault("expected_version", ruleset_runtime.state(instance)["version"])
    result = engine.resolve_intent(instance, request, SequenceRng(list(rolls)))
    assert result["ok"], result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"], applied
    return result


def submitter(actor_id: str) -> str:
    return actor_id.removeprefix("player:") if actor_id.startswith("player:") else "gm"


def cast(
    engine, instance, uid: str, spell_ref: str, slot: int, targets: list[str],
    rolls=(), **extra,
):
    set_turn(engine, instance, f"player:{uid}")
    version = ruleset_runtime.state(instance)["version"]
    request = {
        "intent_id": f"cast:{spell_ref}:{slot}:{len(targets)}:{version}",
        "type": "cast_spell", "submitted_by": submitter(f"player:{uid}"),
        "actor_id": f"player:{uid}",
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": targets,
        **extra,
    }
    return resolve(engine, instance, request, rolls)


def attack(engine, instance, *, actor_id: str, target_id: str, weapon=None, attack_id=None, rolls=()):
    set_turn(engine, instance, actor_id)
    version = ruleset_runtime.state(instance)["version"]
    request = {
        "intent_id": f"attack:{actor_id}:{target_id}:{weapon or attack_id}:{version}",
        "type": "attack", "submitted_by": submitter(actor_id),
        "actor_id": actor_id, "target_id": target_id,
    }
    if weapon:
        request["weapon_ref"] = weapon
    if attack_id:
        request["attack_id"] = attack_id
    return resolve(engine, instance, request, rolls)


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def hit(engine, instance, *, settle_rolls=(), **kwargs):
    """Make one enemy attack and decline any hit-reaction offer.

    The wizard sheets prepare Shield, so every hit against them opens the
    generic reaction decision. Declining it settles the already-resisted
    damage event, which is what the resistance assertions need.
    """

    result = attack(engine, instance, **kwargs)
    pending = next(
        (
            event for event in events_of(result)
            if event["type"] == "dnd2024.decision.pending"
            and event["kind"] == HIT_REACTION_DECISION
        ),
        None,
    )
    if pending is None:
        return result
    return resolve(engine, instance, {
        "intent_id": f"decline:{pending['decision_id']}",
        "type": "decision.resolve",
        "decision_id": pending["decision_id"],
        "submitted_by": pending["assigned_to"], "option": "decline",
    }, settle_rolls)


def damage_to(result: dict, target_id: str) -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "resource.changed"
        and event.get("target_id") == target_id
        and not event.get("healing")
    )


def check_of(result: dict, kind: str) -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def catalog_bundle(base, **override):
    class _Bundle:
        def get(self, kind, ident=None):
            if kind == "combat_catalog":
                raw = deepcopy(base.get(kind, ident))
                raw.update(deepcopy(override))
                return raw
            return base.get(kind, ident)

        def list(self, kind):
            return base.list(kind)

    return _Bundle()


# --- #1 catalog validation -------------------------------------------------

def test_catalog_rejects_invalid_resistance_declarations() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")

    bad_condition_types = {
        # Unknown damage type, empty list, duplicate entry.
        "stoneskinned": {"resist_damage_types": ["holy"]},
        "poison_resistant": {"resist_damage_types": []},
        "rage": {"resist_damage_types": ["slashing", "slashing"]},
    }
    bad_resist_choice = {
        "protection_from_energy": {
            "mode": "buff", "range": 5, "condition": "energy_resistant",
            "resist_choice": ["cold", "cold"],
        }
    }
    bad_removes = {
        "protection_from_poison": {
            "mode": "buff", "range": 5, "condition": "poison_resistant",
            "removes_conditions": ["Poisoned"],
        }
    }

    for entry in bad_condition_types.values():
        with pytest.raises(CombatCatalogError):
            Dnd2024CombatCatalog.from_bundle(
                catalog_bundle(bundle, conditions={"stoneskinned": entry})
            )
    with pytest.raises(CombatCatalogError):
        Dnd2024CombatCatalog.from_bundle(
            catalog_bundle(bundle, spell_effects=bad_resist_choice)
        )
    with pytest.raises(CombatCatalogError):
        Dnd2024CombatCatalog.from_bundle(
            catalog_bundle(bundle, spell_effects=bad_removes)
        )


# --- #2 stoneskin halves slashing damage -------------------------------

def test_stoneskin_halves_slashing_damage_once_with_floor_rounding() -> None:
    _runtime, engine, instance = wizard_and_ally()
    grant(instance, "gm", "spell:stoneskin")

    cast(engine, instance, "gm", "spell:stoneskin", 4, ["player:ally"])

    result = hit(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:ally",
        attack_id="scimitar", rolls=[15, 5],
    )

    damage = damage_to(result, "player:ally")
    assert damage["damage_type"] == "slashing"
    assert damage["amount"] == 3  # (5 + 2) = 7, halved rounds down
    assert damage["resisted"] is True
    assert damage["resistance"] == "stoneskinned"
    assert damage["rolls"] == [5]
    applied = canonical(instance, "ally")["conditions"]["stoneskinned"]
    assert applied["duration"] == "concentration"


# --- #3 stoneskin leaves fire alone ------------------------------------

def test_stoneskin_does_not_reduce_fire_damage() -> None:
    _runtime, engine, instance = wizard_and_ally()
    grant(instance, "gm", "spell:stoneskin")
    cast(engine, instance, "gm", "spell:stoneskin", 4, ["player:ally"])
    set_enemy_attack(instance, damage_type="fire")

    result = hit(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:ally",
        attack_id="scimitar", rolls=[15, 5],
    )

    damage = damage_to(result, "player:ally")
    assert damage["amount"] == 7
    assert "resisted" not in damage
    assert "resistance" not in damage


# --- #4 concentration DC uses the resisted amount ----------------------

def test_concentration_dc_uses_the_halved_damage() -> None:
    _runtime, engine, instance = wizard_and_ally()
    grant(instance, "gm", "spell:stoneskin")
    cast(engine, instance, "gm", "spell:stoneskin", 4, ["player:ally"])
    canonical(instance, "ally")["spellcasting"]["class"]["concentration"] = {
        "spell_ref": "spell:invisibility", "target_ids": ["player:ally"],
    }
    set_enemy_attack(instance, damage="4d12+4")

    result = hit(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:ally",
        attack_id="scimitar", rolls=[15, 10, 10, 10, 10, 10],
        settle_rolls=[10],
    )

    damage = damage_to(result, "player:ally")
    assert damage["amount"] == 22  # 44 halved once
    check = check_of(result, "concentration")
    assert check["target"] == 11  # max(10, halved 22 // 2); the raw 44 would give 22
    assert check["success"] is True


# --- #5 the Rage debt is now paid by the registry ----------------------

def test_rage_resistance_comes_from_the_registry() -> None:
    runtime = Dnd2024Runtime()
    _runtime, engine, instance = party_instance(
        preset_sheet(runtime, "relentless_vanguard", level=5),
    )
    add_player_condition(instance, "gm", "rage", {
        "duration": "rage", "turns_remaining": 10, "source_actor_id": "player:gm",
    })

    result = attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:gm",
        attack_id="scimitar", rolls=[15, 5],
    )
    damage = damage_to(result, "player:gm")
    assert damage["amount"] == 3
    assert damage["resistance"] == "rage"

    fresh_runtime = Dnd2024Runtime()
    _runtime, engine, instance = party_instance(
        preset_sheet(fresh_runtime, "relentless_vanguard", level=5),
    )
    add_player_condition(instance, "gm", "rage", {
        "duration": "rage", "turns_remaining": 10, "source_actor_id": "player:gm",
    })
    set_enemy_attack(instance, damage_type="fire")

    result = attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:gm",
        attack_id="scimitar", rolls=[15, 5],
    )
    damage = damage_to(result, "player:gm")
    assert damage["amount"] == 7
    assert "resisted" not in damage


# --- #6 multiple resistance sources never stack ------------------------

def test_rage_and_stoneskin_still_halve_only_once() -> None:
    runtime = Dnd2024Runtime()
    barbarian = preset_sheet(runtime, "relentless_vanguard", level=5)
    wizard = wizard_sheet(runtime, level=7)
    _runtime, engine, instance = party_instance(barbarian, wizard)
    grant(instance, "ally", "spell:stoneskin")
    add_player_condition(instance, "gm", "rage", {
        "duration": "rage", "turns_remaining": 10, "source_actor_id": "player:gm",
    })

    cast(engine, instance, "ally", "spell:stoneskin", 4, ["player:gm"])

    result = attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:gm",
        attack_id="scimitar", rolls=[15, 5],
    )

    damage = damage_to(result, "player:gm")
    assert damage["amount"] == 3  # 7 halved once, never 1
    assert damage["resisted"] is True


# --- #7 protection from energy requires a choice -----------------------

def test_protection_from_energy_requires_and_applies_its_choice() -> None:
    _runtime, engine, instance = wizard_and_ally()
    grant(instance, "gm", "spell:protection_from_energy", "spell:stoneskin")

    missing = engine.validate_intent(instance, {
        "intent_id": "energy-missing", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:protection_from_energy", "slot_level": 3,
        "target_ids": ["player:ally"],
    })
    assert missing["ok"] is False
    assert "resist_type" in missing["error"]

    illegal = engine.validate_intent(instance, {
        "intent_id": "stoneskin-illegal", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:stoneskin", "slot_level": 4,
        "target_ids": ["player:ally"], "resist_type": "cold",
    })
    assert illegal["ok"] is False

    cast(
        engine, instance, "gm", "spell:protection_from_energy", 3,
        ["player:ally"], resist_type="cold",
    )

    applied = canonical(instance, "ally")["conditions"]["energy_resistant"]
    assert applied["resist_damage_types"] == ["cold"]

    set_enemy_attack(instance, damage_type="cold")
    cold = hit(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:ally",
        attack_id="scimitar", rolls=[15, 5],
    )
    assert damage_to(cold, "player:ally")["amount"] == 3

    set_enemy_attack(instance, damage_type="fire")
    fire = hit(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:ally",
        attack_id="scimitar", rolls=[15, 5],
    )
    assert damage_to(fire, "player:ally")["amount"] == 7


# --- #8 protection from poison removes the poisoned condition ----------

def test_protection_from_poison_removes_poisoned_before_applying() -> None:
    _runtime, engine, instance = wizard_and_ally()
    grant(instance, "gm", "spell:protection_from_poison")
    add_player_condition(instance, "ally", "poisoned")

    result = cast(
        engine, instance, "gm", "spell:protection_from_poison", 2, ["player:ally"],
    )

    types = [event["type"] for event in events_of(result)]
    removed = next(
        event for event in events_of(result)
        if event["type"] == "condition.removed"
    )
    assert removed["condition"] == "poisoned"
    assert types.index("condition.removed") < types.index("condition.applied")
    assert "poisoned" not in canonical(instance, "ally")["conditions"]
    assert "poison_resistant" in canonical(instance, "ally")["conditions"]

    set_enemy_attack(instance, damage_type="poison")
    attack_result = hit(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:ally",
        attack_id="scimitar", rolls=[15, 5],
    )
    assert damage_to(attack_result, "player:ally")["amount"] == 3


# --- #9 automatic damage is resisted -----------------------------------

def test_magic_missile_is_halved_by_a_matching_resistance() -> None:
    _runtime, engine, instance = wizard_instance()
    add_enemy_condition(instance, "force_resistant", {"resist_damage_types": ["force"]})

    result = cast(
        engine, instance, "gm", "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[4, 4, 4],
    )

    damage = damage_to(result, "enemy:goblin-1")
    assert damage["amount"] == 7  # (4 + 4 + 4 + 3) = 15, halved
    assert damage["resisted"] is True


# --- #10 save half then resistance -> a quarter ------------------------

def test_saving_throw_half_then_resistance_deals_a_quarter() -> None:
    _runtime, engine, instance = wizard_instance()
    grant(instance, "gm", "spell:inflict_wounds")
    grant_slot(instance, "gm", 1)
    add_enemy_condition(instance, "necrotic_resistant", {
        "resist_damage_types": ["necrotic"],
    })

    result = cast(
        engine, instance, "gm", "spell:inflict_wounds", 1, ["enemy:goblin-1"],
        rolls=[20, 6, 5],
    )

    damage = damage_to(result, "enemy:goblin-1")
    assert damage["amount"] == 2  # 11 -> half 5 -> resisted 2
    assert damage["resisted"] is True


# --- #11 resistance then Uncanny Dodge -> a quarter --------------------

def test_stoneskin_and_uncanny_dodge_stack_two_halvings() -> None:
    runtime = Dnd2024Runtime()
    rogue = preset_sheet(runtime, "lucky_scout", level=5)
    wizard = wizard_sheet(runtime, level=7)
    _runtime, engine, instance = party_instance(rogue, wizard)
    grant(instance, "ally", "spell:stoneskin")

    cast(engine, instance, "ally", "spell:stoneskin", 4, ["player:gm"])
    hit = attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:gm",
        attack_id="scimitar", rolls=[15, 5],
    )
    pending = next(
        event for event in events_of(hit)
        if event["type"] == "dnd2024.decision.pending"
        and event["kind"] == HIT_REACTION_DECISION
    )
    assert pending["damage_event"]["amount"] == 3

    decided = resolve(engine, instance, {
        "intent_id": "dodge-1", "type": "decision.resolve",
        "decision_id": pending["decision_id"],
        "submitted_by": "gm", "option": "feature",
        "feature_id": "uncanny_dodge",
    })
    damage = damage_to(decided, "player:gm")
    assert damage["amount"] == 1  # 7 -> resisted 3 -> dodged 1
    assert damage["delta"] == -1


# --- #12 healing bypasses resistance -----------------------------------

def test_healing_is_not_reduced_by_resistance() -> None:
    _runtime, engine, instance = wizard_and_ally()
    grant(instance, "gm", "spell:protection_from_poison")
    cast(engine, instance, "gm", "spell:protection_from_poison", 2, ["player:ally"])

    grant(instance, "gm", "spell:cure_wounds")
    result = cast(
        engine, instance, "gm", "spell:cure_wounds", 1, ["player:ally"],
        rolls=[4, 5],
    )

    healing = next(
        event for event in events_of(result)
        if event["type"] == "resource.changed" and event.get("healing")
    )
    assert healing["amount"] == 12  # 2d8 (4+5) + Intelligence +3
    assert "resisted" not in healing


# --- #13 fire shield: resistance only, no retaliation rider ------------

def test_fire_shield_resists_its_chosen_type_without_a_rider() -> None:
    _runtime, engine, instance = wizard_instance()
    grant(instance, "gm", "spell:fire_shield")
    cast(
        engine, instance, "gm", "spell:fire_shield", 4, ["player:gm"],
        resist_type="fire",
    )
    applied = canonical(instance, "gm")["conditions"]["fire_shield_resistant"]
    assert applied["resist_damage_types"] == ["fire"]

    set_enemy_attack(instance, damage_type="fire")
    fire = hit(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:gm",
        attack_id="scimitar", rolls=[15, 5],
    )
    assert damage_to(fire, "player:gm")["amount"] == 3
    # The 2d8 retaliation rider is out of scope: the attacker takes no damage.
    assert not any(
        event["type"] == "resource.changed" and event.get("target_id") == "enemy:goblin-1"
        for event in events_of(fire)
    )

    set_enemy_attack(instance, damage_type="cold")
    cold = hit(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:gm",
        attack_id="scimitar", rolls=[15, 5],
    )
    assert damage_to(cold, "player:gm")["amount"] == 7


@pytest.mark.parametrize("spell_ref,slot,extra", [
    ("spell:blur", 2, {}),
    ("spell:see_invisibility", 2, {}),
    ("spell:fire_shield", 4, {"resist_type": "fire"}),
])
def test_self_range_buffs_only_target_the_caster(spell_ref, slot, extra) -> None:
    _runtime, engine, instance = wizard_and_ally()
    grant(instance, "gm", spell_ref)
    grant_slot(instance, "gm", slot)
    set_turn(engine, instance, "player:gm")
    version = ruleset_runtime.state(instance)["version"]

    rejected = engine.resolve_intent(instance, {
        "intent_id": "cast-ally", "type": "cast_spell", "expected_version": version,
        "submitted_by": "gm", "actor_id": "player:gm", "spell_ref": spell_ref,
        "slot_level": slot, "target_ids": ["player:ally"], **extra,
    }, SequenceRng([]))
    assert rejected["ok"] is False

    cast(engine, instance, "gm", spell_ref, slot, ["player:gm"], **extra)
    assert canonical(instance, "gm")["conditions"]
    assert not canonical(instance, "ally").get("conditions")


def test_a_long_rest_ends_spell_effects_and_temporary_hit_points() -> None:
    from src.rulesets.dnd2024.resting import Dnd2024RestEngine

    runtime, engine, instance = wizard_instance()
    grant(instance, "gm", "spell:stoneskin", "spell:false_life")
    grant_slot(instance, "gm", 4)
    grant_slot(instance, "gm", 1)
    cast(engine, instance, "gm", "spell:stoneskin", 4, ["player:gm"])
    cast(engine, instance, "gm", "spell:false_life", 1, ["player:gm"], rolls=[2, 2])
    resolve(engine, instance, {"intent_id": "end", "type": "combat.end", "submitted_by": "gm"})
    character = canonical(instance, "gm")
    assert "stoneskinned" in character["conditions"]
    assert int(character["resources"]["temp_hp"]) > 0

    rested = Dnd2024RestEngine(runtime.load_bundle("en")).complete_long_rest(character)

    assert "stoneskinned" not in rested["character"]["conditions"]
    assert int(rested["character"]["resources"].get("temp_hp", 0) or 0) == 0
    types = [event["type"] for event in rested["events"]]
    assert {"end_concentration", "lose_temp_hp", "end_spell_effects"} <= set(types)


def test_combat_end_clears_turn_scoped_conditions_but_keeps_timed_spells() -> None:
    _runtime, engine, instance = wizard_instance()
    grant(instance, "gm", "spell:stoneskin")
    grant_slot(instance, "gm", 4)
    cast(engine, instance, "gm", "spell:stoneskin", 4, ["player:gm"])
    add_player_condition(instance, "gm", "dodging", {
        "duration": "actor_turn_start", "source_actor_id": "player:gm",
    })

    resolve(engine, instance, {"intent_id": "end", "type": "combat.end", "submitted_by": "gm"})

    conditions = canonical(instance, "gm")["conditions"]
    assert "dodging" not in conditions
    assert "stoneskinned" in conditions  # a 1-hour spell outlives the encounter
