"""Misty Step (P6): teleport primitive and registry-driven speed modifiers."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.catalog import (
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.combat.check_modes import effective_speed
from src.rulesets.dnd2024.combat.primitives import CombatIntentError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
)


CANTRIPS = ["spell:light", "spell:mage_hand", "spell:ray_of_frost"]
# Ordered by spell level: every level's spellbook minimum prefix stays inside
# that level's maximum spell level. misty_step sits at index 9 so the level 3
# spellbook (10 entries) includes it while lower levels never do.
BASE_SPELLBOOK = [
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:shield",
    "spell:thunderwave",
    "spell:false_life",
    "spell:charm_person",
    "spell:sleep",
    "spell:burning_hands",
    "spell:misty_step",
    "spell:hold_person",
    "spell:invisibility",
    "spell:blur",
    "spell:fireball",
]


def wizard_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        "class:wizard", level
    )
    spellbook = BASE_SPELLBOOK[: requirements["spellbook_minimum"]]
    if level >= 3:
        prepared = [
            *BASE_SPELLBOOK[: requirements["prepared_spell_count"] - 1],
            "spell:misty_step",
        ]
    else:
        prepared = BASE_SPELLBOOK[: requirements["prepared_spell_count"]]
    assert set(prepared).issubset(spellbook)
    return {
        "cantrip_refs": CANTRIPS[: requirements["cantrip_count"]],
        "prepared_spell_refs": prepared,
        "spellbook_refs": spellbook,
    }


def wizard_sheet(runtime: Dnd2024Runtime, *, level: int = 3) -> dict:
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


def start_with(engine, instance, enemies: list[dict], rolls: list[int]) -> None:
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemies,
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"


def new_combat(
    *, level: int = 3, position: int = 5, enemies: list[dict] | None = None,
) -> tuple[Dnd2024Runtime, object, object, SequenceRng]:
    runtime = Dnd2024Runtime()
    sheet = wizard_sheet(runtime, level=level)
    engine, instance = monk_instance(runtime, sheet)
    rng = SequenceRng([20, 1])
    start_with(engine, instance, enemies or [goblin(position=position, hp=100)], rng.values)
    return runtime, engine, instance, rng


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def combat(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def intent(instance, kind: str, **extra) -> dict:
    return {
        "intent_id": f"{kind}:{ruleset_runtime.state(instance)['version']}",
        "type": kind,
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
        "actor_id": "player:gm",
        **extra,
    }


def cast(
    engine, instance, spell_ref: str, slot: int, targets: list[str],
    *, rolls: list[int] = (), **extra,
) -> dict:
    resolved = engine.resolve_intent(
        instance,
        intent(instance, "cast_spell", spell_ref=spell_ref, slot_level=slot,
               target_ids=targets, **extra),
        SequenceRng(list(rolls)),
    )
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return resolved


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def event_of(result: dict, kind: str, **match) -> dict:
    for event in events_of(result):
        if event["type"] == kind and all(event.get(key) == value for key, value in match.items()):
            return event
    raise AssertionError(f"event not found: {kind} {match}")


def end_turn(engine, instance) -> dict:
    resolved = engine.resolve_intent(
        instance, intent(instance, "end_turn", actor_id="player:gm"), SequenceRng([]),
    )
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return resolved


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


# --- #1 catalog validation of speed_modifier and teleport range ------------

def test_catalog_rejects_invalid_speed_modifier_and_teleport_range() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    for bad in (True, "-10", 0, 601, -601):
        with pytest.raises(CombatCatalogError):
            Dnd2024CombatCatalog.from_bundle(
                catalog_bundle(bundle, conditions={"slowed_10": {"speed_modifier": bad}}),
            )
    with pytest.raises(CombatCatalogError):
        Dnd2024CombatCatalog.from_bundle(catalog_bundle(
            bundle,
            spell_effects={"misty_step": {"mode": "teleport", "range": 3, "target_count": 1}},
        ))
    # 既有合法条目仍然通过。
    Dnd2024CombatCatalog.from_bundle(bundle)


# --- #2 a 3rd-level wizard teleports without spending movement -------------

def test_misty_step_teleports_without_movement_cost() -> None:
    _runtime, engine, instance, _rng = new_combat(level=3)
    action = next(
        item for item in engine.available_intents(instance, "gm")
        if item["type"] == "cast_spell"
    )
    spell = next(item for item in action["spells"] if item["spell_ref"] == "spell:misty_step")
    assert spell["mode"] == "teleport"
    assert spell["casting_time"] == "Bonus Action"

    economy_before = deepcopy(combat(instance)["economy"])
    result = cast(engine, instance, "spell:misty_step", 2, ["player:gm"], teleport_distance=25)

    events = [event for event in events_of(result) if event["type"] != "intent.submitted"]
    assert [event["type"] for event in events] == [
        "dnd2024.action.spent",
        "dnd2024.spell.slot_spent",
        "dnd2024.spell.cast",
        "dnd2024.position.changed",
    ]
    assert events[0]["resource"] == "bonus_action"
    assert events[1]["slot_level"] == 2
    assert events[2]["slot_level"] == 2
    assert events[2]["target_ids"] == ["player:gm"]
    position_event = events[3]
    assert position_event["type"] == "dnd2024.position.changed"
    assert position_event["actor_id"] == "player:gm"
    assert position_event["distance"] == 25
    assert position_event["forced"] is True
    assert "movement_cost" not in position_event

    assert not any(event["type"] == "dnd2024.concentration.started" for event in events)
    assert not any(event["type"] == "check.resolved" for event in events)
    assert combat(instance)["positions"]["player:gm"] == 25
    economy = combat(instance)["economy"]
    assert economy["bonus_action"] == 0
    assert economy["movement"] == economy_before["movement"]
    assert canonical(instance)["spellcasting"]["class"]["slots_current"]["2"] == 1


# --- #3 teleport_distance is mandatory, non-zero, in range -----------------

@pytest.mark.parametrize("bad", [None, 0, "25", True, 31, -31])
def test_teleport_distance_must_be_valid(bad) -> None:
    _runtime, engine, instance, _rng = new_combat(level=3)
    extra = {} if bad is None else {"teleport_distance": bad}
    validated = engine.validate_intent(
        instance,
        intent(instance, "cast_spell", spell_ref="spell:misty_step", slot_level=2,
               target_ids=["player:gm"], **extra),
    )
    assert validated["ok"] is False
    assert "teleport" in validated["error"]


# --- #4 teleport may only target the caster --------------------------------

def test_teleport_targets_only_the_caster() -> None:
    _runtime, engine, instance, _rng = new_combat(level=3)
    ally_sheet = wizard_sheet(_runtime, level=1)
    instance.players["p2"] = {"character_name": "Ally", "character_sheet": ally_sheet}
    for targets in (["enemy:goblin-1"], ["player:p2"]):
        validated = engine.validate_intent(
            instance,
            intent(instance, "cast_spell", spell_ref="spell:misty_step", slot_level=2,
                   target_ids=targets, teleport_distance=25),
        )
        assert validated["ok"] is False
    validated = engine.validate_intent(
        instance,
        intent(instance, "cast_spell", spell_ref="spell:misty_step", slot_level=2,
               target_ids=["player:gm"], teleport_distance=25),
    )
    assert validated["ok"] is True


# --- #5 teleport_distance is rejected on non-teleport spells ---------------

def test_non_teleport_spell_rejects_teleport_distance() -> None:
    _runtime, engine, instance, _rng = new_combat(level=3)
    validated = engine.validate_intent(
        instance,
        intent(instance, "cast_spell", spell_ref="spell:magic_missile", slot_level=2,
               target_ids=["enemy:goblin-1"], teleport_distance=25),
    )
    assert validated["ok"] is False
    assert "teleport distance is not valid" in validated["error"]


# --- #6 teleport does not trigger opportunity attacks ---------------------

def test_misty_step_does_not_trigger_opportunity_attacks() -> None:
    _runtime, engine, instance, _rng = new_combat(level=3, position=5)
    goblin_reaction = combat(instance)["reactions"]["enemy:goblin-1"]
    assert goblin_reaction == 1

    result = cast(engine, instance, "spell:misty_step", 2, ["player:gm"], teleport_distance=25)
    assert not any(event["type"] == "dnd2024.decision.pending" for event in events_of(result))
    assert not any(event["type"] == "check.resolved" for event in events_of(result))
    assert combat(instance)["positions"]["player:gm"] == 25
    assert combat(instance)["reactions"]["enemy:goblin-1"] == 1

    # 对照：使用速度的普通移动离开触及仍会挂起借机攻击决策（既有行为）。
    _runtime2, engine2, instance2, _rng2 = new_combat(level=3, position=5)
    moved = engine2.resolve_intent(
        instance2, intent(instance2, "move", actor_id="player:gm", distance=25), SequenceRng([]),
    )
    assert moved["ok"], moved
    pending = event_of(moved, "dnd2024.decision.pending")
    assert pending["kind"] == "opportunity_attack"
    assert pending["threat_actor_ids"] == ["enemy:goblin-1"]


# --- #7 turn-start economy snapshots slowed_10 before it expires ----------

def test_turn_start_economy_snapshots_slowed_10_before_expiry() -> None:
    _runtime, engine, instance, _rng = new_combat(level=3, position=50)
    cast(engine, instance, "spell:ray_of_frost", 0, ["enemy:goblin-1"], rolls=[15, 3])
    assert "slowed_10" in combat(instance)["enemies"]["goblin-1"]["conditions"]

    result = end_turn(engine, instance)
    advanced = event_of(result, "dnd2024.turn.advanced")
    assert advanced["actor_id"] == "enemy:goblin-1"
    assert advanced["economy"]["movement"] == 20
    assert advanced["economy"]["speed"] == 20
    assert "slowed_10" not in combat(instance)["enemies"]["goblin-1"]["conditions"]
    assert combat(instance)["economy"]["movement"] == 20


# --- #8 speed modifiers floor at zero --------------------------------------

def test_slowed_10_floors_at_zero_for_slow_enemies() -> None:
    enemy = goblin(position=100)
    enemy["speed"] = 10
    _runtime, engine, instance, _rng = new_combat(level=3, enemies=[enemy])
    combat(instance)["enemies"]["goblin-1"]["conditions"]["slowed_10"] = {
        "duration": "target_turn_start", "source_actor_id": "player:gm",
    }
    result = end_turn(engine, instance)
    advanced = event_of(result, "dnd2024.turn.advanced")
    assert advanced["economy"]["movement"] == 0
    assert advanced["economy"]["speed"] == 0
    assert combat(instance)["economy"]["movement"] == 0


# --- #9 speed_zero projects into economy and blocks movement ---------------

def test_paralyzed_speed_zero_projects_into_economy_and_blocks_move() -> None:
    _runtime, engine, instance, _rng = new_combat(level=3, position=100)
    combat(instance)["enemies"]["goblin-1"]["conditions"]["paralyzed"] = {
        "source": "test",
    }
    result = end_turn(engine, instance)
    advanced = event_of(result, "dnd2024.turn.advanced")
    assert advanced["actor_id"] == "enemy:goblin-1"
    assert advanced["economy"]["movement"] == 0
    assert advanced["economy"]["speed"] == 0

    validated = engine.validate_intent(instance, {
        "intent_id": "goblin-move", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1", "distance": 5,
    })
    assert validated["ok"] is False
    assert "speed zero" in validated["error"]


# --- #10 effective_speed is a pure aggregation -----------------------------

def test_effective_speed_pure_function() -> None:
    registry = {
        "slowed_10": {"speed_modifier": -10},
        "grappled_slow": {"speed_modifier": -15},
        "paralyzed": {"speed_zero": True},
        "longstrider": {"speed_modifier": 10},
    }
    assert effective_speed(registry, {}, 30) == 30
    assert effective_speed(registry, {"slowed_10": {}}, 30) == 20
    assert effective_speed(
        registry, {"slowed_10": {}, "grappled_slow": {}}, 30,
    ) == 5
    assert effective_speed(
        registry, {"slowed_10": {}, "grappled_slow": {}}, 10,
    ) == 0
    assert effective_speed(registry, {"slowed_10": {}, "paralyzed": {}}, 30) == 0
    assert effective_speed(registry, {"longstrider": {}}, 30) == 40


# --- #11 exhausted movement still allows teleporting -----------------------

def test_misty_step_ignores_exhausted_movement() -> None:
    _runtime, engine, instance, _rng = new_combat(level=3, position=100)
    moved = engine.resolve_intent(
        instance, intent(instance, "move", actor_id="player:gm", distance=30), SequenceRng([]),
    )
    assert moved["ok"], moved
    assert engine.apply_batch(instance, moved["event_batch"])["applied"]
    assert combat(instance)["economy"]["movement"] == 0
    assert combat(instance)["positions"]["player:gm"] == 30

    cast(engine, instance, "spell:misty_step", 2, ["player:gm"], teleport_distance=25)
    assert combat(instance)["positions"]["player:gm"] == 55
    assert combat(instance)["economy"]["movement"] == 0


# --- #12 bonus action economy and zero RNG consumption --------------------

def test_misty_step_spends_bonus_action_and_blocks_a_second_one() -> None:
    _runtime, engine, instance, rng = new_combat(level=3)
    assert rng.values == []

    # 哨兵骰：任何一次 randint 都会弹出它并触发范围断言，从而暴露 RNG 消耗。
    zero_rng = SequenceRng([999])
    resolved = engine.resolve_intent(
        instance,
        intent(instance, "cast_spell", spell_ref="spell:misty_step", slot_level=2,
               target_ids=["player:gm"], teleport_distance=25),
        zero_rng,
    )
    assert resolved["ok"], resolved
    assert zero_rng.values == [999]
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]

    economy = combat(instance)["economy"]
    assert economy["action"] == 1
    assert economy["bonus_action"] == 0

    validated = engine.validate_intent(
        instance,
        intent(instance, "cast_spell", spell_ref="spell:misty_step", slot_level=2,
               target_ids=["player:gm"], teleport_distance=25),
    )
    assert validated["ok"] is False
    assert "bonus action" in validated["error"]
