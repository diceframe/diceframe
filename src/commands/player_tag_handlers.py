"""玩家状态标签的表驱动分派。"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from src.engine.dice import roll as dice_roll

logger = logging.getLogger("trpg")
PlayerTagHandler = Callable[[str, dict, dict], None]


def _split(value: str) -> tuple[str, str] | None:
    parts = value.split(":", 1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else None


def _parse_int(value: str, *, tag: str, uid: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        logger.warning("%s 数值解析失败，已忽略: %s = %s", tag, uid, value)
        return None


def _player_update(result: dict, uid: str) -> dict:
    return result["state_update"]["players"].setdefault(uid, {})


def _set_int_change(result: dict, uid: str, field: str, value: int) -> None:
    update = _player_update(result, uid)
    update[field] = update.get(field, 0) + value


def _hp(value: str, result: dict, _limits: dict) -> None:
    if split := _split(value):
        uid, change = split
        parsed = _parse_int(change, tag="HP", uid=uid)
        if parsed is not None:
            _set_int_change(result, uid, "hp_change", parsed)


def _gold(value: str, result: dict, limits: dict) -> None:
    if split := _split(value):
        uid, change = split
        raw_amount, separator, reason = change.partition(":")
        parsed = _parse_int(raw_amount.strip(), tag="GOLD", uid=uid)
        if parsed is None:
            return
        if parsed > 0 and parsed <= limits["gold_max"] and separator and reason.strip():
            # Narrative rewards are untrusted model output. They need an explicit
            # human-readable cause and GM approval before changing the balance.
            result.setdefault("state_update", {}).setdefault("economy_proposals", []).append({
                "kind": "reward",
                "uid": uid,
                "amount": parsed,
                "reason": reason.strip()[:240],
                "approval_policy": "gm",
                "source": "legacy_gold_tag",
            })
        else:
            logger.warning("GOLD 奖励缺少正数金额或明确原因，已忽略: %s = %s", uid, change)


def _string_update(value: str, result: dict, field: str, message: str) -> None:
    if split := _split(value):
        uid, text = split
        _player_update(result, uid)[field] = text
        logger.info(message, uid, text)


def _use(value: str, result: dict, _limits: dict) -> None:
    _string_update(value, result, "use_item", "道具使用: %s 使用了 %s")


def _equip(value: str, result: dict, _limits: dict) -> None:
    _string_update(value, result, "equip_gain", "装备获得: %s 获得 %s")


def _weapon(value: str, result: dict, limits: dict) -> None:
    if not (split := _split(value)):
        return
    uid, rest = split
    weapon_name, custom_damage = rest, None
    if ":" in rest:
        name_part, damage_part = rest.rsplit(":", 1)
        parsed = _parse_int(damage_part.strip(), tag="WEAPON", uid=uid)
        if parsed is not None:
            custom_damage = parsed
            weapon_name = name_part.strip()
    from src.engine.constants import WEAPON_DAMAGE

    damage = custom_damage or WEAPON_DAMAGE.get(weapon_name, 3)
    damage = max(1, min(damage, limits["weapon"]))
    update = _player_update(result, uid)
    update["weapon_change"] = weapon_name
    update["weapon_damage"] = damage
    logger.info("武器切换: %s 装备 %s (伤害%d)", uid, weapon_name, damage)


def _xp(value: str, result: dict, _limits: dict) -> None:
    if split := _split(value):
        uid, xp_text = split
        parsed = _parse_int(xp_text, tag="XP", uid=uid)
        if parsed is not None and 0 < parsed <= 500:
            result["xp_rewards"][uid] = result["xp_rewards"].get(uid, 0) + parsed


def _milestone(value: str, result: dict, _limits: dict) -> None:
    target = value.strip()
    if target and len(target) <= 160:
        result.setdefault("milestone_grants", []).append(target)


def _san(value: str, result: dict, _limits: dict) -> None:
    if not (split := _split(value)):
        return
    uid, change = split
    try:
        match = re.match(r"^([+-]?\d+)$", change)
        if match:
            san_change = int(match.group(1))
        else:
            rolled = dice_roll(change)
            san_change = -abs(rolled.total) if not change.startswith("+") else rolled.total
    except (TypeError, ValueError):
        logger.warning("SAN 表达式解析失败，已忽略: %s = %s", uid, change, exc_info=True)
        san_change = 0
    if san_change:
        _set_int_change(result, uid, "san_change", san_change)
        logger.info("理智值标签: %s %+d", uid, san_change)


def _san_check(value: str, result: dict, _limits: dict) -> None:
    _string_update(value, result, "san_check_loss", "理智检定标签: %s 损失=%s")


def _luck(value: str, result: dict, _limits: dict) -> None:
    if split := _split(value):
        uid, change = split
        parsed = _parse_int(change, tag="LUCK", uid=uid)
        if parsed:
            _set_int_change(result, uid, "luck_change", parsed)


def _skill_growth(value: str, result: dict, _limits: dict) -> None:
    if split := _split(value):
        uid, skill_name = split
        result.setdefault("growth_skills", []).append({"uid": uid, "skill": skill_name})


def _push(value: str, result: dict, _limits: dict) -> None:
    _string_update(value, result, "push_skill", "推动检定: %s 推动技能 %s")


def _mana(value: str, result: dict, _limits: dict) -> None:
    if split := _split(value):
        uid, change = split
        parsed = _parse_int(change, tag="MANA", uid=uid)
        if parsed is not None and -50 <= parsed <= 50:
            _set_int_change(result, uid, "mana_change", parsed)


def _revive(value: str, result: dict, _limits: dict) -> None:
    if split := _split(value):
        uid, method = split
        result.setdefault("revive_commands", []).append({"uid": uid, "method": method})


# 这些资源有专属标签或专属结算通道，STAT 不得重复使用。
_STAT_EXCLUSIVE_KEYS = {"hp", "gold", "currency", "pay", "mana", "sanity", "san", "luck", "xp"}


def _stat(value: str, result: dict, _limits: dict) -> None:
    """规则自定义资源标签：STAT:玩家ID:资源key:变化量。"""
    parts = value.split(":")
    if len(parts) != 3:
        logger.warning("STAT 格式无效，已忽略: %s", value)
        return
    uid, stat_key, delta_text = (part.strip() for part in parts)
    stat_key = stat_key.lower()
    if stat_key in _STAT_EXCLUSIVE_KEYS:
        logger.warning("STAT 使用了专属标签资源，已忽略（请用 HP/GOLD/MANA/SAN/LUCK/XP）: %s %s", uid, stat_key)
        return
    parsed = _parse_int(delta_text, tag="STAT", uid=uid)
    if parsed is None:
        return
    if not -100 <= parsed <= 100:
        logger.warning("STAT 变更幅度异常，已忽略: %s %s %+d", uid, stat_key, parsed)
        return
    update = _player_update(result, uid)
    changes = update.setdefault("stat_changes", {})
    changes[stat_key] = changes.get(stat_key, 0) + parsed


PLAYER_TAG_HANDLERS: dict[str, PlayerTagHandler] = {
    "HP": _hp,
    "GOLD": _gold,
    "USE": _use,
    "EQUIP": _equip,
    "WEAPON": _weapon,
    "XP": _xp,
    "MILESTONE": _milestone,
    "SAN": _san,
    "SAN_CHECK": _san_check,
    "LUCK": _luck,
    "SKILL_GROWTH": _skill_growth,
    "PUSH": _push,
    "MANA": _mana,
    "REVIVE": _revive,
    "STAT": _stat,
}


def parse_player_tag(tag: str, value: str, result: dict, limits: dict) -> None:
    handler = PLAYER_TAG_HANDLERS.get(tag)
    if handler is not None:
        handler(value, result, limits)
