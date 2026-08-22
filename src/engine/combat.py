"""战斗伤害结算。

命中与成败由统一检定引擎生成的 ``CheckResult`` 唯一决定；
本模块不产生 d20/d100 命中骰，也不重新判定成败。处理顺序固定为：
``CheckResult -> 伤害计算 -> 全部修正 -> HP 修改``。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .character_utils import armor_value, set_hp
from .dice_rng import DiceResult

logger = logging.getLogger("trpg")

_SUCCESS_VERDICTS = {
    "成功",
    "普通成功",
    "困难成功",
    "极难成功",
    "大成功",
    "success",
    "regular success",
    "hard success",
    "extreme success",
    "critical success",
}


@dataclass
class AttackResult:
    """已结算的单次攻击结果。

    前八个字段保持旧结构；新字段用于多人对应和幂等复用。
    ``dice`` 仅保留为旧 Python 调用兼容字段，新路径不写入它。
    """

    attacker: str
    target: str
    damage: int
    actual_damage: int
    target_hp_before: int
    target_hp_after: int
    description: str
    dice: DiceResult | None = None
    attacker_uid: str = ""
    target_ref: str = ""
    check_id: str = ""
    weapon: str = "徒手"
    verdict: str = ""
    is_critical: bool = False
    is_fumble: bool = False

    def to_record(self) -> dict[str, Any]:
        """转换为可序列化战斗缓存，不序列化旧 ``DiceResult``。"""
        record = asdict(self)
        record.pop("dice", None)
        return record

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "AttackResult":
        """从存档/重试缓存恢复；额外字段保持向前兼容。"""
        fields = cls.__dataclass_fields__
        values = {key: value for key, value in record.items() if key in fields and key != "dice"}
        return cls(**values)


def _check_succeeded(check_result: Mapping[str, Any]) -> bool:
    if bool(check_result.get("is_fumble")):
        return False
    return str(check_result.get("verdict") or "").strip().casefold() in _SUCCESS_VERDICTS


def calc_hp_based_damage(
    weapon_damage: int,
    attr_modifier: int = 0,
    target_armor: int = 0,
    check_result: Mapping[str, Any] | None = None,
) -> int:
    """计算 HP 模型的命中伤害，不修改角色状态。

    骰面浮动和暴击都只读取已有 ``CheckResult``。该函数本身不负责
    判定是否命中；外层 ``calculate_attack_damage`` 先执行成败门禁。
    """
    base = int(weapon_damage) + int(attr_modifier)
    if check_result:
        if bool(check_result.get("is_fumble")):
            return 0
        if bool(check_result.get("is_critical")):
            base *= 2
        elif str(check_result.get("dice") or check_result.get("dice_system") or "").lower() == "d20":
            resolved_total = int(
                check_result.get("total")
                or check_result.get("roll")
                or 10
            )
            base += (resolved_total - 10) // 3
    return max(1, base - int(target_armor))


def calc_lethal_damage(
    weapon_damage: int,
    attr_modifier: int = 0,
    target_armor: int = 0,
) -> int:
    """计算致命叙事模型的命中伤害，不修改角色状态。"""
    damage = int(weapon_damage) + int(attr_modifier) * 2 - int(target_armor) // 2
    return max(1, damage)


def calculate_attack_damage(
    check_result: Mapping[str, Any],
    *,
    weapon_damage: int,
    attr_modifier: int = 0,
    target_armor: int = 0,
    combat_model: str = "hp_based",
    same_faction: bool = False,
) -> int:
    """消费权威检定并应用全部伤害修正；不修改 HP。"""
    if combat_model == "narrative" or not _check_succeeded(check_result):
        return 0
    if combat_model == "lethal_narrative":
        damage = calc_lethal_damage(weapon_damage, attr_modifier, target_armor)
        if bool(check_result.get("is_critical")):
            damage *= 2
    else:
        damage = calc_hp_based_damage(
            weapon_damage,
            attr_modifier,
            target_armor,
            check_result,
        )
    if same_faction and damage > 0:
        damage //= 2
    return max(0, damage)


def apply_damage(target: dict[str, Any], damage: int) -> tuple[int, int]:
    """在最终伤害已确定后才修改 HP，返回 ``(before, after)``。"""
    before = int(target.get("hp", 0) or 0)
    applied = max(0, int(damage))
    if applied <= 0:
        return before, before
    after = max(0, before - applied)
    set_hp(target, after, target.get("max_hp", before))
    return before, after


def resolve_attack(
    attacker_name: str,
    target: dict,
    weapon: dict | None,
    attr_value: int = 10,
    combat_model: str = "hp_based",
    difficulty: str = "standard",
    *,
    check_result: Mapping[str, Any] | None = None,
    same_faction: bool = False,
    attacker_uid: str = "",
    target_ref: str = "",
    target_name: str = "",
) -> AttackResult:
    """基于已有 ``CheckResult`` 结算一次攻击。

    ``check_result`` 缺失时采用 fail-closed：不掷骰、不扣 HP，从而保留
    旧函数调用形式的同时不再提供隐式二次命中入口。
    """
    del difficulty  # 难度已在 CheckResult 的 DC/阈值中结算。
    weapon_damage = int(weapon.get("damage", 1) or 1) if weapon else 1
    weapon_name = str(weapon.get("name", "徒手") or "徒手") if weapon else "徒手"
    resolved_target_name = target_name or str(
        target.get("character_name") or target.get("name") or "目标"
    )
    target_armor = armor_value(target)
    before = int(target.get("hp", 0) or 0)
    attr_modifier = (int(attr_value) - 10) // 2

    if not check_result:
        return AttackResult(
            attacker=attacker_name,
            target=resolved_target_name,
            damage=0,
            actual_damage=0,
            target_hp_before=before,
            target_hp_after=before,
            description=(
                f"{attacker_name} 尝试使用 {weapon_name} 攻击 {resolved_target_name}"
                "（缺少服务端攻击检定，未结算伤害）"
            ),
            attacker_uid=attacker_uid,
            target_ref=target_ref,
            weapon=weapon_name,
        )

    verdict = str(check_result.get("verdict") or "")
    calculated_damage = calculate_attack_damage(
        check_result,
        weapon_damage=weapon_damage,
        attr_modifier=attr_modifier,
        target_armor=target_armor,
        combat_model=combat_model,
        same_faction=same_faction,
    )
    hp_before, hp_after = apply_damage(target, calculated_damage)
    damage = hp_before - hp_after
    check_id = str(check_result.get("check_id") or "")
    is_critical = bool(check_result.get("is_critical"))
    is_fumble = bool(check_result.get("is_fumble"))

    if combat_model == "narrative":
        description = (
            f"{attacker_name} 对 {resolved_target_name} 发起攻击"
            f"（叙事模式，{verdict}，GM 只可按该检定结果叙事）"
        )
    else:
        friendly_note = "，友军伤害减半" if same_faction and damage > 0 else ""
        description = (
            f"{attacker_name} 使用 {weapon_name} 攻击 {resolved_target_name}"
            f"（{verdict}，伤害 {damage}{friendly_note}）"
            f"{resolved_target_name} HP: {hp_before} → {hp_after}"
        )
        if hp_after <= 0 and hp_before > 0:
            description += f"💀 {resolved_target_name} 倒地昏迷！"

    return AttackResult(
        attacker=attacker_name,
        target=resolved_target_name,
        damage=damage,
        actual_damage=damage,
        target_hp_before=hp_before,
        target_hp_after=hp_after,
        description=description,
        attacker_uid=attacker_uid,
        target_ref=target_ref,
        check_id=check_id,
        weapon=weapon_name,
        verdict=verdict,
        is_critical=is_critical,
        is_fumble=is_fumble,
    )
