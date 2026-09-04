"""Read-only projections for game lists, detail views, logs, and health."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from src.engine.game_instance import GameState
from src.engine.health import health_payload
from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.llm.parser import sanitize_narration
from src.rulesets.contracts import GameDetailProjectionRuntime
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services._common import _GAME_KEY_SEP
from src.webui.ruleset_rest_projection import public_rest_session

logger = logging.getLogger("trpg")


@dataclass(frozen=True)
class GameQueryDependencies:
    list_instances: Callable[[], list[Any]]
    get_instance: Callable[[tuple[str, ...]], Any | None]
    parse_game_key: Callable[[str], tuple[str, ...]]
    load_world_template: Callable[[str], dict[str, Any] | None] | None
    load_rule_for_game: Callable[[Any], Any | None]
    ruleset_registry: RulesetRuntimeRegistry


def projected_rule_id(
    dependencies: GameQueryDependencies,
    instance: Any,
) -> str:
    """Resolve a legacy save's effective rule ID without modifying the save."""

    rule_id = str(getattr(instance, "rule_id", "") or "").strip()
    if rule_id:
        return rule_id
    try:
        template = (
            dependencies.load_world_template(str(instance.world_id or ""))
            if dependencies.load_world_template
            else None
        )
        rule_id = str((template or {}).get("default_rule") or "").strip()
    except Exception:
        logger.warning(
            "读取旧对局的默认规则失败: %s",
            getattr(instance, "world_id", ""),
            exc_info=True,
        )
    return rule_id or "freeform_fantasy"


def list_games(dependencies: GameQueryDependencies) -> dict[str, Any]:
    active = []
    for instance in dependencies.list_instances():
        multiplayer = instance.multiplayer_status()
        active.append({
            "game_key": _GAME_KEY_SEP.join(instance.game_key),
            "world_id": instance.world_id,
            "world_name": instance.world_name,
            "rule_id": projected_rule_id(dependencies, instance),
            "scene_image": dict(getattr(instance, "scene_image", {}) or {}),
            "map_background": dict(getattr(instance, "map_background", {}) or {}),
            "group_name": instance.group_name,
            "state": instance.state.value,
            "round_number": instance.round_number,
            "player_count": len(instance.players),
            "max_players": max(1, int(getattr(instance, "max_players", 6) or 6)),
            "combat_active": instance.combat_active,
            "scene": instance.scene,
            "total_llm_calls": instance.total_llm_calls,
            "total_tokens": instance.total_tokens,
            "started_at": instance.started_at,
            "last_activity": instance.last_activity,
            "seed_code": instance.seed_code,
            "language": normalize_language(
                getattr(instance, "language", DEFAULT_LANGUAGE)
            ),
            "solo_mode": instance.solo_mode,
            "narrative_perspective": getattr(
                instance, "narrative_perspective", "auto"
            ),
            "gm_uid": instance.gm_uid or "",
            "ready_count": multiplayer["ready_count"],
            "alive_count": multiplayer["alive_count"],
        })
    active.sort(
        key=lambda game: str(
            game.get("last_activity") or game.get("started_at") or ""
        ),
        reverse=True,
    )
    return {"games": active, "total": len(active)}


def game_detail(
    dependencies: GameQueryDependencies,
    game_key: str,
    viewer_uid: str = "",
) -> dict[str, Any] | None:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if not instance:
        return None
    economy_proposals = [
        dict(proposal)
        for proposal in (getattr(instance, "economy", {}).get("proposals", []) or [])
        if isinstance(proposal, dict)
        and proposal.get("status") == "pending"
        and (
            not viewer_uid
            or viewer_uid == instance.gm_uid
            or proposal.get("visibility") == "party"
            or viewer_uid in {
                str(proposal.get("payer_uid") or ""),
                str(proposal.get("recipient_uid") or ""),
            }
            or viewer_uid in {
                str(item.get("uid") or "")
                for item in (proposal.get("contributors") or [])
                if isinstance(item, dict)
            }
        )
    ]
    detail = {
        "game_key": _GAME_KEY_SEP.join(instance.game_key),
        "run_id": instance.run_id,
        "world_id": instance.world_id or "",
        "rule_id": projected_rule_id(dependencies, instance),
        "scene_image": dict(getattr(instance, "scene_image", {}) or {}),
        "map_background": dict(getattr(instance, "map_background", {}) or {}),
        "world_name": instance.world_name,
        "group_name": instance.group_name,
        "state": instance.state.value,
        "round_number": instance.round_number,
        "player_count": len(instance.players),
        "scene": instance.scene,
        "total_llm_calls": instance.total_llm_calls,
        "total_tokens": instance.total_tokens,
        "started_at": instance.started_at,
        "last_activity": instance.last_activity,
        "seed_code": instance.seed_code,
        "language": normalize_language(
            getattr(instance, "language", DEFAULT_LANGUAGE)
        ),
        "gm_uid": instance.gm_uid or "",
        "player_access_open": bool(
            getattr(instance, "player_access_open", True)
        ),
        "has_room_password": bool(getattr(instance, "room_password", "")),
        "quick_actions": getattr(instance, "quick_actions", []),
        "economy_proposals": economy_proposals,
        "pending_luck_decisions": instance.pending_luck_checks(),
        "round_check_results": (
            [dict(item) for item in instance.last_checks]
            if (
                instance.state == GameState.ACTIVE_JUDGMENT
                and instance.round_checks_prepared
            )
            else []
        ),
        "difficulty": instance.difficulty,
        "solo_mode": instance.solo_mode,
        "narrative_perspective": getattr(
            instance, "narrative_perspective", "auto"
        ),
        "max_players": instance.max_players,
        "multiplayer": instance.multiplayer_status(),
        "rest_session": public_rest_session(instance),
        "plot_tracker": (
            instance.plot_tracker.to_dict() if instance.plot_tracker else None
        ),
        "recap": _public_recap(instance),
        "token_budget_bump": getattr(instance, "last_token_budget_bump", None),
        "adventure_binding": dict(
            getattr(instance, "adventure_binding", {}) or {}
        ),
    }
    if getattr(instance, "ruleset_runtime", None):
        binding = dict(instance.ruleset_runtime)
        rule = dependencies.load_rule_for_game(instance)
        runtime = None
        if rule is not None:
            try:
                binding = {
                    **binding,
                    **dependencies.ruleset_registry.describe(rule.template).to_dict(),
                }
                runtime = dependencies.ruleset_registry.resolve(rule.template)
            except ValueError:
                logger.warning(
                    "对局规则运行时元数据不可用: %s",
                    instance.game_key,
                    exc_info=True,
                )
        detail["ruleset_runtime"] = binding
        if isinstance(runtime, GameDetailProjectionRuntime):
            detail.update(runtime.game_detail_projection(instance))
    return detail


def clean_public_narration(text: str) -> str:
    """Return public narration without structured tags after the separator."""

    return sanitize_narration(str(text or ""))


def _public_recap(instance: Any) -> dict[str, Any]:
    """Build a public recap without exposing private logs."""

    def action_view(action: dict) -> dict[str, Any] | None:
        if not isinstance(action, dict):
            return None
        user_id = str(action.get("user_id") or "")
        if user_id == "system":
            return None
        name = (
            instance.players.get(user_id, {}).get("character_name")
            or user_id
            or "冒险者"
        )
        action_text = str(action.get("text") or "").strip()
        if not action_text:
            return None
        signature = f"{user_id}:{action.get('timestamp') or action_text[:32]}"
        return {
            "character_name": name,
            "text": action_text,
            "signature": signature,
            "source": str(action.get("source") or ""),
            "dice_pending": bool(action.get("dice_pending")),
            "dice_roll_source": str(action.get("dice_roll_source") or ""),
        }

    recent_rounds: list[dict[str, Any]] = []
    for entry in (instance.log or [])[-3:]:
        actions = [
            view
            for action in entry.get("actions", []) or []
            if (view := action_view(action))
        ]
        recent_rounds.append({
            "round": entry.get("round", "?"),
            "actions": actions,
            "gm_response": clean_public_narration(entry.get("gm_response", "")),
            "state_changes": list(entry.get("state_changes", []) or []),
        })
    pending_actions = [
        view
        for action in (instance.action_queue or [])
        if (view := action_view(action))
    ]
    return {
        "narrative": clean_public_narration(
            (getattr(instance, "summary", {}) or {}).get("narrative") or ""
        ),
        "key_facts": list(getattr(instance, "key_facts", []) or [])[-8:],
        "recent_rounds": recent_rounds,
        "pending_actions": pending_actions,
        "current_scene": instance.scene,
        "round_number": instance.round_number,
    }


def multiplayer_status(
    dependencies: GameQueryDependencies,
    game_key: str,
) -> dict[str, Any]:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if not instance:
        return {"ok": False, "error": "游戏不存在"}
    return {"ok": True, **instance.multiplayer_status()}


def player_context(
    *, preview: bool = False, delegate: bool = False, user_id: str = "",
) -> dict[str, Any]:
    """Project server-authenticated player context without accepting client claims."""

    return {
        "ok": True,
        "preview": bool(preview),
        "delegate": bool(delegate),
        "user_id": user_id,
    }


def private_log(
    dependencies: GameQueryDependencies,
    game_key: str,
) -> dict[str, Any]:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if not instance:
        return {"ok": False, "error": "游戏不存在"}
    return {"ok": True, "messages": _private_log_messages(instance)[-50:]}


def private_log_for_user(
    dependencies: GameQueryDependencies,
    game_key: str,
    user_id: str,
) -> dict[str, Any]:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if not instance:
        return {"ok": False, "error": "游戏不存在"}
    if user_id not in instance.players:
        return {"ok": False, "error": "玩家不存在"}
    return {
        "ok": True,
        "messages": _private_log_messages(instance, user_id)[-50:],
    }


def table_talk(
    dependencies: GameQueryDependencies,
    game_key: str,
) -> dict[str, Any]:
    """Return the bounded public table-talk channel, separate from round logs."""

    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if not instance:
        return {"ok": False, "error": "游戏不存在"}
    exchanges = [
        dict(item)
        for item in (instance.table_talk or [])
        if isinstance(item, dict) and item.get("visibility") == "party"
    ]
    return {"ok": True, "exchanges": exchanges[-50:]}


def _private_log_messages(
    instance: Any, only_user_id: str = "",
) -> list[dict[str, Any]]:
    def player_name(user_id: str) -> str:
        return (
            instance.players.get(user_id, {}).get("character_name") or user_id
        )

    messages: list[dict[str, Any]] = []
    for user_id, items in (instance.private_log or {}).items():
        if only_user_id and user_id != only_user_id:
            continue
        for item in items or []:
            messages.append({
                "user_id": user_id,
                "character_name": player_name(user_id),
                "round": item.get("round", 0),
                "text": item.get("text", ""),
                "source": item.get("source", "system"),
                "timestamp": item.get("timestamp", ""),
            })
    messages.sort(
        key=lambda item: (
            int(item.get("round", 0) or 0),
            str(item.get("timestamp", "")),
        )
    )
    return messages


def game_health(
    dependencies: GameQueryDependencies,
    game_key: str,
    include_resolved: bool = False,
) -> dict[str, Any]:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if not instance:
        return {"ok": False, "error": "game not found"}
    return health_payload(instance, include_resolved)
