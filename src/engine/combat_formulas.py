"""受限战斗公式 DSL（Issue 212）。

规则模板与动作目录中的伤害/消耗金额是 JSON AST，而不是可执行表达式。
本模块是唯一的求值入口：白名单节点、深度/节点数/骰子/结果上限全部 fail
closed，非法引用（未知属性、未知资源、非法骰式）直接拒绝，绝不回退成 0
或猜测用户意图。求值是纯函数：只读 context，不修改任何角色或战斗状态。

禁止事项：不使用 eval/exec；不引入 if/loop/函数调用/任意字符串查找；
不支持嵌套动作调用。需要更强表达力时，扩展白名单节点并补测试。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.engine.dice_rng import roll

# 求值上限：超出即 FormulaError，不做静默截断。
MAX_FORMULA_DEPTH = 8
MAX_FORMULA_NODES = 64
MAX_FORMULA_DICE_NODES = 8
# 骰式必须是最简 NdM 形式且骰数/面数均 ≥1；骰数最多三位（上层规则目录
# 负责更严格的面数白名单）。默认骰源 dice_rng 自带上限并会抛 ValueError。
_DICE_FORMULA_RE = re.compile(r"^[1-9]\d{0,2}d[1-9]\d{0,3}$")
# 结果绝对值上限：与经济金额同量级，防止公式爆炸。
MAX_FORMULA_ABSOLUTE_RESULT = 100_000

_ALLOWED_OPS = frozenset(
    {
        "constant",
        "attribute",
        "derived_stat",
        "resource",
        "equipment_stat",
        "dice",
        "add",
        "subtract",
        "multiply",
        "min",
        "max",
        "negate",
    }
)


class FormulaError(ValueError):
    """公式非法或求值失败；调用方应把整个 action 拒绝而不是部分应用。"""


@dataclass(frozen=True)
class FormulaContext:
    """公式求值可见的全部数据。公式只能读这里，读不到的即不存在。"""

    attributes: Mapping[str, int]
    derived_stats: Mapping[str, int]
    resources: Mapping[str, int]
    equipment_stats: Mapping[str, int]
    actor_id: str
    target_id: str | None = None
    check_result: Mapping[str, Any] | None = None


def validate_formula(
    node: Mapping[str, Any],
    *,
    _depth: int = 0,
    _counter: list[int] | None = None,
    _dice_nodes: list[int] | None = None,
) -> None:
    """Validate a formula AST without evaluating references or rolling dice.

    Rule editors call this at save/load time so malformed nodes fail before a
    player attempts the action.  Runtime evaluation repeats the same bounds
    because persisted data remains an untrusted boundary.
    """

    if _counter is None:
        _counter = [0]
    if _dice_nodes is None:
        _dice_nodes = [0]
    _counter[0] += 1
    if _counter[0] > MAX_FORMULA_NODES:
        raise FormulaError("formula has too many nodes")
    if _depth > MAX_FORMULA_DEPTH:
        raise FormulaError("formula nesting is too deep")
    if not isinstance(node, Mapping):
        raise FormulaError("formula node must be an object")

    op = node.get("op")
    if op not in _ALLOWED_OPS:
        raise FormulaError(f"unknown formula op: {op!r}")
    if op == "constant":
        _as_int(node.get("value"), what="constant value")
        return
    if op in {"attribute", "derived_stat", "equipment_stat", "resource"}:
        ref = node.get("id")
        if not isinstance(ref, str) or not ref.strip():
            raise FormulaError(f"formula {op} reference must be a non-empty string")
        return
    if op == "dice":
        formula = node.get("formula")
        if not isinstance(formula, str) or not _DICE_FORMULA_RE.fullmatch(formula):
            raise FormulaError(f"invalid dice formula: {formula!r}")
        _dice_nodes[0] += 1
        if _dice_nodes[0] > MAX_FORMULA_DICE_NODES:
            raise FormulaError("formula has too many dice nodes")
        return

    args = node.get("args")
    if not isinstance(args, list) or not args:
        raise FormulaError(f"formula op {op!r} requires non-empty args")
    if op == "negate" and len(args) != 1:
        raise FormulaError("negate takes exactly one arg")
    if op in {"subtract", "multiply"} and len(args) != 2:
        raise FormulaError(f"{op} takes exactly two args")
    for arg in args:
        validate_formula(
            arg,
            _depth=_depth + 1,
            _counter=_counter,
            _dice_nodes=_dice_nodes,
        )


def _as_int(value: Any, *, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FormulaError(f"formula {what} must be an integer")
    return value


def evaluate_formula(
    node: Mapping[str, Any],
    context: FormulaContext,
    *,
    dice_roller: Callable[[str], Any] | None = None,
    _depth: int = 0,
    _counter: list[int] | None = None,
    _dice_nodes: list[int] | None = None,
) -> int:
    """求值一个公式 AST 节点；任何非法输入都抛 FormulaError。

    ``dice_roller`` 允许宿主规则注入确定性骰源（返回带 ``total`` 的对象）；
    缺省使用服务端 dice_rng。
    """

    if _counter is None:
        _counter = [0]
    if _dice_nodes is None:
        _dice_nodes = [0]
    _counter[0] += 1
    if _counter[0] > MAX_FORMULA_NODES:
        raise FormulaError("formula has too many nodes")
    if _depth > MAX_FORMULA_DEPTH:
        raise FormulaError("formula nesting is too deep")
    if not isinstance(node, Mapping):
        raise FormulaError("formula node must be an object")

    op = node.get("op")
    if op not in _ALLOWED_OPS:
        raise FormulaError(f"unknown formula op: {op!r}")

    if op == "constant":
        return _as_int(node.get("value"), what="constant value")

    if op in {"attribute", "derived_stat", "equipment_stat", "resource"}:
        ref = node.get("id")
        table = {
            "attribute": context.attributes,
            "derived_stat": context.derived_stats,
            "equipment_stat": context.equipment_stats,
            "resource": context.resources,
        }[op]
        if not isinstance(ref, str) or ref not in table:
            raise FormulaError(f"unknown {op} reference: {ref!r}")
        value = table[ref]
        return _as_int(value, what=f"{op} value")

    if op == "dice":
        formula = node.get("formula")
        if not isinstance(formula, str) or not _DICE_FORMULA_RE.fullmatch(formula):
            raise FormulaError(f"invalid dice formula: {formula!r}")
        _dice_nodes[0] += 1
        if _dice_nodes[0] > MAX_FORMULA_DICE_NODES:
            raise FormulaError("formula has too many dice nodes")
        try:
            roller = dice_roller or roll
            return int(roller(formula).total)
        except ValueError as exc:
            raise FormulaError(f"invalid dice formula: {formula!r}") from exc

    args = node.get("args")
    if not isinstance(args, list) or not args:
        raise FormulaError(f"formula op {op!r} requires non-empty args")

    if op == "negate":
        if len(args) != 1:
            raise FormulaError("negate takes exactly one arg")
        return -evaluate_formula(
            args[0], context,
            dice_roller=dice_roller,
            _depth=_depth + 1, _counter=_counter, _dice_nodes=_dice_nodes,
        )

    values = [
        evaluate_formula(
            arg, context,
            dice_roller=dice_roller,
            _depth=_depth + 1, _counter=_counter, _dice_nodes=_dice_nodes,
        )
        for arg in args
    ]
    if op == "add":
        return sum(values)
    if op == "subtract":
        if len(values) != 2:
            raise FormulaError("subtract takes exactly two args")
        return values[0] - values[1]
    if op == "multiply":
        if len(values) != 2:
            raise FormulaError("multiply takes exactly two args")
        return values[0] * values[1]
    if op == "min":
        return min(values)
    # op == "max"（白名单已保证）
    return max(values)


def evaluate_formula_trace(
    node: Mapping[str, Any],
    context: FormulaContext,
    *,
    dice_roller: Callable[[str], Any] | None = None,
) -> tuple[int, list[int]]:
    """求值并返回各骰节点按求值顺序展开的骰值明细。

    供宿主规则在权威事件里保留掷骰明细（例如 D&D 的 ``rolls`` 字段）；
    结果与 :func:`evaluate_formula` 完全一致，仅额外收集骰迹。
    """

    rolls: list[int] = []

    def roller(formula: str) -> Any:
        result = (dice_roller or roll)(formula)
        raw_rolls = getattr(result, "rolls", None)
        if isinstance(raw_rolls, list):
            rolls.extend(int(item) for item in raw_rolls)
        return result

    value = evaluate_formula(node, context, dice_roller=roller)
    return value, rolls


def evaluate_formula_bound(node: Mapping[str, Any], context: FormulaContext) -> int:
    """求值并强制结果在允许范围内；越界视为公式非法（fail closed）。"""

    result = evaluate_formula(node, context)
    if not -MAX_FORMULA_ABSOLUTE_RESULT <= result <= MAX_FORMULA_ABSOLUTE_RESULT:
        raise FormulaError(f"formula result out of range: {result}")
    return result
