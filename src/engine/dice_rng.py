"""骰子 RNG 与通用公式解析 —— 只负责真实随机与骰值，不做任何规则判定。

分层：本模块 = Dice RNG + Generic Dice Parser；
规则层见 dice_rules_d20（d20/娱乐房规）与 dice_rules_coc（CoC 7e）。
"投骰"与"判定成功"不绑死：DC、优势、暴击、剧情结果全由上层规则决定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import random
import re
from src.localization.messages import t


@dataclass(frozen=True)
class DiceTerm:
    """单个骰子项，例如 +2d20kh1 或 -1d4。"""
    sign: int
    count: int
    sides: int
    keep_mode: str | None = None   # "kh" | "kl" | None
    keep_count: int | None = None


@dataclass(frozen=True)
class ParsedDiceExpression:
    """已校验的通用多组骰子表达式。"""
    formula: str
    terms: tuple[DiceTerm, ...]
    modifier: int


@dataclass
class DiceResult:
    """掷骰结果。"""
    formula: str           # "d20+3"
    rolls: list[int]       # [14]
    modifier: int          # 3
    total: int             # 17
    natural: int           # 未加修正的原始值：单骰=该面值，多骰=已采纳骰面带符号之和
    is_critical: bool = False   # 由规则层置位（d20 房规：自然 20）
    is_fumble: bool = False     # 由规则层置位（d20 房规：自然 1）
    faces: list[dict[str, Any]] | None = None  # 逐颗骰面明细（sides/value/taken/sign）


# 公网 bot 防滥用：骰数/面数/修正/项数上下限。
MAX_DICE_COUNT = 100
MAX_DICE_SIDES = 10000
MAX_DICE_MODIFIER = 100
MAX_DICE_TERMS = 10

_TOKEN_RE = re.compile(r"([+-]?)([^+-]+)")
_DICE_TERM_RE = re.compile(r"^(\d+)?d(\d+)(?:(kh|kl)(\d+)?)?$")
_INT_TERM_RE = re.compile(r"^\d+$")


def roll_die(sides: int, *, zero_based: bool = False) -> int:
    """生成一个骰面值；规则层通过此入口取随机值。"""
    if not 2 <= sides <= MAX_DICE_SIDES:
        raise ValueError(t("engine.dice_rng.die_sides_out_of_range", max_dice_sides=MAX_DICE_SIDES, sides=sides))
    return random.randint(0, sides - 1) if zero_based else random.randint(1, sides)


def parse_dice_expression(formula: str) -> ParsedDiceExpression:
    """Validate and normalize a generic dice expression without consuming RNG.

    支持的格式:
    - 基础单组: d20, d20+3, 2d6, 2d6+1, d100, 3d8-2
    - 取高低: 2d20kh1, 2d20kl1, 4d6kh3+1, 2d20kh, 2d20kl
    - 多组加减: 1d8+1d6+3, 2d20kh1+1d4-1
    """
    cleaned = str(formula or "").strip().lower().replace(" ", "")
    if not cleaned:
        raise ValueError(t("engine.dice_rng.formula_invalid", formula=cleaned))

    pos = 0
    terms: list[DiceTerm] = []
    modifier = 0
    total_dice = 0

    for match in _TOKEN_RE.finditer(cleaned):
        if match.start() != pos:
            raise ValueError(t("engine.dice_rng.formula_invalid", formula=cleaned))
        sign_str, body = match.group(1), match.group(2)
        if pos > 0 and not sign_str:
            raise ValueError(t("engine.dice_rng.formula_invalid", formula=cleaned))
        pos = match.end()
        sign = -1 if sign_str == "-" else 1

        dice_match = _DICE_TERM_RE.fullmatch(body)
        if dice_match:
            count = int(dice_match.group(1) or 1)
            sides = int(dice_match.group(2))
            keep_mode = dice_match.group(3)
            keep_count_raw = dice_match.group(4)
            keep_count = int(keep_count_raw) if keep_count_raw is not None else (1 if keep_mode else None)

            if not 1 <= count <= MAX_DICE_COUNT:
                raise ValueError(t("engine.dice_rng.count_out_of_range", max_dice_count=MAX_DICE_COUNT, formula=cleaned))
            if not 2 <= sides <= MAX_DICE_SIDES:
                raise ValueError(t("engine.dice_rng.sides_out_of_range", max_dice_sides=MAX_DICE_SIDES, formula=cleaned))
            if keep_count is not None and not 1 <= keep_count <= count:
                raise ValueError(t("engine.dice_rng.formula_invalid", formula=cleaned))

            total_dice += count
            if total_dice > MAX_DICE_COUNT:
                raise ValueError(t("engine.dice_rng.count_out_of_range", max_dice_count=MAX_DICE_COUNT, formula=cleaned))
            terms.append(DiceTerm(sign=sign, count=count, sides=sides, keep_mode=keep_mode, keep_count=keep_count))
            if len(terms) > MAX_DICE_TERMS:
                raise ValueError(t("engine.dice_rng.formula_invalid", formula=cleaned))
        elif _INT_TERM_RE.fullmatch(body):
            modifier += sign * int(body)
        else:
            raise ValueError(t("engine.dice_rng.formula_invalid", formula=cleaned))

    if pos != len(cleaned) or not terms:
        raise ValueError(t("engine.dice_rng.formula_invalid", formula=cleaned))
    if not -MAX_DICE_MODIFIER <= modifier <= MAX_DICE_MODIFIER:
        raise ValueError(t("engine.dice_rng.modifier_out_of_range", max_dice_modifier=MAX_DICE_MODIFIER, formula=cleaned))

    return ParsedDiceExpression(formula=cleaned, terms=tuple(terms), modifier=modifier)


def roll(formula: str) -> DiceResult:
    """掷骰并返回结果。

    支持基础公式（d20, 2d6+1, 3d8-2）、取高低（2d20kh1, 4d6kh3）及多组加减（1d8+1d6+3）。
    """
    expr = parse_dice_expression(formula)
    rolls: list[int] = []
    faces: list[dict[str, Any]] = []
    natural = 0

    for term in expr.terms:
        term_rolls = [roll_die(term.sides) for _ in range(term.count)]
        rolls.extend(term_rolls)
        if term.keep_mode and term.keep_count is not None:
            indexed = list(enumerate(term_rolls))
            if term.keep_mode == "kh":
                indexed.sort(key=lambda pair: (-pair[1], pair[0]))
            else:
                indexed.sort(key=lambda pair: (pair[1], pair[0]))
            kept_indices = {idx for idx, _val in indexed[:term.keep_count]}
        else:
            kept_indices = set(range(term.count))

        for idx, value in enumerate(term_rolls):
            taken = idx in kept_indices
            faces.append({"sides": term.sides, "value": value, "taken": taken, "sign": term.sign})
            if taken:
                natural += term.sign * value

    total = natural + expr.modifier
    return DiceResult(
        formula=expr.formula,
        rolls=rolls,
        modifier=expr.modifier,
        total=total,
        natural=natural,
        faces=faces,
    )


def parse_dice_formula(formula: str) -> tuple[str, int, int, int]:
    """Validate and normalize a generic dice formula without consuming RNG."""
    formula = str(formula or "").strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d+)?d(\d+)([+-]\d+)?", formula)
    if not match:
        raise ValueError(t("engine.dice_rng.formula_invalid", formula=formula))

    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    mod_str = match.group(3)
    modifier = int(mod_str) if mod_str else 0
    if not 1 <= count <= MAX_DICE_COUNT:
        raise ValueError(t("engine.dice_rng.count_out_of_range", max_dice_count=MAX_DICE_COUNT, formula=formula))
    if not 2 <= sides <= MAX_DICE_SIDES:
        raise ValueError(t("engine.dice_rng.sides_out_of_range", max_dice_sides=MAX_DICE_SIDES, formula=formula))
    if not -MAX_DICE_MODIFIER <= modifier <= MAX_DICE_MODIFIER:
        raise ValueError(t("engine.dice_rng.modifier_out_of_range", max_dice_modifier=MAX_DICE_MODIFIER, formula=formula))

    return formula, count, sides, modifier
