"""骰子 RNG 与通用公式解析 —— 只负责真实随机与骰值，不做任何规则判定。

分层：本模块 = Dice RNG + Generic Dice Parser；
规则层见 dice_rules_d20（d20/娱乐房规）与 dice_rules_coc（CoC 7e）。
"投骰"与"判定成功"不绑死：DC、优势、暴击、剧情结果全由上层规则决定。
"""

from __future__ import annotations

from dataclasses import dataclass

import random
import re
from src.localization.messages import t


@dataclass
class DiceResult:
    """掷骰结果。"""
    formula: str           # "d20+3"
    rolls: list[int]       # [14]
    modifier: int          # 3
    total: int             # 17
    natural: int           # 未加修正的原始值：单骰=该面值，多骰=骰面之和
    is_critical: bool = False   # 由规则层置位（d20 房规：自然 20）
    is_fumble: bool = False     # 由规则层置位（d20 房规：自然 1）


# 公网 bot 防滥用：骰数/面数/修正上下限。
MAX_DICE_COUNT = 100
MAX_DICE_SIDES = 10000
MAX_DICE_MODIFIER = 100


def roll_die(sides: int, *, zero_based: bool = False) -> int:
    """生成一个骰面值；规则层通过此入口取随机值。"""
    if not 2 <= sides <= MAX_DICE_SIDES:
        raise ValueError(t("engine_dice_rng.die_sides_out_of_range", max_dice_sides=MAX_DICE_SIDES, sides=sides))
    return random.randint(0, sides - 1) if zero_based else random.randint(1, sides)


def roll(formula: str) -> DiceResult:
    """掷骰并返回结果。

    支持的格式: d20, d20+3, 2d6, 2d6+1, d100, 3d8-2
    """
    formula, count, sides, modifier = parse_dice_formula(formula)
    rolls = [roll_die(sides) for _ in range(count)]
    total = sum(rolls) + modifier
    natural = rolls[0] if count == 1 else sum(rolls)
    return DiceResult(formula=formula, rolls=rolls, modifier=modifier, total=total, natural=natural)


def parse_dice_formula(formula: str) -> tuple[str, int, int, int]:
    """Validate and normalize a generic dice formula without consuming RNG."""
    formula = str(formula or "").strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d+)?d(\d+)([+-]\d+)?", formula)
    if not match:
        raise ValueError(t("engine_dice_rng.formula_invalid", formula=formula))

    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    mod_str = match.group(3)
    modifier = int(mod_str) if mod_str else 0
    if not 1 <= count <= MAX_DICE_COUNT:
        raise ValueError(t("engine_dice_rng.count_out_of_range", max_dice_count=MAX_DICE_COUNT, formula=formula))
    if not 2 <= sides <= MAX_DICE_SIDES:
        raise ValueError(t("engine_dice_rng.sides_out_of_range", max_dice_sides=MAX_DICE_SIDES, formula=formula))
    if not -MAX_DICE_MODIFIER <= modifier <= MAX_DICE_MODIFIER:
        raise ValueError(t("engine_dice_rng.modifier_out_of_range", max_dice_modifier=MAX_DICE_MODIFIER, formula=formula))

    return formula, count, sides, modifier
