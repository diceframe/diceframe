"""AI-generated temporary encounter proposals for D&D 2024.

模型只负责"临时提案"：GM 确认后提交现有 ``combat.start``（mode=sandbox），
服务端权威校验仍然生效。生成是只读操作 —— 不写 Adventure Bundle、不写
encounter catalog、不改任何游戏状态；失败时完全无副作用。
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.rulesets.dnd2024.combat.validation import validate_enemy_profiles

TEMPORARY_ENCOUNTER_TOOL_NAME = "dnd2024_temporary_encounter"

# 临时生成在底层敌人校验（validate_enemy_profiles）之上额外收紧的合理范围：
# 不做 CR 系统，只防止模型输出明显失控的数值。
MAX_TEMPORARY_ENEMIES = 12
_ENEMY_FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    "hp": (1, 500),
    "armor_class": (8, 25),
    "speed": (0, 80),
    "position": (-1000, 1000),
    "initiative_modifier": (-5, 10),
}
_ATTACK_FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    "attack_bonus": (-2, 15),
    "range": (5, 600),
    "long_range": (5, 600),
}
_ENEMY_FIELD_DEFAULTS: dict[str, int] = {
    "speed": 30, "position": 30, "initiative_modifier": 0,
}
_ATTACK_FIELD_DEFAULTS: dict[str, int] = {"range": 5}

_ID_SANITIZE = re.compile(r"[^a-z0-9]+")


TEMPORARY_ENCOUNTER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TEMPORARY_ENCOUNTER_TOOL_NAME,
        "description": (
            "Propose one temporary free-play encounter matching the current fiction and party. "
            "Enemies must be simple stat blocks the combat engine fully supports: move, attack, "
            "dodge, end turn."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "maxLength": 80},
                "description": {"type": "string", "maxLength": 300},
                "enemies": {
                    "type": "array", "minItems": 1, "maxItems": MAX_TEMPORARY_ENEMIES,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string", "maxLength": 60},
                            "hp": {"type": "integer", "minimum": 1, "maximum": 500},
                            "armor_class": {"type": "integer", "minimum": 8, "maximum": 25},
                            "speed": {"type": "integer", "minimum": 0, "maximum": 80},
                            "position": {"type": "integer"},
                            "initiative_modifier": {
                                "type": "integer", "minimum": -5, "maximum": 10,
                            },
                            "attacks": {
                                "type": "array", "minItems": 1, "maxItems": 3,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "id": {"type": "string"},
                                        "name": {"type": "string", "maxLength": 60},
                                        "attack_bonus": {
                                            "type": "integer", "minimum": -2, "maximum": 15,
                                        },
                                        "damage": {"type": "string", "maxLength": 40},
                                        "range": {"type": "integer", "minimum": 5, "maximum": 600},
                                        "long_range": {
                                            "type": "integer", "minimum": 5, "maximum": 600,
                                        },
                                    },
                                    "required": ["id", "name", "attack_bonus", "damage"],
                                },
                            },
                        },
                        "required": ["id", "name", "hp", "armor_class", "speed", "attacks"],
                    },
                },
            },
            "required": ["title", "description", "enemies"],
        },
    },
}


def _temporary_prompt(language: str) -> str:
    if str(language or "").lower().startswith("zh"):
        return (
            "你是 D&D 2024 临时遭遇生成器。根据当前剧情、冒险步骤、敌情与队伍构成，"
            "生成一场普通难度、预计持续 2-5 轮的自由战斗（GM 已明确请求临时遭遇）。"
            "敌人只能使用战斗引擎完整支持的简单模型：移动、普通攻击、闪避、结束回合；"
            "不要生成法术、传奇动作、巢穴动作、复杂被动或新机制。"
            "不要生成明显能秒杀整个队伍的敌人，也不要使用当前队伍等级明显无法处理的极端数值。"
            "所有数值必须落在给定范围内，伤害必须是骰子表达式，id 必须唯一。"
            "只返回工具调用，不要输出额外文本。"
        )
    return (
        "You are the D&D 2024 temporary encounter generator. Based on the current fiction, "
        "adventure step, opposition, and party, propose one normal-difficulty free encounter "
        "expected to last 2-5 rounds (the GM explicitly requested a temporary encounter). Enemies "
        "must be simple stat blocks the combat engine fully supports: move, attack, dodge, end "
        "turn — no spells, legendary actions, lair actions, complex passives, or new mechanics. "
        "Never generate enemies that can obviously wipe the party, and avoid extreme values the "
        "party's level cannot handle. Keep every value inside the given ranges, write damage as "
        "dice expressions, and make ids unique. Return only the tool call."
    )


def _generation_context(instance: Any, campaign: dict[str, Any]) -> dict[str, Any]:
    """生成输入只包含与当前敌情直接相关的信息，不携带整段冒险历史。"""

    players = getattr(instance, "players", {}) or {}
    members: list[dict[str, Any]] = []
    for uid, player in players.items():
        sheet = player.get("character_sheet") if isinstance(player, dict) else {}
        sheet = sheet if isinstance(sheet, dict) else {}
        profile = sheet.get("ruleset_character")
        profile = profile if isinstance(profile, dict) else sheet
        member = {
            "name": str(profile.get("character_name") or sheet.get("character_name") or uid)[:60],
            "class": str(profile.get("class") or profile.get("class_name") or "")[:40],
            "level": profile.get("level"),
            "hp": profile.get("hp"),
            "max_hp": profile.get("max_hp"),
            "armor_class": profile.get("armor_class"),
        }
        members.append({key: value for key, value in member.items() if value not in (None, "")})
    tutorial = campaign.get("tutorial") if isinstance(campaign, dict) else None
    tutorial = tutorial if isinstance(tutorial, dict) else {}
    step = tutorial.get("current_step") if isinstance(tutorial.get("current_step"), dict) else {}
    request = campaign.get("encounter_request")
    request = request if isinstance(request, dict) else {}
    adventure = None
    if tutorial.get("status") == "active":
        adventure = {
            "id": str(tutorial.get("adventure_id") or "")[:80],
            "step_id": str(step.get("id") or "")[:80],
            "title": str(step.get("title") or "")[:200],
            "narration": str(step.get("narration") or step.get("objective") or "")[:600],
        }
    return {
        "scene": str(getattr(instance, "scene", "") or "")[:500],
        "recent_narration": [
            str(item.get("gm_response") or "")[:800]
            for item in (getattr(instance, "log", []) or [])[-2:]
            if isinstance(item, dict) and item.get("gm_response")
        ],
        "adventure": adventure,
        "encounter_request": {
            "status": str(request.get("status") or ""),
            "reason": str(request.get("reason") or "")[:300],
        },
        "party": {"size": len(members), "members": members},
    }


def _coerce_bounded_int(
    value: Any, field_name: str, bounds: tuple[int, int], default: int | None = None,
) -> int:
    minimum, maximum = bounds
    if value is None:
        if default is None:
            raise ValueError(f"敌人 {field_name} 数值无效")
        value = default
    if isinstance(value, bool):
        raise ValueError(f"敌人 {field_name} 数值无效")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"敌人 {field_name} 必须是整数") from None
    if not minimum <= number <= maximum:
        raise ValueError(
            f"敌人 {field_name} 超出临时生成允许范围（{minimum}-{maximum}）",
        )
    return number


def _slug(value: str, fallback_index: int) -> str:
    slug = _ID_SANITIZE.sub("_", str(value or "").strip().casefold()).strip("_")[:48]
    if not slug or not re.match(r"[a-z0-9]", slug[0]):
        slug = f"enemy_{fallback_index + 1}"
    return slug


def _assign_unique_ids(enemies: list[dict[str, Any]]) -> None:
    """同名敌人由服务端派生唯一 id（wolf / wolf_2 / wolf_3…）。"""

    seen: set[str] = set()
    for index, enemy in enumerate(enemies):
        base = _slug(str(enemy.get("name") or ""), index)
        candidate, suffix = base, 2
        while candidate in seen:
            candidate = f"{base}_{suffix}"
            suffix += 1
        enemy["id"] = candidate
        seen.add(candidate)
    for enemy in enemies:
        attack_seen: set[str] = set()
        for attack_index, attack in enumerate(enemy["attacks"]):
            base = _slug(str(attack.get("name") or ""), attack_index)
            candidate, suffix = base, 2
            while candidate in attack_seen:
                candidate = f"{base}_{suffix}"
                suffix += 1
            attack["id"] = candidate
            attack_seen.add(candidate)


def normalize_temporary_encounter(payload: Any) -> dict[str, Any]:
    """把模型输出收敛成合法敌人档案；非法输入抛 ValueError，绝不放行。"""

    if not isinstance(payload, dict):
        raise ValueError("AI 临时遭遇结构无效")
    raw_enemies = payload.get("enemies")
    if not isinstance(raw_enemies, list) or not 1 <= len(raw_enemies) <= MAX_TEMPORARY_ENEMIES:
        raise ValueError(f"临时遭遇需要 1 到 {MAX_TEMPORARY_ENEMIES} 个敌人")
    enemies: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_enemies):
        if not isinstance(raw, dict):
            raise ValueError("敌人数据无效")
        name = str(raw.get("name") or "").strip()[:60] or f"敌人{index + 1}"
        enemy: dict[str, Any] = {"id": "", "name": name}
        for field_name, bounds in _ENEMY_FIELD_BOUNDS.items():
            enemy[field_name] = _coerce_bounded_int(
                raw.get(field_name), field_name, bounds,
                _ENEMY_FIELD_DEFAULTS.get(field_name),
            )
        raw_attacks = raw.get("attacks")
        if not isinstance(raw_attacks, list) or not 1 <= len(raw_attacks) <= 3:
            raise ValueError(f"敌人 {name} 需要 1 到 3 个攻击")
        attacks: list[dict[str, Any]] = []
        for attack_index, raw_attack in enumerate(raw_attacks):
            if not isinstance(raw_attack, dict):
                raise ValueError(f"敌人 {name} 的攻击数据无效")
            attack = {
                "id": "",
                "name": str(raw_attack.get("name") or "").strip()[:60] or f"攻击{attack_index + 1}",
                "attack_bonus": _coerce_bounded_int(
                    raw_attack.get("attack_bonus"), "attack_bonus",
                    _ATTACK_FIELD_BOUNDS["attack_bonus"],
                ),
                "damage": str(raw_attack.get("damage") or "").strip()[:40],
                "range": _coerce_bounded_int(
                    raw_attack.get("range"), "range",
                    _ATTACK_FIELD_BOUNDS["range"], _ATTACK_FIELD_DEFAULTS["range"],
                ),
            }
            attack["long_range"] = _coerce_bounded_int(
                raw_attack.get("long_range"), "long_range",
                _ATTACK_FIELD_BOUNDS["long_range"], attack["range"],
            )
            if attack["long_range"] < attack["range"]:
                attack["long_range"] = attack["range"]
            attacks.append(attack)
        enemy["attacks"] = attacks
        enemies.append(enemy)
    _assign_unique_ids(enemies)
    # 与 combat.start / preset loading 完全同一份底层安全边界。
    validate_enemy_profiles(enemies)
    title = str(payload.get("title") or "").strip()[:80]
    if not title:
        title = f"{enemies[0]['name']} ×{len(enemies)}"
    description = str(payload.get("description") or "").strip()[:300]
    return {"title": title, "description": description, "enemies": enemies}


async def plan_temporary_encounter(
    instance: Any, campaign: dict[str, Any], llm_client: Any,
) -> dict[str, Any]:
    """生成一场临时遭遇提案；输出只是 proposal，不落任何权威状态。"""

    if not llm_client or not hasattr(llm_client, "call_tools"):
        raise ValueError("未配置可用的 AI 服务商")
    language = str(getattr(instance, "language", "") or "")
    context = json.dumps(
        _generation_context(instance, campaign),
        ensure_ascii=False, separators=(",", ":"),
    )
    response = await llm_client.call_tools(
        _temporary_prompt(language),
        context,
        tools=[TEMPORARY_ENCOUNTER_TOOL],
        max_tokens=2048,
        temperature=0.2,
    )
    instance.record_llm_usage(int(getattr(response, "total_tokens", 0) or 0))
    for call in getattr(response, "tool_calls", []) or []:
        if str(call.get("name") or "") != TEMPORARY_ENCOUNTER_TOOL_NAME:
            continue
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        proposal = normalize_temporary_encounter(arguments)
        proposal["planner"] = {
            "provider": str(getattr(response, "provider_used", "") or ""),
            "native_tools": bool(getattr(response, "native_tools", False)),
        }
        return proposal
    raise ValueError("AI 没有返回有效的临时遭遇结构")
