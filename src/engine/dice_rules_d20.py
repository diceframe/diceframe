"""d20 规则层：借鉴 D&D 优势/劣势机制的娱乐房规 + DC 硬上限。

注意：标准 D&D 5e 属性检定并没有自然 20/1 自动成功/失败（自动命中/失手
主要属于攻击检定）。本产品将自然 20/1 保留为娱乐暴击通道，不作严格
D&D 规则宣传。
"""

from __future__ import annotations

from src.engine import check_verdict
from src.engine.dice_rng import DiceResult, roll, roll_die


DEFAULT_D20_DC_CAP = 20
MAX_D20_DC_CAP = 40


def d20_dc_cap(rule: object) -> int:
    """d20 情境 DC 的硬上限，默认 20，可由规则显式提高到 40。

    难度档位表只描述推荐值，不能反向推导安全上限；否则内置规则的
    ``extreme=25`` 会把上限错误推高到 30，重现“只有自然 20 才成功”。
    """
    raw_cap = getattr(rule, "max_check_dc", DEFAULT_D20_DC_CAP) if rule is not None else DEFAULT_D20_DC_CAP
    try:
        cap = int(raw_cap)
    except (TypeError, ValueError):
        cap = DEFAULT_D20_DC_CAP
    return max(1, min(MAX_D20_DC_CAP, cap))


def d20_critical_thresholds(rule: object) -> tuple[int | None, int | None]:
    """从规则元数据读取 d20 大成功/大失败阈值；空配置可禁用房规。"""
    mechanic = getattr(rule, "check_mechanic", None) if rule is not None else None
    if not isinstance(mechanic, dict):
        return 20, 1
    critical = mechanic.get("critical")
    if not isinstance(critical, dict):
        return 20, 1

    def threshold(name: str) -> int | None:
        raw = critical.get(name)
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if 1 <= value <= 20 else None

    return threshold("success"), threshold("failure")


def d20_verdict(
    natural: int,
    total: int,
    dc: int,
    *,
    crit_on: int | None = 20,
    fumble_on: int | None = 1,
) -> str:
    """按规则阈值结算已掷出的 d20；阈值为 None 时只比较总值与 DC。"""
    if crit_on is not None and crit_on <= natural <= 20:
        return check_verdict.CRITICAL_SUCCESS
    if fumble_on is not None and 1 <= natural <= fumble_on:
        return check_verdict.CRITICAL_FAILURE
    return check_verdict.SUCCESS if total >= dc else check_verdict.FAILURE


def check_d20(
    modifier: int = 0,
    dc: int = 10,
    crit_on: int | None = 20,
    fumble_on: int | None = 1,
) -> tuple[DiceResult, str]:
    """d20 属性检定，返回 (结果, 成功等级 code)。

    娱乐化房规：自然 20/1 走大成功/大失败通道（见模块 docstring）。

    Args:
        modifier: 属性修正值
        dc: 难度等级 (Difficulty Class)
        crit_on: 大成功阈值（默认 20，轻松模式可降为 19）
        fumble_on: 大失败阈值（默认 1，硬核模式可升为 2）
    """
    result = roll(f"d20{modifier:+d}" if modifier else "d20")
    verdict = d20_verdict(
        result.natural, result.total, dc, crit_on=crit_on, fumble_on=fumble_on,
    )
    result.is_critical = check_verdict.is_critical(verdict)
    result.is_fumble = check_verdict.is_fumble(verdict)
    return result, verdict


def check_d20_advantage(
    modifier: int = 0,
    dc: int = 10,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    crit_on: int | None = 20,
    fumble_on: int | None = 1,
) -> tuple[DiceResult, str]:
    """d20 优势/劣势检定（借鉴 D&D 优势/劣势机制的娱乐房规）。

    优势：掷 2 个 d20 取高；劣势：掷 2 个 d20 取低。
    同时存在优势和劣势时互相抵消，退回普通 d20 检定。
    """
    if advantage and disadvantage:
        return check_d20(modifier=modifier, dc=dc, crit_on=crit_on, fumble_on=fumble_on)
    if not advantage and not disadvantage:
        return check_d20(modifier=modifier, dc=dc, crit_on=crit_on, fumble_on=fumble_on)

    rolls = [roll_die(20), roll_die(20)]
    natural = max(rolls) if advantage else min(rolls)
    total = natural + modifier
    mode = "kh1" if advantage else "kl1"
    result = DiceResult(
        formula=f"2d20{mode}{modifier:+d}" if modifier else f"2d20{mode}",
        rolls=rolls,
        modifier=modifier,
        total=total,
        natural=natural,
        is_critical=crit_on is not None and crit_on <= natural <= 20,
        is_fumble=fumble_on is not None and 1 <= natural <= fumble_on,
    )
    return result, d20_verdict(
        natural, total, dc, crit_on=crit_on, fumble_on=fumble_on,
    )
