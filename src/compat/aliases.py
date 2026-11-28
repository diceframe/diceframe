"""Legacy display-name aliases for the D&D V2 pilot."""

from __future__ import annotations

import re

_CLASS_ALIASES = {
    "野蛮人": "barbarian", "蛮族": "barbarian", "barbarian": "barbarian",
    "吟游诗人": "bard", "吟遊詩人": "bard", "bard": "bard",
    "牧师": "cleric", "クレリック": "cleric", "cleric": "cleric",
    "德鲁伊": "druid", "ドルイド": "druid", "druid": "druid",
    "战士": "fighter", "ファイター": "fighter", "fighter": "fighter",
    "武僧": "monk", "モンク": "monk", "monk": "monk",
    "圣骑士": "paladin", "パラディン": "paladin", "paladin": "paladin",
    "游侠": "ranger", "レンジャー": "ranger", "ranger": "ranger",
    "游荡者": "rogue", "ローグ": "rogue", "rogue": "rogue",
    "术士": "sorcerer", "ソーサラー": "sorcerer", "sorcerer": "sorcerer",
    # 5R（2024）官方中文把 Warlock 译作「魔契师」；「邪术师」是 2014 版译名，
    # 留作旧卡导入的别名，不删。
    "魔契师": "warlock", "邪术师": "warlock", "ウォーロック": "warlock", "warlock": "warlock",
    "法师": "wizard", "ウィザード": "wizard", "wizard": "wizard",
}

_ITEM_ALIASES = {
    "铁剑": "iron_sword", "iron sword": "iron_sword", "鉄の剣": "iron_sword",
    "长剑": "longsword", "longsword": "longsword", "ロングソード": "longsword",
    "链甲": "chain_mail", "chain mail": "chain_mail", "チェインメイル": "chain_mail",
    "盾牌": "shield", "盾": "shield", "shield": "shield",
    "法器": "arcane_focus", "arcane focus": "arcane_focus", "秘術焦点": "arcane_focus",
    "皮甲": "leather_armor", "leather armor": "leather_armor", "革鎧": "leather_armor",
    "长弓": "longbow", "longbow": "longbow", "ロングボウ": "longbow",
    "短弓": "shortbow", "shortbow": "shortbow", "ショートボウ": "shortbow",
    "短剑": "shortsword", "shortsword": "shortsword", "ショートソード": "shortsword",
    "匕首": "dagger", "dagger": "dagger", "短剣": "dagger",
    "法术书": "spellbook", "spellbook": "spellbook", "魔法書": "spellbook",
    "箭矢×20": "arrows", "arrows": "arrows", "矢×20": "arrows",
    "撬锁工具": "lockpicks", "lockpicks": "lockpicks", "ピッキングツール": "lockpicks",
}


def _key(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def canonical_class_id(value: object) -> str:
    return _CLASS_ALIASES.get(_key(value), "")


def canonical_item_id(value: object) -> str:
    return _ITEM_ALIASES.get(_key(value), "")


def canonical_skill_id(value: object) -> str:
    candidate = _key(value).replace(" ", "_")
    return candidate if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", candidate) else ""
