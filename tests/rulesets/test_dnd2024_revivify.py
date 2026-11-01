"""Revivify (P9): the ``revive`` mode turns a dead player ally back to 1 HP.

The mode itself is data-driven (``srd_combat_core.json``); resolution only
emits ``dnd2024.character.revived`` and lets the existing reducer handler
perform the state cleanup, so these tests lock the event, the cost side
(action + third-level slot), and the target-state boundaries.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.catalog import (
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import SequenceRng, goblin, monk_instance


CANTRIPS = ["spell:guidance", "spell:light", "spell:sacred_flame", "spell:thaumaturgy"]
# Ordered by spell level so every level's prepared prefix fits that level's
# maximum spell level. revivify and dispel_magic only join at level 5.
PREPARED = [
    "spell:bless",
    "spell:cure_wounds",
    "spell:guiding_bolt",
    "spell:shield_of_faith",
    "spell:healing_word",
    "spell:aid",
    "spell:hold_person",
    "spell:revivify",
    "spell:dispel_magic",
]
DEATH_STATE = {"dead", "unconscious", "stable", "death_saves"}
REVIVE_EVENTS = [
    "dnd2024.action.spent",
    "dnd2024.spell.slot_spent",
    "dnd2024.spell.cast",
    "dnd2024.character.revived",
]


def _preset_draft(runtime: Dnd2024Runtime, preset_id: str, name: str) -> dict:
    presets = runtime.builder_choices(None, {"locale": "en"})["quick_presets"]
    preset = next(item for item in presets if item["id"] == preset_id)
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": name},
    )


def cleric_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        "class:cleric", level
    )
    return {
        "cantrip_refs": CANTRIPS[: requirements["cantrip_count"]],
        "prepared_spell_refs": PREPARED[: requirements["prepared_spell_count"]],
    }


def _advance_choices(preview: dict) -> dict:
    choices: dict = {}
    for requirement in preview.get("requirements") or []:
        requirement_id = str(requirement["id"])
        if requirement_id == "subclass_ref":
            choices["subclass_ref"] = str(requirement["options"][0]["value"])
        elif requirement_id in {"feat_ref", "epic_boon_ref"}:
            available = [
                option for option in requirement["options"] if option.get("available")
            ]
            choices[requirement_id] = str(available[0]["value"])
        elif requirement_id == "ability_score_increases":
            allowed = requirement.get("allowed", "any")
            ability = "str" if allowed == "any" else str(allowed[0])
            choices["ability_score_increases"] = {ability: int(requirement.get("total", 2))}
    return choices


def cleric_sheet(runtime: Dnd2024Runtime, *, level: int = 5) -> dict:
    sheet = _preset_draft(runtime, "kindly_bulwark", "Caster")
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        choices = _advance_choices(runtime.preview_advancement(None, sheet, {}))
        choices["class_spell_choices"] = cleric_choices(runtime, next_level)
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def ally_sheet(runtime: Dnd2024Runtime, *, source: str = "death_saves") -> dict:
    sheet = _preset_draft(runtime, "stalwart_guardian", "Fallen Ally")
    canonical = sheet["ruleset_character"]
    canonical["resources"]["hp"] = 0
    canonical["conditions"] = {
        "dead": {"source": source},
        "death_saves": {"successes": 0, "failures": 3},
    }
    return sheet


def dead_companion() -> dict:
    return {
        "id": "mira", "name": "Mira", "controller": "ai", "active": True,
        "ruleset_character": {
            "resources": {"hp": 0, "max_hp": 20},
            "conditions": {
                "dead": {"source": "death_saves"},
                "death_saves": {"successes": 0, "failures": 3},
            },
            "abilities": {
                "str": 14, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 10,
            },
            "derived": {
                "armor_class": 16, "speed": 30, "initiative": 1,
                "proficiency_bonus": 2, "saving_throws": {"str": 4, "con": 2},
            },
            "proficiencies": {
                "skill_values": {"athletics": 6},
                "weapon_category_refs": ["weapon_category:martial"],
            },
            "equipment": {"item_refs": ["item:greatsword"]},
            "spellcasting": {"class": {
                "ability": "wis", "slots_current": {}, "concentration": None,
                "prepared_spell_refs": [], "cantrip_refs": [],
            }},
            "build": {"class_levels": [{"class_ref": "class:fighter", "level": 5}]},
        },
    }


class NoRollRng:
    """Fails the test if resolution asks for any dice at all."""

    def randint(self, minimum: int, maximum: int) -> int:
        raise AssertionError(f"no dice expected, got randint({minimum}, {maximum})")


def new_combat(
    *, ally_source: str = "death_saves", ally_position: int = 0,
    with_companion: bool = False, slot3: int | None = None,
) -> tuple[Dnd2024CombatEngine, object]:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, cleric_sheet(runtime, level=5), uid="gm")
    instance.players["ally"] = {
        "character_name": "Fallen Ally",
        "character_sheet": ally_sheet(runtime, source=ally_source),
    }
    if with_companion:
        party = ruleset_runtime.state(instance).setdefault("party", {"companions": {}})
        party.setdefault("companions", {})["mira"] = dead_companion()
    if slot3 is not None:
        instance.get_character_sheet("gm")["ruleset_character"]["spellcasting"][
            "class"
        ]["slots_current"]["3"] = slot3
    players = sorted(instance.players)
    player_rolls = [1 for _ in players]
    player_rolls[players.index("gm")] = 20
    companion_rolls = [1] if with_companion else []
    rolls = [*player_rolls, *companion_rolls, 1]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin(position=30)],
        "player_positions": {"ally": ally_position},
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"
    return engine, instance


def request(instance, intent: dict) -> dict:
    intent.setdefault(
        "expected_version", int(ruleset_runtime.state(instance).get("version", 0) or 0),
    )
    return intent


def resolve(engine, instance, intent: dict, rng=None):
    result = engine.resolve_intent(
        instance, request(instance, intent), rng or SequenceRng([]),
    )
    assert result["ok"], result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"], applied
    return result


def validate(engine, instance, **payload) -> dict:
    payload.setdefault("intent_id", f"validate:{payload['type']}")
    payload.setdefault("submitted_by", "gm")
    return engine.validate_intent(instance, request(instance, payload))


def cast_intent(
    *, targets, slot: int = 3, spell_ref: str = "spell:revivify", uid: str = "gm",
) -> dict:
    return {
        "intent_id": f"cast:{spell_ref}:{slot}:{','.join(targets)}",
        "type": "cast_spell", "submitted_by": uid, "actor_id": f"player:{uid}",
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": list(targets),
    }


def set_turn(engine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, actor_id)
    )


def event_types(result: dict) -> list[str]:
    return [
        event["type"] for event in result["event_batch"]["events"]
        if event["type"] != "intent.submitted"
    ]


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


def assert_revived(engine, instance, result: dict) -> None:
    assert event_types(result) == REVIVE_EVENTS
    revived = next(
        event for event in result["event_batch"]["events"]
        if event["type"] == "dnd2024.character.revived"
    )
    assert revived["actor_id"] == "player:ally"
    assert revived["hp"] == 1
    ally = instance.get_character_sheet("ally")["ruleset_character"]
    assert ally["resources"]["hp"] == 1
    assert set(ally["conditions"]) & DEATH_STATE == set()
    caster = instance.get_character_sheet("gm")["ruleset_character"]
    assert caster["spellcasting"]["class"]["slots_current"]["3"] == 1
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 0


# --- #1 catalog validation of the new mode -------------------------------


def test_catalog_accepts_revive_and_rejects_unknown_modes() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    effects = bundle.get("combat_catalog", "srd_combat_core")["spell_effects"]
    revivify = deepcopy(effects["revivify"])

    catalog = Dnd2024CombatCatalog.from_bundle(bundle)
    assert catalog.spell_effects["revivify"] == {"mode": "revive", "range": 5}

    for bad in (
        {**revivify, "mode": "raise_dead"},
        {**revivify, "mode": "revive", "range": 4},
    ):
        with pytest.raises(CombatCatalogError):
            Dnd2024CombatCatalog.from_bundle(
                catalog_bundle(bundle, spell_effects={**effects, "revivify": bad}),
            )


# --- #2 full closed loop --------------------------------------------------


def test_revivify_restores_one_hp_and_spends_slot_and_action() -> None:
    engine, instance = new_combat()
    result = resolve(
        engine, instance, cast_intent(targets=["player:ally"]), NoRollRng(),
    )
    assert_revived(engine, instance, result)


# --- #3 dying (0 HP, still rolling death saves) is not dead ---------------


def test_dying_ally_without_dead_condition_is_rejected() -> None:
    engine, instance = new_combat()
    canonical = instance.get_character_sheet("ally")["ruleset_character"]
    canonical["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 2},
    }
    result = validate(engine, instance, **cast_intent(targets=["player:ally"]))
    assert result["ok"] is False
    assert "revive spells require a dead target" in result["error"]


# --- #4 the dead rejection must not leak into healing ---------------------


def test_healing_still_rejects_a_dead_ally() -> None:
    engine, instance = new_combat()
    result = validate(
        engine, instance,
        **cast_intent(targets=["player:ally"], slot=1, spell_ref="spell:cure_wounds"),
    )
    assert result["ok"] is False
    assert "dead targets require a resurrection effect" in result["error"]


# --- #5 hostile target is rejected ---------------------------------------


def test_revivify_rejects_a_hostile_target() -> None:
    engine, instance = new_combat()
    result = validate(engine, instance, **cast_intent(targets=["enemy:goblin-1"]))
    assert result["ok"] is False
    assert "allied target" in result["error"]


# --- #6 dead companion cannot reach the reducer --------------------------


def test_dead_companion_is_rejected_before_apply() -> None:
    engine, instance = new_combat(with_companion=True)
    result = validate(engine, instance, **cast_intent(targets=["companion:mira"]))
    assert result["ok"] is False
    assert "revive spells target players only" in result["error"]


# --- #7 touch range -------------------------------------------------------


def test_out_of_range_dead_ally_is_rejected() -> None:
    engine, instance = new_combat(ally_position=10)
    result = validate(engine, instance, **cast_intent(targets=["player:ally"]))
    assert result["ok"] is False
    assert "out of range" in result["error"]


# --- #8 available intents recover after revival ---------------------------


def test_dead_ally_intents_restore_after_revive() -> None:
    engine, instance = new_combat()
    set_turn(engine, instance, "player:ally")
    dead_intents = [item["type"] for item in engine.available_intents(instance, "ally")]
    assert "death_save" in dead_intents
    assert "attack" not in dead_intents

    set_turn(engine, instance, "player:gm")
    resolve(engine, instance, cast_intent(targets=["player:ally"]), NoRollRng())

    set_turn(engine, instance, "player:ally")
    alive_intents = [item["type"] for item in engine.available_intents(instance, "ally")]
    assert "death_save" not in alive_intents
    assert "attack" in alive_intents


# --- #9 slot gates --------------------------------------------------------


def test_revivify_rejects_low_slot_and_exhausted_level_three() -> None:
    engine, instance = new_combat()
    low = validate(engine, instance, **cast_intent(targets=["player:ally"], slot=2))
    assert low["ok"] is False
    assert "lower than the spell level" in low["error"]

    engine, instance = new_combat(slot3=0)
    exhausted = validate(engine, instance, **cast_intent(targets=["player:ally"]))
    assert exhausted["ok"] is False
    assert "selected spell slot is not available" in exhausted["error"]


# --- #10 the dead source is irrelevant -----------------------------------


def test_revive_ignores_the_death_source() -> None:
    engine, instance = new_combat(ally_source="damage_at_zero_hp")
    result = resolve(
        engine, instance, cast_intent(targets=["player:ally"]), NoRollRng(),
    )
    assert_revived(engine, instance, result)
    ally = instance.get_character_sheet("ally")["ruleset_character"]
    assert set(ally["conditions"]) & DEATH_STATE == set()
