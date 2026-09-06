"""D&D 动作适配层测试：目录骰式 → 通用公式 AST（Issue 212 PR 3）。"""

from __future__ import annotations

import pytest

from src.engine.combat_formulas import FormulaContext, evaluate_formula_trace
from src.rulesets.dnd2024.combat.action_adapter import (
    attack_damage_node,
    double_dice_counts,
    formula_node_from_dice_formula,
    heal_ability_modifier_node,
    roll_damage_node,
    spell_formula_node,
)
from src.rulesets.dnd2024.combat.primitives import roll as legacy_roll


class _SeqRng:
    """确定性 rng：按给定序列回放 randint 结果，并记录每次调用区间。"""

    def __init__(self, values: list[int]) -> None:
        self.values = list(values)
        self.calls: list[tuple[int, int]] = []

    def randint(self, a: int, b: int) -> int:
        assert self.values, "rng sequence exhausted"
        self.calls.append((a, b))
        return self.values.pop(0)


def test_formula_node_from_dice_formula() -> None:
    assert formula_node_from_dice_formula("1d8") == {"op": "dice", "formula": "1d8"}
    assert formula_node_from_dice_formula("2d6+3") == {
        "op": "add",
        "args": [{"op": "dice", "formula": "2d6"}, {"op": "constant", "value": 3}],
    }
    with pytest.raises(Exception, match="unsupported dice formula"):
        # 面数白名单（d4/6/8/10/12/20）由目录加载时校验；适配层只拒绝
        # 结构上不是 NdM[+K] 的字符串。
        formula_node_from_dice_formula("not-a-dice")


def test_roll_damage_node_matches_legacy_roll_exactly() -> None:
    # 相同 rng 序列下，新路径与旧 primitives.roll 的结果与骰迹完全一致。
    for formula, values in [
        ("1d8", [6]),
        ("2d6+3", [4, 2]),
        ("8d6", [1, 2, 3, 4, 5, 6, 1, 2]),
    ]:
        legacy_total, legacy_rolls = legacy_roll(formula, _SeqRng(list(values)))
        node = formula_node_from_dice_formula(formula)
        new_total, new_rolls = roll_damage_node(node, _SeqRng(list(values)))
        assert (new_total, new_rolls) == (legacy_total, legacy_rolls)


def test_crit_doubling_doubles_every_dice_node_in_order() -> None:
    # 旧路径：主骰 critical=True，附加骰（魔化）也 critical=True。
    # 新路径：主骰 + 附加骰组成一个 AST，再整体翻倍。
    values = [3, 5, 2, 6]
    legacy_main = legacy_roll("1d8", _SeqRng(list(values[:2])), critical=True)
    legacy_hex = legacy_roll("1d6", _SeqRng(list(values[2:])), critical=True)
    legacy_total = max(0, legacy_main[0] + legacy_hex[0] + 2)
    legacy_rolls = [*legacy_main[1], *legacy_hex[1]]

    node = attack_damage_node(
        damage_formula="1d8", modifier=2, critical=True,
        extra_dice_formula="1d6",
    )
    new_total, new_rolls = roll_damage_node(node, _SeqRng(list(values)))

    assert (new_total, new_rolls) == (legacy_total, legacy_rolls)
    assert double_dice_counts({"op": "dice", "formula": "2d6"}) == {
        "op": "dice", "formula": "4d6",
    }


def test_spell_formula_node_upcast_and_cantrip_scaling() -> None:
    spell = {"level": 3}
    actor = {"build": {"level": 5}}
    # 升环：slot 5 相对 level 3 多 2 层，每层 +1d6/+1。
    node = spell_formula_node("8d6", "1d6", spell, 5, actor)
    assert node == {"op": "dice", "formula": "10d6"}

    # 升环面数不匹配 → 拒绝。
    with pytest.raises(Exception, match="upcast dice sides"):
        spell_formula_node("8d6", "1d8", spell, 5, actor)

    # 戏法按施法者等级缩放（level 11 → ×3）。
    cantrip = {"level": 0}
    high_level = {"build": {"level": 11}}
    node = spell_formula_node("2d8", None, cantrip, 1, high_level)
    assert node == {"op": "dice", "formula": "6d8"}


def test_heal_ability_modifier_node_appends_constant() -> None:
    actor = {"abilities": {"wis": 14}, "spell_ability": "wis"}
    node = heal_ability_modifier_node(
        {"op": "dice", "formula": "1d8"}, actor,
    )
    assert node == {"op": "add", "args": [
        {"op": "dice", "formula": "1d8"}, {"op": "constant", "value": 2},
    ]}
    context = FormulaContext(
        attributes={}, derived_stats={}, resources={}, equipment_stats={},
        actor_id="a",
    )
    # 无修正时不包裹；有修正时骰迹只含骰子，不含常量。
    value, rolls = evaluate_formula_trace(node, context, dice_roller=lambda f: type(
        "R", (), {"total": 5, "rolls": [5]},
    )())
    assert value == 7
    assert rolls == [5]
