"""Integration regressions for the D3 item-activation review remediation."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.exploration.engine import (
    Dnd2024ExplorationEngine,
    ExplorationIntentError,
)
from src.rulesets.dnd2024.items.charges import recharge_for_rest
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.events import EventBatchError


POTION = "item:potion_of_healing"
ALLY_POTION = "item:potion_of_allies"
WAND = "item:wand_of_healing"


@dataclass
class SequenceRng:
    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum
        return value


def _runtime_sheet() -> tuple[Dnd2024Runtime, dict]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian")
    sheet = runtime.finalize_character(None, {**preset["draft"], "locale": "en", "name": "Arden"})
    return runtime, sheet


def _install_item(runtime: Dnd2024Runtime, definition: dict) -> str:
    runtime.load_bundle("en").entities["item"][definition["id"]] = definition
    return f"item:{definition['id']}"


def _potion(*, consumable: object = True) -> dict:
    definition = {
        "schema_version": 1, "kind": "item", "id": "potion_of_healing",
        "source_ref": "test:item:potion_of_healing", "automation_level": "deterministic",
        "item_type": "potion",
        "activation": {
            "cost": "action", "target": "self",
            "effect": {"mode": "healing", "healing": "2d4+2"},
        },
    }
    if consumable is not None:
        definition["consumable"] = consumable
    return definition


def _ally_potion() -> dict:
    return {
        "schema_version": 1, "kind": "item", "id": "potion_of_allies",
        "source_ref": "test:item:potion_of_allies", "automation_level": "deterministic",
        "item_type": "potion", "consumable": True,
        "activation": {
            "cost": "action", "target": "ally",
            "effect": {"mode": "healing", "healing": "2d4+2"},
        },
    }


def _wand(*, recharge: dict | None = None, item_id: str = "wand_of_healing") -> dict:
    definition = {
        "schema_version": 1, "kind": "item", "id": item_id,
        "source_ref": f"test:item:{item_id}", "automation_level": "deterministic",
        "item_type": "wand", "consumable": False, "charges": {"max": 3},
        "activation": {
            "cost": "bonus_action", "target": "self", "charge_cost": 1,
            "effect": {"mode": "healing", "healing": "1d6+1"},
        },
    }
    if recharge is not None:
        definition["recharge"] = recharge
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
    sheet.setdefault("ruleset_character", {}).setdefault("equipment", {})[
        "item_refs"
    ] = list(refs)


def _reconcile(runtime: Dnd2024Runtime, sheet: dict) -> None:
    Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    )


def _instance(sheet: dict) -> GameInstance:
    instance = GameInstance(
        game_key=("test", "dnd2024-item-remediation", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])
    return instance


def _goblin() -> dict:
    return {
        "id": "goblin-1", "name": "Goblin", "hp": 18, "armor_class": 12,
        "speed": 30, "position": 5, "initiative_modifier": 2,
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": -1, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _start(engine: Dnd2024CombatEngine, instance: GameInstance) -> None:
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin()],
    }, SequenceRng([20, 1]))
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True


def _combat(
    *, items: list[dict], carry: dict[str, int],
) -> tuple[Dnd2024CombatEngine, GameInstance, dict]:
    return _combat_with_equipment(items=items, carry=carry, equip=[])


def _combat_with_equipment(
    *, items: list[dict], carry: dict[str, int], equip: list[str],
) -> tuple[Dnd2024CombatEngine, GameInstance, dict]:
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


def _broken_bundle(tmp_path: Path, item_definition: dict) -> Dnd2024Runtime:
    source = Path(__file__).resolve().parents[2] / "templates" / "rulesets" / "dnd2024_srd"
    destination = tmp_path / "rulesets" / "dnd2024_srd"
    shutil.copytree(source, destination)
    target = destination / "content" / "items" / "review_invalid_item.json"
    target.write_text(json.dumps(item_definition, ensure_ascii=False), encoding="utf-8")
    return Dnd2024Runtime(tmp_path / "rulesets")


_INVALID_ITEMS = [
    pytest.param(
        {
            "schema_version": 1, "kind": "item", "id": "bad_recharge",
            "source_ref": "test:item:bad_recharge", "automation_level": "deterministic",
            "item_type": "wand", "charges": {"max": 3},
            "recharge": {"on": "sunrise", "amount": 1},
        },
        "on must be", id="unknown-recharge-trigger",
    ),
    pytest.param(
        {
            "schema_version": 1, "kind": "item", "id": "bad_activation",
            "source_ref": "test:item:bad_activation", "automation_level": "deterministic",
            "item_type": "potion",
            "activation": {
                "cost": "reaction", "target": "self",
                "effect": {"mode": "healing", "healing": "1d4"},
            },
        },
        "cost must be", id="invalid-activation",
    ),
    pytest.param(
        {
            "schema_version": 1, "kind": "item", "id": "bad_consumable",
            "source_ref": "test:item:bad_consumable", "automation_level": "deterministic",
            "item_type": "potion", "consumable": "false",
        },
        "consumable must be a boolean", id="non-bool-consumable",
    ),
    pytest.param(
        {
            "schema_version": 1, "kind": "item", "id": "bad_charges",
            "source_ref": "test:item:bad_charges", "automation_level": "deterministic",
            "item_type": "wand", "charges": {"max": 0},
        },
        "max must be", id="invalid-charges",
    ),
]


@pytest.mark.parametrize(("item_definition", "message"), _INVALID_ITEMS)
def test_invalid_item_declarations_fail_at_the_real_load_boundary(
    tmp_path: Path, item_definition: dict, message: str,
) -> None:
    runtime = _broken_bundle(tmp_path, item_definition)
    with pytest.raises(ValueError, match=message):
        runtime.load_bundle("en")

    # 装载失败后，战斗/探索/休息都无法拿到该 bundle，因而不能绕过。
    _, sheet = _runtime_sheet()
    with pytest.raises(ValueError, match=message):
        runtime.complete_rest(None, sheet, "long", {})
    with pytest.raises(ValueError, match=message):
        Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    with pytest.raises(ValueError, match=message):
        Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")


def test_valid_recharge_triggers_and_the_real_srd_bundle_still_load() -> None:
    runtime = Dnd2024Runtime()
    for trigger in ("dawn", "short_rest", "none"):
        definition = _wand(recharge={"on": trigger} if trigger != "none" else {"on": "none"})
        definition["id"] = f"wand_{trigger}"
        _install_item(runtime, definition)
    assert runtime.load_bundle("en") is not None
    assert Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())


def test_exploration_rejects_non_bool_consumable_without_partial_state() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _potion())
    _carry(sheet, {POTION: 1})
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    canonical = sheet["ruleset_character"]
    canonical["resources"]["hp"] = 5

    for invalid in ("false", "true", 0, 1, [], {}):
        item = _potion(consumable=invalid)
        item["id"] = f"invalid_consumable_{type(invalid).__name__}_{len(str(invalid))}"
        engine.bundle.entities["item"][item["id"]] = item
        _carry(instance.get_character_sheet("gm"), {POTION: 1, f"item:{item['id']}": 1})
        before = deepcopy(instance.get_character_sheet("gm"))
        result = engine.resolve_intent(instance, {
            "intent_id": f"invalid-{item['id']}",
            "type": "exploration.activate_item", "expected_version": 0,
            "submitted_by": "gm", "actor_id": "player:gm",
            "item_ref": f"item:{item['id']}",
        }, SequenceRng([]))
        assert result["ok"] is False
        assert "consumable must be a boolean" in str(result["error"])
        assert instance.get_character_sheet("gm") == before


def test_exploration_strict_bool_consumable_controls_consumption() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _potion(consumable=True))
    _carry(sheet, {POTION: 2})
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    sheet["ruleset_character"]["resources"]["hp"] = 5
    result = engine.resolve_intent(instance, {
        "intent_id": "consume-true", "type": "exploration.activate_item",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": POTION,
    }, SequenceRng([1, 1]))
    events = engine.apply_batch(instance, result["event_batch"])
    assert events["applied"] is True
    assert [row["qty"] for row in instance.get_character_sheet("gm")["inventory"]] == [1]

    non_consumable = _potion(consumable=False)
    non_consumable["id"] = "reusable_potion"
    engine.bundle.entities["item"]["reusable_potion"] = non_consumable
    _carry(instance.get_character_sheet("gm"), {"item:reusable_potion": 1})
    result = engine.resolve_intent(instance, {
        "intent_id": "consume-false", "type": "exploration.activate_item",
        "expected_version": 1, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": "item:reusable_potion",
    }, SequenceRng([1, 1]))
    engine.apply_batch(instance, result["event_batch"])
    assert [row["qty"] for row in instance.get_character_sheet("gm")["inventory"]] == [1]


def test_recharge_clamps_corrupt_current_with_audit() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _wand(recharge={"on": "dawn", "amount": 1}))
    _carry(sheet, {WAND: 1})
    _reconcile(runtime, sheet)
    canonical = sheet["ruleset_character"]
    canonical["equipment"]["item_state"][WAND]["charges"]["current"] = 9

    events = recharge_for_rest(
        canonical, runtime.load_bundle("en"), {WAND}, "long",
    )

    # 旧存档的 current 高于当前内容上限：收敛到上限并保留原值审计，
    # 不再让整次休息失败。
    assert canonical["equipment"]["item_state"][WAND]["charges"]["current"] == 3
    assert events[0]["before"] == 9
    assert events[0]["after"] == 3


def test_recharge_isolates_a_corrupt_item_from_healthy_ones() -> None:
    runtime, sheet = _runtime_sheet()
    good = _wand(recharge={"on": "dawn", "amount": 1}, item_id="aaa_wand")
    bad = _wand(recharge={"on": "dawn", "amount": 1}, item_id="zzz_wand")
    _install_item(runtime, good)
    _install_item(runtime, bad)
    _carry(sheet, {"item:aaa_wand": 1, "item:zzz_wand": 1})
    _reconcile(runtime, sheet)
    canonical = sheet["ruleset_character"]
    state = canonical["equipment"]["item_state"]
    state["item:aaa_wand"]["charges"]["current"] = 1
    state["item:zzz_wand"]["charges"]["current"] = 9

    events = recharge_for_rest(
        canonical, runtime.load_bundle("en"),
        {"item:aaa_wand", "item:zzz_wand"}, "long",
    )

    assert state["item:aaa_wand"]["charges"]["current"] == 2
    assert state["item:zzz_wand"]["charges"]["current"] == 3
    assert {event["item_ref"] for event in events} == {
        "item:aaa_wand", "item:zzz_wand",
    }


def test_zero_quantity_never_initialises_or_recharges() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _wand(recharge={"on": "dawn", "amount": 1}))
    _carry(sheet, {WAND: 0})
    _reconcile(runtime, sheet)
    canonical = sheet["ruleset_character"]
    assert WAND not in (canonical.get("equipment", {}).get("item_state") or {})

    canonical.setdefault("equipment", {}).setdefault("item_state", {})[WAND] = {
        "charges": {"current": 0},
    }
    result = runtime.complete_rest(None, sheet, "long", {})
    assert result["character"]["ruleset_character"]["equipment"]["item_state"][WAND][
        "charges"
    ]["current"] == 0
    assert not [e for e in result["events"] if e["type"] == "recharge_item"]

    _carry(sheet, {WAND: 1})
    assert Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    ) is None
    assert canonical["equipment"]["item_state"][WAND]["charges"]["current"] == 0
    canonical["equipment"]["item_state"][WAND]["charges"]["current"] = 1
    assert Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    ) is None
    assert canonical["equipment"]["item_state"][WAND]["charges"]["current"] == 1


def test_zero_quantity_cannot_use_a_residual_item_state() -> None:
    engine, instance, _sheet = _combat(items=[_wand()], carry={WAND: 0})
    instance.get_character_sheet("gm")["ruleset_character"].setdefault(
        "equipment", {},
    ).setdefault("item_state", {})[WAND] = {"charges": {"current": 3}}
    result = engine.resolve_intent(instance, {
        "intent_id": "zero-copy", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": WAND,
    }, SequenceRng([]))
    assert result["ok"] is False
    assert "not carried" in str(result["error"])


def test_combat_and_exploration_item_healing_are_isomorphic() -> None:
    engine, combat_instance, _sheet = _combat(items=[_potion()], carry={POTION: 1})
    combat_instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] = 5
    combat = engine.resolve_intent(combat_instance, {
        "intent_id": "heal-combat", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": POTION,
    }, SequenceRng([1, 1]))

    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _potion())
    _carry(sheet, {POTION: 1})
    _reconcile(runtime, sheet)
    explore_instance = _instance(sheet)
    exploration = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    sheet["ruleset_character"]["resources"]["hp"] = 5
    explore = exploration.resolve_intent(explore_instance, {
        "intent_id": "heal-explore", "type": "exploration.activate_item",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": POTION,
    }, SequenceRng([1, 1]))

    def resource(result: dict) -> dict:
        return next(
            e for e in result["event_batch"]["events"] if e["type"] == "resource.changed"
        )

    combat_event = resource(combat)
    explore_event = resource(explore)
    for key in (
        "resource", "target_id", "delta", "amount", "healing", "rolls",
        "item_ref", "heal_before", "heal_after", "hp_after",
    ):
        assert combat_event[key] == explore_event[key], key
    assert combat_event["delta"] == 4
    assert combat_event["heal_before"] == 5
    assert combat_event["heal_after"] == 9


def test_combat_and_exploration_block_healing_with_the_same_event() -> None:
    engine, combat_instance, _sheet = _combat(items=[_potion()], carry={POTION: 1})
    canonical = combat_instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 5
    canonical.setdefault("conditions", {})["cannot_regain_hp"] = {"source_ref": "test"}
    combat = engine.resolve_intent(combat_instance, {
        "intent_id": "blocked-combat", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": POTION,
    }, SequenceRng([4, 4]))

    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _potion())
    _carry(sheet, {POTION: 1})
    _reconcile(runtime, sheet)
    explore_instance = _instance(sheet)
    exploration = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    sheet["ruleset_character"]["resources"]["hp"] = 5
    sheet["ruleset_character"].setdefault("conditions", {})["cannot_regain_hp"] = {
        "source_ref": "test",
    }
    explore = exploration.resolve_intent(explore_instance, {
        "intent_id": "blocked-explore", "type": "exploration.activate_item",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": POTION,
    }, SequenceRng([4, 4]))

    def resource(result: dict) -> dict:
        return next(
            e for e in result["event_batch"]["events"] if e["type"] == "resource.changed"
        )

    combat_event = resource(combat)
    explore_event = resource(explore)
    assert combat_event["delta"] == 0
    assert explore_event["delta"] == 0
    assert combat_event["blocked_by"] == "cannot_regain_hp"
    assert explore_event["blocked_by"] == "cannot_regain_hp"
    assert combat_event["heal_before"] == combat_event["heal_after"] == 5
    assert explore_event["heal_before"] == explore_event["heal_after"] == 5
    assert combat_event["heal_after"] == explore_event["heal_after"]
    assert [e["type"] for e in combat["event_batch"]["events"]].count(
        "dnd2024.item.consumed"
    ) == 1
    assert [e["type"] for e in explore["event_batch"]["events"]].count(
        "dnd2024.item.consumed"
    ) == 1
    assert engine.apply_batch(combat_instance, combat["event_batch"])["applied"] is True
    assert exploration.apply_batch(explore_instance, explore["event_batch"])["applied"] is True
    assert combat_instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] == 5
    assert explore_instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] == 5


def test_available_intents_project_only_legal_item_activations() -> None:
    engine, instance, _sheet = _combat(items=[_wand(), _potion()], carry={WAND: 1, POTION: 1})
    actions = engine.available_intents(instance, "gm")
    activation = next(item for item in actions if item["type"] == "activate_item")
    assert {item["item_ref"] for item in activation["items"]} == {WAND, POTION}
    assert all(item["name"] for item in activation["items"])
    assert activation["targets"]

    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["equipment"]["item_state"][WAND]["charges"]["current"] = 0
    actions = engine.available_intents(instance, "gm")
    activation = next(item for item in actions if item["type"] == "activate_item")
    assert {item["item_ref"] for item in activation["items"]} == {POTION}


def test_down_party_members_remain_valid_item_targets() -> None:
    runtime, sheet = _runtime_sheet()
    ally_sheet = deepcopy(sheet)
    _install_item(runtime, _ally_potion())
    _carry(sheet, {ALLY_POTION: 1})
    _reconcile(runtime, sheet)
    instance = GameInstance(
        game_key=("test", "dnd2024-item-remediation", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": sheet}
    instance.players["ally"] = {"character_name": "Briar", "character_sheet": ally_sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    _start(engine, instance)

    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = list(combat["initiative"]).index("player:gm")
    ally = instance.get_character_sheet("ally")["ruleset_character"]
    ally["resources"]["hp"] = 0
    ally.setdefault("conditions", {})["unconscious"] = {"source": "zero_hp"}

    actions = engine.available_intents(instance, "gm")
    activation = next(item for item in actions if item["type"] == "activate_item")
    assert "player:ally" in {target["actor_id"] for target in activation["targets"]}

    resolved = engine.resolve_intent(instance, {
        "intent_id": "heal-downed-ally", "type": "activate_item",
        "expected_version": activation["expected_version"], "submitted_by": "gm",
        "actor_id": "player:gm", "item_ref": ALLY_POTION, "target_id": "player:ally",
    }, SequenceRng([1, 1]))
    assert resolved["ok"] is True
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert instance.get_character_sheet("ally")["ruleset_character"]["resources"]["hp"] == 4

    # dead 才是非法目标；投影与校验必须保持同一边界。
    combat = ruleset_runtime.state(instance)["combat"]
    combat["economy"]["action"] = 1
    _carry(instance.get_character_sheet("gm"), {ALLY_POTION: 1})
    ally = instance.get_character_sheet("ally")["ruleset_character"]
    ally.setdefault("conditions", {})["dead"] = {"source": "test"}
    actions = engine.available_intents(instance, "gm")
    activation = next(item for item in actions if item["type"] == "activate_item")
    assert "player:ally" not in {target["actor_id"] for target in activation["targets"]}


def test_exploration_available_intents_project_carried_potions() -> None:
    runtime, sheet = _runtime_sheet()
    _install_item(runtime, _potion())
    _carry(sheet, {POTION: 1})
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    actions = engine.available_intents(instance, "player:gm")
    activation = next(item for item in actions if item["type"] == "exploration.activate_item")
    assert [item["item_ref"] for item in activation["items"]] == [POTION]

    _carry(instance.get_character_sheet("gm"), {POTION: 0})
    assert not [
        item for item in engine.available_intents(instance, "player:gm")
        if item["type"] == "exploration.activate_item"
    ]


def test_failed_apply_leaves_sheet_state_and_ledger_unchanged() -> None:
    engine, instance, _sheet = _combat(items=[_potion()], carry={POTION: 1})
    instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"] = 5
    resolved = engine.resolve_intent(instance, {
        "intent_id": "fault-injection", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": POTION,
    }, SequenceRng([2, 2]))
    tampered = dict(resolved["event_batch"])
    tampered["events"] = [*resolved["event_batch"]["events"], {
        "type": "dnd2024.item.consumed", "actor_id": "player:gm",
        "item_ref": POTION, "amount": 1, "injected": True,
    }]
    sheet_before = deepcopy(instance.get_character_sheet("gm"))
    state_before = deepcopy(ruleset_runtime.state(instance))
    ledger_before = deepcopy(ruleset_runtime.event_ledger(instance))
    with pytest.raises(EventBatchError):
        engine.apply_batch(instance, tampered)
    assert instance.get_character_sheet("gm") == sheet_before
    assert ruleset_runtime.state(instance) == state_before
    assert ruleset_runtime.event_ledger(instance) == ledger_before
