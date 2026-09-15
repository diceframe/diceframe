"""Compatibility boundary for legacy character-sheet fields."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.rules.rule_system import RuleSystem

__all__ = ["migrate_legacy_character_sheet", "normalize_character_sheet", "normalize_character_payload"]

# 技能效果说明是玩家自定义文本（descriptive metadata，非规则权威）：
# 长度上限同时防止单技能塞入超长文本撑大 planner context。
MAX_SKILL_EFFECT_CHARS = 500


def _skill_row(skill: Any) -> dict[str, Any]:
    """Normalize one skill while preserving the optional ``effect`` text.

    字符串技能与旧对象技能继续归一化为 ``{name, value}``；只有确有非空
    ``effect`` 时才写入该字段（旧卡不凭空长出空字段）。
    """

    if isinstance(skill, str):
        return {"name": skill, "value": 20}
    if not isinstance(skill, dict):
        return {"name": "", "value": 20}
    row: dict[str, Any] = {
        "name": skill.get("name", ""),
        "value": skill.get("value", 20),
    }
    effect = str(skill.get("effect") or "").strip()
    if effect:
        row["effect"] = effect[:MAX_SKILL_EFFECT_CHARS]
    return row


def migrate_legacy_character_sheet(
    character_sheet: dict[str, Any],
    rule: "RuleSystem | None" = None,
) -> dict[str, Any]:
    """Populate the generic model from persisted pre-V2 character fields."""
    identity = character_sheet.setdefault("identity", {})
    identity.setdefault("origin", character_sheet.get("race", ""))
    identity.setdefault("archetype", character_sheet.get("class", ""))
    identity.setdefault("background", character_sheet.get("background", ""))

    progression = character_sheet.setdefault("progression", {})
    progression.setdefault(
        "type",
        rule.progression_schema.get("type", rule.growth_system) if rule else "xp_level",
    )
    progression.setdefault("level", int(character_sheet.get("level", 1) or 1))
    progression.setdefault("xp", int(character_sheet.get("xp", 0) or 0))

    currency = character_sheet.setdefault("currency", {})
    currency.setdefault("amount", int(character_sheet.get("gold", 0) or 0))
    if rule:
        # base_unit 只描述「1 canonical amount 是什么单位」，legacy 规则不做
        # 任何数值换算（gold → currency.amount 保留原值）。
        currency.setdefault("base_unit", rule.currency_spec.base_unit)
        currency.setdefault("label", rule.ui_schema.get("currency_label", rule.currency))

    resources = character_sheet.setdefault("resources", {})
    hp = resources.setdefault("hp", {})
    hp.setdefault("label", "生命")
    hp.setdefault("current", int(character_sheet.get("hp", 0) or 0))
    hp.setdefault("max", int(character_sheet.get("max_hp", hp.get("current", 0)) or 0))
    hp.setdefault("min", 0)
    if rule:
        for spec in rule.resource_schema:
            key = spec.get("key")
            if not key or key == "hp":
                continue
            resource = resources.setdefault(key, {})
            resource.setdefault("label", spec.get("label", key))
            if key in character_sheet:
                resource.setdefault("current", int(character_sheet.get(key, 0) or 0))
            if f"max_{key}" in character_sheet:
                resource.setdefault("max", int(character_sheet.get(f"max_{key}", 0) or 0))
            elif "max" in spec:
                resource.setdefault("max", spec.get("max"))
            resource.setdefault("min", spec.get("min", 0))
    return character_sheet


def normalize_character_sheet(
    character_sheet: dict[str, Any],
    rule: "RuleSystem | None" = None,
) -> dict[str, Any]:
    """Synchronize the canonical model with persisted legacy aliases."""
    migrate_legacy_character_sheet(character_sheet, rule)
    identity = character_sheet.setdefault("identity", {})
    if character_sheet.get("race"):
        identity["origin"] = character_sheet.get("race", "")
    else:
        character_sheet["race"] = identity.get("origin", "人类") or "人类"
    if character_sheet.get("class"):
        identity["archetype"] = character_sheet.get("class", "")
    else:
        character_sheet["class"] = identity.get("archetype", "冒险者") or "冒险者"
    if "background" in character_sheet:
        identity["background"] = character_sheet.get("background", "")
    else:
        character_sheet["background"] = identity.get("background", "")

    progression = character_sheet.setdefault("progression", {})
    if "level" in character_sheet:
        progression["level"] = int(character_sheet.get("level", 1) or 1)
    else:
        character_sheet["level"] = int(progression.get("level", 1) or 1)
    if "xp" in character_sheet:
        progression["xp"] = int(character_sheet.get("xp", 0) or 0)
    else:
        character_sheet["xp"] = int(progression.get("xp", 0) or 0)

    resources = character_sheet.setdefault("resources", {})
    hp = resources.setdefault("hp", {})
    if "hp" in character_sheet:
        hp["current"] = int(character_sheet.get("hp", 0) or 0)
    else:
        character_sheet["hp"] = int(hp.get("current", 0) or 0)
    if "max_hp" in character_sheet:
        hp["max"] = int(character_sheet.get("max_hp", 0) or 0)
    else:
        character_sheet["max_hp"] = int(hp.get("max", character_sheet.get("hp", 0)) or 0)

    currency = character_sheet.setdefault("currency", {})
    if "gold" in character_sheet:
        currency["amount"] = int(character_sheet.get("gold", 0) or 0)
    else:
        character_sheet["gold"] = int(currency.get("amount", 0) or 0)
    character_sheet["skills"] = [
        _skill_row(skill) for skill in character_sheet.get("skills", [])
    ]
    return character_sheet


def normalize_character_payload(character: dict[str, Any], rule: object | None = None) -> dict[str, Any]:
    """Normalize a character without deleting legacy aliases."""
    return normalize_character_sheet(character, rule)  # type: ignore[arg-type]
