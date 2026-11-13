"""Fabula Ultima 的静态内容：伤害类型、异常状态、敌人模板、反派等级。"""

from __future__ import annotations

#: 伤害类型（核心书 p92）。
DAMAGE_TYPES = ("physical", "lightning", "wind", "ice", "fire", "earth", "poison", "light", "dark")

#: 异常状态 → 被降低的属性骰（核心书 p94）。
CONDITION_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "slow": ("dex",),      # 迟缓
    "shaken": ("wlp",),    # 动摇
    "weak": ("mig",),      # 虚弱
    "dazed": ("ins",),     # 眩晕
    "enraged": ("dex", "ins"),  # 激怒
    "poisoned": ("mig", "wlp"),  # 中毒
}

CONDITION_NAMES = {
    "slow": "迟缓",
    "shaken": "动摇",
    "weak": "虚弱",
    "dazed": "眩晕",
    "enraged": "激怒",
    "poisoned": "中毒",
}

#: 反派等级 → 终结点（核心书 p101）。
VILLAIN_FATE = {"minor": 5, "major": 10, "ultimate": 15}

#: 敌人模板。数值按核心书 p294–330 的档位整理，首版 10 个。
ENEMIES: dict[str, dict] = {
    "slime": {
        "name": "史莱姆", "tier": "minor", "hp": 20, "defense": 8, "mdef": 8, "initiative": 6,
        "attacks": [{"name": "酸液撞击", "attributes": ("mig", "mig"), "damage": 5}],
        "affinities": {"fire": "weak", "ice": "resist"},
    },
    "goblin": {
        "name": "哥布林", "tier": "minor", "hp": 28, "defense": 9, "mdef": 8, "initiative": 8,
        "attacks": [{"name": "锈刀", "attributes": ("dex", "mig"), "damage": 6}],
        "affinities": {},
    },
    "orc": {
        "name": "兽人", "tier": "minor", "hp": 45, "defense": 11, "mdef": 9, "initiative": 7,
        "attacks": [{"name": "巨斧", "attributes": ("mig", "mig"), "damage": 10}],
        "affinities": {"ice": "weak"},
    },
    "skeleton": {
        "name": "骷髅兵", "tier": "minor", "hp": 30, "defense": 10, "mdef": 8, "initiative": 7,
        "attacks": [{"name": "骨剑", "attributes": ("dex", "mig"), "damage": 7}],
        "affinities": {"light": "weak", "dark": "resist"},
    },
    "golem": {
        "name": "青铜魔像", "tier": "major", "hp": 70, "defense": 13, "mdef": 11, "initiative": 5,
        "attacks": [{"name": "重拳", "attributes": ("mig", "mig"), "damage": 14}],
        "affinities": {"lightning": "weak", "physical": "resist"},
    },
    "whelp": {
        "name": "幼龙", "tier": "major", "hp": 60, "defense": 12, "mdef": 12, "initiative": 10,
        "attacks": [{"name": "爪击", "attributes": ("dex", "mig"), "damage": 11},
                    {"name": "吐息", "attributes": ("ins", "wlp"), "damage": 12}],
        "affinities": {"ice": "weak"},
    },
    "assassin": {
        "name": "刺客", "tier": "minor", "hp": 36, "defense": 12, "mdef": 10, "initiative": 12,
        "attacks": [{"name": "毒刃", "attributes": ("dex", "ins"), "damage": 9}],
        "affinities": {"poison": "resist"},
    },
    "mage": {
        "name": "敌法师", "tier": "major", "hp": 40, "defense": 10, "mdef": 13, "initiative": 9,
        "attacks": [{"name": "奥术冲击", "attributes": ("ins", "wlp"), "damage": 12}],
        "affinities": {"physical": "weak"},
    },
    "captain": {
        "name": "守卫队长", "tier": "major", "hp": 55, "defense": 12, "mdef": 11, "initiative": 8,
        "attacks": [{"name": "长戟", "attributes": ("mig", "dex"), "damage": 11}],
        "affinities": {},
    },
    "witch": {
        "name": "锈钟女巫", "tier": "ultimate", "hp": 90, "defense": 13, "mdef": 14, "initiative": 11,
        "villain": True, "fate": 15,
        "attacks": [{"name": "钟鸣", "attributes": ("ins", "wlp"), "damage": 15},
                    {"name": "锈蚀之触", "attributes": ("mig", "wlp"), "damage": 12}],
        "affinities": {"light": "weak", "dark": "immune"},
    },
}

#: 属性 key → 中文名（用于日志与视图）。
ATTRIBUTE_NAMES = {"dex": "敏捷", "ins": "洞察", "mig": "力量", "wlp": "意志"}

#: 敌人技能：scope=players（单体）/all_players（全体）/self（自身增益/回复）。
_ENEMY_SKILLS = {
    "soldier": [{"name": "雷击", "scope": "players", "damage_type": "lightning", "bonus": 5, "cooldown": 3}],
    "eye": [{"name": "凝视", "scope": "players", "status": "shaken", "duration": 2, "cooldown": 3}],
    "mage": [{"name": "火球术", "scope": "all_players", "damage_type": "fire", "bonus": 5, "cooldown": 3}],
    "captain": [{"name": "号令", "scope": "self", "buff": {"stat": "damage", "value": 2, "duration": 2}, "cooldown": 4}],
    "golem": [{"name": "大地震击", "scope": "all_players", "damage_type": "earth", "bonus": 4, "cooldown": 3}],
    "whelp": [{"name": "烈焰吐息", "scope": "all_players", "damage_type": "fire", "bonus": 4, "cooldown": 3}],
    "assassin": [{"name": "毒刃", "scope": "players", "damage_type": "physical", "bonus": 3, "status": "poisoned", "duration": 2, "cooldown": 3}],
    "witch": [{"name": "钟鸣", "scope": "all_players", "damage_type": "dark", "bonus": 5, "cooldown": 3}],
    "desdemona": [{"name": "暗影爆发", "scope": "all_players", "damage_type": "dark", "bonus": 6, "cooldown": 3}],
    "tristan": [{"name": "撼地", "scope": "all_players", "damage_type": "physical", "bonus": 5, "cooldown": 3}],
}
for _enemy_id, _skills in _ENEMY_SKILLS.items():
    if _enemy_id in ENEMIES:
        ENEMIES[_enemy_id]["skills"] = _skills
