"""Fabula Ultima 规则层：两枚属性骰相加、双同点爆击、高值伤害。

与 d20 / CoC 的差别（这是本模块存在的理由）：

- **两枚骰子相加**，不是单骰，也不是取高。骰面来自角色卡上的属性值，
  合法值只有 6 / 8 / 10 / 12 四档（d6 / d8 / d10 / d12）。
- **大成功 = 两枚骰子同点且 ≥6**（双 6、双 7……），自动成功并获得一个“机会”；
  **大失败 = 两枚都是 1**。两者都不改变伤害，只改变成败。
- **伤害读“高值”**：命中后取同一对骰子里较大的那枚 + 武器/法术加值。
  高值由 ``fu_high_roll`` 提供给战斗层，不在这里重复实现。
- 没有优势/劣势骰。叙事资源（物语点重掷）由上层处理，本层只负责一次成型的判定。
"""

from __future__ import annotations

from src.engine.dice_rng import DiceResult, roll_die

#: 合法骰面，从小到大。角色卡上的属性值直接存这四个数。
FU_DIE_SIZES: tuple[int, ...] = (6, 8, 10, 12)

#: 双同点达到这个点数才算大成功（双 6 起）。
FU_CRIT_MIN = 6

#: 单次检定的最大可能值（d12 + d12）。
FU_MAX_ROLL = FU_DIE_SIZES[-1] * 2


def fu_die_size(value: object, default: int = 6) -> int:
    """把角色卡上的属性值规范成合法骰面。

    容错策略：非数字、越界、非偶数一律向下取到最近的合法档，
    保证旧卡（3-18 属性）也能跑起来而不是抛异常。
    """
    try:
        raw = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if raw <= FU_DIE_SIZES[0]:
        return FU_DIE_SIZES[0]
    for size in FU_DIE_SIZES:
        if raw <= size:
            return size
    return FU_DIE_SIZES[-1]


def fu_high_roll(rolls: list[int] | tuple[int, ...]) -> int:
    """伤害用的高值：两枚骰子中较大的那枚。"""
    return max(int(value) for value in rolls) if rolls else 0


def fu_is_critical(rolls: list[int] | tuple[int, ...], crit_min: int = FU_CRIT_MIN) -> bool:
    """双同点 ≥6 即大成功。"""
    if len(rolls) < 2:
        return False
    first, second = int(rolls[0]), int(rolls[1])
    return first == second and first >= crit_min


def fu_is_fumble(rolls: list[int] | tuple[int, ...]) -> bool:
    """两枚都是 1 即大失败。"""
    return len(rolls) >= 2 and all(int(value) == 1 for value in rolls[:2])


def fu_verdict(
    rolls: list[int] | tuple[int, ...],
    total: int,
    dc: int,
    *,
    crit_min: int = FU_CRIT_MIN,
) -> str:
    """Fabula Ultima 裁决：先判双同点/双 1，再比总值与 DL。"""
    if fu_is_fumble(rolls):
        return "大失败"
    if fu_is_critical(rolls, crit_min):
        return "大成功"
    return "成功" if total >= dc else "失败"


def check_fu(
    sides_a: int,
    sides_b: int,
    modifier: int = 0,
    dc: int = 10,
    *,
    crit_min: int = FU_CRIT_MIN,
) -> tuple[DiceResult, str]:
    """掷两枚属性骰并裁决，返回 (结果, 裁决)。

    与 d20 的实现保持同一形状：``natural`` 是未加修正的骰面和，
    ``rolls`` 保留两枚原始骰值（伤害要高值、爆击要判同点，都依赖它）。
    """
    first = roll_die(fu_die_size(sides_a))
    second = roll_die(fu_die_size(sides_b))
    rolls = [first, second]
    natural = first + second
    total = natural + int(modifier)
    verdict = fu_verdict(rolls, total, int(dc), crit_min=crit_min)
    formula = f"d{fu_die_size(sides_a)}+d{fu_die_size(sides_b)}"
    if modifier:
        formula += f"{modifier:+d}"
    result = DiceResult(
        formula=formula,
        rolls=rolls,
        modifier=int(modifier),
        total=total,
        natural=natural,
        is_critical=verdict == "大成功",
        is_fumble=verdict == "大失败",
    )
    return result, verdict


def fu_roll_pair(sides_a: int, sides_b: int) -> list[int]:
    """只掷骰、不裁决：供需要先落骰再结算的管线使用。"""
    return [roll_die(fu_die_size(sides_a)), roll_die(fu_die_size(sides_b))]
