"""D&D 2024 战斗动作到通用公式 AST 的适配层（Issue 212 PR 3）。

职责边界：
- D&D 专属的检定、豁免、法术位、专注、目标关系仍然完全归 D&D runtime；
- 本适配层只把目录中的伤害/治疗骰式（``NdM`` / ``NdM+K``）翻译成通用
  公式 AST，并通过通用求值器（注入本局 rng、保留骰迹）产出与旧
  ``primitives.roll`` 完全一致的结果与骰迹顺序；
- generic engine 不出现任何 D&D 分支：这里全部是 D&D 侧代码。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from src.engine.combat_formulas import (
    FormulaContext,
    evaluate_formula_trace,
)
from src.rulesets.dnd2024.character.builder import ability_modifier
from .primitives import DICE_RE, CombatIntentError

# 通用求值器骰节点的 NdM 形式（与 combat_formulas 的骰式白名单一致）。
_NDM_RE = re.compile(r"^([1-9]\d{0,2})d([1-9]\d{0,3})$")
# 固定伤害（徒手打击的 1 点基础伤害）：没有骰子，重击不翻倍。
_FLAT_DAMAGE_RE = re.compile(r"^[1-9]\d{0,3}$")

# 伤害/治疗节点只由骰子与常量构成，不需要角色属性表。
_BLANK_CONTEXT = FormulaContext(
    attributes={}, derived_stats={}, resources={}, equipment_stats={},
    actor_id="combat",
)


def _parse_ndm(formula: str) -> tuple[int, int]:
    match = _NDM_RE.fullmatch(str(formula or ""))
    if match is None:
        raise CombatIntentError(f"unsupported dice formula: {formula!r}")
    return int(match.group(1)), int(match.group(2))


def formula_node_from_dice_formula(formula: str) -> dict[str, Any]:
    """把目录骰式 ``NdM`` / ``NdM+K`` / 固定值翻译成通用公式 AST。

    固定值（例如徒手打击的 ``1``）是常量节点，因此重击不会翻倍它——这与
    D&D 2024「重击只翻倍伤害骰」的语义一致。
    """

    text = str(formula or "")
    if _FLAT_DAMAGE_RE.fullmatch(text):
        return {"op": "constant", "value": int(text)}
    match = DICE_RE.fullmatch(text)
    if match is None:
        raise CombatIntentError(f"unsupported dice formula: {formula!r}")
    count = int(match.group(1))
    sides = int(match.group(2))
    bonus = int(match.group(3) or 0)
    node: dict[str, Any] = {"op": "dice", "formula": f"{count}d{sides}"}
    if bonus:
        node = {"op": "add", "args": [node, {"op": "constant", "value": bonus}]}
    return node


def double_dice_counts(node: Mapping[str, Any]) -> dict[str, Any]:
    """重击语义：把 AST 中每个骰节点的骰数翻倍（D&D 专属规则留在本层）。"""

    if not isinstance(node, dict):
        raise CombatIntentError("formula node must be an object")
    walked = deepcopy(node)
    op = walked.get("op")
    if op == "dice":
        count, sides = _parse_ndm(str(walked.get("formula") or ""))
        walked["formula"] = f"{count * 2}d{sides}"
        return walked
    if op in {"add", "subtract", "multiply", "min", "max"}:
        args = walked.get("args")
        if isinstance(args, list):
            walked["args"] = [
                double_dice_counts(arg) if isinstance(arg, dict) else arg
                for arg in args
            ]
        return walked
    return walked


def spell_formula_node(
    base: str, upcast: Any, spell: dict[str, Any], slot_level: int,
    actor: dict[str, Any],
) -> dict[str, Any]:
    """目录法术效果 + 升环 → 公式 AST（语义与旧 ``_spell_formula`` 一致）。"""

    match = DICE_RE.fullmatch(base)
    if match is None:
        raise CombatIntentError(f"unsupported spell dice formula: {base}")
    count, sides, bonus = (int(value or 0) for value in match.groups())
    if int(spell["level"]) == 0:
        level = int(actor.get("build", {}).get("level", 1) or 1)
        multiplier = 4 if level >= 17 else 3 if level >= 11 else 2 if level >= 5 else 1
        count *= multiplier
    elif upcast and slot_level > int(spell["level"]):
        extra = DICE_RE.fullmatch(str(upcast))
        if extra is None:
            raise CombatIntentError("upcast dice formula is invalid")
        extra_count, extra_sides, extra_bonus = (
            int(value or 0) for value in extra.groups()
        )
        if extra_sides != sides:
            raise CombatIntentError("upcast dice sides must match the base formula")
        levels = slot_level - int(spell["level"])
        count += extra_count * levels
        bonus += extra_bonus * levels
    node: dict[str, Any] = {"op": "dice", "formula": f"{count}d{sides}"}
    if bonus:
        node = {"op": "add", "args": [node, {"op": "constant", "value": bonus}]}
    return node


def _rng_dice_roller(rng: Any) -> Any:
    """把宿主 rng 包装成通用求值器的骰源；骰迹按骰顺序展开。"""

    def roller(formula: str) -> Any:
        count, sides = _parse_ndm(formula)
        rolls = [int(rng.randint(1, sides)) for _ in range(count)]
        return SimpleNamespace(total=sum(rolls), rolls=rolls)

    return roller


def roll_damage_node(
    node: dict[str, Any], rng: Any,
) -> tuple[int, list[int]]:
    """用通用求值器结算伤害/治疗节点，返回 (总值, 按骰顺序的明细)。"""

    value, rolls = evaluate_formula_trace(node, _BLANK_CONTEXT, dice_roller=_rng_dice_roller(rng))
    return value, rolls


def attack_damage_node(
    *,
    damage_formula: str,
    modifier: int,
    critical: bool,
    extra_dice_formulas: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """武器伤害 AST：主骰（可含奖励）→ 各附加骰（按调用方顺序）→ 属性修正。

    骰节点顺序即 rng 消耗顺序：主骰在前、附加骰按给定顺序在后，与旧路径
    一致。重击翻倍覆盖主骰与全部附加骰，属性修正永远不翻倍。
    """

    node = formula_node_from_dice_formula(damage_formula)
    for extra in extra_dice_formulas or ():
        node = {"op": "add", "args": [node, formula_node_from_dice_formula(extra)]}
    if critical:
        node = double_dice_counts(node)
    if modifier:
        node = {"op": "add", "args": [node, {"op": "constant", "value": modifier}]}
    return node


def heal_ability_modifier_node(healing_node: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    """治疗附加施法属性修正（旧 ``add_spell_ability`` 语义）。"""

    modifier = ability_modifier(actor["abilities"][actor["spell_ability"]])
    if not modifier:
        return healing_node
    return {"op": "add", "args": [
        healing_node, {"op": "constant", "value": modifier},
    ]}


__all__ = [
    "attack_damage_node",
    "double_dice_counts",
    "formula_node_from_dice_formula",
    "heal_ability_modifier_node",
    "roll_damage_node",
    "spell_formula_node",
]
