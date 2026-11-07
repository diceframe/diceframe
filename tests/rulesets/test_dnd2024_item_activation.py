from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.validation import CombatIntentError
from src.rulesets.dnd2024.exploration.engine import (
    Dnd2024ExplorationEngine,
    ExplorationIntentError,
)
from src.rulesets.dnd2024.items.activation import (
    ItemActivationError,
    validate_activation_declaration,
    validate_consumable_declaration,
)
from src.rulesets.dnd2024.items.charges import ChargesError
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.events import EventBatchError


@dataclass
class SequenceRng:
    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum
        return value


POTION = "item:potion_of_healing"
WAND = "item:wand_of_healing"
BOW = "item:shortbow"
CROSSBOW = "item:crossbow_light"
ARROWS = "item:arrows"
BOLTS = "item:bolts"


def _runtime_sheet() -> tuple[Dnd2024Runtime, dict]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian")
    sheet = runtime.finalize_character(None, {**preset["draft"], "locale": "en", "name": "Arden"})
    return runtime, sheet


def _install_item(runtime: Dnd2024Runtime, definition: dict) -> str:
    runtime.load_bundle("en").entities["item"][definition["id"]] = definition
    return f"item:{definition['id']}"


def _potion() -> dict:
    return {
        "schema_version": 1, "kind": "item", "id": "potion_of_healing",
        "source_ref": "test:item:potion_of_healing", "automation_level": "deterministic",
        "item_type": "potion", "consumable": True,
        "activation": {
            "cost": "action", "target": "self",
            "effect": {"mode": "healing", "healing": "2d4+2"},
        },
    }


def _wand(
    *, requires_attunement: bool = False, recharge: dict | None = None,
    item_id: str = "wand_of_healing",
) -> dict:
    definition = {
        "schema_version": 1, "kind": "item", "id": item_id,
        "source_ref": f"test:item:{item_id}", "automation_level": "deterministic",
        "item_type": "wand", "consumable": False, "charges": {"max": 3},
        "activation": {
            "cost": "bonus_action", "target": "self", "charge_cost": 1,
            "effect": {"mode": "healing", "healing": "1d6+1"},
        },
    }
    if requires_attunement:
        definition["requires_attunement"] = True
    if recharge is not None:
        definition["recharge"] = recharge
    return definition


def _charged_potion() -> dict:
    definition = _potion()
    definition["id"] = "charged_potion"
    definition["charges"] = {"max": 2}
    definition["activation"] = {**definition["activation"], "charge_cost": 1}
    return definition


def _carry(sheet: dict, refs: dict[str, int]) -> None:
    sheet["inventory"] = [
        {"name": ref, "item_ref": ref, "type": "gear", "qty": qty}
        for ref, qty in refs.items()
    ]


def _equip(sheet: dict, refs: list[str]) -> None:
    sheet["equipment"] = [
        {"name": ref, "item_ref": ref, "type": "weapon", "slot": "main_hand", "qty": 1}
        for ref in refs
    ]
    sheet.setdefault("ruleset_character", {}).setdefault("equipment", {})["item_refs"] = list(refs)


def _reconcile(runtime: Dnd2024Runtime, sheet: dict) -> None:
    Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    )


def _instance(sheet: dict) -> GameInstance:
    instance = GameInstance(
        game_key=("test", "dnd2024-item-activation", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])
    return instance


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


def _start(engine: Dnd2024CombatEngine, instance: GameInstance, *, position: int = 5) -> None:
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin(position=position)],
    }, SequenceRng([20, 1]))
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True


def _combat(*, items: list[dict], carry: dict[str, int], equip: list[str]) -> tuple[
    Dnd2024CombatEngine, GameInstance, dict,
]:
    runtime, sheet = _runtime_sheet()
    for definition in items:
        _install_item(runtime, definition)
    _carry(sheet, carry)
    _equip(sheet, equip)
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    _start(engine, instance)
    return engine, instance, sheet


def _item_state(sheet: dict, ref: str) -> dict:
    return sheet["ruleset_character"]["equipment"]["item_state"][ref]["charges"]


def _invalid(result: dict) -> str:
    assert result["ok"] is False, result
    return str(result["error"])


def _carry_live(instance: GameInstance, refs: dict[str, int]) -> None:
    instance.get_character_sheet("gm")["inventory"] = [
        {"name": ref, "item_ref": ref, "type": "gear", "qty": qty}
        for ref, qty in refs.items()
    ]


def test_equipment_only_consumable_is_rejected_before_any_event() -> None:
    # 装备行不是 take_from_inventory 的扣除路径；药剂必须在背包里真正存在，
    # 否则只会到 apply_batch 才失败。
    engine, instance, _sheet = _combat(items=[_potion()], carry={}, equip=[POTION])

    resolved = engine.resolve_intent(instance, {
        "intent_id": "potion-equipped", "type": "activate_item",
        "expected_version": 1, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": POTION,
    }, SequenceRng([4, 4]))

    assert "not carried" in _invalid(resolved)


def test_exploration_accepts_the_bare_uid_used_by_the_gameplay_boundary() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _potion())
    _carry(sheet, {POTION: 1})
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")

    assert any(
        action["type"] == "exploration.activate_item"
        for action in engine.available_intents(instance, "gm")
    )
    resolved = engine.resolve_intent(instance, {
        "intent_id": "bare-uid", "type": "exploration.activate_item",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "gm",
        "item_ref": POTION,
    }, SequenceRng([4, 4]))

    assert resolved["ok"] is True
    consumed = [
        e for e in resolved["event_batch"]["events"]
        if e["type"] == "dnd2024.item.consumed"
    ]
    assert consumed and consumed[0]["actor_id"] == "player:gm"


def test_exploration_consumable_must_exist_in_inventory() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _potion())
    sheet["equipment"] = [
        {"name": POTION, "item_ref": POTION, "type": "potion", "qty": 1},
    ]
    sheet["inventory"] = []
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")

    resolved = engine.resolve_intent(instance, {
        "intent_id": "explore-equipped", "type": "exploration.activate_item",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": POTION,
    }, SequenceRng([4, 4]))

    assert resolved["ok"] is False
    assert "not carried" in str(resolved.get("error") or "")


def test_combat_potion_activation_pays_action_heals_and_consumes_copy() -> None:
    engine, instance, _sheet = _combat(items=[_potion()], carry={POTION: 1}, equip=[])
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 5
    slots_before = dict(canonical.get("spell_slots") or {})

    resolved = engine.resolve_intent(instance, {
        "intent_id": "potion-1", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "player:gm",
        "item_ref": POTION,
    }, SequenceRng([2, 2]))
    assert resolved["ok"] is True
    assert any(e["type"] == "dnd2024.item.consumed" for e in resolved["event_batch"]["events"])
    healing = next(
        e for e in resolved["event_batch"]["events"] if e["type"] == "resource.changed"
    )
    assert healing["healing"] is True
    assert healing["rolls"] == [2, 2]
    assert not any("movement_cost" in e for e in resolved["event_batch"]["events"])
    assert not any(e.get("kind") == "opportunity_attack" for e in resolved["event_batch"]["events"])
    applied = engine.apply_batch(instance, resolved["event_batch"])

    updated = instance.get_character_sheet("gm")
    assert applied["state_version"] == 2
    assert updated["ruleset_character"]["resources"]["hp"] == 11
    assert dict(updated["ruleset_character"].get("spell_slots") or {}) == slots_before
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 0
    assert updated["inventory"] == []


def test_combat_potion_healing_caps_at_max_hp() -> None:
    engine, instance, _sheet = _combat(items=[_potion()], carry={POTION: 1}, equip=[])
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    maximum = int(canonical["resources"]["max_hp"])
    canonical["resources"]["hp"] = maximum - 2
    resolved = engine.resolve_intent(instance, {
        "intent_id": "potion-cap", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": POTION,
    }, SequenceRng([4, 4]))
    healing = next(
        e for e in resolved["event_batch"]["events"] if e["type"] == "resource.changed"
    )
    assert healing["heal_before"] == maximum - 2
    assert healing["heal_after"] == maximum
    # delta is the HP actually restored; the raw 2d4+2 roll stays audit-visible.
    assert healing["delta"] == 2
    assert healing["amount"] == 2
    assert healing["rolls"] == [4, 4]
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert instance.get_character_sheet("gm")["ruleset_character"]["resources"][
        "hp"
    ] == maximum


def test_ally_item_use_beyond_five_feet_is_rejected() -> None:
    runtime, sheet = _runtime_sheet()
    ally_potion = _potion()
    ally_potion["id"] = "potion_for_ally"
    ally_potion["activation"] = {**ally_potion["activation"], "target": "ally"}
    _install_item(runtime, ally_potion)
    _carry(sheet, {POTION: 1, "item:potion_for_ally": 1})
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    ally_sheet = deepcopy(sheet)
    ally_sheet["inventory"] = []
    instance.players["ally"] = {"character_name": "Borin", "character_sheet": ally_sheet}
    engine = Dnd2024CombatEngine(
        runtime.load_bundle("en"), EncounterAccess.sandbox(),
    )
    _start(engine, instance)
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index("player:gm")
    combat["positions"]["player:ally"] = 30

    resolved = engine.resolve_intent(instance, {
        "intent_id": "potion-far-ally", "type": "activate_item",
        "expected_version": 1, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": "item:potion_for_ally", "target_id": "player:ally",
    }, SequenceRng([4, 4]))

    assert "out of item range" in _invalid(resolved)


def test_exploration_potion_activation_heals_without_touching_spell_slots() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _potion())
    _carry(sheet, {POTION: 1})
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    canonical = sheet["ruleset_character"]
    canonical["resources"]["hp"] = 5
    slots_before = dict(canonical.get("spell_slots") or {})

    resolved = engine.resolve_intent(instance, {
        "intent_id": "explore-potion", "type": "exploration.activate_item",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": POTION,
    }, SequenceRng([3, 4]))
    applied = engine.apply_batch(instance, resolved["event_batch"])

    updated = instance.get_character_sheet("gm")
    assert applied["applied"] is True
    assert updated["ruleset_character"]["resources"]["hp"] == 12
    assert updated["inventory"] == []
    assert dict(updated["ruleset_character"].get("spell_slots") or {}) == slots_before
    assert not any(e["type"] == "spell.slot_spent" for e in resolved["events"])
    assert not any(e["type"] == "counterspell" for e in resolved["events"])


def test_exploration_rejects_non_potions_and_non_self_targets() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _potion())
    _install_item(runtime, _wand())
    _carry(sheet, {POTION: 1, WAND: 1})
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    sheet["ruleset_character"]["resources"]["hp"] = 5

    with pytest.raises(ExplorationIntentError, match="healing potions"):
        engine._validate(instance, {
            "intent_id": "wand-outside", "type": "exploration.activate_item",
            "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
            "item_ref": WAND,
        })
    with pytest.raises(ExplorationIntentError, match="target the user"):
        engine._validate(instance, {
            "intent_id": "potion-other", "type": "exploration.activate_item",
            "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
            "target_id": "player:other", "item_ref": POTION,
        })


def test_activation_rejects_missing_or_empty_copies() -> None:
    engine, instance, _sheet = _combat(items=[_potion()], carry={POTION: 0}, equip=[])
    assert "not carried" in _invalid(engine.resolve_intent(instance, {
        "intent_id": "potion-empty", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": POTION,
    }, SequenceRng([])))

    engine, instance, _sheet = _combat(items=[_potion()], carry={}, equip=[])
    assert "not carried" in _invalid(engine.resolve_intent(instance, {
        "intent_id": "potion-missing", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": POTION,
    }, SequenceRng([])))


def test_activation_rejects_a_spent_action_without_consuming() -> None:
    engine, instance, _sheet = _combat(items=[_potion()], carry={POTION: 2}, equip=[])
    instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] = 5
    first = engine.resolve_intent(instance, {
        "intent_id": "potion-first", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": POTION,
    }, SequenceRng([2, 2]))
    assert engine.apply_batch(instance, first["event_batch"])["applied"] is True

    second = engine.resolve_intent(instance, {
        "intent_id": "potion-second", "type": "activate_item", "expected_version": 2,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": POTION,
    }, SequenceRng([]))
    assert "already been spent" in _invalid(second)
    assert [row["qty"] for row in instance.get_character_sheet("gm")["inventory"]] == [1]


def test_bonus_action_activation_rejects_a_spent_bonus_action() -> None:
    engine, instance, _sheet = _combat(items=[_wand()], carry={WAND: 1}, equip=[])
    ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] = 0
    assert "already been spent" in _invalid(engine.resolve_intent(instance, {
        "intent_id": "wand-no-bonus", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": WAND,
    }, SequenceRng([])))
    assert _item_state(instance.get_character_sheet("gm"), WAND) == {"current": 3}


def test_invalid_activation_declarations_fail_closed() -> None:
    base = {
        "activation": {
            "cost": "action", "target": "self",
            "effect": {"mode": "healing", "healing": "2d4+2"},
        }
    }
    assert validate_activation_declaration(base) == {
        "cost": "action", "target": "self",
        "effect": {"mode": "healing", "healing": "2d4+2"}, "charge_cost": None,
    }
    assert validate_activation_declaration({}) is None

    def broken(mutate) -> dict:
        definition = {"activation": {
            "cost": "action", "target": "self",
            "effect": {"mode": "healing", "healing": "2d4+2"},
        }}
        mutate(definition["activation"])
        return definition

    cases = [
        (lambda a: a.__setitem__("cost", "reaction"), "cost must be action"),
        (lambda a: a.__setitem__("target", "enemy"), "target must be self or ally"),
        (lambda a: a["effect"].__setitem__("mode", "damage"), "mode must be healing"),
        (lambda a: a["effect"].__setitem__("healing", "two d four"), "must be a dice formula"),
        (lambda a: a.__setitem__("charge_cost", 0), "charge_cost must be a positive integer"),
        (lambda a: a.__setitem__("label", "Drink"), "unknown activation field"),
        (lambda a: a["effect"].__setitem__("condition", "poisoned"), "unknown activation.effect"),
    ]
    for mutate, message in cases:
        with pytest.raises(ItemActivationError, match=message):
            validate_activation_declaration(broken(mutate))

    assert validate_consumable_declaration({}) is False
    assert validate_consumable_declaration({"consumable": True}) is True
    with pytest.raises(ItemActivationError, match="consumable must be a boolean"):
        validate_consumable_declaration({"consumable": "yes"})


def test_charged_item_initialises_spends_and_exhausts_fail_closed() -> None:
    engine, instance, sheet = _combat(
        items=[_wand()], carry={WAND: 1}, equip=[],
    )
    assert _item_state(instance.get_character_sheet("gm"), WAND) == {"current": 3}
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 5

    resolved = engine.resolve_intent(instance, {
        "intent_id": "wand-1", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": WAND,
    }, SequenceRng([4]))
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert _item_state(instance.get_character_sheet("gm"), WAND)["current"] == 2

    instance.get_character_sheet("gm")["ruleset_character"]["equipment"]["item_state"][WAND][
        "charges"
    ]["current"] = 0
    ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] = 1
    assert "not enough charges" in _invalid(engine.resolve_intent(instance, {
        "intent_id": "wand-2", "type": "activate_item", "expected_version": 2,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": WAND,
    }, SequenceRng([])))


def test_charged_item_requires_attunement_and_declaration_pairs() -> None:
    engine, instance, sheet = _combat(
        items=[_wand(requires_attunement=True)], carry={WAND: 1}, equip=[],
    )
    assert "requires attunement" in _invalid(engine.resolve_intent(instance, {
        "intent_id": "wand-unattuned", "type": "activate_item",
        "expected_version": 1, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": WAND,
    }, SequenceRng([])))
    instance.get_character_sheet("gm")["ruleset_character"]["attunement"] = {"refs": [WAND]}

    attuned = engine.resolve_intent(instance, {
        "intent_id": "wand-attuned", "type": "activate_item",
        "expected_version": 1, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": WAND,
    }, SequenceRng([4]))
    assert attuned["ok"] is True
    assert engine.apply_batch(instance, attuned["event_batch"])["applied"] is True
    assert _item_state(instance.get_character_sheet("gm"), WAND)["current"] == 2
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0

    ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] = 1
    no_cost = _wand()
    del no_cost["activation"]["charge_cost"]
    no_cost["id"] = "wand_without_cost"
    engine.bundle.entities["item"]["wand_without_cost"] = no_cost
    _carry_live(instance, {WAND: 1, "item:wand_without_cost": 1})
    assert "requires charge_cost" in _invalid(engine.resolve_intent(instance, {
        "intent_id": "wand-no-cost", "type": "activate_item",
        "expected_version": 2, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": "item:wand_without_cost",
    }, SequenceRng([])))

    free = _potion()
    free["id"] = "free_potion"
    free["activation"] = {**free["activation"], "charge_cost": 1}
    engine.bundle.entities["item"]["free_potion"] = free
    _carry_live(instance, {"item:free_potion": 1})
    assert "has no charges" in _invalid(engine.resolve_intent(instance, {
        "intent_id": "free-with-cost", "type": "activate_item",
        "expected_version": 2, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": "item:free_potion",
    }, SequenceRng([])))


def test_charges_reducer_rejects_over_budget_state_and_unknown_fields() -> None:
    engine, instance, _sheet = _combat(items=[_wand()], carry={WAND: 1}, equip=[])
    resolved = engine.resolve_intent(instance, {
        "intent_id": "wand-tamper", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": WAND,
    }, SequenceRng([4]))
    instance.get_character_sheet("gm")["ruleset_character"]["equipment"]["item_state"][WAND][
        "charges"
    ]["current"] = 9
    with pytest.raises(EventBatchError, match="maximum"):
        engine.apply_batch(instance, resolved["event_batch"])

    tampered = dict(resolved["event_batch"])
    instance.get_character_sheet("gm")["ruleset_character"]["equipment"]["item_state"][WAND][
        "charges"
    ]["current"] = 3
    tampered["events"] = [*resolved["event_batch"]["events"], {
        "type": "dnd2024.item.consumed", "actor_id": "player:gm",
        "item_ref": POTION, "amount": 1, "injected": True,
    }]
    with pytest.raises(EventBatchError, match="unknown"):
        engine.apply_batch(instance, tampered)


def test_bow_consumes_arrows_and_rejects_an_empty_quiver() -> None:
    engine, instance, sheet = _combat(
        items=[], carry={ARROWS: 1}, equip=[BOW],
    )
    instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] = 5
    resolved = engine.resolve_intent(instance, {
        "intent_id": "bow-1", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": BOW,
    }, SequenceRng([15, 4]))
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert instance.get_character_sheet("gm")["inventory"] == []

    ruleset_runtime.state(instance)["combat"]["economy"]["action"] = 1
    assert "ammunition" in _invalid(engine.resolve_intent(instance, {
        "intent_id": "bow-2", "type": "attack", "expected_version": 2,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": BOW,
    }, SequenceRng([])))


@pytest.mark.parametrize(
    ("weapon", "ammunition"),
    [
        ("item:shortbow", "item:arrows"),
        ("item:longbow", "item:arrows"),
        ("item:crossbow_hand", "item:bolts"),
        ("item:crossbow_light", "item:bolts"),
        ("item:crossbow_heavy", "item:bolts"),
    ],
)
def test_every_bow_and_crossbow_consumes_its_ammunition_once(
    weapon: str, ammunition: str,
) -> None:
    engine, instance, _sheet = _combat(
        items=[], carry={ammunition: 1}, equip=[weapon],
    )
    goblin = ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]
    goblin["position"] = 30
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"{weapon}-shot", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": weapon,
    }, SequenceRng([10, 3]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert instance.get_character_sheet("gm")["inventory"] == []
    ruleset_runtime.state(instance)["combat"]["economy"]["action"] = 1
    assert "ammunition" in _invalid(engine.resolve_intent(instance, {
        "intent_id": f"{weapon}-empty", "type": "attack", "expected_version": 2,
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": weapon,
    }, SequenceRng([])))


def test_crossbow_needs_bolts_not_arrows_and_melee_needs_no_ammunition() -> None:
    engine, instance, sheet = _combat(
        items=[], carry={ARROWS: 1}, equip=[CROSSBOW],
    )
    goblin = ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]
    goblin["position"] = 30
    assert "ammunition" in _invalid(engine.resolve_intent(instance, {
        "intent_id": "crossbow-wrong", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": CROSSBOW,
    }, SequenceRng([])))

    _carry(sheet, {ARROWS: 1, BOLTS: 1})
    instance.get_character_sheet("gm")["inventory"] = [
        {"name": BOLTS, "item_ref": BOLTS, "type": "gear", "qty": 1},
    ]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "crossbow-ok", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": CROSSBOW,
    }, SequenceRng([10, 3]))
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True

    instance.get_character_sheet("gm")["equipment"] = [
        {"name": "item:greatsword", "item_ref": "item:greatsword", "slot": "main_hand", "qty": 1},
    ]
    instance.get_character_sheet("gm")["ruleset_character"]["equipment"]["item_refs"] = [
        "item:greatsword",
    ]
    instance.get_character_sheet("gm")["inventory"] = [
        {"name": ARROWS, "item_ref": ARROWS, "type": "gear", "qty": 1},
    ]
    ruleset_runtime.state(instance)["combat"]["economy"]["action"] = 1
    melee = engine.resolve_intent(instance, {
        "intent_id": "melee-no-ammo", "type": "attack", "expected_version": 2,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:greatsword",
    }, SequenceRng([12, 3, 3]))
    assert melee["ok"] is True
    assert not any(e["type"] == "dnd2024.item.consumed" for e in melee["event_batch"]["events"])


def test_long_rest_recharges_at_dawn_and_short_rest_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.rulesets.dnd2024.items.charges as charges_module

    class FixedRandom:
        def randint(self, minimum: int, maximum: int) -> int:
            return maximum

    monkeypatch.setattr(charges_module, "SystemRandom", lambda: FixedRandom())
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _wand(recharge={"on": "dawn", "amount": 1}))
    _install_item(runtime, _wand(
        recharge={"on": "dawn", "amount": "1d4+1"}, item_id="wand_dice",
    ))
    _carry(sheet, {WAND: 1, "item:wand_dice": 1})
    _equip(sheet, [WAND, "item:wand_dice"])
    _reconcile(runtime, sheet)
    state = sheet["ruleset_character"]["equipment"]["item_state"]
    state[WAND]["charges"]["current"] = 1
    state["item:wand_dice"]["charges"]["current"] = 0

    short = runtime.complete_rest(None, sheet, "short", {})
    assert short["character"]["ruleset_character"]["equipment"]["item_state"][WAND][
        "charges"
    ]["current"] == 1
    assert not [e for e in short["events"] if e["type"] == "recharge_item"]

    long = runtime.complete_rest(None, sheet, "long", {})
    assert long["character"]["ruleset_character"]["equipment"]["item_state"][WAND][
        "charges"
    ]["current"] == 2
    assert long["character"]["ruleset_character"]["equipment"]["item_state"][
        "item:wand_dice"
    ]["charges"]["current"] == 3
    fixed = next(
        e for e in long["events"]
        if e["type"] == "recharge_item" and e["item_ref"] == WAND
    )
    dice = next(
        e for e in long["events"]
        if e["type"] == "recharge_item" and e["item_ref"] == "item:wand_dice"
    )
    assert fixed["rolls"] == []
    assert dice["rolls"] == [4]
    assert dice["after"] == 3


def test_corrupt_charge_state_is_isolated_and_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.rulesets.dnd2024.items.charges as charges_module

    class FixedRandom:
        def randint(self, minimum: int, maximum: int) -> int:
            return maximum

    monkeypatch.setattr(charges_module, "SystemRandom", lambda: FixedRandom())
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _wand(recharge={"on": "dawn", "amount": 1}))
    _install_item(runtime, _wand(
        recharge={"on": "dawn", "amount": 1}, item_id="wand_corrupt",
    ))
    _carry(sheet, {WAND: 1, "item:wand_corrupt": 1})
    _equip(sheet, [WAND, "item:wand_corrupt"])
    _reconcile(runtime, sheet)
    state = sheet["ruleset_character"]["equipment"]["item_state"]
    state[WAND]["charges"]["current"] = 0
    state["item:wand_corrupt"]["charges"]["current"] = 9

    long = runtime.complete_rest(None, sheet, "long", {})
    state = long["character"]["ruleset_character"]["equipment"]["item_state"]

    # 一件损坏的旧存档不能中断整次休息，也不能静默改写：
    # 上限收敛为当前内容值，原值保留在审计事件里。
    assert state[WAND]["charges"]["current"] == 1
    assert state["item:wand_corrupt"]["charges"]["current"] == 3
    corrupt = next(
        e for e in long["events"]
        if e["type"] == "recharge_item" and e["item_ref"] == "item:wand_corrupt"
    )
    assert corrupt["before"] == 9
    assert corrupt["after"] == 3


def test_short_rest_recharges_dice_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.rulesets.dnd2024.items.charges as charges_module

    class FixedRandom:
        def randint(self, minimum: int, maximum: int) -> int:
            return maximum

    monkeypatch.setattr(charges_module, "SystemRandom", lambda: FixedRandom())
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _wand(recharge={"on": "short_rest", "amount": "1d6+4"}))
    _carry(sheet, {WAND: 1})
    _equip(sheet, [WAND])
    _reconcile(runtime, sheet)
    sheet["ruleset_character"]["equipment"]["item_state"][WAND]["charges"]["current"] = 0

    result = runtime.complete_rest(None, sheet, "short", {})
    charges = result["character"]["ruleset_character"]["equipment"]["item_state"][WAND][
        "charges"
    ]
    assert charges["current"] == 3
    event = next(e for e in result["events"] if e["type"] == "recharge_item")
    assert event["rolls"] == [6]

    long = runtime.complete_rest(None, sheet, "long", {})
    assert long["character"]["ruleset_character"]["equipment"]["item_state"][WAND][
        "charges"
    ]["current"] == 0
    assert not [e for e in long["events"] if e["type"] == "recharge_item"]


def test_recharge_declaration_rejects_unknown_keys_and_none_stops_recharge() -> None:
    from src.rulesets.dnd2024.items.charges import validate_recharge_declaration

    with pytest.raises(ChargesError, match="unknown recharge field"):
        validate_recharge_declaration({"recharge": {"on": "dawn", "amount": 1, "bonus": 2}})
    with pytest.raises(ChargesError, match="on must be"):
        validate_recharge_declaration({"recharge": {"on": "sunrise", "amount": 1}})
    assert validate_recharge_declaration({"recharge": {"on": "none"}}) == {"on": "none"}
    with pytest.raises(ChargesError, match="does not accept amount"):
        validate_recharge_declaration({"recharge": {"on": "none", "amount": 2}})

    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _wand(recharge={"on": "none"}))
    _carry(sheet, {WAND: 1})
    _equip(sheet, [WAND])
    _reconcile(runtime, sheet)
    sheet["ruleset_character"]["equipment"]["item_state"][WAND]["charges"]["current"] = 0
    result = runtime.complete_rest(None, sheet, "long", {})
    assert not [e for e in result["events"] if e["type"] == "recharge_item"]


def test_activation_replay_does_not_consume_twice() -> None:
    engine, instance, _sheet = _combat(items=[_potion()], carry={POTION: 1}, equip=[])
    instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] = 5
    resolved = engine.resolve_intent(instance, {
        "intent_id": "replay-potion", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": POTION,
    }, SequenceRng([2, 2]))
    first = engine.apply_batch(instance, resolved["event_batch"])
    replay = engine.apply_batch(instance, resolved["event_batch"])

    assert first["applied"] is True
    assert replay["duplicate"] is True
    assert instance.get_character_sheet("gm")["inventory"] == []
    assert instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] == 11
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 0


def test_charged_activation_replay_does_not_spend_a_second_charge() -> None:
    engine, instance, _sheet = _combat(items=[_wand()], carry={WAND: 1}, equip=[])
    instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] = 5
    resolved = engine.resolve_intent(instance, {
        "intent_id": "replay-wand", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": WAND,
    }, SequenceRng([2]))
    first = engine.apply_batch(instance, resolved["event_batch"])
    replay = engine.apply_batch(instance, resolved["event_batch"])
    assert first["applied"] is True
    assert replay["duplicate"] is True
    assert _item_state(instance.get_character_sheet("gm"), WAND)["current"] == 2
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0


def test_exploration_charged_activation_replay_does_not_consume_twice() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _charged_potion())
    _carry(sheet, {"item:charged_potion": 1})
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    sheet["ruleset_character"]["resources"]["hp"] = 5
    resolved = engine.resolve_intent(instance, {
        "intent_id": "replay-explore-charged", "type": "exploration.activate_item",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": "item:charged_potion",
    }, SequenceRng([2, 2]))
    first = engine.apply_batch(instance, resolved["event_batch"])
    replay = engine.apply_batch(instance, resolved["event_batch"])
    assert first["applied"] is True
    assert replay["duplicate"] is True
    assert instance.get_character_sheet("gm")["inventory"] == []
    assert instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] == 11
    assert _item_state(instance.get_character_sheet("gm"), "item:charged_potion")[
        "current"
    ] == 1


def _add_companion(
    instance: GameInstance, sheet: dict, *, item_refs: list[str],
    item_state: dict | None = None,
) -> None:
    """B4：给 companion 一份可被 owned_ref_counts 读到的 canonical 物品。"""

    canonical = deepcopy(sheet["ruleset_character"])
    canonical["equipment"] = {
        "item_refs": list(item_refs),
        "item_grants": [{"item_ref": ref, "quantity": 1} for ref in item_refs],
    }
    if item_state is not None:
        canonical["equipment"]["item_state"] = deepcopy(item_state)
    canonical["resources"]["hp"] = 5
    party = ruleset_runtime.state(instance).setdefault("party", {"companions": {}})
    party.setdefault("companions", {})["mira"] = {
        "id": "mira", "name": "Mira", "controller": "ai", "active": True,
        "ruleset_character": canonical,
    }


def _end_player_turn(engine: Dnd2024CombatEngine, instance: GameInstance) -> None:
    version = int(ruleset_runtime.state(instance)["version"])
    resolved = engine.resolve_intent(instance, {
        "intent_id": "end-player-turn", "type": "end_turn",
        "expected_version": version, "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True


def test_companion_consumable_activation_fails_before_any_cost_or_event() -> None:
    """B4：companion consumable 必须在权威扣件前整体拒绝，不能只拒绝玩家事件链。"""

    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _potion())
    instance = _instance(sheet)
    _add_companion(instance, sheet, item_refs=[POTION])
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    _start(engine, instance)
    _end_player_turn(engine, instance)

    state_before = deepcopy(ruleset_runtime.state(instance))
    version = int(state_before["version"])
    for attempt in range(2):
        result = engine.resolve_intent(instance, {
            "intent_id": f"companion-potion-{attempt}", "type": "activate_item",
            "expected_version": version, "submitted_by": "gm",
            "actor_id": "companion:mira", "target_id": "companion:mira",
            "item_ref": POTION,
        }, SequenceRng([4, 4]))
        assert result["ok"] is False, result
        assert "authoritative backpack" in str(result["error"]), result
        assert ruleset_runtime.state(instance) == state_before


def test_companion_charged_activation_without_consumable_is_unchanged() -> None:
    """B4 只关闭不安全的 consumable 路径；已支持的 charged 行为保持不动。"""

    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _wand())
    instance = _instance(sheet)
    wand_state = {WAND: {"charges": {"current": 3, "max": 3}}}
    _add_companion(instance, sheet, item_refs=[WAND], item_state=wand_state)
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    _start(engine, instance)
    _end_player_turn(engine, instance)

    version = int(ruleset_runtime.state(instance)["version"])
    resolved = engine.resolve_intent(instance, {
        "intent_id": "companion-wand", "type": "activate_item",
        "expected_version": version, "submitted_by": "gm",
        "actor_id": "companion:mira", "target_id": "companion:mira",
        "item_ref": WAND,
    }, SequenceRng([3]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True

    companion = ruleset_runtime.state(instance)["party"]["companions"]["mira"]
    canonical = companion["ruleset_character"]
    assert canonical["resources"]["hp"] == 9
    assert canonical["equipment"]["item_state"][WAND]["charges"]["current"] == 2
    charge_event = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "dnd2024.item.charge_spent"
    )
    assert charge_event["actor_id"] == "companion:mira"
    assert charge_event["item_ref"] == WAND


def test_consumable_with_the_same_display_name_deducts_only_the_target_ref() -> None:
    engine, instance, _sheet = _combat(
        items=[_potion(), _charged_potion()],
        carry={POTION: 1, "item:charged_potion": 1},
        equip=[],
    )
    instance.get_character_sheet("gm")["inventory"] = [
        {"name": "Potion", "item_ref": POTION, "type": "gear", "qty": 1},
        {"name": "Potion", "item_ref": "item:charged_potion", "type": "gear", "qty": 1},
    ]
    instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] = 5
    resolved = engine.resolve_intent(instance, {
        "intent_id": "same-name", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": POTION,
    }, SequenceRng([1, 1]))
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert [row["item_ref"] for row in instance.get_character_sheet("gm")["inventory"]] == [
        "item:charged_potion",
    ]


def test_reconcile_initialises_once_and_never_resets_used_charges() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _wand())
    _carry(sheet, {WAND: 1})
    _reconcile(runtime, sheet)
    assert _item_state(sheet, WAND) == {"current": 3}
    sheet["ruleset_character"]["equipment"]["item_state"][WAND]["charges"]["current"] = 1
    assert Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    ) is None
    assert _item_state(sheet, WAND)["current"] == 1

    # 装备组合变化后再次 reconcile 仍不得重置既有充能。
    sheet["equipment"] = [
        {"name": WAND, "item_ref": WAND, "slot": "main_hand", "qty": 1},
        {"name": "item:greatsword", "item_ref": "item:greatsword", "slot": "off_hand", "qty": 1},
    ]
    sheet["ruleset_character"]["equipment"]["item_refs"] = [WAND, "item:greatsword"]
    Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    )
    assert _item_state(sheet, WAND)["current"] == 1
    assert Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    ) is None
    assert _item_state(sheet, WAND)["current"] == 1

    runtime.load_bundle("en").entities["item"].pop("wand_of_healing")
    assert Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    ) is None
    assert _item_state(sheet, WAND)["current"] == 1


def test_charged_duplicates_are_rejected_in_combat_and_exploration() -> None:
    engine, instance, _sheet = _combat(items=[_wand()], carry={WAND: 2}, equip=[])
    assert "multiple copies" in _invalid(engine.resolve_intent(instance, {
        "intent_id": "dup-combat", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": WAND,
    }, SequenceRng([])))

    runtime, explore_sheet = _runtime_sheet()
    _install_item(runtime, _charged_potion())
    _carry(explore_sheet, {"item:charged_potion": 2})
    _reconcile(runtime, explore_sheet)
    exploration = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    explore_instance = _instance(explore_sheet)
    assert "multiple copies" in _invalid(exploration.resolve_intent(explore_instance, {
        "intent_id": "dup-explore", "type": "exploration.activate_item",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": "item:charged_potion",
    }, SequenceRng([])))
