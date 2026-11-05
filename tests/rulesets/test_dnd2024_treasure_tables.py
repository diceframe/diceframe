"""D4 random treasure table contract tests (T1-T13)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.economy import filter_unconfirmed_purchase_grants
from src.engine.game_instance import GameInstance
from src.engine.modules import economy_state, ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.content.catalog import catalog_from_sources
from src.rulesets.dnd2024.content.rewards import reward_intent_from_ref
from src.rulesets.dnd2024.combat.primitives import INTENT_TYPES as COMBAT_INTENT_TYPES
from src.rulesets.dnd2024.exploration.engine import Dnd2024ExplorationEngine
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.treasure import (
    Dnd2024TreasureCatalog,
    TreasureTableError,
    resolve_table,
    validate_treasure_tables,
)

TABLE = "treasure_table:test_gemstones"
NESTED = "treasure_table:test_trinkets"
ITEM = "item:dagger"
BOW = "item:shortbow"


class SequenceRng:
    def __init__(self, values: list[int]) -> None:
        self.values = values

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum, (minimum, value, maximum)
        return value


class FakeBundle:
    def __init__(self, tables: list[dict], item_ids: tuple[str, ...] = ("dagger", "shortbow")):
        self._entities = {
            "treasure_table": {table["id"]: table for table in tables},
            "item": {item_id: {"id": item_id} for item_id in item_ids},
        }

    def list(self, kind: str) -> list[dict]:
        return [deepcopy(value) for value in self._entities.get(kind, {}).values()]

    def get(self, kind: str, entity_id: str) -> dict | None:
        value = self._entities.get(kind, {}).get(entity_id)
        return deepcopy(value) if value is not None else None


def _table(table_id: str, entries: list[dict], *, roll: str = "1d6", **extra) -> dict:
    return {
        "schema_version": 1, "kind": "treasure_table", "id": table_id,
        "source_ref": f"test:treasure_table:{table_id}", "automation_level": "deterministic",
        "name": table_id, "roll": roll, "entries": entries, **extra,
    }


def _entry(start: int, end: int, result: dict) -> dict:
    return {"from": start, "to": end, "result": result}


def _item_result(qty: int = 1, item_ref: str = ITEM) -> dict:
    return {"items": [{"item_ref": item_ref, "qty": qty}]}


def _valid_tables() -> list[dict]:
    return [
        _table("test_gemstones", [
            _entry(1, 1, {"coins_gp": 10}),
            _entry(2, 2, _item_result()),
            _entry(3, 3, {"table_ref": NESTED}),
            _entry(4, 4, {"coins_gp": 25}),
            _entry(5, 5, _item_result(2)),
            _entry(6, 6, {"coins_gp": 50}),
        ]),
        _table("test_trinkets", [
            _entry(1, 1, {"coins_gp": 5}),
            _entry(2, 2, _item_result()),
            _entry(3, 3, {"coins_gp": 15}),
            _entry(4, 4, _item_result(item_ref=BOW)),
        ], roll="1d4"),
    ]


def _catalog(tables: list[dict] | None = None) -> Dnd2024TreasureCatalog:
    return validate_treasure_tables(FakeBundle(tables or _valid_tables()))


def _runtime_sheet() -> tuple[Dnd2024Runtime, dict]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian")
    sheet = runtime.finalize_character(None, {**preset["draft"], "locale": "en", "name": "Arden"})
    return runtime, sheet


def _instance(sheet: dict) -> GameInstance:
    instance = GameInstance(
        game_key=("test", "dnd2024-treasure-tables", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])
    return instance


def _test_bundle(runtime: Dnd2024Runtime, locale: str) -> object:
    """Real content bundle plus explicit test-only treasure tables.

    Test tables must never ship in the SRD template; this is the single
    injection point the tests use.
    """

    bundle = runtime.load_bundle(locale)
    entities = deepcopy(bundle.entities)
    entities["treasure_table"] = {
        table["id"]: table for table in _valid_tables()
    }
    return replace(bundle, entities=entities)


def _exploration() -> tuple[Dnd2024ExplorationEngine, GameInstance, dict]:
    runtime, sheet = _runtime_sheet()
    instance = _instance(sheet)
    return Dnd2024ExplorationEngine(_test_bundle(runtime, "en")), instance, sheet


def _intent(version: int, table_ref: str = TABLE, **extra) -> dict:
    return {
        "intent_id": "treasure-1", "type": "exploration.roll_treasure",
        "expected_version": version, "submitted_by": "gm",
        "actor_id": "player:gm", "table_ref": table_ref, **extra,
    }


def _invalid(result: dict) -> str:
    assert result["ok"] is False, result
    return str(result["error"])


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


# N4: localized labels and structured currency never change canonical identity.
def test_n4_projection_localizes_labels_without_changing_item_refs() -> None:
    runtime, sheet = _runtime_sheet()
    instance = _instance(sheet)
    english = Dnd2024ExplorationEngine(_test_bundle(runtime, "en"))
    chinese = Dnd2024ExplorationEngine(_test_bundle(runtime, "zh-CN"))

    en_event = next(
        event for event in english.resolve_intent(instance, _intent(0), SequenceRng([5]))["event_batch"]["events"]
        if event["type"] == "dnd2024.treasure.rolled"
    )
    zh_event = next(
        event for event in chinese.resolve_intent(instance, _intent(0), SequenceRng([5]))["event_batch"]["events"]
        if event["type"] == "dnd2024.treasure.rolled"
    )

    assert en_event["result"]["items"] == [
        {"item_ref": "item:dagger", "qty": 2, "label": "Dagger"}
    ]
    assert zh_event["result"]["items"] == [
        {"item_ref": "item:dagger", "qty": 2, "label": "匕首"}
    ]
    assert en_event["result"]["items"][0]["item_ref"] == zh_event["result"]["items"][0]["item_ref"]

    coins = next(
        event for event in english.resolve_intent(instance, _intent(0), SequenceRng([4]))["event_batch"]["events"]
        if event["type"] == "dnd2024.treasure.rolled"
    )
    assert coins["result"]["coins_gp"] == 25
    assert coins["result"]["coins"] == {"amount": 25, "unit": "gp"}


# T1: valid tables load with full coverage and multiple result shapes.
def test_t1_valid_tables_cover_full_range() -> None:
    catalog = _catalog()
    assert set(catalog.tables) == {"test_gemstones", "test_trinkets"}
    assert catalog.tables["test_gemstones"].sides == 6
    assert catalog.tables["test_gemstones"].entries[0].from_value == 1
    assert catalog.tables["test_gemstones"].entries[-1].to_value == 6
    assert catalog.tables["test_trinkets"].entries[-1].to_value == 4


# T2: interval boundaries are inclusive and out-of-range/holes fail closed.
def test_t2_interval_boundaries_reject_holes_and_overflow() -> None:
    with pytest.raises(TreasureTableError, match="outside 1-6"):
        _catalog([_table("bad", [_entry(0, 0, {"coins_gp": 1}), _entry(1, 6, {"coins_gp": 2})])])
    with pytest.raises(TreasureTableError, match="outside 1-6"):
        _catalog([_table("bad", [_entry(1, 7, {"coins_gp": 1})])])
    with pytest.raises(TreasureTableError, match="gaps or overlaps"):
        _catalog([_table("bad", [_entry(1, 2, {"coins_gp": 1}), _entry(4, 6, {"coins_gp": 2})])])
    with pytest.raises(TreasureTableError, match="gaps or overlaps"):
        _catalog([_table("bad", [_entry(1, 3, {"coins_gp": 1}), _entry(3, 6, {"coins_gp": 2})])])
    with pytest.raises(TreasureTableError, match="must be an integer"):
        _catalog([_table("bad", [_entry(1, 6.5, {"coins_gp": 1})])])
    with pytest.raises(TreasureTableError, match="must be an integer"):
        _catalog([_table("bad", [_entry(1, 6, {"coins_gp": 1.5})])])


# T2: inclusive boundaries hit the intended entry and adjacent entries never leak.
def test_t2_boundary_rolls_stay_in_their_interval() -> None:
    catalog = _catalog()
    assert catalog.resolve(TABLE, 1) == {"coins_gp": 10}
    assert catalog.resolve(TABLE, 2) == _item_result()
    assert catalog.resolve(TABLE, 3) == {"table_ref": NESTED}
    assert catalog.resolve(TABLE, 4) == {"coins_gp": 25}
    assert catalog.resolve(TABLE, 5) == _item_result(2)
    assert catalog.resolve(TABLE, 6) == {"coins_gp": 50}
    assert catalog.resolve(NESTED, 1) == {"coins_gp": 5}
    assert catalog.resolve(NESTED, 4) == _item_result(item_ref=BOW)


# T3/T10: each nesting layer rolls once and the path carries the final result.
def test_t3_nested_rolls_layer_by_layer() -> None:
    engine, instance, _sheet = _exploration()
    resolved = engine.resolve_intent(instance, _intent(0), SequenceRng([3, 4]))
    assert resolved["ok"] is True, resolved
    event = next(e for e in resolved["events"] if e["type"] == "dnd2024.treasure.rolled")
    assert event["table_ref"] == TABLE
    assert event["path"] == [
        {"table_ref": TABLE, "roll": 3, "from": 3, "to": 3},
        {"table_ref": NESTED, "roll": 4, "from": 4, "to": 4},
    ]
    assert event["result"] == {
        "items": [{"item_ref": BOW, "qty": 1, "label": "Shortbow"}],
    }


# T4: cycles and excessive nesting are rejected at load time.
def test_t4_nesting_cycles_and_depth_fail_closed() -> None:
    cycle_a = _table("a", [_entry(1, 2, {"table_ref": "treasure_table:b"})], roll="1d2")
    cycle_b = _table("b", [_entry(1, 2, {"table_ref": "treasure_table:a"})], roll="1d2")
    with pytest.raises(TreasureTableError, match="cycle"):
        _catalog([cycle_a, cycle_b])
    tables = []
    for index in range(1, 10):
        target = (
            {"table_ref": f"treasure_table:t{index + 1}"}
            if index < 9 else {"coins_gp": 1}
        )
        tables.append(_table(f"t{index}", [_entry(1, 2, target)], roll="1d2"))
    with pytest.raises(TreasureTableError, match="exceeds 8 levels"):
        _catalog(tables)


# T5: selection is pure and independent of entry order.
def test_t5_resolution_is_pure_and_order_independent() -> None:
    catalog = _catalog()
    first = resolve_table(catalog.tables, "test_gemstones", 4)
    reordered = _valid_tables()
    reordered[0]["entries"] = list(reversed(reordered[0]["entries"]))
    other = _catalog(reordered)
    assert resolve_table(other.tables, "test_gemstones", 4) == first == {"coins_gp": 25}
    first["coins_gp"] = 99
    assert resolve_table(catalog.tables, "test_gemstones", 4) == {"coins_gp": 25}


# T6/T7: replay is duplicate, no re-roll, and no inventory/economy award.
def test_t6_replay_is_stable_and_does_not_award() -> None:
    engine, instance, _sheet = _exploration()
    sheet_before = deepcopy(instance.get_character_sheet("gm"))
    economy_before = deepcopy(economy_state.state(instance))
    resolved = engine.resolve_intent(instance, _intent(0), SequenceRng([2]))
    assert resolved["ok"] is True
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    replay = engine.apply_batch(instance, resolved["event_batch"])
    assert replay["duplicate"] is True
    assert resolved["event_batch"]["events"][1]["path"] == [
        {"table_ref": TABLE, "roll": 2, "from": 2, "to": 2},
    ]
    assert instance.get_character_sheet("gm")["inventory"] == sheet_before["inventory"]
    assert economy_state.state(instance) == economy_before
    assert not any(
        event["type"] in {"dnd2024.item.granted", "economy.changed"}
        for event in resolved["event_batch"]["events"]
    )


# T7b: B2 audit-only ruling — both item and currency results must leave every
# asset and reward proposal untouched; the audit event is never an award path.
def _asset_snapshot(instance: GameInstance) -> dict:
    sheet = deepcopy(instance.get_character_sheet("gm"))
    canonical = sheet["ruleset_character"]
    return {
        "inventory": sheet["inventory"],
        "equipment": sheet["equipment"],
        "equipment_canonical": canonical["equipment"],
        "resources": canonical["resources"],
        "attunement": canonical["attunement"],
        "currency": sheet.get("currency"),
        "economy": deepcopy(economy_state.state(instance)),
    }


@pytest.mark.parametrize("roll_value", [2, 4, 5, 6])
def test_t7b_roll_leaves_assets_and_reward_proposals_untouched(roll_value: int) -> None:
    engine, instance, _sheet = _exploration()
    before = _asset_snapshot(instance)
    resolved = engine.resolve_intent(instance, _intent(0), SequenceRng([roll_value]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True

    event = next(e for e in resolved["event_batch"]["events"] if e["type"] == "dnd2024.treasure.rolled")
    assert any(key in event["result"] for key in ("items", "coins_gp"))
    assert _asset_snapshot(instance) == before
    assert not any(
        event_type.startswith(("economy.", "dnd2024.item."))
        for event_type in (entry["type"] for entry in resolved["event_batch"]["events"])
    )


# T7: roll output never bypasses the reward proposal / GM settlement gate.
def test_t7_roll_does_not_touch_reward_authority() -> None:
    engine, instance, _sheet = _exploration()
    sheet_before = deepcopy(instance.get_character_sheet("gm"))
    resolved = engine.resolve_intent(instance, _intent(0), SequenceRng([5]))
    assert resolved["ok"] is True
    engine.apply_batch(instance, resolved["event_batch"])
    sheet_after = instance.get_character_sheet("gm")
    assert sheet_after["inventory"] == sheet_before["inventory"]
    assert sheet_after["equipment"] == sheet_before["equipment"]
    assert sheet_after["ruleset_character"]["resources"] == sheet_before["ruleset_character"]["resources"]
    assert not any(
        event["type"].startswith("economy.") for event in resolved["event_batch"]["events"]
    )
    catalog = catalog_from_sources([("module:test", {"item": {"dagger": {
        "item_id": "dagger", "name": "Dagger", "source_ref": "module:test",
        "category": "equipment", "ruleset_item_ref": ITEM,
    }}})])
    event = next(e for e in resolved["event_batch"]["events"] if e["type"] == "dnd2024.treasure.rolled")
    for rolled in event["result"]["items"]:
        intent = reward_intent_from_ref(
            catalog, rolled["item_ref"], default_source="module:test", recipient_uid="gm",
        )
        assert intent["kind"] == "item_grant"
        assert intent["item_ref"] == ITEM
    data = {"state_update": {"players": {"gm": {"item_gains": [{"name": "Dagger", "qty": 1}]}}}}
    removed = filter_unconfirmed_purchase_grants(
        instance, data,
        unpriced_purchase_intents=[{"payer_uid": "gm", "target": "Dagger"}],
    )
    assert removed == 1
    assert "item_gains" not in data["state_update"]["players"]["gm"]


# T10: nested tables expose every layer in the path and the terminal result only.
def test_t10_nested_path_and_terminal_result() -> None:
    engine, instance, _sheet = _exploration()
    resolved = engine.resolve_intent(instance, _intent(0), SequenceRng([3, 1]))
    assert resolved["ok"] is True
    event = next(e for e in resolved["events"] if e["type"] == "dnd2024.treasure.rolled")
    assert [layer["table_ref"] for layer in event["path"]] == [TABLE, NESTED]
    assert event["path"][-1] == {"table_ref": NESTED, "roll": 1, "from": 1, "to": 1}
    assert event["result"] == {
        "coins_gp": 5, "coins": {"amount": 5, "unit": "gp"},
    }


# T8: only the GM sees/executes the action, and combat active rejects it.
def test_t8_gm_only_and_combat_active_rejection() -> None:
    engine, instance, sheet = _exploration()
    instance.players["p2"] = {"character_name": "Pip", "character_sheet": deepcopy(sheet)}
    actions = engine.available_intents(instance, "player:gm")
    assert any(action["type"] == "exploration.roll_treasure" for action in actions)
    # The gameplay response boundary hands the engine a bare uid; GM-only
    # visibility must not depend on the ``player:`` prefix.
    assert any(
        action["type"] == "exploration.roll_treasure"
        for action in engine.available_intents(instance, "gm")
    )
    assert not any(
        action["type"] == "exploration.roll_treasure"
        for action in engine.available_intents(instance, "player:p2")
    )
    result = engine.resolve_intent(
        instance, {**_intent(0), "submitted_by": "p2", "actor_id": "player:p2"},
        SequenceRng([]),
    )
    assert "game master" in _invalid(result)

    runtime, combat_sheet = _runtime_sheet()
    combat_instance = _instance(combat_sheet)
    combat = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    started = combat.resolve_intent(combat_instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin()],
    }, SequenceRng([20, 1]))
    assert combat.apply_batch(combat_instance, started["event_batch"])["applied"] is True
    assert engine.available_intents(combat_instance, "player:gm") == []
    version = int(ruleset_runtime.state(combat_instance)["version"])
    result = engine.resolve_intent(combat_instance, _intent(version), SequenceRng([]))
    assert "active combat" in _invalid(result)


# T9: result shapes are strict: exactly one known shape, positive qty, known item.
def test_t9_invalid_result_shapes_fail_closed() -> None:
    with pytest.raises(TreasureTableError, match="unknown key"):
        _catalog([_table("bad", [_entry(1, 6, {"coins_gp": 1, "extra": 2})])])
    with pytest.raises(TreasureTableError, match="exactly one result shape"):
        _catalog([_table("bad", [_entry(1, 6, {"coins_gp": 1, "items": []})])])
    with pytest.raises(TreasureTableError, match="positive integer"):
        _catalog([_table("bad", [_entry(1, 6, _item_result(qty=0))])])
    with pytest.raises(TreasureTableError, match="unknown item"):
        _catalog([_table("bad", [_entry(1, 6, _item_result(item_ref="item:missing"))])])
    with pytest.raises(TreasureTableError, match="non-empty array"):
        _catalog([_table("bad", [_entry(1, 6, {"items": []})])])


# T11: treasure rolling never enters the combat intent set.
def test_t11_treasure_is_not_a_combat_intent() -> None:
    assert "exploration.roll_treasure" not in COMBAT_INTENT_TYPES


# T12: unknown or non-canonical table refs are rejected without rolling.
def test_t12_unknown_and_non_canonical_refs_fail_closed() -> None:
    engine, instance, _sheet = _exploration()
    assert "must start with treasure_table:" in _invalid(
        engine.resolve_intent(instance, _intent(0, "test_gemstones"), SequenceRng([]))
    )
    assert "not defined" in _invalid(
        engine.resolve_intent(instance, _intent(0, "treasure_table:missing"), SequenceRng([]))
    )
    runtime, _ = _runtime_sheet()
    # Shipped SRD content must not carry test fixtures; tests inject their
    # tables explicitly through ``_test_bundle``.
    assert runtime.load_bundle("en").list("treasure_table") == []


# T13: future/invalid schema and malformed table fields are never guessed.
def test_t13_future_schema_and_malformed_fields_fail_closed() -> None:
    future = _table("future", [_entry(1, 6, {"coins_gp": 1})])
    future["schema_version"] = 99
    with pytest.raises(TreasureTableError, match="schema_version is unsupported"):
        _catalog([future])
    bad_roll = _table("bad_roll", [_entry(1, 6, {"coins_gp": 1})], roll="2d6")
    with pytest.raises(TreasureTableError, match="single die"):
        _catalog([bad_roll])
