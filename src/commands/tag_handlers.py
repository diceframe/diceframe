"""Typed handlers for GM state tags parsed from LLM output."""

from __future__ import annotations

import logging
import re

from src.engine.dice import roll as dice_roll

logger = logging.getLogger("trpg")

KNOWN_TAGS = frozenset({
    "HP", "GOLD", "PAY", "SCENE", "NPC", "LOOT", "KEY_ITEM", "DECISION", "QUEST", "USE",
    "WEAPON", "EQUIP", "PRIVATE", "XP", "SAN", "SAN_CHECK", "LUCK", "SKILL_GROWTH",
    "PUSH", "PUZZLE", "MANA", "SPELL", "QUICK_ACTIONS", "COMBAT", "REVIVE",
    "CONFIRMED", "MEMORY",
})

LIMITS_BY_COMBAT_MODEL = {
    "lethal_narrative": {"hp_max": 20, "hp_heal": 10, "gold_max": 200, "gold_loss": 50, "weapon": 12},
    "narrative": {"hp_max": 30, "hp_heal": 15, "gold_max": 300, "gold_loss": 80, "weapon": 15},
    "hp_based": {"hp_max": 50, "hp_heal": 20, "gold_max": 500, "gold_loss": 100, "weapon": 15},
}

PLAYER_TAGS = frozenset({
    "HP", "PAY", "GOLD", "USE", "EQUIP", "WEAPON", "XP", "SAN", "SAN_CHECK",
    "LUCK", "SKILL_GROWTH", "PUSH", "MANA", "REVIVE",
})
WORLD_TAGS = frozenset({
    "CONFIRMED", "MEMORY", "SCENE", "NPC", "DECISION", "QUEST", "PRIVATE",
})
LOOT_TAGS = frozenset({"LOOT", "KEY_ITEM"})
ACTION_TAGS = frozenset({"PUZZLE", "SPELL", "QUICK_ACTIONS", "COMBAT"})


_PERSON_SUFFIXES = (
    "年轻人", "中年人", "老年人", "老人", "小孩", "孩子", "少年", "少女", "青年",
    "男子", "女子", "男人", "女人", "老头", "老太", "小伙子", "姑娘", "婴儿",
    "先生", "小姐", "女士", "太太", "夫人", "少爷", "大人", "同志",
    "博士", "教授", "医生", "护士", "律师", "侦探",
    "警官", "警察", "探员", "特工", "士兵", "军官",
    "记者", "学者", "作家", "画家", "诗人",
    "师傅", "师父", "老板", "掌柜", "管家",
    "神父", "牧师", "修女", "僧人", "道士", "和尚",
    "渔夫", "农夫", "铁匠", "商人", "仆人", "佣人", "侍女", "侍从",
    "证人", "嫌疑人", "嫌犯", "罪犯", "凶手", "受害者", "死者", "当事人",
    "目击者", "知情者", "参与者", "幸存者", "失踪者",
    "门徒", "弟子", "信徒", "追随者",
)


def _looks_like_person(name: str) -> bool:
    """检查物品名是否疑似人物而非实体物品（仅用于告警，不拦截）。

    GM 误将人物标为 KEY_ITEM 时打 warning log 供运维监控，
    物品照常写入 key_items，不重定向、不丢弃。
    仅做后缀匹配，避免误伤含人称词的实体物品名（如"年轻人的日记"不会命中）。
    """
    return any(name.endswith(suffix) for suffix in _PERSON_SUFFIXES)


def _split_tag_value(value: str) -> tuple[str, str] | None:
    parts = value.split(":", 1)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def _parse_int(value: str, *, tag: str = "", uid: str = "") -> int | None:
    try:
        return int(value)
    except ValueError:
        if tag:
            logger.warning("%s 数值解析失败，已忽略: %s = %s", tag, uid, value)
        return None


def _player_update(result: dict, uid: str) -> dict:
    return result["state_update"]["players"].setdefault(uid, {})


def _set_int_change(
    result: dict,
    uid: str,
    field: str,
    value: int,
    *,
    add: bool = False,
) -> dict:
    update = _player_update(result, uid)
    update[field] = update.get(field, 0) + value if add else value
    return update


def parse_player_tag(tag: str, value: str, result: dict, limits: dict) -> None:
    """玩家字段标签：HP/GOLD/PAY/SAN/LUCK/MANA/XP/SKILL_GROWTH/USE/EQUIP/WEAPON/PUSH/REVIVE。"""
    if tag == "HP":
        split = _split_tag_value(value)
        if split:
            uid, change = split
            v = _parse_int(change, tag=tag, uid=uid)
            if v is not None:
                # D8: 解析时不限上限，由 StateUpdateApplier 按玩家 max_hp 截断
                _set_int_change(result, uid, "hp_change", v, add=True)
    elif tag == "PAY":
        # PAY 不直接扣金币，转为待确认支付条目，由玩家在弹窗里确认/拒绝
        split = _split_tag_value(value)
        if split:
            uid, change = split
            v = _parse_int(change, tag=tag, uid=uid)
            if v is not None:
                amount = abs(v)
                if 0 < amount <= limits["gold_loss"]:
                    result.setdefault("state_update", {}).setdefault("pending_payments", []).append(
                        {"uid": uid, "amount": amount, "reason": "GM 建议支付"}
                    )
                else:
                    logger.warning("PAY 金额异常，已忽略: %s = %d", uid, amount)
    elif tag == "GOLD":
        split = _split_tag_value(value)
        if split:
            uid, change = split
            v = _parse_int(change, tag=tag, uid=uid)
            if v is not None:
                # GOLD 负数表示支出（GM 偶尔写 GOLD:-3），正数表示获得
                if -limits["gold_loss"] <= v <= limits["gold_max"]:
                    _set_int_change(result, uid, "gold_change", v, add=True)
                else:
                    logger.warning("GOLD 变更异常，已忽略: %s = %d", uid, v)
    elif tag == "USE":
        # 玩家使用道具: USE:玩家ID:道具名 -> 扣库存+效果
        split = _split_tag_value(value)
        if split:
            uid, item_name = split
            player_update = _player_update(result, uid)
            player_update["use_item"] = item_name
            logger.info("道具使用: %s 使用了 %s", uid, item_name)
    elif tag == "EQUIP":
        split = _split_tag_value(value)
        if split:
            uid, item_name = split
            player_update = _player_update(result, uid)
            player_update["equip_gain"] = item_name
            logger.info("装备获得: %s 获得 %s", uid, item_name)
    elif tag == "WEAPON":
        split = _split_tag_value(value)
        if split:
            uid, rest = split
            weapon_name, custom_dmg = rest, None
            if ":" in rest:
                name_part, dmg_part = rest.rsplit(":", 1)
                parsed_dmg = _parse_int(dmg_part.strip(), tag=tag, uid=uid)
                if parsed_dmg is not None:
                    custom_dmg = parsed_dmg
                    weapon_name = name_part.strip()
            from src.engine.constants import WEAPON_DAMAGE
            dmg = custom_dmg or WEAPON_DAMAGE.get(weapon_name, 3)
            dmg = max(1, min(dmg, limits["weapon"]))
            player_update = _player_update(result, uid)
            player_update["weapon_change"] = weapon_name
            player_update["weapon_damage"] = dmg
            logger.info("武器切换: %s 装备 %s (伤害%d)", uid, weapon_name, dmg)
    elif tag == "XP":
        split = _split_tag_value(value)
        if split:
            uid, xp_str = split
            xp_val = _parse_int(xp_str, tag=tag, uid=uid)
            if xp_val is not None:
                if 0 < xp_val <= 500:
                    result["xp_rewards"][uid] = result["xp_rewards"].get(uid, 0) + xp_val
    elif tag == "SAN":
        split = _split_tag_value(value)
        if split:
            uid, change = split
            try:
                match = re.match(r"^([+-]?\d+)$", change)
                if match:
                    san_change = int(match.group(1))
                else:
                    dice_res = dice_roll(change)
                    san_change = -abs(dice_res.total) if not change.startswith("+") else dice_res.total
            except Exception:
                san_change = 0
            if san_change != 0:
                player_update = result["state_update"]["players"].setdefault(uid, {})
                player_update["san_change"] = player_update.get("san_change", 0) + san_change
                logger.info("理智值标签: %s %+d", uid, san_change)
    elif tag == "SAN_CHECK":
        split = _split_tag_value(value)
        if split:
            uid, loss_expr = split
            player_update = _player_update(result, uid)
            player_update["san_check_loss"] = loss_expr
            logger.info("理智检定标签: %s 损失=%s", uid, loss_expr)
    elif tag == "LUCK":
        split = _split_tag_value(value)
        if split:
            uid, change = split
            luck_change = _parse_int(change, tag=tag, uid=uid)
            if luck_change is not None:
                if luck_change != 0:
                    _set_int_change(result, uid, "luck_change", luck_change, add=True)
    elif tag == "SKILL_GROWTH":
        split = _split_tag_value(value)
        if split:
            uid, skill_name = split
            result.setdefault("growth_skills", [])
            result["growth_skills"].append({"uid": uid, "skill": skill_name})
    elif tag == "PUSH":
        split = _split_tag_value(value)
        if split:
            uid, skill_name = split
            _player_update(result, uid)["push_skill"] = skill_name
            logger.info("推动检定: %s 推动技能 %s", uid, skill_name)
    elif tag == "MANA":
        split = _split_tag_value(value)
        if split:
            uid, change = split
            v = _parse_int(change, tag=tag, uid=uid)
            if v is not None:
                if -50 <= v <= 50:
                    _set_int_change(result, uid, "mana_change", v, add=True)
    elif tag == "REVIVE":
        split = _split_tag_value(value)
        if split:
            uid, method = split
            result.setdefault("revive_commands", [])
            result["revive_commands"].append({"uid": uid, "method": method})


def parse_world_tag(tag: str, value: str, result: dict) -> None:
    """世界/剧情标签：SCENE/NPC/QUEST/DECISION/MEMORY/PRIVATE/CONFIRMED。"""
    if tag == "CONFIRMED":
        result.setdefault("confirmed", []).append(value)
    elif tag == "MEMORY":
        if value.strip():
            result["memory_delta"]["add"].append(value)
        else:
            logger.warning("MEMORY tag empty, skipped")
    elif tag == "SCENE":
        result["state_update"]["scene_change"] = value[:200]
    elif tag == "NPC":
        parts_n = value.split(":", 1)
        if len(parts_n) == 2:
            name, relation = parts_n[0].strip(), parts_n[1].strip()
            name = name[:80]
            relation = relation[:40]
            result["state_update"]["npcs"][name] = {"name": name, "tier": relation}
    elif tag == "DECISION":
        result["plot_update"]["decisions"].append(value[:300])
    elif tag == "QUEST":
        parts_q = value.rsplit(":", 1)
        if len(parts_q) == 2:
            result["plot_update"]["quests"].append({
                "title": parts_q[0].strip(),
                "status": parts_q[1].strip(),
            })
    elif tag == "PRIVATE":
        parts_p = value.split(":", 1)
        if len(parts_p) == 2:
            result["info_asymmetry"][parts_p[0].strip()] = parts_p[1].strip()


def parse_loot_tag(tag: str, value: str, result: dict) -> None:
    """战利品标签：LOOT/KEY_ITEM。"""
    if tag == "LOOT":
        parts_l = value.split(":", 1)
        if len(parts_l) >= 1:
            uid = parts_l[0].strip()
            item = parts_l[1].strip() if len(parts_l) > 1 else ""
            result["state_update"]["loot"].append({"player": uid, "item": item[:120]})
    elif tag == "KEY_ITEM":
        parts_k = value.split(":", 1)
        if len(parts_k) == 2:
            uid, item = parts_k[0].strip(), parts_k[1].strip()
            if _looks_like_person(item):
                logger.warning(
                    "KEY_ITEM 疑似人物而非物品（已照常写入，请检查 GM prompt）: %s", item,
                )
            result["state_update"]["loot"].append({
                "player": uid,
                "item": item[:120],
                "category": "key_item",
            })


def parse_action_tag(tag: str, value: str, result: dict) -> None:
    """行动/谜题标签：PUZZLE/SPELL/QUICK_ACTIONS/COMBAT。"""
    if tag == "PUZZLE":
        parts_pz = value.split(":", 1)
        if len(parts_pz) == 2:
            puzzle_id, puzzle_state = parts_pz[0].strip(), parts_pz[1].strip()
            result.setdefault("puzzle_updates", {})
            result["puzzle_updates"][puzzle_id] = puzzle_state
    elif tag == "SPELL":
        parts_sp = value.split(":", 1)
        if len(parts_sp) == 2:
            uid, spell_name = parts_sp[0].strip(), parts_sp[1].strip()
            player_update = result["state_update"]["players"].setdefault(uid, {})
            player_update["cast_spell"] = spell_name
            logger.info("施法: %s 使用了 %s", uid, spell_name)
    elif tag == "QUICK_ACTIONS":
        result["quick_actions"] = [a.strip() for a in value.split("|") if a.strip()][:4]
    elif tag == "COMBAT":
        result["combat_command"] = value.strip()  # start / end
