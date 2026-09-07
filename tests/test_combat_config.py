"""规则模板 combat 声明解析的契约测试（Issue 212 phase 2 Slice 2.1）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.combat_config import (
    CombatConfigError,
    combat_extension_from_template,
)


def _template(combat: dict | None) -> dict:
    template: dict = {"rule_id": "freeform_wuxia", "dice_system": "d20"}
    if combat is not None:
        template["combat"] = combat
    return template


def _valid_combat() -> dict:
    return {
        "scheduler": {"kind": "threshold", "gauge": "action_gauge",
                      "speed": "action_speed", "threshold": 100},
        "resources": [
            {"id": "hp", "source": "hp"},
            {"id": "qi", "source": "special_stat", "stat": "qi", "maximum": 100},
            {"id": "barrier", "source": "combat_state", "maximum": 50,
             "damage_priority": "before_hp", "damage_types": ["all"]},
        ],
        "actions": [
            {"id": "item:healing_pill.use", "kind": "consumable", "name": "回春丹",
             "effects": [{"kind": "resource_change", "resource": "hp",
                          "amount": {"op": "add", "args": [
                              {"op": "constant", "value": 10},
                              {"op": "attribute", "id": "wis"}]}}]},
            {"id": "ability:qi_palm", "kind": "ability", "name": "内力掌",
             "costs": [{"resource": "qi", "amount": {"op": "constant", "value": 8}}],
             "effects": [{"kind": "damage", "amount": {
                 "op": "multiply", "args": [
                     {"op": "attribute", "id": "str"}, {"op": "constant", "value": 2}]},
                 "damage_type": "bludgeoning"}]},
        ],
    }


def test_no_combat_block_returns_none() -> None:
    """没有 combat 块 = 保持原有玩法；出现 speed 字段也不会自动启用 ATB。"""
    assert combat_extension_from_template(_template(None)) is None


def test_valid_declaration_parses() -> None:
    config = combat_extension_from_template(_template(_valid_combat()))
    assert config is not None
    assert config.scheduler is not None and config.scheduler.kind == "threshold"
    assert config.resource_ids == {"hp", "qi", "barrier"}
    assert config.hp_resource == "hp"
    assert config.barrier_resources == {"barrier": frozenset({"all"})}
    palm = config.action("ability:qi_palm")
    assert palm is not None and palm.costs[0].resource == "qi"
    assert config.action("spell:fireball") is None


def test_freeform_wuxia_healing_pill_requires_inventory_item() -> None:
    template = json.loads(
        (Path(__file__).parents[1] / "templates" / "rules" / "freeform_wuxia.json")
        .read_text(encoding="utf-8")
    )

    config = combat_extension_from_template(template)
    action = config.action("item:healing_pill.use") if config else None

    assert action is not None
    assert action.consume_item is not None
    assert (action.consume_item.item, action.consume_item.qty) == ("回春丹", 1)


def test_requires_exactly_one_hp_source_pool() -> None:
    combat = _valid_combat()
    combat["resources"].append({"id": "wounds", "source": "hp"})

    with pytest.raises(CombatConfigError, match="exactly one hp"):
        combat_extension_from_template(_template(combat))


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_costable_must_be_a_boolean(value: object) -> None:
    combat = _valid_combat()
    combat["resources"][1]["costable"] = value

    with pytest.raises(CombatConfigError, match="costable must be a boolean"):
        combat_extension_from_template(_template(combat))


def test_action_cannot_spend_non_costable_resource() -> None:
    combat = _valid_combat()
    combat["resources"][1]["costable"] = False

    with pytest.raises(CombatConfigError, match="non-costable resource"):
        combat_extension_from_template(_template(combat))


@pytest.mark.parametrize("value", [False, 0, ""])
def test_action_costs_must_be_a_list_when_present(value: object) -> None:
    combat = _valid_combat()
    combat["actions"][1]["costs"] = value

    with pytest.raises(CombatConfigError, match="costs must be a list"):
        combat_extension_from_template(_template(combat))


@pytest.mark.parametrize("qty", [True, 1.5, "2", 0])
def test_consume_item_quantity_requires_a_positive_integer(qty: object) -> None:
    combat = _valid_combat()
    combat["actions"][0]["consume_item"] = {"item": "回春丹", "qty": qty}

    with pytest.raises(CombatConfigError, match="consume_item qty"):
        combat_extension_from_template(_template(combat))


def test_special_stat_resource_must_reference_declared_stat() -> None:
    template = _template(_valid_combat())
    template["special_stats"] = [{"key": "mana", "name": "Mana"}]

    with pytest.raises(CombatConfigError, match="undeclared special_stat"):
        combat_extension_from_template(template)


def test_empty_damage_type_catalog_keeps_unrestricted_legacy_semantics() -> None:
    combat = _valid_combat()
    combat["damage_types"] = []

    assert combat_extension_from_template(_template(combat)) is not None


def test_present_but_empty_scheduler_is_rejected() -> None:
    combat = _valid_combat()
    combat["scheduler"] = {}

    with pytest.raises(Exception, match="scheduler kind"):
        combat_extension_from_template(_template(combat))


@pytest.mark.parametrize(
    "formula",
    [
        {"op": "eval", "value": "2 + 2"},
        {"op": "dice", "formula": "1000d99999"},
        {"op": "multiply", "args": [{"op": "constant", "value": 2}]},
        {"op": "constant", "value": True},
    ],
)
def test_formula_shape_is_validated_when_rule_is_loaded(formula: dict) -> None:
    combat = _valid_combat()
    combat["actions"][1]["costs"][0]["amount"] = formula

    with pytest.raises(CombatConfigError, match="cost formula"):
        combat_extension_from_template(_template(combat))


def test_formula_references_must_exist_in_rule_contract() -> None:
    template = _template(_valid_combat())
    template["attributes"] = [{"key": "dex", "name": "Dexterity"}]

    with pytest.raises(CombatConfigError, match="unknown attribute reference"):
        combat_extension_from_template(template)


def test_scheduler_speed_formula_must_be_deterministic() -> None:
    combat = _valid_combat()
    combat["scheduler"]["speed_formula"] = {"op": "dice", "formula": "1d6"}

    with pytest.raises(CombatConfigError, match="dice is not allowed"):
        combat_extension_from_template(_template(combat))


@pytest.mark.parametrize("value", ["bludgeoning", {"bludgeoning": True}, [""]])
def test_malformed_damage_type_catalog_fails_closed(value: object) -> None:
    combat = _valid_combat()
    combat["damage_types"] = value

    with pytest.raises(CombatConfigError, match="damage_types must be a string list"):
        combat_extension_from_template(_template(combat))


def test_unknown_scheduler_kind_fails_closed() -> None:
    combat = _valid_combat()
    combat["scheduler"] = {"kind": "xianxia_atb"}
    with pytest.raises(Exception, match="unknown scheduler kind"):
        combat_extension_from_template(_template(combat))


def test_resources_fail_closed() -> None:
    base = _valid_combat()
    # 没有 hp 池：拒绝。
    combat = {**base, "resources": [r for r in base["resources"] if r["id"] != "hp"]}
    with pytest.raises(CombatConfigError, match="hp source pool"):
        combat_extension_from_template(_template(combat))
    # 重复 id：拒绝。
    combat = {**base, "resources": [*base["resources"], base["resources"][1]]}
    with pytest.raises(CombatConfigError, match="unique"):
        combat_extension_from_template(_template(combat))
    # special_stat 缺 stat：拒绝。
    combat = {**base, "resources": [
        {"id": "hp", "source": "hp"},
        {"id": "qi", "source": "special_stat"},
    ]}
    with pytest.raises(CombatConfigError, match="requires stat"):
        combat_extension_from_template(_template(combat))
    # 未知来源：拒绝。
    combat = {**base, "resources": [
        {"id": "hp", "source": "hp"},
        {"id": "qi", "source": "character_field"},
    ]}
    with pytest.raises(CombatConfigError, match="unknown source"):
        combat_extension_from_template(_template(combat))


def test_actions_reference_declared_resources_only() -> None:
    base = _valid_combat()
    base["actions"] = [{
        "id": "ability:bad", "kind": "ability", "name": "坏招",
        "costs": [{"resource": "qigong", "amount": {"op": "constant", "value": 1}}],
        "effects": [{"kind": "damage", "amount": {"op": "constant", "value": 1}}],
    }]
    with pytest.raises(CombatConfigError, match="undeclared resource"):
        combat_extension_from_template(_template(base))


def test_actions_require_known_effect_kinds() -> None:
    base = _valid_combat()
    base["actions"] = [{
        "id": "ability:bad", "kind": "ability", "name": "坏招",
        "effects": [{"kind": "fireball", "amount": {"op": "constant", "value": 1}}],
    }]
    with pytest.raises(CombatConfigError, match="unknown effect kind"):
        combat_extension_from_template(_template(base))


def test_broken_combat_block_type_fails_closed() -> None:
    with pytest.raises(CombatConfigError, match="must be an object"):
        combat_extension_from_template(_template("threshold"))
