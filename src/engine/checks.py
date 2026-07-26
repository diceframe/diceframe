"""统一检定请求：从玩家行动生成规则无关的 CheckRequest，并完成原始掷骰。"""

from __future__ import annotations

import re
import uuid
from typing import Any

from src.engine.dice import roll
from src.engine.game_instance import GameInstance
from src.engine.language import is_english
from src.rules.rule_system import RuleSystem


_INTENT_SPECS: tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        "stealth",
        ("潜行", "潜入", "隐匿", "隐藏", "悄悄", "偷偷", "蹑手蹑脚", "无声", "绕后"),
        ("潜行", "隐匿", "潜伏", "躲藏"),
        "dex",
    ),
    (
        "investigate",
        ("调查", "探查", "检查", "搜查", "搜索", "观察", "侦查", "寻找", "找线索", "辨认", "识别", "追踪"),
        ("侦查", "调查", "观察", "搜索", "图书馆使用", "追踪"),
        "int",
    ),
    (
        "perception",
        ("聆听", "倾听", "察觉", "感知", "留意", "环顾"),
        ("侦查", "聆听", "察觉", "感知"),
        "wis",
    ),
    (
        "social",
        ("说服", "交涉", "谈判", "欺骗", "威吓", "威胁", "套话", "表演"),
        ("说服", "话术", "魅惑", "威吓", "欺骗", "表演"),
        "cha",
    ),
    (
        "athletics",
        ("攀爬", "攀登", "跳跃", "游泳", "冲刺", "奔跑", "推开", "拉开", "撬开", "撞开", "搬起", "举起"),
        ("攀爬", "游泳", "跳跃", "运动", "体能"),
        "str",
    ),
    (
        "combat",
        ("攻击", "射击", "开枪", "挥砍", "砍", "刺", "踢", "突袭", "格挡", "闪避", "施法", "吟唱"),
        ("射击", "手枪", "步枪", "格斗", "斗殴", "剑术", "法术"),
        "dex",
    ),
)

_GENERIC_CHECK_WORDS = (
    "检定", "判定", "掷骰", "roll", "check",
)


def _normalized(text: object) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _skill_name(skill: object) -> str:
    if isinstance(skill, dict):
        return str(skill.get("name") or "").strip()
    return str(skill or "").strip()


def _find_skill(character_sheet: dict, text: str, candidates: tuple[str, ...]) -> str:
    skills = character_sheet.get("skills", [])
    names = [_skill_name(skill) for skill in skills]
    direct = [name for name in names if name and _normalized(name) in text]
    if direct:
        return max(direct, key=len)
    for candidate in candidates:
        for name in names:
            if name and (_normalized(candidate) in _normalized(name) or _normalized(name) in _normalized(candidate)):
                return name
    return ""


def _attribute_name(rule: RuleSystem | None, key: str) -> str:
    if rule:
        for attribute in rule.attributes:
            if attribute.get("key") == key:
                return str(attribute.get("name") or key)
    return key


def _d20_advantage(text: str, action: dict, rule: RuleSystem | None) -> tuple[str, str]:
    if not rule or rule.mechanics != "dnd5e_core":
        return "", ""
    raw_mode = str(action.get("advantage_mode") or action.get("advantage") or "").strip().lower()
    has_advantage = raw_mode in {"advantage", "优势", "有利", "bonus"} or any(
        word in text for word in ("优势", "有利", "占优", "奖励骰", "帮忙", "协助", "偷袭", "高地")
    )
    has_disadvantage = raw_mode in {"disadvantage", "劣势", "不利", "penalty"} or any(
        word in text for word in ("劣势", "不利", "受阻", "惩罚骰", "黑暗", "负伤", "疲惫", "干扰")
    )
    if has_advantage and has_disadvantage:
        return "", "优势与劣势同时存在，已抵消"
    if has_advantage:
        return "advantage", "优势：2d20 取高"
    if has_disadvantage:
        return "disadvantage", "劣势：2d20 取低"
    return "", ""


def build_check_request(
    instance: GameInstance,
    action: dict,
    rule: RuleSystem | None,
) -> dict[str, Any] | None:
    """为单个玩家行动生成结构化检定请求；不需要检定时返回 None。"""
    uid = str(action.get("user_id") or "")
    if uid not in instance.players:
        return None
    dice_system = str(rule.dice_system if rule else "d20").lower()
    if dice_system == "none":
        return None

    text = _normalized(action.get("text"))
    selected_skill = str(action.get("selected_skill") or "").strip()
    selected_attribute = str(action.get("selected_attribute") or "").strip()
    character_sheet = instance.get_character_sheet(uid)
    intent = ""
    candidates: tuple[str, ...] = ()
    attribute = selected_attribute

    for intent_name, aliases, skill_candidates, attr_key in _INTENT_SPECS:
        if any(_normalized(alias) in text for alias in aliases):
            intent = intent_name
            candidates = skill_candidates
            if not attribute:
                attribute = attr_key
            break

    direct_skill = _find_skill(character_sheet, text, ())
    skill = selected_skill or direct_skill
    if not skill and candidates:
        skill = _find_skill(character_sheet, text, candidates)

    explicit_check = bool(selected_skill or selected_attribute)
    generic_check = any(word in text for word in _GENERIC_CHECK_WORDS)
    if not (explicit_check or intent or direct_skill or generic_check):
        return None

    if attribute and rule and attribute not in rule.attribute_keys:
        attribute = "int" if "int" in rule.attribute_keys else (rule.attribute_keys[0] if rule.attribute_keys else "")
    if not attribute:
        attribute = "dex" if not rule or "dex" in rule.attribute_keys else (rule.attribute_keys[0] if rule.attribute_keys else "")

    english = is_english(instance.language)
    subject = skill or _attribute_name(rule, attribute)
    label = f"{subject} Check" if english else f"{subject}检定"
    advantage_mode, advantage_note = _d20_advantage(text, action, rule)
    actor_name = str(instance.players.get(uid, {}).get("character_name") or uid)
    return {
        "check_id": uuid.uuid4().hex,
        "required": True,
        "actor_uid": uid,
        "actor_name": actor_name,
        "dice_system": "d100" if dice_system == "d100" else "d20",
        "label": label,
        "intent": intent or "generic",
        "skill": skill,
        "attribute": attribute,
        "advantage_mode": advantage_mode,
        "advantage_note": advantage_note or None,
    }


def roll_check_request(request: dict[str, Any]) -> dict[str, Any]:
    """按 CheckRequest 只生成原始骰值；规则修正与成败由判定解析器计算。"""
    dice_system = str(request.get("dice_system") or "").lower()
    if dice_system == "d100":
        result = roll("d100")
        rolls = [result.natural]
        value = result.natural
    elif dice_system == "d20":
        mode = str(request.get("advantage_mode") or "")
        if mode in {"advantage", "disadvantage"}:
            rolls = [roll("d20").natural, roll("d20").natural]
            value = max(rolls) if mode == "advantage" else min(rolls)
        else:
            result = roll("d20")
            rolls = [result.natural]
            value = result.natural
    else:
        raise ValueError(f"不支持的检定骰制: {dice_system}")
    return {
        "ok": True,
        "check_id": str(request.get("check_id") or ""),
        "dice_system": dice_system,
        "value": value,
        "rolls": rolls,
        "critical": value == (1 if dice_system == "d100" else 20),
        "fumble": value >= 96 if dice_system == "d100" else value == 1,
    }
