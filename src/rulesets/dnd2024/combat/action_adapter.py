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


def is_supported_damage_formula(formula: str) -> bool:
    """Whether the formula layer can evaluate this damage line.

    Validation must not be stricter than the evaluator: Tiny beasts hit for a
    flat ``1``, and the AST already models that as a constant node.
    """

    text = str(formula or "")
    return bool(_FLAT_DAMAGE_RE.fullmatch(text) or DICE_RE.fullmatch(text))


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


def average_damage(formula: str, *, flat_bonus: float = 0.0) -> float:
    """Expected value of a catalog damage formula plus a flat modifier."""

    text = str(formula or "")
    if _FLAT_DAMAGE_RE.fullmatch(text):
        return float(text) + flat_bonus
    match = DICE_RE.fullmatch(text)
    if match is None:
        return 0.0
    count, sides, bonus = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    return count * (sides + 1) / 2 + bonus + flat_bonus


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


def dice_count(formula: str) -> int:
    """Number of dice in a plain ``NdM`` pool (the Sneak Attack budget)."""

    return _parse_ndm(formula)[0]


def damage_dice_count(formula: str) -> int:
    """Number of dice in a damage line, or ``0`` for a flat value.

    ``dice_count`` is deliberately strict because it parses a dice *pool* the
    caller may later reduce; a weapon line may also be ``1d8+3`` or the flat
    ``1`` of a Tiny beast, and neither is a pool.  Both are legal damage, so
    this reader answers the narrower question without turning them into errors.
    """

    text = str(formula or "")
    if _FLAT_DAMAGE_RE.fullmatch(text):
        return 0
    match = DICE_RE.fullmatch(text)
    return int(match.group(1)) if match else 0


def reduce_dice_count(formula: str, spent: int) -> str | None:
    """Remove dice from a plain ``NdM`` pool, or ``None`` when nothing is left.

    Cunning Strike pays for its effects by spending Sneak Attack dice *before*
    they are rolled, so the pool itself shrinks.  Returning ``None`` instead of
    ``"0d6"`` is deliberate: ``_NDM_RE`` requires at least one die, and a
    critical hit would double the zero and raise ``CombatIntentError`` right
    when the feature is used at full budget.
    """

    if isinstance(spent, bool) or not isinstance(spent, int) or spent < 0:
        raise CombatIntentError("spent dice must be a non-negative integer")
    count, sides = _parse_ndm(formula)
    remaining = count - spent
    if remaining <= 0:
        return None
    return f"{remaining}d{sides}"


def maximize_dice_nodes(node: Mapping[str, Any]) -> dict[str, Any]:
    """骰面最大化（Supreme Healing / Overchannel）：每个骰节点 → 常量节点。

    D&D 侧 AST 变换，与 double_dice_counts 同构：RNG 零消耗、骰迹为空。
    调用方约定：先 crit 翻倍、后最大化（数学等价，锁死顺序）。
    """

    if not isinstance(node, dict):
        raise CombatIntentError("formula node must be an object")
    walked = deepcopy(node)
    op = walked.get("op")
    if op == "dice":
        count, sides = _parse_ndm(str(walked.get("formula") or ""))
        return {"op": "constant", "value": count * sides}
    if op in {"add", "subtract", "multiply", "min", "max"}:
        args = walked.get("args")
        if isinstance(args, list):
            walked["args"] = [
                maximize_dice_nodes(arg) if isinstance(arg, dict) else arg
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


def _rng_dice_roller(
    rng: Any, *,
    die_floor: int = 0,
    floored_dice: int = 0,
    floored: list[tuple[int, int]] | None = None,
) -> Any:
    """把宿主 rng 包装成通用求值器的骰源；骰迹按骰顺序展开。

    ``die_floor`` 只作用于前 ``floored_dice`` 颗骰。攻击伤害 AST 的主骰永远
    排在附加骰之前（``attack_damage_node`` 的既有约定），所以按颗数计数就能
    把下限限定在武器伤害骰上，而不碰偷袭、印记这类附骰。RNG 的调用次数与
    顺序不变：这里只抬高已掷出的点数，不是重掷。
    """

    remaining = max(0, int(floored_dice))

    def roller(formula: str) -> Any:
        nonlocal remaining
        count, sides = _parse_ndm(formula)
        rolls: list[int] = []
        for _ in range(count):
            rolled = int(rng.randint(1, sides))
            value = rolled
            if remaining > 0:
                if die_floor > 0:
                    value = max(value, int(die_floor))
                    if value != rolled and floored is not None:
                        floored.append((rolled, value))
                remaining -= 1
            rolls.append(value)
        return SimpleNamespace(total=sum(rolls), rolls=rolls)

    return roller


def roll_damage_node_with_trace(
    node: dict[str, Any], rng: Any, *,
    die_floor: int = 0,
    floored_dice: int = 0,
) -> tuple[int, list[int], list[tuple[int, int]]]:
    """Damage settlement with the optional die floor, plus what it changed.

    The third element lists ``(rolled, effective)`` for the dice the floor
    actually raised, so the event can say "rolled 1, counted as 3" without
    making every caller unpack three values.
    """

    floored: list[tuple[int, int]] = []
    value, rolls = evaluate_formula_trace(
        node,
        _BLANK_CONTEXT,
        dice_roller=_rng_dice_roller(
            rng, die_floor=die_floor, floored_dice=floored_dice, floored=floored,
        ),
    )
    return value, rolls, floored


def roll_damage_node(
    node: dict[str, Any], rng: Any,
) -> tuple[int, list[int]]:
    """用通用求值器结算伤害/治疗节点，返回 (总值, 按骰顺序的明细)。"""

    value, rolls, _floored = roll_damage_node_with_trace(node, rng)
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
    "maximize_dice_nodes",
    "roll_damage_node",
    "roll_damage_node_with_trace",
    "spell_formula_node",
]
