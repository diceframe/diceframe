"""受限战斗公式 DSL 的契约测试（Issue 212 PR 1）。"""

from __future__ import annotations

import pytest

from src.engine.combat_formulas import (
    MAX_FORMULA_ABSOLUTE_RESULT,
    MAX_FORMULA_DEPTH,
    MAX_FORMULA_NODES,
    FormulaContext,
    FormulaError,
    evaluate_formula,
    evaluate_formula_bound,
)


def _context(**overrides) -> FormulaContext:
    values = {
        "attributes": {"strength": 5, "intelligence": 4, "wisdom": 3},
        "derived_stats": {"strength_modifier": 2, "proficiency": 3},
        "resources": {"mana": 12, "hp": 30},
        "equipment_stats": {"weapon_damage": 3},
        "actor_id": "player:x",
        "target_id": "enemy:goblin",
        "check_result": None,
    }
    values.update(overrides)
    return FormulaContext(**values)


def test_constant_and_references() -> None:
    context = _context()
    assert evaluate_formula({"op": "constant", "value": 12}, context) == 12
    assert evaluate_formula({"op": "attribute", "id": "intelligence"}, context) == 4
    assert evaluate_formula({"op": "derived_stat", "id": "strength_modifier"}, context) == 2
    assert evaluate_formula({"op": "resource", "id": "mana"}, context) == 12
    assert evaluate_formula({"op": "equipment_stat", "id": "weapon_damage"}, context) == 3


def test_arithmetic_and_min_max_negate() -> None:
    context = _context()
    add = {"op": "add", "args": [
        {"op": "attribute", "id": "strength"},
        {"op": "derived_stat", "id": "proficiency"},
    ]}
    assert evaluate_formula(add, context) == 8
    assert evaluate_formula({"op": "subtract", "args": [add, {"op": "constant", "value": 3}]}, context) == 5
    assert evaluate_formula({"op": "multiply", "args": [
        {"op": "attribute", "id": "intelligence"},
        {"op": "constant", "value": 3},
    ]}, context) == 12
    assert evaluate_formula({"op": "min", "args": [
        {"op": "constant", "value": 2},
        {"op": "attribute", "id": "strength"},
    ]}, context) == 2
    assert evaluate_formula({"op": "max", "args": [
        {"op": "constant", "value": 2},
        {"op": "attribute", "id": "strength"},
    ]}, context) == 5
    assert evaluate_formula({"op": "negate", "args": [{"op": "constant", "value": 7}]}, context) == -7


def test_dice_node_returns_value_in_die_range() -> None:
    context = _context()
    result = evaluate_formula({"op": "dice", "formula": "1d6"}, context)
    assert 1 <= result <= 6
    ranged = evaluate_formula({"op": "add", "args": [
        {"op": "dice", "formula": "2d6"},
        {"op": "constant", "value": 1},
    ]}, context)
    assert 3 <= ranged <= 13


def test_unknown_references_and_ops_fail_closed() -> None:
    context = _context()
    with pytest.raises(FormulaError):
        evaluate_formula({"op": "attribute", "id": "charisma"}, context)
    with pytest.raises(FormulaError):
        evaluate_formula({"op": "resource", "id": "qigong"}, context)
    with pytest.raises(FormulaError):
        evaluate_formula({"op": "fireball_damage"}, context)
    with pytest.raises(FormulaError):
        evaluate_formula({"op": "constant", "value": "12"}, context)
    with pytest.raises(FormulaError):
        evaluate_formula({"op": "constant", "value": True}, context)
    with pytest.raises(FormulaError):
        evaluate_formula("1d6", context)
    with pytest.raises(FormulaError):
        evaluate_formula({"op": "add", "args": []}, context)


def test_invalid_dice_forms_fail_closed() -> None:
    context = _context()
    for formula in ("0d6", "1d0", "abc", "1d6+1d4", "1000d6"):
        with pytest.raises(FormulaError):
            evaluate_formula({"op": "dice", "formula": formula}, context)


def test_depth_and_node_limits_fail_closed() -> None:
    context = _context()
    deep: dict = {"op": "constant", "value": 1}
    for _ in range(MAX_FORMULA_DEPTH + 2):
        deep = {"op": "add", "args": [deep, {"op": "constant", "value": 0}]}
    with pytest.raises(FormulaError):
        evaluate_formula(deep, context)

    wide = {"op": "add", "args": [{"op": "constant", "value": 1}] * (MAX_FORMULA_NODES + 1)}
    with pytest.raises(FormulaError):
        evaluate_formula(wide, context)

    with pytest.raises(FormulaError):
        evaluate_formula(
            {"op": "add", "args": [{"op": "dice", "formula": "1d4"}] * 9},
            context,
        )


def test_result_bound_is_enforced_not_clamped() -> None:
    context = _context(attributes={"strength": MAX_FORMULA_ABSOLUTE_RESULT})
    huge = {"op": "multiply", "args": [
        {"op": "attribute", "id": "strength"},
        {"op": "constant", "value": 10},
    ]}
    with pytest.raises(FormulaError):
        evaluate_formula_bound(huge, context)


def test_evaluation_is_pure_and_does_not_mutate_context() -> None:
    attributes = {"strength": 5}
    resources = {"mana": 12}
    context = _context(attributes=attributes, resources=resources)
    node = {"op": "add", "args": [
        {"op": "attribute", "id": "strength"},
        {"op": "resource", "id": "mana"},
    ]}
    assert evaluate_formula(node, context) == 17
    assert attributes == {"strength": 5}
    assert resources == {"mana": 12}
