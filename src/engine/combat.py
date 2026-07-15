"""战斗结算 —— 三种战斗模型：hp_based / lethal_narrative / narrative。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .character_utils import set_hp
from .dice import DiceResult, check_d100, check_d20, roll

logger = logging.getLogger("trpg")


@dataclass
class AttackResult:
    """攻击结算结果。"""
    attacker: str
    target: str
    damage: int
    actual_damage: int    # 扣除护甲后
    target_hp_before: int
    target_hp_after: int
    description: str
    dice: DiceResult | None = None


def calc_hp_based_damage(
    weapon_damage: int,
    attr_modifier: int = 0,
    target_armor: int = 0,
    dice_result: DiceResult | None = None,
) -> int:
    """标准 HP 战斗伤害计算。

    伤害 = 武器基础 + 属性修正 +/- d20 浮动 - 目标护甲
    普通命中/大成功最小伤害 = 1；大失败 = 0（不应用最小伤害下限）
    """
    base = weapon_damage + attr_modifier
    if dice_result:
        if dice_result.is_critical:
            base *= 2
        elif dice_result.is_fumble:
            return 0
        else:
            dice_offset = dice_result.total - 10  # d20 与平均值 10.5 的偏差
            base += dice_offset // 3
    return max(1, base - target_armor)


def calc_lethal_damage(
    weapon_damage: int,
    attr_modifier: int = 0,
    target_armor: int = 0,
) -> int:
    """致命叙事战斗 —— 伤害更高，每次命中都可能致命。"""
    dmg = weapon_damage + attr_modifier * 2 - target_armor // 2
    return max(1, dmg)


def resolve_attack(
    attacker_name: str,
    target: dict,
    weapon: dict | None,
    attr_value: int = 10,
    combat_model: str = "hp_based",
    difficulty: str = "standard",
) -> AttackResult:
    """解析一次攻击。

    Args:
        attacker_name: 攻击者名称
        target: 目标玩家/NPC 字典（含 hp, armor, character_name 等）
        weapon: 武器字典（含 damage, name 等），None 表示徒手
        attr_value: 攻击方的相关属性值
        combat_model: hp_based / lethal_narrative / narrative
        difficulty: easy / standard / hardcore

    Returns:
        AttackResult
    """
    weapon_damage = weapon.get("damage", 1) if weapon else 1
    weapon_name = weapon.get("name", "徒手") if weapon else "徒手"
    target_name = target.get("character_name", target.get("name", "目标"))
    target_armor = target.get("armor") or target.get("_armor", 0)
    target_hp = target.get("hp", 0)
    attr_mod = (attr_value - 10) // 2  # D&D 式属性修正

    # 难度修正：仅调整大成功/大失败阈值。HP 缩放已移除（原代码每次受击都乘
    # 难度系数导致硬核越打血越多/轻松一碰就碎）。敌人 max_hp 的难度缩放应由
    # 敌人初始化系统处理，见 D10。
    if difficulty == "轻松":
        crit_threshold = 19
        fumble_threshold = 1
    elif difficulty == "硬核":
        crit_threshold = 20
        fumble_threshold = 2
    else:
        crit_threshold = 20
        fumble_threshold = 1

    dice_res: DiceResult | None = None
    verdict: str = ""

    if combat_model == "narrative":
        dice_res, verdict = check_d20(attr_mod, dc=10, crit_on=crit_threshold, fumble_on=fumble_threshold)
        return AttackResult(
            attacker=attacker_name, target=target_name,
            damage=0, actual_damage=0,
            target_hp_before=target_hp, target_hp_after=target_hp,
            description=f"{attacker_name} 对 {target_name} 发起攻击（叙事模式，{verdict}，GM 裁定结果）",
            dice=dice_res,
        )

    if combat_model == "lethal_narrative":
        # D9: d100 命中检定（CoC 风格），未命中 dmg=0
        dice_res, verdict = check_d100(50)
        if verdict in ("失败", "大失败"):
            dmg = 0
        else:
            dmg = calc_lethal_damage(weapon_damage, attr_mod, target_armor)
    else:
        dice_res, verdict = check_d20(attr_mod, dc=12, crit_on=crit_threshold, fumble_on=fumble_threshold)
        dmg = calc_hp_based_damage(weapon_damage, attr_mod, target_armor, dice_res)

    new_hp = max(0, target_hp - dmg)
    set_hp(target, new_hp, target.get("max_hp", target_hp))

    desc_parts = [f"{attacker_name} 使用 {weapon_name} 攻击 {target_name}"]
    if combat_model == "hp_based":
        desc_parts.append(f"（{verdict}，伤害 {dmg}）")
    else:
        desc_parts.append(f"（伤害 {dmg}）")
    desc_parts.append(f"{target_name} HP: {target_hp} → {new_hp}")

    if new_hp <= 0:
        desc_parts.append(f"💀 {target_name} 倒地昏迷！")

    return AttackResult(
        attacker=attacker_name, target=target_name,
        damage=dmg, actual_damage=dmg,
        target_hp_before=target_hp, target_hp_after=new_hp,
        description="".join(desc_parts),
        dice=dice_res,
    )
