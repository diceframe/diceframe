"""D3b：充能耗尽销毁。

语义边界（任务书 §1.2）：只有「因激活而恰好耗尽」触发判定；未声明
``destroy_on_empty`` 的物品一次随机数都不消费；判定位于全部效果之后。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

import pytest

from src.commands.state_items import remove_destroyed_item
from src.engine.game_instance import GameInstance
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.exploration.engine import Dnd2024ExplorationEngine
from src.rulesets.dnd2024.items.activation import validate_item_declarations
from src.rulesets.dnd2024.items.charges import (
    ChargesError,
    destroy_check_events,
    should_check_destroy,
    validate_destroy_declaration,
)
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


@dataclass
class StrictRng:
    """哨兵骰：比预期多掷一次就失败，用来证明「不该掷时零消费」。"""

    values: list[int] = field(default_factory=list)
    consumed: list[tuple[int, int]] = field(default_factory=list)

    def randint(self, minimum: int, maximum: int) -> int:
        if not self.values:
            raise AssertionError("rng consumed more rolls than the contract allows")
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        self.consumed.append((minimum, maximum))
        return value


WAND = "item:wand_of_destruction"
TWIN = "item:wand_of_destruction_twin"


def _runtime_sheet() -> tuple[Dnd2024Runtime, dict]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian")
    sheet = runtime.finalize_character(None, {**preset["draft"], "locale": "en", "name": "Arden"})
    return runtime, sheet


def _install(runtime: Dnd2024Runtime, definition: dict) -> str:
    runtime.load_bundle("en").entities["item"][definition["id"]] = definition
    return f"item:{definition['id']}"


def _wand(
    *, item_id: str = "wand_of_destruction", charges: int = 1,
    destroy: dict | None = None, attuned: bool = False,
) -> dict:
    definition = {
        "schema_version": 1, "kind": "item", "id": item_id,
        "source_ref": f"test:item:{item_id}", "automation_level": "deterministic",
        "item_type": "wand", "consumable": False, "charges": {"max": charges},
        "activation": {
            "cost": "bonus_action", "target": "self", "charge_cost": 1,
            "effect": {"mode": "healing", "healing": "1d6+1"},
        },
    }
    if destroy is not None:
        definition["destroy_on_empty"] = destroy
    if attuned:
        definition["requires_attunement"] = True
    return definition


def _charged_potion(*, item_id: str = "charged_destroy_potion") -> dict:
    """探索层只允许药水类激活，所以 T6 用带充能的药水验证同一判定。"""

    return {
        "schema_version": 1, "kind": "item", "id": item_id,
        "source_ref": f"test:item:{item_id}", "automation_level": "deterministic",
        "item_type": "potion", "consumable": True, "charges": {"max": 1},
        "activation": {
            "cost": "action", "target": "self", "charge_cost": 1,
            "effect": {"mode": "healing", "healing": "2d4+2"},
        },
        "destroy_on_empty": {"die": "1d20", "threshold": 1},
    }


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


def _combat(
    *, items: list[dict], carry: dict[str, int], attune: list[str] | None = None,
) -> tuple[Dnd2024CombatEngine, GameInstance, dict]:
    runtime, sheet = _runtime_sheet()
    for definition in items:
        _install(runtime, definition)
    sheet["inventory"] = [
        {"name": ref, "item_ref": ref, "type": "wand", "qty": qty}
        for ref, qty in carry.items()
    ]
    Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    )
    instance = GameInstance(
        game_key=("test", "dnd2024-item-destroy", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])
    if attune:
        canonical = instance.get_character_sheet("gm")["ruleset_character"]
        canonical.setdefault("attunement", {})["refs"] = list(attune)
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin()],
    }, StrictRng([20, 1]))
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return engine, instance, sheet


def _activate(
    engine: Dnd2024CombatEngine, instance: GameInstance, ref: str, rng: StrictRng,
) -> dict:
    return engine.resolve_intent(instance, {
        "intent_id": "destroy-1", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "player:gm",
        "item_ref": ref,
    }, rng)


def _state(sheet: dict, ref: str) -> dict:
    return sheet["ruleset_character"]["equipment"]["item_state"][ref]["charges"]


def _types(result: dict) -> list[str]:
    return [str(event["type"]) for event in result["event_batch"]["events"]]


# ---------- 声明校验（T7） ----------


def test_destroy_declaration_defaults_and_bounds() -> None:
    assert validate_destroy_declaration({}) is None
    assert validate_destroy_declaration({"destroy_on_empty": {}}) == {
        "die": "1d20", "threshold": 1,
    }
    assert validate_destroy_declaration(
        {"destroy_on_empty": {"die": "1d20", "threshold": 20}}
    ) == {"die": "1d20", "threshold": 20}


@pytest.mark.parametrize("declaration", [
    {"die": "1d12"},
    {"die": "1d20", "threshold": 0},
    {"die": "1d20", "threshold": 21},
    {"die": "1d20", "threshold": True},
    {"die": "1d20", "threshold": "1"},
    {"die": "1d20", "threshold": 1, "explode": 2},
    "always",
])
def test_destroy_declaration_fails_closed(declaration) -> None:
    with pytest.raises(ChargesError):
        validate_destroy_declaration({"destroy_on_empty": declaration})


class _ItemBundle:
    def __init__(self, *items: dict) -> None:
        self._items = list(items)

    def list(self, kind: str) -> list[dict]:
        return list(self._items) if kind == "item" else []


def test_bundle_load_rejects_a_bad_or_chargeless_destroy_declaration() -> None:
    """写错的声明、以及没有充能却声明销毁，都在加载边界拒绝。"""

    validate_item_declarations(_ItemBundle(_wand(destroy={"die": "1d20", "threshold": 3})))
    with pytest.raises(ChargesError, match="die must be 1d20"):
        validate_item_declarations(_ItemBundle(_wand(destroy={"die": "1d6"})))
    chargeless = _wand(destroy={"die": "1d20", "threshold": 1})
    del chargeless["charges"]
    chargeless["activation"]["charge_cost"] = None
    with pytest.raises(ChargesError, match="destroy_on_empty without charges"):
        validate_item_declarations(_ItemBundle(chargeless))


def test_a_bad_destroy_declaration_is_rejected_before_any_roll() -> None:
    """绕过加载校验的坏声明也只能得到可读的拒绝，不能在结算中途抛出。"""

    wand = _wand(charges=3, destroy={"die": "1d20", "threshold": 1})
    engine, instance, _sheet = _combat(items=[wand], carry={WAND: 1})
    wand["destroy_on_empty"] = {"die": "1d6"}

    rng = StrictRng([])
    result = _activate(engine, instance, WAND, rng)

    assert result["ok"] is False
    assert result["code"] == "INVALID_INTENT"
    assert "1d20" in result["error"]
    assert rng.consumed == []


def test_destroyed_cleanup_takes_the_anonymous_row_the_counts_derive() -> None:
    """没有显式 ref 的行按 item:<item_key> 计数，销毁时也要一起删掉。"""

    sheet = {
        "inventory": [
            {"name": "Wand", "item_key": "wand_of_destruction", "qty": 1},
            {"name": "Wand", "item_key": "wand_of_destruction", "item_ref": TWIN, "qty": 1},
        ],
        "equipment": [{"name": "Wand", "item_key": "wand_of_destruction", "qty": 1}],
    }

    assert remove_destroyed_item(sheet, WAND) is True
    # 显式 ref 不同的同名行是另一件物品，不受影响。
    assert [row.get("item_ref") for row in sheet["inventory"]] == [TWIN]
    assert sheet["equipment"] == []
    assert remove_destroyed_item(sheet, WAND) is False


def test_should_check_only_when_exactly_emptied() -> None:
    item = {"destroy_on_empty": {"die": "1d20", "threshold": 1}}
    assert should_check_destroy(item, 0) is True
    assert should_check_destroy(item, 1) is False
    assert should_check_destroy(item, None) is False
    assert should_check_destroy({}, 0) is False


# ---------- 战斗内判定（T1–T5、T9） ----------


def test_undeclared_item_never_consumes_a_destroy_roll() -> None:
    engine, instance, sheet = _combat(items=[_wand()], carry={WAND: 1})
    # 只有治疗骰一次；多掷一次 StrictRng 就会抛。
    rng = StrictRng([4])
    resolved = _activate(engine, instance, WAND, rng)
    assert resolved["ok"] is True
    assert "dnd2024.item.destroy_check" not in _types(resolved)
    assert len(rng.consumed) == 1


def test_declared_item_with_charges_left_does_not_roll() -> None:
    engine, instance, sheet = _combat(
        items=[_wand(charges=3, destroy={"die": "1d20", "threshold": 1})],
        carry={WAND: 1},
    )
    rng = StrictRng([4])
    resolved = _activate(engine, instance, WAND, rng)
    assert resolved["ok"] is True
    assert "dnd2024.item.destroy_check" not in _types(resolved)
    assert len(rng.consumed) == 1
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert _state(instance.get_character_sheet("gm"), WAND)["current"] == 2


def test_empty_with_a_roll_above_threshold_keeps_the_item() -> None:
    engine, instance, _sheet = _combat(
        items=[_wand(destroy={"die": "1d20", "threshold": 1})], carry={WAND: 1},
    )
    resolved = _activate(engine, instance, WAND, StrictRng([4, 2]))
    assert resolved["ok"] is True
    check = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "dnd2024.item.destroy_check"
    )
    assert check["roll"] == 2 and check["threshold"] == 1 and check["destroyed"] is False
    assert "dnd2024.item.destroyed" not in _types(resolved)
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    sheet = instance.get_character_sheet("gm")
    assert _state(sheet, WAND)["current"] == 0


def test_empty_on_threshold_destroys_and_unbinds() -> None:
    engine, instance, _sheet = _combat(
        items=[_wand(destroy={"die": "1d20", "threshold": 1}, attuned=True)],
        carry={WAND: 1}, attune=[WAND],
    )
    before = instance.get_character_sheet("gm")
    assert before["ruleset_character"]["attunement"]["refs"] == [WAND]

    resolved = _activate(engine, instance, WAND, StrictRng([4, 1]))
    assert resolved["ok"] is True
    assert _types(resolved).count("dnd2024.item.destroyed") == 1
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True

    sheet = instance.get_character_sheet("gm")
    canonical = sheet["ruleset_character"]
    assert WAND not in canonical["equipment"]["item_refs"]
    assert WAND not in canonical["attunement"]["refs"]
    assert WAND not in (canonical["equipment"].get("item_state") or {})
    assert all(row.get("item_ref") != WAND for row in sheet.get("inventory") or [])


def test_healing_settles_before_the_destroy_check() -> None:
    engine, instance, _sheet = _combat(
        items=[_wand(destroy={"die": "1d20", "threshold": 20})], carry={WAND: 1},
    )
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 4
    resolved = _activate(engine, instance, WAND, StrictRng([5, 10]))
    types = _types(resolved)
    assert types.index("dnd2024.item.destroy_check") > types.index("resource.changed")
    healing = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "resource.changed"
    )
    assert healing["healing"] is True


def test_destroying_one_ref_leaves_a_same_named_copy_alone() -> None:
    engine, instance, _sheet = _combat(
        items=[
            _wand(destroy={"die": "1d20", "threshold": 1}),
            _wand(item_id="wand_of_destruction_twin", destroy={"die": "1d20", "threshold": 1}),
        ],
        carry={WAND: 1, TWIN: 1},
    )
    resolved = _activate(engine, instance, WAND, StrictRng([4, 1]))
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    sheet = instance.get_character_sheet("gm")
    rows = [row.get("item_ref") for row in sheet.get("inventory") or []]
    assert WAND not in rows
    assert TWIN in rows
    assert _state(sheet, TWIN)["current"] == 1


# ---------- 探索层（T6）与回放（T8） ----------


def test_exploration_uses_the_same_event_shape() -> None:
    runtime, sheet = _runtime_sheet()
    potion_ref = _install(runtime, _charged_potion())
    sheet["inventory"] = [
        {"name": potion_ref, "item_ref": potion_ref, "type": "potion", "qty": 1},
    ]
    Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    )
    instance = GameInstance(
        game_key=("test", "dnd2024-item-destroy", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")

    resolved = engine.resolve_intent(instance, {
        "intent_id": "explore-destroy", "type": "exploration.activate_item",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "gm",
        "item_ref": potion_ref,
    }, StrictRng([3, 3, 1]))
    assert resolved["ok"] is True
    check = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "dnd2024.item.destroy_check"
    )
    assert check["roll"] == 1 and check["destroyed"] is True
    assert "dnd2024.item.destroyed" in _types(resolved)


def test_replaying_the_same_batch_does_not_reroll_or_duplicate() -> None:
    engine, instance, _sheet = _combat(
        items=[_wand(destroy={"die": "1d20", "threshold": 1})], carry={WAND: 1},
    )
    resolved = _activate(engine, instance, WAND, StrictRng([4, 1]))
    first = engine.apply_batch(instance, resolved["event_batch"])
    assert first["applied"] is True
    second = engine.apply_batch(instance, deepcopy(resolved["event_batch"]))
    assert second["duplicate"] is True
    assert second["applied"] is False
    sheet = instance.get_character_sheet("gm")
    assert [row.get("item_ref") for row in sheet.get("inventory") or []].count(WAND) == 0


def test_destroy_check_events_are_pure() -> None:
    item = {"destroy_on_empty": {"die": "1d20", "threshold": 2}}
    assert destroy_check_events(
        item, actor_id="player:gm", item_ref=WAND, remaining_charges=1, rng=StrictRng([]),
    ) == []
    events = destroy_check_events(
        item, actor_id="player:gm", item_ref=WAND, remaining_charges=0,
        rng=StrictRng([2]),
    )
    assert [event["type"] for event in events] == [
        "dnd2024.item.destroy_check", "dnd2024.item.destroyed",
    ]
