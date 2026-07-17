"""角色管理服务：角色列表 / 规则属性辅助 / 角色CRUD / 建卡。"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from src.engine.character_utils import (
    apply_currency_delta,
    build_starter_items,
    calc_hp_from_rule,
    initial_special_stat_value,
    make_default_character,
    normalize_character_sheet,
)
from src.engine.health import record_health_event

if TYPE_CHECKING:
    from src.webui.api import WebAPI

logger = logging.getLogger("trpg")

MAX_BIO_CHARS = 2000  # 角色背景上限（防超长文本污染上下文）

_ATTR_NAME_EN = {
    "str": "STR",
    "con": "CON",
    "dex": "DEX",
    "int": "INT",
    "edu": "EDU",
    "app": "APP",
    "pow": "POW",
    "siz": "SIZ",
    "wis": "WIS",
    "cha": "CHA",
}

_ATTR_NAME_ZH = {
    "str": "力量",
    "con": "体质",
    "dex": "敏捷",
    "int": "智力",
    "edu": "教育",
    "app": "外貌",
    "pow": "意志",
    "siz": "体型",
    "wis": "感知",
    "cha": "魅力",
}


def _normalize_skills(skills: list, rule=None) -> list[dict]:
    """规范化技能列表：字符串转为含数值的对象格式。"""
    base_values: dict[str, int] = rule.skill_base_values if rule else {}
    result: list[dict] = []
    for s in skills:
        if isinstance(s, str):
            result.append({"name": s, "value": base_values.get(s, 20)})
        elif isinstance(s, dict):
            name = s.get("name", "")
            result.append({
                "name": name,
                "value": s.get("value", base_values.get(name, 20)),
            })
    return result


def _format_rule_attr(attr: dict) -> dict:
    key = attr["key"]
    name = attr.get("name") or _ATTR_NAME_ZH.get(key, key)
    name_en = attr.get("name_en") or _ATTR_NAME_EN.get(key, key.upper())
    return {
        "key": key,
        "name": name,
        "name_en": name_en,
        "display_name": f"{name} ({name_en})" if name_en else name,
        "min": attr.get("min", 3),
        "max": attr.get("max", 18),
    }


def format_attribute_map(attributes: dict, rule_attrs: list[dict]) -> str:
    """按规则属性顺序格式化属性，中文界面同时显示中文名与英文 key。"""
    attr_by_key = {a["key"]: a for a in rule_attrs}
    keys = [a["key"] for a in rule_attrs]
    keys.extend(k for k in attributes if k not in attr_by_key)
    parts = []
    for key in keys:
        if key not in attributes:
            continue
        attr = _format_rule_attr(attr_by_key.get(key) or {"key": key})
        parts.append(f"{attr['display_name']}:{attributes[key]}")
    return " ".join(parts)


def list_characters(api: "WebAPI", game_key: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"players": [], "npcs": [], "rule_attrs": []}
    players = [{"user_id": uid, **p} for uid, p in inst.players.items()]
    rule_attrs = _get_rule_attrs_for_game(api, inst)
    rule = api._load_rule_for_game(inst)
    for player in players:
        cs = player.get("character_sheet", {})
        normalize_character_sheet(cs, rule)
        cs["attributes_display"] = format_attribute_map(cs.get("attributes", {}), rule_attrs)
    npcs_by_name: dict[str, dict[str, Any]] = {}
    for nid, npc in inst.npcs.items():
        name = npc.get("character_name") or npc.get("name") or nid
        npcs_by_name[name] = {"npc_id": nid, **npc, "name": name}
    if api._lore and inst.world_id:
        for entry in api._lore.list_entries(inst.world_id, "npc"):
            name = entry.get("name", "")
            if not name or name in npcs_by_name:
                continue
            npcs_by_name[name] = {
                "npc_id": entry.get("id", name),
                "name": name,
                "character_name": name,
                "tier": entry.get("tier", ""),
                "status": "世界书",
                "relation": entry.get("relation", ""),
                "content": entry.get("content", ""),
            }
    npcs = list(npcs_by_name.values())
    rule_attrs_total = _get_rule_attrs_total(api, inst)
    return {"players": players, "npcs": npcs, "rule_attrs": rule_attrs,
            "rule_attrs_total": rule_attrs_total,
            "rule_classes": _get_rule_classes_for_game(api, inst),
            "rule_special_stats": _get_rule_special_stats(api, inst),
            "rule_meta": _get_rule_meta_for_game(api, inst)}


def _get_rule_classes_for_game(api: "WebAPI", inst) -> list[str]:
    try:
        rule = api._load_rule_for_game(inst)
        if rule:
            return rule.get_class_names()
    except Exception:
        logger.exception("读取规则职业失败: world_id=%s", inst.world_id)
    return ["战士", "法师", "游侠", "盗贼", "牧师", "冒险者"]


def _get_rule_attrs_for_game(api: "WebAPI", inst) -> list[dict]:
    try:
        rule = api._load_rule_for_game(inst)
        if rule:
            return [_format_rule_attr(a) for a in rule.attributes]
    except Exception:
        logger.exception("读取规则属性失败: world_id=%s", inst.world_id)
    return [
        _format_rule_attr({"key": k, "name": n, "min": 3, "max": 18})
        for k, n in [("str","力量"),("dex","敏捷"),("con","体质"),("int","智力"),("wis","感知"),("cha","魅力")]
    ]


def _get_rule_attrs_total(api: "WebAPI", inst) -> int:
    try:
        rule = api._load_rule_for_game(inst)
        if rule:
            return rule.attribute_points
    except Exception:
        logger.exception("读取规则属性点失败: world_id=%s", inst.world_id)
    return 60


def _get_rule_special_stats(api: "WebAPI", inst) -> list[dict]:
    try:
        rule = api._load_rule_for_game(inst)
        if rule:
            return rule.special_stats
    except Exception:
        logger.exception("读取特殊属性失败: world_id=%s", inst.world_id)
    return []


def _get_rule_meta_for_game(api: "WebAPI", inst) -> dict[str, Any]:
    try:
        rule = api._load_rule_for_game(inst)
        if rule:
            return {
                "dice_system": rule.dice_system,
                "rule_id": rule.rule_id,
                "mechanics": rule.mechanics,
                "hp_formula": rule.hp_formula,
                "auto_hp": rule.mechanics == "coc7e_core",
                "attr_hint": rule.attr_hint,
                "skill_mode": rule.skill_mode,
                "skill_hint": rule.skill_hint,
                "max_skills": rule.max_skills,
                "skill_point_total": rule.skill_point_total,
                "max_skill_value": rule.max_skill_value,
                "skill_point_spend_mode": rule.skill_point_spend_mode,
                "skill_pools": rule.skill_pools,
                "skill_base_values": rule.skill_base_values,
                "currency": rule.currency,
                "conflict_model": rule.conflict_model,
                "currency_system": rule.currency_system,
                "resource_schema": rule.resource_schema,
                "identity_schema": rule.identity_schema,
                "progression_schema": rule.progression_schema,
                "ui_schema": rule.ui_schema,
            }
    except Exception:
        logger.exception("读取规则建卡提示失败: world_id=%s", inst.world_id)
    return {
        "dice_system": "d20",
        "rule_id": "",
        "mechanics": "freeform_d20_core",
        "hp_formula": "",
        "auto_hp": False,
        "attr_hint": "",
        "skill_mode": "narrative",
        "skill_hint": "",
        "max_skills": 3,
        "skill_point_total": 0,
        "max_skill_value": 0,
        "skill_point_spend_mode": "total_value",
        "skill_pools": {},
        "skill_base_values": {},
        "currency": "金币",
        "conflict_model": {"type": "hp_based"},
        "currency_system": {"base_unit": "unit", "units": [{"id": "unit", "name": "金币", "rate": 1}]},
        "resource_schema": [{"key": "hp", "label": "生命", "min": 0}],
        "identity_schema": [
            {"key": "origin", "label": "种族", "type": "text", "legacy_field": "race"},
            {"key": "archetype", "label": "职业", "type": "text", "legacy_field": "class"},
            {"key": "background", "label": "背景", "type": "text", "legacy_field": "background"},
        ],
        "progression_schema": {"type": "xp_level"},
        "ui_schema": {
            "primary_resources": ["hp"],
            "secondary_resources": [],
            "identity_labels": {"origin": "种族", "archetype": "职业", "background": "背景"},
            "show_level": True,
            "show_xp": True,
            "currency_label": "金币",
            "equipment_label": "装备",
        },
    }


def get_character(api: "WebAPI", game_key: str, user_id: str) -> dict[str, Any] | None:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst or user_id not in inst.players:
        return None
    normalize_character_sheet(inst.players[user_id].setdefault("character_sheet", {}), api._load_rule_for_game(inst))
    return {"user_id": user_id, **inst.players[user_id]}


async def update_character(api: "WebAPI", game_key: str, user_id: str, updates: dict) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst or user_id not in inst.players:
        return {"ok": False, "error": "角色不存在"}
    character_name = str(updates.pop("character_name", "")).strip()
    if character_name:
        inst.players[user_id]["character_name"] = character_name
    cs = inst.get_character_sheet(user_id)
    if "background" in updates and len(str(updates.get("background", ""))) > MAX_BIO_CHARS:
        return {"ok": False, "error": f"角色背景过长（上限 {MAX_BIO_CHARS} 字）"}
    rule = api._load_rule_for_game(inst)
    explicit_hp_update = (
        "hp" in updates
        or "max_hp" in updates
        or (
            isinstance(updates.get("resources"), dict)
            and isinstance(updates.get("resources", {}).get("hp"), dict)
        )
    )
    # 消耗属性点
    new_attrs = updates.get("attributes")
    old_attrs = cs.get("attributes", {})
    pool = cs.get("level_up_points", 0)
    if isinstance(new_attrs, dict) and pool > 0 and isinstance(old_attrs, dict):
        used = sum(max(0, int(new_attrs.get(k, 0)) - int(old_attrs.get(k, 0)))
                   for k in set(old_attrs) | set(new_attrs))
        updates["level_up_points"] = max(0, pool - used)
    cs.update(updates)
    # 确保 attr_points_max 不低于当前属性总和，避免升级后编辑界面卡上线
    try:
        cur_attrs = cs.get("attributes", {})
        if isinstance(cur_attrs, dict) and cur_attrs:
            total = sum(int(v) for v in cur_attrs.values())
            stored = cs.get("attr_points_max", 0)
            if total > stored:
                cs["attr_points_max"] = total
                logger.info("attr_points_max 修正: %d -> %d (uid=%s)", stored, total, user_id)
    except Exception:
        pass
    # 规范化技能格式
    if "skills" in updates:
        cs["skills"] = _normalize_skills(updates.get("skills", []), rule)
    # 属性变化后可按规则补算 HP；若用户明确手填 HP，则尊重手填值。
    new_attrs = updates.get("attributes")
    if isinstance(new_attrs, dict) and not explicit_hp_update:
        try:
            base_hp = (
                rule.calculate_hp(new_attrs, cs.get("class", ""))
                if rule
                else calc_hp_from_rule(new_attrs, rules_dir=api._rules_dir, language=getattr(inst, "language", ""))
            )
            lv_bonus = max(0, (cs.get("level", 1) - 1) * 5)
            new_hp = base_hp + lv_bonus
            curr_hp_ratio = cs.get("hp", 1) / max(1, cs.get("max_hp", 1))
            cs["max_hp"] = new_hp
            cs["hp"] = max(1, round(new_hp * curr_hp_ratio))
            logger.info("HP 重算: con=%s HP=%d->%d",
                new_attrs.get("con", "?"), cs.get("max_hp", 0), new_hp)
        except Exception as exc:
            logger.warning("属性变化后 HP 重算失败: %s", exc)
    normalize_character_sheet(cs, rule)
    inst.set_character_sheet(user_id, cs)
    await api._reg.save(inst)
    try:
        api.save_character_card({
            "character_name": inst.players[user_id].get("character_name", ""),
            "character_sheet": cs,
        })
    except Exception:
        logger.warning("角色卡同步入库失败: uid=%s", user_id, exc_info=True)
    return {"ok": True}


async def resolve_payment(api: "WebAPI", game_key: str, payment_id: str, accepted: bool, session_uid: str = "") -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    payment = next(
        (p for p in getattr(inst, "pending_payments", []) if p.get("id") == payment_id),
        None,
    )
    if not payment:
        return {"ok": False, "error": "支付请求不存在"}
    if payment.get("status") != "pending":
        return {"ok": False, "error": "支付请求已处理"}

    uid = payment.get("uid", "")
    # 权限：GM 或支付当事玩家可处理（玩家自己确认/拒绝购买）
    if session_uid and session_uid != inst.gm_uid and session_uid != uid:
        return {"ok": False, "error": "仅 GM 或当事玩家可处理支付"}
    amount = int(payment.get("amount", 0) or 0)
    if accepted:
        if uid not in inst.players:
            return {"ok": False, "error": "支付角色不存在"}
        cs = inst.get_character_sheet(uid)
        current_gold = int(cs.get("gold", 0) or 0)
        if current_gold < amount:
            return {"ok": False, "error": f"金币不足：需要 {amount}，当前 {current_gold}"}
        apply_currency_delta(cs, -amount)
        inst.set_character_sheet(uid, cs)
        payment["status"] = "accepted"
    else:
        payment["status"] = "rejected"
        name = inst.players.get(uid, {}).get("character_name", uid)
        record_health_event(
            inst,
            component="economy",
            code="payment_rejected",
            severity="info",
            title="玩家拒绝支付",
            message=f"{name} 拒绝支付 {amount} 金币（第 {payment.get('round', inst.round_number)} 轮建议）",
        )
    payment["resolved_at"] = time.time()
    inst.pending_payments = [
        p for p in getattr(inst, "pending_payments", [])
        if p.get("status") == "pending"
    ]
    await api._reg.save(inst)
    return {"ok": True, "accepted": bool(accepted), "payment": payment}


async def delete_character(api: "WebAPI", game_key: str, user_id: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst or user_id not in inst.players:
        return {"ok": False, "error": "角色不存在"}
    if len(inst.players) <= 1:
        return {"ok": False, "error": "至少保留一个角色，无法删除最后一个"}
    name = inst.players[user_id].get("character_name", user_id)
    removed = await inst.remove_player(user_id)
    if not removed:
        return {"ok": False, "error": "角色不存在"}
    inst.pending_payments = [
        p for p in getattr(inst, "pending_payments", [])
        if p.get("uid") != user_id
    ]
    inst.private_log.pop(user_id, None)
    await api._reg.save(inst)
    logger.info("角色已删除: %s (%s)", name, game_key)
    return {"ok": True}


async def create_player(api: "WebAPI", game_key: str, character: dict,
                       force_uid: str = "", assign_new_id: bool = False) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    requested_uid = str(character.get("user_id") or "").strip()
    if requested_uid and requested_uid in inst.players:
        return {
            "ok": True,
            "user_id": requested_uid,
            "character_name": inst.players[requested_uid].get("character_name", requested_uid),
            "reused": True,
        }
    # uid 决策：GM 代建(assign_new_id)生成独立 uid；否则优先 force_uid（session 身份）或显式 requested_uid
    if assign_new_id:
        uid = "player_" + str(time.time_ns())[-12:]
    elif force_uid:
        if force_uid in inst.players:
            return {"ok": True, "user_id": force_uid,
                    "character_name": inst.players[force_uid].get("character_name", force_uid),
                    "reused": True}
        uid = force_uid
    elif requested_uid:
        uid = requested_uid
    else:
        uid = "player_" + str(time.time_ns())[-12:]
    rule = api._load_rule_for_game(inst)
    rule_id = rule.rule_id if rule else "freeform_fantasy"
    # 仅传了名字的轻量加入会自动生成默认角色卡。
    has_full_sheet = bool(character.get("attributes") or character.get("equipment") or character.get("skills"))
    if not has_full_sheet:
        name = character.get("name") or character.get("character_name") or "冒险者"
        try:
            templates_base = api._rules_dir.parent if api._rules_dir else None
            character = make_default_character(name, rule_id or "freeform_fantasy", templates_base, language=getattr(inst, "language", ""))
            character["character_name"] = name
        except Exception:
            logger.exception("生成默认角色卡失败: %s", name)
    attrs = character.get("attributes", {})
    rule_attrs = _get_rule_attrs_for_game(api, inst)
    total_points = sum(int(attrs.get(r["key"], 10)) for r in rule_attrs) if rule_attrs else 60
    default_weapons = [{"name": "徒手", "type": "weapon", "damage": 2, "slot": "main_hand", "quality": "common"}]
    hp = character.get("hp")
    max_hp = character.get("max_hp")
    if hp is None or max_hp is None:
        hp = (
            rule.calculate_hp(attrs, character.get("class", ""))
            if rule
            else calc_hp_from_rule(attrs, rule_id, api._rules_dir, character.get("class", ""), language=getattr(inst, "language", ""))
        )
        max_hp = hp
    default_class = rule.classes[0]["name"] if (rule and rule.classes) else "冒险者"
    if len(str(character.get("background", ""))) > MAX_BIO_CHARS:
        return {"ok": False, "error": f"角色背景过长（上限 {MAX_BIO_CHARS} 字）"}
    starter_equip, starter_inv = build_starter_items(rule, character.get("class") or default_class)
    cs = {
        "race": character.get("race", "人类"),
        "class": character.get("class") or default_class,
        "level": 1, "xp": 0,
        "attributes": attrs,
        "hp": hp, "max_hp": max_hp,
        "equipment": character.get("equipment") or starter_equip or default_weapons,
        "inventory": character.get("inventory") or starter_inv,
        "key_items": character.get("key_items", []),
        "skills": _normalize_skills(character.get("skills", []), rule),
        "background": character.get("background", ""),
        "deceased": False,
        "gold": character.get("gold", 30),
        "attr_points_max": total_points,
    }
    # 初始化 special_stats（理智值/幸运值/内力等）
    try:
        if rule:
            for ss in rule.special_stats:
                max_val = ss.get("max", 99)
                init_val = initial_special_stat_value(ss, attrs)
                cs[ss["key"]] = init_val
                cs[f"max_{ss['key']}"] = max_val
    except Exception as exc:
        logger.exception("初始化特殊属性失败: %s", exc)
    normalize_character_sheet(cs, rule)
    inst.players[uid] = {
        "character_name": character.get("character_name") or character.get("name") or "冒险者",
        "character_sheet": cs,
    }
    api.save_character_card(inst.players[uid])
    await api._reg.save(inst)
    return {"ok": True, "user_id": uid, "character_name": inst.players[uid]["character_name"]}
