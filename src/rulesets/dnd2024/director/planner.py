"""Model-assisted planning constrained by canonical adventure and encounter catalogs."""

from __future__ import annotations

import json
from typing import Any

from src.engine.modules import narrative_notes


ADVENTURE_CHOICE_TOOL_NAME = "dnd2024_adventure_choice"
ENCOUNTER_PRESET_TOOL_NAME = "dnd2024_encounter_preset"
ADVENTURE_CHOICE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": ADVENTURE_CHOICE_TOOL_NAME,
        "description": (
            "Map one solo player's natural-language action to an available canonical adventure choice. "
            "Return no choice when the intent is ambiguous or does not complete an offered direction."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selections": {
                    "type": "array", "maxItems": 8,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "player_id": {"type": "string"},
                            "choice_id": {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "reason": {"type": "string", "maxLength": 160},
                        },
                        "required": ["player_id", "choice_id", "confidence", "reason"],
                    },
                },
            },
            "required": ["selections"],
        },
    },
}

ENCOUNTER_PRESET_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": ENCOUNTER_PRESET_TOOL_NAME,
        "description": (
            "Select one canonical encounter preset that clearly matches the established opposition. "
            "Return an empty preset id when none of the offered presets is a credible match."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "encounter_preset_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string", "maxLength": 160},
            },
            "required": ["encounter_preset_id", "confidence", "reason"],
        },
    },
}


def _planner_prompt(language: str) -> str:
    if str(language or "").lower().startswith("zh"):
        return (
            "你是 D&D 冒险节点意图解析器。只判断玩家已经提交的行动是否明确对应当前节点的一个可用选项。"
            "只能返回输入中存在的 choice_id；不要推进剧情、生成检定、创造事实或修改规则。"
            "只是询问、犹豫、与选项无关或存在多个同样合理解释时，choice_id 返回空字符串。"
        )
    return (
        "You are a D&D adventure-node intent parser. Decide only whether the submitted action clearly "
        "matches one available choice in the current node. Return only an offered choice_id. Do not advance "
        "the story, invent a check or fact, or alter rules. Return an empty choice_id when ambiguous."
    )


def _encounter_prompt(language: str) -> str:
    if str(language or "").lower().startswith("zh"):
        return (
            "你是 D&D 遭遇目录匹配器。根据已建立的场景和全队行动，只能从输入给出的 canonical "
            "encounter_preset_id 中选择一个与敌情明确匹配的预设。不得创造敌人、数量、HP、AC、攻击或其他数值。"
            "如果预设与当前敌人种类或情境不可信，返回空字符串；不要为了必须选择而强行匹配。"
        )
    return (
        "You are a D&D encounter-catalog matcher. Based on the established scene and all party actions, "
        "select only one offered canonical encounter_preset_id that clearly matches the opposition. Never "
        "invent enemies, counts, HP, AC, attacks, or other mechanics. Return an empty id when no offered "
        "preset is a credible match."
    )


async def plan_encounter_preset(
    instance: Any,
    proposal: dict[str, Any],
    presets: list[dict[str, Any]],
    llm_client: Any,
) -> dict[str, Any] | None:
    """Select one server-offered preset; model output remains a non-authoritative proposal."""

    if not llm_client or not hasattr(llm_client, "call_tools"):
        return None
    mode = str(proposal.get("mode") or "assist")
    if proposal.get("kind") != "combat" or mode == "manual":
        return None
    allowed = {
        str(item.get("id") or ""): {
            "encounter_preset_id": str(item.get("id") or ""),
            "name": str(item.get("name") or "")[:200],
            "description": str(item.get("description") or "")[:500],
            "difficulty": str(item.get("difficulty") or "")[:80],
            "opposition": [
                str(enemy.get("name") or enemy.get("id") or "")[:120]
                for enemy in (item.get("enemies") or [])
                if isinstance(enemy, dict)
            ][:12],
        }
        for item in presets
        if isinstance(item, dict)
        and item.get("id")
        and str(item.get("difficulty") or "") != "tutorial"
    }
    if not allowed:
        return None
    actions = [
        {"player_id": str(item.get("user_id") or ""), "text": str(item.get("text") or "")[:1000]}
        for item in (getattr(instance, "action_queue", []) or [])
        if isinstance(item, dict) and item.get("user_id") in getattr(instance, "players", {})
    ]
    if not actions:
        return None
    context = json.dumps({
        "scene": str(narrative_notes.scene(instance) or "")[:500],
        "recent_narration": [
            str(item.get("gm_response") or "")[:1000]
            for item in (getattr(instance, "log", []) or [])[-2:]
            if isinstance(item, dict) and item.get("gm_response")
        ],
        "actions": actions,
        "encounter_presets": list(allowed.values()),
    }, ensure_ascii=False, separators=(",", ":"))
    response = await llm_client.call_tools(
        _encounter_prompt(str(getattr(instance, "language", "") or "")),
        context,
        tools=[ENCOUNTER_PRESET_TOOL],
        max_tokens=384,
        temperature=0.0,
    )
    instance.record_llm_usage(int(getattr(response, "total_tokens", 0) or 0))
    for call in response.tool_calls:
        if str(call.get("name") or "") != ENCOUNTER_PRESET_TOOL_NAME:
            continue
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        preset_id = str(arguments.get("encounter_preset_id") or "")
        if preset_id not in allowed:
            continue
        try:
            confidence = max(0.0, min(1.0, float(arguments.get("confidence", 0) or 0)))
        except (TypeError, ValueError):
            continue
        if confidence < 0.75:
            continue
        return {
            **proposal,
            "encounter_preset_id": preset_id,
            "confidence": confidence,
            "rationale": str(arguments.get("reason") or "")[:160],
            "requires_gm_confirmation": mode != "auto",
            "planner": {
                "provider": str(getattr(response, "provider_used", "") or ""),
                "native_tools": bool(getattr(response, "native_tools", False)),
            },
        }
    return None


async def plan_adventure_choice(
    instance: Any, campaign: dict[str, Any], llm_client: Any,
) -> dict[str, Any] | None:
    """Return a validated proposal; model output never becomes authority directly."""

    if not llm_client or not hasattr(llm_client, "call_tools"):
        return None
    automation = campaign.get("automation") if isinstance(campaign, dict) else None
    if not isinstance(automation, dict) or automation.get("mode") != "auto":
        return None
    players = getattr(instance, "players", {}) or {}
    tutorial = campaign.get("tutorial") if isinstance(campaign.get("tutorial"), dict) else {}
    step = tutorial.get("current_step") if isinstance(tutorial.get("current_step"), dict) else {}
    choices = step.get("choices") if isinstance(step.get("choices"), list) else []
    if tutorial.get("status") != "active" or not tutorial.get("requirement_met") or not choices:
        return None
    actions = [
        {"player_id": str(item.get("user_id") or ""), "text": str(item.get("text") or "")[:1000]}
        for item in (getattr(instance, "action_queue", []) or [])
        if isinstance(item, dict) and item.get("user_id") in instance.players
    ]
    if len(actions) != len(players) or not actions or any(not item["text"].strip() for item in actions):
        return None
    allowed = {
        str(item.get("id") or ""): {
            "choice_id": str(item.get("id") or ""),
            "label": str(item.get("label") or "")[:200],
            "description": str(item.get("description") or "")[:500],
        }
        for item in choices if isinstance(item, dict) and item.get("id")
    }
    if not allowed:
        return None
    context = json.dumps({
        "scene": str(narrative_notes.scene(instance) or "")[:300],
        "step": {
            "id": str(step.get("id") or ""),
            "title": str(step.get("title") or "")[:200],
            "objective": str(step.get("objective") or "")[:500],
        },
        "actions": actions,
        "choices": list(allowed.values()),
    }, ensure_ascii=False, separators=(",", ":"))
    response = await llm_client.call_tools(
        _planner_prompt(str(getattr(instance, "language", "") or "")),
        context,
        tools=[ADVENTURE_CHOICE_TOOL],
        max_tokens=512,
        temperature=0.0,
    )
    tokens = int(getattr(response, "total_tokens", 0) or 0)
    instance.record_llm_usage(tokens)
    selected: dict[str, Any] | None = None
    for call in response.tool_calls:
        if str(call.get("name") or "") != ADVENTURE_CHOICE_TOOL_NAME:
            continue
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        raw_selections = arguments.get("selections")
        if not isinstance(raw_selections, list):
            continue
        by_player: dict[str, dict[str, Any]] = {}
        action_players = {item["player_id"] for item in actions}
        for raw in raw_selections:
            if not isinstance(raw, dict):
                continue
            player_id = str(raw.get("player_id") or "")
            choice_id = str(raw.get("choice_id") or "")
            if player_id not in action_players or player_id in by_player or choice_id not in allowed:
                continue
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence", 0) or 0)))
            except (TypeError, ValueError):
                continue
            if confidence < 0.75:
                continue
            by_player[player_id] = {
                "player_id": player_id, "choice_id": choice_id,
                "confidence": confidence, "reason": str(raw.get("reason") or "")[:160],
            }
        if set(by_player) != action_players:
            continue
        ordered = [by_player[item["player_id"]] for item in actions]
        if len(players) == 1:
            selected = {
                "kind": "adventure_choice",
                "choice_id": ordered[0]["choice_id"],
                "confidence": ordered[0]["confidence"],
                "rationale": ordered[0]["reason"],
                "requires_gm_confirmation": False,
                "mode": "auto",
                "action_ids": ["action:0"],
            }
        else:
            selected = {
                "kind": "party_decision", "selections": ordered,
                "confidence": min(item["confidence"] for item in ordered),
                "rationale": "all submitted player actions map to current adventure choices",
                "requires_gm_confirmation": False, "mode": "auto",
                "action_ids": [f"action:{index}" for index in range(len(ordered))],
            }
        break
    if selected is not None:
        selected["planner"] = {
            "provider": str(getattr(response, "provider_used", "") or ""),
            "native_tools": bool(getattr(response, "native_tools", False)),
        }
    return selected
