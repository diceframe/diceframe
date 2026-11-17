"""Fabula Ultima 冲突技能目录（起手 5 职业，按核心规则书修正）。

每个技能按书里的效果声明：

- ``kind``：active / passive / reaction / spell
- ``mp`` / ``hp``：消耗
- ``target``：scope + count
- ``multiattack``：单次检定 vs 各目标物防，命中者吃**武器伤害**
- ``no_damage`` + ``choices``：命中后改为不造成伤害，选择一个分支
- ``passive``：命中加值 / 减伤 / 最大 HP / 抵抗等
- ``reaction``：counter / intercept / guard
- ``spell``：法术属性、基础伤害
"""

from __future__ import annotations

from typing import Any


def _skill(
    name: str,
    cls: str,
    *,
    kind: str = "active",
    mp: int = 0,
    hp: int = 0,
    scope: str = "enemy",
    count: int = 1,
    attributes: tuple[str, str] = ("mig", "mig"),
    dl: str = "defense",
    multiattack: bool = False,
    no_check: bool = False,
    no_damage: bool = False,
    choices: tuple[str, ...] = (),
    effects: list[dict[str, Any]] | None = None,
    passive: dict[str, Any] | None = None,
    reaction: str = "",
    spell: dict[str, Any] | None = None,
    text: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "class": cls,
        "kind": kind,
        "mp": mp,
        "hp": hp,
        "target": {"scope": scope, "count": count},
        "check": {"attributes": attributes, "dl": dl},
        "multiattack": multiattack,
        "no_check": no_check,
        "no_damage": no_damage,
        "choices": list(choices),
        "effects": effects or [],
        "passive": passive or {},
        "reaction": reaction,
        "spell": spell or {},
        "text": text,
    }


SKILLS: dict[str, dict[str, Any]] = {
    # ---------------- 武器大师 ----------------
    "利刃风暴": _skill(
        "利刃风暴", "weaponmaster", mp=10, scope="enemies", count=2,
        multiattack=True, text="近战攻击获得多重攻击（2），命中者吃武器伤害",
    ),
    "碎骨": _skill(
        "碎骨", "weaponmaster", no_damage=True, choices=("daze", "weak", "mp"),
        text="近战命中后可不造成伤害：眩晕 / 虚弱 / 失去技能等级×10 MP",
    ),
    "破防打击": _skill(
        "破防打击", "weaponmaster", mp=5, no_damage=True,
        choices=("shield", "armor", "vulnerable"),
        text="5 MP：单体近战，命中后不造成伤害：毁盾 / 毁甲 / 到下次行动前所有伤害 +技能等级×2",
    ),
    "反击": _skill(
        "反击", "weaponmaster", kind="reaction", reaction="counter",
        text="敌人近战攻击后，若命中检定结算值为偶数，可对其顺势攻击（高值视为 0）",
    ),
    "近战武器精通": _skill(
        "近战武器精通", "weaponmaster", kind="passive",
        passive={"attack_bonus": "level", "weapon": "melee"},
        text="近战武器命中检定 +技能等级",
    ),
    # ---------------- 神射手 ----------------
    "弹幕射击": _skill(
        "弹幕射击", "sharpshooter", mp=10, scope="enemies", count=2,
        multiattack=True, text="远程攻击获得多重攻击（2），命中者吃武器伤害",
    ),
    "干涉火力": _skill(
        "干涉火力", "sharpshooter", kind="reaction", reaction="intercept",
        text="看见远程攻击后，消耗 5+命中结算值 MP 使其自动失败；大成功无效",
    ),
    "鹰眼": _skill(
        "鹰眼", "sharpshooter", mp=0, scope="self", no_check=True,
        effects=[{"type": "buff", "stat": "damage", "value": 3, "duration": 1, "scale": "level"}],
        text="防御且不掩护时：本场景下一次远程攻击 +技能等级×3 伤害",
    ),
    "远程武器精通": _skill(
        "远程武器精通", "sharpshooter", kind="passive",
        passive={"attack_bonus": "level", "weapon": "ranged"},
        text="远程武器命中检定 +技能等级",
    ),
    "威慑射击": _skill(
        "威慑射击", "sharpshooter", no_damage=True, choices=("shaken", "slow", "mp"),
        text="远程命中后可不造成伤害：动摇 / 迟缓 / 失去技能等级×10 MP",
    ),
    # ---------------- 暗刃骑士 ----------------
    "暗影击": _skill(
        "暗影击", "darkblade", kind="active", hp=-1, attributes=("mig", "mig"),
        effects=[{"type": "damage", "bonus": 0, "damage_type": "dark"}],
        text="行动：掷力量骰，失去等量 HP；未归零则武器顺势攻击，命中额外 +技能等级+力量骰，全部暗属性",
    ),
    "痛楚": _skill(
        "痛楚", "darkblade", kind="passive", passive={"heal_on_damage": True},
        text="每回合一次：对羁绊生物造成伤害后，回复技能等级×2 HP/MP",
    ),
    "身负黑血": _skill(
        "身负黑血", "darkblade", kind="passive", passive={"crisis_resist": ["dark", "poison"]},
        text="危机中，对暗、毒伤害抵抗",
    ),
    "黑暗之心": _skill(
        "黑暗之心", "darkblade", kind="passive", passive={"crisis_bond": True},
        text="每场景一次：进入危机时可对一生物建立憎恨羁绊",
    ),
    "苦痛教训": _skill(
        "苦痛教训", "darkblade", kind="reaction", reaction="painful_lesson",
        text="失去 HP 后，立即对来源执行调查顺势行动，+技能等级",
    ),
    # ---------------- 守护者 ----------------
    "保镖": _skill(
        "保镖", "guardian", kind="passive", passive={"cover_resist": True},
        text="防御并为他人掩护时，该生物对所有伤害抵抗到你的下回合",
    ),
    "防御精通": _skill(
        "防御精通", "guardian", kind="passive", passive={"damage_reduction": "level"},
        text="装备盾/职业防具时，受到的所有伤害 -技能等级（先于相性）",
    ),
    "双盾战士": _skill(
        "双盾战士", "guardian", kind="passive", passive={"dual_shield": True},
        text="主手可装备盾；双盾可当双手格斗武器（力+力，高值+5）",
    ),
    "铁壁": _skill(
        "铁壁", "guardian", kind="passive", passive={"max_hp": "level*3"},
        text="最大 HP +技能等级×3",
    ),
    "挺身守护": _skill(
        "挺身守护", "guardian", kind="reaction", reaction="guard",
        text="代替他人承受一次攻击/法术/险情；冲突中每轮一次",
    ),
    # ---------------- 元素使 ----------------
    "元素魔法": _skill(
        "元素魔法", "elementalist", kind="spell", mp=10, scope="enemy",
        attributes=("ins", "wlp"), dl="mdef",
        spell={"damage_type": "fire", "bonus": 15, "offensive": True, "elements": ["fire", "ice", "lightning", "light", "dark"]},
        text="施放元素攻击法术：洞察+意志 vs 魔防，高值+15 元素伤害（默认火）",
    ),
    "天灾骤降": _skill(
        "天灾骤降", "elementalist", kind="passive", passive={"spell_boost": True},
        text="瞬发法术可将 MP 提高至多技能等级×10，每多 10 MP 伤害 +5",
    ),
    "魔法炮击": _skill(
        "魔法炮击", "elementalist", kind="passive", passive={"spell_bonus": "level*2"},
        text="装备魔法武器时，攻击法术施法检定 +技能等级×2",
    ),
    "以械引咒": _skill(
        "以械引咒", "elementalist", kind="passive", passive={"spell_weapon_check": True},
        text="单体攻击法术可用武器命中算式替代施法检定；含敏捷时 +技能等级",
    ),
    "元素系仪式": _skill(
        "元素系仪式", "elementalist", kind="passive", passive={"ritual": True},
        text="可启动元素学派仪式（洞察+意志）",
    ),
    # ---------------- 快速开始固定卡技能 ----------------
    "灵光洞见": _skill(
        "灵光洞见", "loremaster", kind="passive", passive={"investigate_questions": True},
        text="调查检定掷出 13+ 时，可向 GM 提出至多技能等级个与调查对象有关的问题",
    ),
    "治疗": _skill(
        "治疗", "spiritist", kind="spell", mp=10, scope="self_or_ally", count=3,
        attributes=("ins", "wlp"), dl="mdef",
        spell={"heal": 40},
        text="法术：至多 3 个生物，每个 10 MP，各恢复 40 HP",
    ),
    "鼓舞": _skill(
        "鼓舞", "orator", mp=5, scope="ally", no_check=True,
        attributes=("ins", "wlp"),
        effects=[{"type": "heal", "scale": "level*10"},
                 {"type": "buff", "stat": "attribute_die_up", "target": "ally", "value": 1, "duration": 2}],
        text="5 MP：一名盟友恢复技能等级×10 HP，选择一项属性骰等级 +1 到你的下回合",
    ),
    "光耀术": _skill(
        "光耀术", "spiritist", kind="spell", mp=10, scope="enemies", count=3,
        attributes=("ins", "wlp"), dl="mdef",
        spell={"damage_type": "light", "bonus": 15, "offensive": True},
        text="法术：至多 3 个生物，每个 10 MP，造成高值+15 光系伤害",
    ),
    "元素武器": _skill(
        "元素武器", "elementalist", kind="spell", mp=10, scope="self", no_check=True,
        attributes=("ins", "wlp"), dl="mdef",
        spell={"weapon_element": True, "elements": ["wind", "lightning", "ice", "fire", "earth"]},
        text="法术：10 MP，为一件武器注入元素（风/雷/冰/火/土），伤害类型变为所选元素",
    ),
    "翱翔击": _skill(
        "翱翔击", "elementalist", kind="spell", mp=10, scope="enemy",
        attributes=("dex", "mig"), dl="defense",
        spell={"weapon_attack": True, "bonus": 5},
        text="法术：10 MP，用近战武器顺势攻击，伤害 +5，可攻击飞行目标",
    ),
    "药剂雨": _skill(
        "药剂雨", "tinkerer", kind="passive", passive={"potion_splash": True},
        text="制造的恢复药剂可额外影响至多技能等级个生物（恢复量减半）",
    ),
    "肾上腺素": _skill(
        "肾上腺素", "fury", kind="passive", passive={"crisis_damage": "level*2"},
        text="危机状态时，造成的伤害 +技能等级×2",
    ),
}


def generic_skill(name: str, level: int = 1) -> dict[str, Any]:
    """未收录的自由技能：按普通攻击结算，避免空过回合。"""
    return _skill(
        name, "generic", attributes=("dex", "mig"),
        effects=[{"type": "damage", "bonus": 0, "damage_type": "physical"}],
        text="自由技能（按普通攻击结算）",
    )


def skill_for(name: str, level: int = 1) -> dict[str, Any] | None:
    entry = SKILLS.get(str(name or "").strip())
    if entry is not None:
        return entry
    if str(name or "").strip():
        return generic_skill(name, level)
    return None


def known_skills(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in sheet.get("skills") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        level = int(row.get("value") or 1)
        entry = skill_for(name, level)
        if entry is None:
            continue
        spell_info = entry.get("spell") or {}
        boost_level = 0
        for passive_row in sheet.get("skills") or []:
            if isinstance(passive_row, dict) and str(passive_row.get("name") or "") == "天灾骤降":
                boost_level = int(passive_row.get("value") or 1)
                break
        rows.append({
            "name": name,
            "level": level,
            "known": name in SKILLS,
            "kind": entry["kind"],
            "spell": entry["kind"] == "spell",
            "elements": list(spell_info.get("elements") or []),
            "boost_max": (boost_level * 10 if spell_info.get("offensive") else 0),
            "action": "action" if entry["kind"] in {"active", "spell"} else "quick",
            "mp": entry["mp"],
            "hp": entry["hp"],
            "scope": entry["target"]["scope"],
            "count": entry["target"]["count"],
            "choices": entry["choices"],
            "text": entry["text"],
        })
    return rows


def passive_levels(sheet: dict[str, Any]) -> dict[str, int]:
    """角色拥有的被动技能等级（按技能名）。"""
    levels: dict[str, int] = {}
    for row in sheet.get("skills") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if name in SKILLS and SKILLS[name]["kind"] == "passive":
            levels[name] = int(row.get("value") or 1)
    return levels
