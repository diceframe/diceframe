"""骰子系统 —— 纯代码掷骰，不依赖 LLM 生成随机数。"""

from __future__ import annotations

from dataclasses import dataclass

import random
import re


@dataclass
class DiceResult:
    """掷骰结果。"""
    formula: str           # "d20+3"
    rolls: list[int]       # [14]
    modifier: int          # 3
    total: int             # 17
    natural: int           # 14 (未加修正的原始值，对 d20 有意义)
    is_critical: bool = False   # d20=20
    is_fumble: bool = False     # d20=1


def roll(formula: str) -> DiceResult:
    """掷骰并返回结果。

    支持的格式: d20, d20+3, 2d6, 2d6+1, d100, 3d8-2
    """
    formula = formula.strip().lower().replace(" ", "")
    match = re.match(r"(\d+)?d(\d+)([+-]\d+)?$", formula)
    if not match:
        raise ValueError(f"无效的掷骰公式: {formula}")

    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    mod_str = match.group(3)
    modifier = int(mod_str) if mod_str else 0

    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + modifier
    natural = rolls[0] if count == 1 else total

    is_critical = count == 1 and sides == 20 and natural == 20
    is_fumble = count == 1 and sides == 20 and natural == 1

    return DiceResult(
        formula=formula, rolls=rolls, modifier=modifier,
        total=total, natural=natural,
        is_critical=is_critical, is_fumble=is_fumble,
    )


def check_d20(modifier: int = 0, dc: int = 10, crit_on: int = 20, fumble_on: int = 1) -> tuple[DiceResult, str]:
    """d20 属性检定，返回 (结果, "成功"/"失败"/"大成功"/"大失败")。

    Args:
        modifier: 属性修正值
        dc: 难度等级 (Difficulty Class)
        crit_on: 大成功阈值（默认 20，轻松模式可降为 19）
        fumble_on: 大失败阈值（默认 1，硬核模式可升为 2）
    """
    result = roll(f"d20{modifier:+d}" if modifier else "d20")
    if crit_on <= result.natural <= 20:
        result.is_critical = True
        return result, "大成功"
    if 1 <= result.natural <= fumble_on:
        result.is_fumble = True
        return result, "大失败"
    return result, "成功" if result.total >= dc else "失败"


def check_d20_advantage(
    modifier: int = 0,
    dc: int = 10,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    crit_on: int = 20,
    fumble_on: int = 1,
) -> tuple[DiceResult, str]:
    """D&D 5e 风格 d20 优势/劣势检定。

    优势：掷 2 个 d20 取高；劣势：掷 2 个 d20 取低。
    同时存在优势和劣势时互相抵消，退回普通 d20 检定。
    """
    if advantage and disadvantage:
        return check_d20(modifier=modifier, dc=dc, crit_on=crit_on, fumble_on=fumble_on)
    if not advantage and not disadvantage:
        return check_d20(modifier=modifier, dc=dc, crit_on=crit_on, fumble_on=fumble_on)

    rolls = [random.randint(1, 20), random.randint(1, 20)]
    natural = max(rolls) if advantage else min(rolls)
    total = natural + modifier
    mode = "kh1" if advantage else "kl1"
    result = DiceResult(
        formula=f"2d20{mode}{modifier:+d}" if modifier else f"2d20{mode}",
        rolls=rolls,
        modifier=modifier,
        total=total,
        natural=natural,
        is_critical=crit_on <= natural <= 20,
        is_fumble=1 <= natural <= fumble_on,
    )
    if result.is_critical:
        return result, "大成功"
    if result.is_fumble:
        return result, "大失败"
    return result, "成功" if total >= dc else "失败"


def check_d100(threshold: int) -> tuple[DiceResult, str]:
    """d100 技能检定（掷 d100 ≤ skill 值 = 成功，用于 CoC 类规则）。"""
    result = roll("d100")
    if result.natural <= 5:
        return result, "大成功"
    if result.natural >= 96:
        return result, "大失败"
    if result.natural <= threshold:
        return result, "成功"
    return result, "失败"


def coc_success_level(roll_value: int, threshold: int) -> str:
    """CoC 7e 风格成功等级。"""
    if roll_value <= 1:
        return "大成功"
    if roll_value >= 100 or (threshold < 50 and roll_value >= 96):
        return "大失败"
    if roll_value > threshold:
        return "失败"
    if roll_value <= threshold // 5:
        return "极难成功"
    if roll_value <= threshold // 2:
        return "困难成功"
    return "普通成功"


def check_coc(threshold: int) -> tuple[DiceResult, str]:
    """CoC 7e 风格 d100 检定，返回成功等级。"""
    threshold = max(1, min(99, int(threshold)))
    result = roll("d100")
    verdict = coc_success_level(result.natural, threshold)
    result.is_critical = verdict == "大成功"
    result.is_fumble = verdict == "大失败"
    return result, verdict


def check_d100_bonus(threshold: int, bonus_dice: int = 0, penalty_dice: int = 0) -> tuple[DiceResult, str]:
    """CoC 7e 奖励骰/惩罚骰。

    bonus_dice: 奖励骰数量（掷多个十位骰取最优，即最小值）
    penalty_dice: 惩罚骰数量（掷多个十位骰取最差，即最大值）
    """
    if bonus_dice < 0 or penalty_dice < 0:
        raise ValueError("奖励骰和惩罚骰数量不能为负数")
    if bonus_dice and penalty_dice:
        cancel_count = min(bonus_dice, penalty_dice)
        bonus_dice -= cancel_count
        penalty_dice -= cancel_count
    units = random.randint(0, 9)
    extra_count = max(bonus_dice, penalty_dice)
    all_tens = [random.randint(0, 9) * 10 for _ in range(1 + extra_count)]
    if penalty_dice > 0:
        best_ten = max(all_tens)
    else:
        best_ten = min(all_tens)
    total = best_ten + units
    if total == 0:
        total = 100
    if total <= 5:
        verdict = "大成功"
    elif total >= 96:
        verdict = "大失败"
    elif total <= threshold:
        verdict = "成功"
    else:
        verdict = "失败"
    result = DiceResult(formula=f"d100", rolls=[total], modifier=0,
                        total=total, natural=total,
                        is_critical=(total <= 5), is_fumble=(total >= 96))
    return result, verdict


def parse_player_roll(text: str) -> DiceResult | None:
    """尝试从玩家文本中解析手动掷骰指令，如 /掷骰 2d6 或 掷骰 d20+3。"""
    match = re.search(r"掷骰\s*(\d*d\d+\s*[+-]?\s*\d*)", text)
    if not match:
        return None
    formula = match.group(1).strip().replace(" ", "")
    try:
        return roll(formula)
    except ValueError:
        return None


@dataclass
class InitResult:
    """先攻检定结果。"""
    natural: int
    total: int
    modifier: int


def roll_initiative(dex_modifier: int = 0) -> InitResult:
    """先攻检定: d20 + 敏捷修正。"""
    natural = random.randint(1, 20)
    total = natural + dex_modifier
    return InitResult(natural=natural, total=total, modifier=dex_modifier)
