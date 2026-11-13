"""规则声明式成长：分配职业等级、提升属性骰、消耗技能点。

两条写路径都只动角色卡里的 ``class_levels`` / ``skills`` / ``skill_points``，
不碰 HP、XP 或其它规则的资源。
"""

from __future__ import annotations

from typing import Any

from src.engine.language import localized_text

#: 单个职业的等级上限（满级即精通）。
MAX_CLASS_LEVEL = 10

_MESSAGES: dict[str, dict[str, str]] = {
    "sheet_missing": {"zh-CN": "角色卡不存在", "en": "The character sheet does not exist"},
    "class_required": {"zh-CN": "必须指定职业", "en": "A class must be chosen"},
    "class_unknown": {"zh-CN": "未知职业: {name}", "en": "Unknown class: {name}"},
    "no_pending_class_level": {
        "zh-CN": "没有待分配的职业等级", "en": "There is no class level left to assign",
    },
    "class_mastered": {
        "zh-CN": "{name} 已达到 {level} 级（精通）",
        "en": "{name} has already reached level {level} (mastered)",
    },
    "attribute_required": {"zh-CN": "必须指定属性", "en": "An attribute must be chosen"},
    "attribute_unknown": {"zh-CN": "未知属性: {key}", "en": "Unknown attribute: {key}"},
    "no_pending_attribute_step": {
        "zh-CN": "没有待分配的属性提升", "en": "There is no attribute increase left to assign",
    },
    "attribute_maxed": {
        "zh-CN": "{key} 已经是 d{size}，无法再提升",
        "en": "{key} is already d{size} and cannot be raised further",
    },
    "skill_required": {"zh-CN": "必须指定技能", "en": "A skill must be chosen"},
    "no_skill_points": {"zh-CN": "没有可用技能点", "en": "There are no skill points to spend"},
    "skill_missing": {"zh-CN": "角色没有技能: {name}", "en": "The character does not have the skill: {name}"},
    "skill_capped": {
        "zh-CN": "技能 {name} 已达到上限 {cap}", "en": "Skill {name} has reached its cap of {cap}",
    },
}


def _text(instance: Any, key: str, **params: Any) -> str:
    language = getattr(instance, "language", "")
    return localized_text(language, _MESSAGES[key]).format(**params)


def _character(instance: Any, uid: str) -> dict[str, Any]:
    sheet = instance.get_character_sheet(uid)
    if not isinstance(sheet, dict):
        raise ValueError(_text(instance, "sheet_missing"))
    return sheet


def _sync_class_label(sheet: dict[str, Any], levels: dict[str, int]) -> str:
    label = " / ".join(f"{name} {value}" for name, value in levels.items())
    sheet["class"] = label
    identity = sheet.get("identity")
    if not isinstance(identity, dict):
        identity = {}
    identity["archetype"] = label
    sheet["identity"] = identity
    return label


def assign_class_level(instance: Any, uid: str, class_name: str, rule: Any) -> dict[str, Any]:
    """把一个待分配的职业等级投到指定职业上。"""
    name = str(class_name or "").strip()
    if not name:
        raise ValueError(_text(instance, "class_required"))
    allowed = rule.get_class_names() if rule is not None else []
    if allowed and name not in allowed:
        raise ValueError(_text(instance, "class_unknown", name=name))

    sheet = _character(instance, uid)
    pending = int(sheet.get("pending_class_levels", 0) or 0)
    if pending <= 0:
        raise ValueError(_text(instance, "no_pending_class_level"))

    levels = dict(sheet.get("class_levels") or {})
    current = int(levels.get(name, 0) or 0)
    if current >= MAX_CLASS_LEVEL:
        raise ValueError(_text(instance, "class_mastered", name=name, level=MAX_CLASS_LEVEL))

    levels[name] = current + 1
    sheet["class_levels"] = levels
    sheet["pending_class_levels"] = pending - 1
    label = _sync_class_label(sheet, levels)
    instance.set_character_sheet(uid, sheet)
    return {
        "class": label,
        "class_levels": levels,
        "pending_class_levels": sheet["pending_class_levels"],
        "mastered": [key for key, value in levels.items() if value >= MAX_CLASS_LEVEL],
    }


#: 属性骰的合法档位。
ATTRIBUTE_STEPS: tuple[int, ...] = (6, 8, 10, 12)


def apply_attribute_step(instance: Any, uid: str, attribute: str, rule: Any) -> dict[str, Any]:
    """消耗一个里程碑提升，把某项属性骰提高一档（20/40 级各一次）。"""
    key = str(attribute or "").strip()
    allowed = list(getattr(rule, "attribute_keys", []) or []) if rule is not None else []
    if not key:
        raise ValueError(_text(instance, "attribute_required"))
    if allowed and key not in allowed:
        raise ValueError(_text(instance, "attribute_unknown", key=key))

    sheet = _character(instance, uid)
    pending = int(sheet.get("pending_attribute_steps", 0) or 0)
    if pending <= 0:
        raise ValueError(_text(instance, "no_pending_attribute_step"))

    attrs = dict(sheet.get("attributes") or {})
    current = int(attrs.get(key, 0) or 0)
    target = next((step for step in ATTRIBUTE_STEPS if step > current), None)
    if target is None:
        raise ValueError(_text(instance, "attribute_maxed", key=key, size=ATTRIBUTE_STEPS[-1]))

    attrs[key] = target
    sheet["attributes"] = attrs
    sheet["pending_attribute_steps"] = pending - 1
    # 属性点预算同步放宽，避免 UI 把新数值当成超限。
    sheet["attr_points_max"] = int(sheet.get("attr_points_max", 0) or 0) + (target - current)

    old_max_hp = int(sheet.get("max_hp", 0) or 0)
    if rule is not None:
        try:
            new_max_hp = int(rule.calculate_hp(attrs, str(sheet.get("class") or "")))
            if new_max_hp > 0 and new_max_hp != old_max_hp:
                current_hp = int(sheet.get("hp", 0) or 0)
                gained = max(0, new_max_hp - old_max_hp)
                sheet["max_hp"] = new_max_hp
                sheet["hp"] = max(0, min(new_max_hp, current_hp + gained))
                resources = sheet.get("resources")
                if isinstance(resources, dict):
                    hp = resources.get("hp")
                    if isinstance(hp, dict):
                        hp["max"] = new_max_hp
                        hp["current"] = sheet["hp"]
        except Exception:
            pass

    # 公式化资源上限（规则模板的 max_formula）随属性变化重算，并补足当前值。
    if rule is not None:
        for stat in (getattr(rule, "special_stats", None) or []):
            if not isinstance(stat, dict) or not stat.get("max_formula"):
                continue
            key = str(stat.get("key") or "")
            if not key:
                continue
            try:
                new_max = int(rule.special_stat_max(stat, attrs))
            except Exception:
                continue
            old_max = int(sheet.get(f"max_{key}", 0) or 0)
            if new_max == old_max:
                continue
            current = int(sheet.get(key, 0) or 0)
            sheet[f"max_{key}"] = new_max
            sheet[key] = max(0, min(new_max, current + max(0, new_max - old_max)))
            resources = sheet.get("resources")
            if isinstance(resources, dict):
                resource = resources.get(key)
                if isinstance(resource, dict):
                    resource["max"] = new_max
                    resource["current"] = sheet[key]

    instance.set_character_sheet(uid, sheet)
    return {
        "attribute": key,
        "value": target,
        "pending_attribute_steps": sheet["pending_attribute_steps"],
        "max_hp": sheet.get("max_hp", old_max_hp),
    }


def spend_skill_point(instance: Any, uid: str, skill_name: str, rule: Any) -> dict[str, Any]:
    """消耗 1 个技能点，把已有技能 +1。"""
    name = str(skill_name or "").strip()
    if not name:
        raise ValueError(_text(instance, "skill_required"))

    sheet = _character(instance, uid)
    points = int(sheet.get("skill_points", 0) or 0)
    if points <= 0:
        raise ValueError(_text(instance, "no_skill_points"))

    rows = [dict(item) for item in (sheet.get("skills") or []) if isinstance(item, dict)]
    target = next((row for row in rows if str(row.get("name") or "").strip() == name), None)
    if target is None:
        raise ValueError(_text(instance, "skill_missing", name=name))

    cap = int(getattr(rule, "max_skill_value", 0) or 0) if rule is not None else 0
    current = int(target.get("value", 0) or 0)
    if cap and current >= cap:
        raise ValueError(_text(instance, "skill_capped", name=name, cap=cap))

    target["value"] = current + 1
    sheet["skills"] = rows
    sheet["skill_points"] = points - 1
    instance.set_character_sheet(uid, sheet)
    return {
        "skill": name,
        "value": target["value"],
        "skill_points": sheet["skill_points"],
    }
