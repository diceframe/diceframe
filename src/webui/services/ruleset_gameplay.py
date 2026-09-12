"""Authenticated Web/API boundary for authoritative ruleset gameplay intents."""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.webui.ruleset_draft_validation import validate_draft_shape
from src.rulesets.contracts import (
    AdventureBindingMigrationRuntime,
    AuthoritativeIntentHooks,
    AutomaticIntentRuntime,
    PublicTimelineProjectionRuntime,
    TemporaryEncounterPlannerRuntime,
)
from src.rulesets.registry import RulesetRuntimeRegistry


logger = logging.getLogger("trpg")


@dataclass(frozen=True)
class RulesetGameplayDependencies:
    get_instance: Callable[[tuple[str, ...]], Any | None]
    parse_game_key: Callable[[str], tuple[str, ...]]
    load_rule_for_game: Callable[[Any], Any | None]
    ruleset_registry: RulesetRuntimeRegistry
    resolve_adventure_binding: Callable[[str, Any, str, str], dict[str, Any]]
    save_instance: Callable[[Any], Awaitable[None]]
    apply_memory_delta: Callable[[str, dict[str, Any], int], Awaitable[Any]] | None
    # AI 临时遭遇等只读 LLM 提案使用；None 表示环境没有可用的 AI 服务商。
    resolve_llm_client: Callable[[], Any | None] | None = None


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": message}


def _append_ruleset_timeline_entry(
    runtime: PublicTimelineProjectionRuntime,
    instance: Any,
    batch: dict[str, Any],
) -> None:
    intent_type = str(batch.get("intent_type") or "")
    projection = runtime.public_timeline_projection(
        batch, str(getattr(instance, "language", "") or ""),
    )
    action_text = str(projection.get("action_text") or "")
    gm_response = str(projection.get("gm_response") or "")
    submitted_by = next(
        (
            str(event.get("submitted_by") or "")
            for event in batch.get("events", [])
            if isinstance(event, dict) and event.get("type") == "intent.submitted"
        ),
        "",
    )
    next_round = max(
        int(getattr(instance, "round_number", 0) or 0) + 1,
        max((int(item.get("round", 0) or 0) for item in instance.log), default=0) + 1,
    )
    instance.round_number = next_round
    instance.append_log_entry({
        "round": next_round,
        "actions": [{
            "user_id": submitted_by,
            "text": action_text,
            "source": "ruleset_authority",
            "intent_type": intent_type,
            "operation_id": str(batch.get("intent_id") or ""),
        }],
        "gm_response": gm_response,
        "state_changes": [
            str(event.get("type") or "")
            for event in batch.get("events", [])
            if isinstance(event, dict) and str(event.get("type") or "") != "intent.submitted"
        ],
        "check_results": [],
        "swipes": [],
        "current_swipe": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def _is_public_story_milestone(runtime: Any, batch: dict[str, Any]) -> bool:
    return (
        isinstance(runtime, PublicTimelineProjectionRuntime)
        and runtime.is_public_story_milestone(batch)
    )


def _context(
    dependencies: RulesetGameplayDependencies,
    game_key: str,
    requester_id: str,
    requester_is_gm: bool,
) -> tuple[Any, Any, Any, str, dict[str, Any] | None]:
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if instance is None:
        return None, None, None, "", _error("GAME_NOT_FOUND", "游戏不存在")
    if not requester_id:
        return instance, None, None, "", _error("AUTH_REQUIRED", "需要有效的游戏会话")
    effective_requester = str(instance.gm_uid or "") if requester_is_gm else requester_id
    if not effective_requester:
        return instance, None, None, "", _error("GM_IDENTITY_MISSING", "本局缺少 GM 身份")
    if not requester_is_gm and requester_id not in instance.players:
        return instance, None, None, "", _error("PLAYER_NOT_IN_GAME", "当前玩家不在本局中")
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return instance, None, None, effective_requester, _error(
            "RULE_NOT_FOUND", "本局规则不存在",
        )
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except ValueError as exc:
        return instance, rule, None, effective_requester, _error(
            "RULESET_RUNTIME_UNAVAILABLE", str(exc),
        )
    if not runtime.capabilities.authoritative_intents:
        return instance, rule, runtime, effective_requester, _error(
            "RULESET_INTENTS_UNAVAILABLE", "该规则继续使用自由文本回合流程",
        )
    binding = dict(getattr(instance, "ruleset_runtime", {}) or {})
    if binding.get("id") != runtime.runtime_id:
        return instance, rule, runtime, effective_requester, _error(
            "RULESET_BINDING_MISMATCH", "存档未绑定当前权威规则运行时",
        )
    return instance, rule, runtime, effective_requester, None


def _response(
    dependencies: RulesetGameplayDependencies,
    rule: Any,
    runtime: Any,
    instance: Any,
    requester_id: str,
    *, requester_is_gm: bool = False, result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    view = runtime.gameplay_view(instance, requester_id, requester_is_gm)
    actions = runtime.available_intents(instance, requester_id)
    payload: dict[str, Any] = {
        "ok": True,
        "game_key": "|".join(instance.game_key),
        "rule_id": str(rule.rule_id),
        "ruleset_runtime": dependencies.ruleset_registry.describe(
            rule.template
        ).to_dict(),
        "gameplay": view,
        "available_actions": actions,
    }
    if result is not None:
        payload["result"] = result
    return payload


async def _ensure_compatible_adventure_binding(
    dependencies: RulesetGameplayDependencies,
    runtime: Any,
    instance: Any,
) -> dict[str, Any] | None:
    binding = dict(getattr(instance, "adventure_binding", {}) or {})
    if not binding:
        return None
    try:
        expected = dependencies.resolve_adventure_binding(
            str(binding.get("adventure_id") or ""),
            runtime,
            str(getattr(instance, "world_id", "") or ""),
            str(getattr(instance, "language", "") or ""),
        )
    except ValueError as exc:
        return _error("INCOMPATIBLE_ADVENTURE", str(exc))
    if binding == expected:
        return None
    if not isinstance(runtime, AdventureBindingMigrationRuntime):
        return _error(
            "INCOMPATIBLE_ADVENTURE",
            "bound adventure package is missing or has changed",
        )
    migrated = runtime.migrate_adventure_binding(instance, expected)
    if migrated is None:
        return _error(
            "INCOMPATIBLE_ADVENTURE",
            "bound adventure package is missing or has changed",
        )
    if migrated:
        await dependencies.save_instance(instance)
    return None



async def plan_temporary_encounter(
    dependencies: RulesetGameplayDependencies,
    game_key: str,
    requester_id: str,
    requester_is_gm: bool = False,
) -> dict[str, Any]:
    """AI 临时遭遇提案（GM-only，只读）：生成 → GM 预览 → 确认后才 combat.start。

    本函数不改 combat、不加 state version、不写 EventBatch / adventure /
    encounter catalog；生成失败完全无副作用。
    """

    instance, rule, runtime, effective_requester, error = _context(
        dependencies, game_key, requester_id, requester_is_gm,
    )
    if error:
        return error
    if not requester_is_gm:
        return _error("GM_ONLY", "只有 GM 可以生成临时遭遇")
    if not isinstance(runtime, TemporaryEncounterPlannerRuntime):
        return _error(
            "TEMPORARY_ENCOUNTER_UNAVAILABLE",
            "当前规则不支持 AI 临时遭遇",
        )
    async with instance._lock:
        binding_error = await _ensure_compatible_adventure_binding(
            dependencies, runtime, instance,
        )
        if binding_error:
            return binding_error
        view = runtime.gameplay_view(instance, effective_requester, True)
        access = view.get("encounter_access") if isinstance(view, dict) else None
        combat = view.get("combat") if isinstance(view, dict) else None
        request = view.get("encounter_request") if isinstance(view, dict) else None
        if isinstance(combat, dict) and combat.get("status") == "active":
            return _error("COMBAT_ACTIVE", "战斗进行中，无法生成临时遭遇")
        if (
            not isinstance(request, dict)
            or str(request.get("status") or "") != "pending"
        ):
            return _error(
                "NO_PENDING_ENCOUNTER",
                "当前没有待准备的敌情，不能生成临时遭遇",
            )
        if not isinstance(access, dict) or str(access.get("mode") or "") == "story":
            # 正式剧情遭遇永远优先：有绑定 preset 时绝不能用临时生成绕过。
            return _error(
                "STORY_ENCOUNTER_BOUND",
                "当前剧情已绑定正式遭遇，不能覆盖为临时遭遇",
            )
        requested_preset_id = str(request.get("encounter_preset_id") or "")
        available_preset_ids = {
            str(item.get("id") or "")
            for item in (view.get("encounter_presets") or [])
            if isinstance(item, dict)
        }
        if requested_preset_id and requested_preset_id in available_preset_ids:
            return _error(
                "ENCOUNTER_ALREADY_PREPARED",
                "当前敌情已经匹配到合法遭遇，无需生成临时遭遇",
            )
    llm_client = (
        dependencies.resolve_llm_client() if dependencies.resolve_llm_client else None
    )
    if llm_client is None:
        return _error("LLM_NOT_CONFIGURED", "未配置可用的 AI 服务商")
    try:
        proposal = await runtime.plan_temporary_encounter(instance, llm_client)
    except ValueError as exc:
        return _error("TEMPORARY_ENCOUNTER_INVALID", str(exc))
    except Exception:
        logger.exception("AI temporary encounter planning failed")
        return _error(
            "LLM_REQUEST_FAILED",
            "AI 生成临时遭遇失败，你仍可以手动选择自由遭遇",
        )
    if not isinstance(proposal, dict):
        return _error(
            "TEMPORARY_ENCOUNTER_INVALID",
            "无法生成合法的临时遭遇，你仍可以手动选择自由遭遇",
        )
    return {"ok": True, "encounter": proposal}


async def available_actions(
    dependencies: RulesetGameplayDependencies,
    game_key: str,
    requester_id: str,
    requester_is_gm: bool = False,
) -> dict[str, Any]:
    instance, rule, runtime, effective_requester, error = _context(
        dependencies, game_key, requester_id, requester_is_gm,
    )
    if error:
        return error
    async with instance._lock:
        binding_error = await _ensure_compatible_adventure_binding(
            dependencies, runtime, instance,
        )
        if binding_error:
            return binding_error
        return _response(
            dependencies, rule, runtime, instance, effective_requester,
            requester_is_gm=requester_is_gm,
        )

async def submit_intent(
    dependencies: RulesetGameplayDependencies,
    game_key: str,
    requester_id: str,
    requester_is_gm: bool,
    body: Any,
) -> dict[str, Any]:
    instance, rule, runtime, effective_requester, error = _context(
        dependencies, game_key, requester_id, requester_is_gm,
    )
    if error:
        return error
    try:
        intent = deepcopy(validate_draft_shape(body))
    except ValueError as exc:
        return _error("INVALID_INTENT_SHAPE", str(exc))
    intent["submitted_by"] = effective_requester
    if not isinstance(runtime, AuthoritativeIntentHooks):
        return _error(
            "RULESET_INTENT_HOOKS_UNAVAILABLE",
            "权威规则运行时缺少请求身份与投影边界",
        )
    intent = runtime.prepare_intent_submission(
        intent, effective_requester, requester_is_gm,
    )

    async with instance._lock:
        binding_error = await _ensure_compatible_adventure_binding(
            dependencies, runtime, instance,
        )
        if binding_error:
            return binding_error
        before = {
            "ruleset_state": deepcopy(instance.ruleset_state),
            "event_ledger": deepcopy(instance.event_ledger),
            "players": deepcopy(instance.players),
            "combat_state": instance.combat_state,
            "combat_active": instance.combat_active,
            "initiative_order": deepcopy(instance.initiative_order),
            "initiative_current": instance.initiative_current,
            "last_activity": instance.last_activity,
            "log": deepcopy(instance.log),
            "round_number": instance.round_number,
        }
        try:
            rng = random.SystemRandom()
            resolved = runtime.resolve_intent(instance, intent, rng)
            if not resolved.get("ok"):
                return resolved
            batch = resolved.get("event_batch")
            if not isinstance(batch, dict):
                return _error("INVALID_EVENT_BATCH", "规则运行时没有返回有效事件批次")
            applied = runtime.apply_event_batch(instance, batch)
            if applied.get("applied") and _is_public_story_milestone(runtime, batch):
                _append_ruleset_timeline_entry(runtime, instance, batch)
            automatic_batches: list[dict[str, Any]] = []
            automatic_results: list[dict[str, Any]] = []
            if isinstance(runtime, AutomaticIntentRuntime):
                for _ in range(256):
                    automatic_intent = runtime.next_automatic_intent(instance)
                    if automatic_intent is None:
                        break
                    automatic = runtime.resolve_intent(instance, automatic_intent, rng)
                    if not automatic.get("ok"):
                        raise ValueError(
                            str(automatic.get("error") or "automatic combat intent was rejected")
                        )
                    automatic_batch = automatic.get("event_batch")
                    if not isinstance(automatic_batch, dict):
                        raise ValueError("automatic combat intent returned no event batch")
                    automatic_applied = runtime.apply_event_batch(instance, automatic_batch)
                    if not automatic_applied.get("applied"):
                        raise ValueError("automatic combat intent did not advance state")
                    automatic_batches.append(deepcopy(automatic_batch))
                    automatic_results.append(deepcopy(automatic_applied))
                    if _is_public_story_milestone(runtime, automatic_batch):
                        _append_ruleset_timeline_entry(
                            runtime, instance, automatic_batch,
                        )
                else:
                    raise ValueError("automatic combat turn exceeded the safety limit")
            instance.last_activity = datetime.now(timezone.utc).isoformat()
            await dependencies.save_instance(instance)
        except (ValueError, KeyError, TypeError) as exc:
            instance.restore_ruleset_transaction(before)
            return _error("INTENT_REJECTED", str(exc))
        except Exception:
            instance.restore_ruleset_transaction(before)
            raise
        resolved_batches = [batch, *automatic_batches]
        if dependencies.apply_memory_delta:
            for memory in [
                memory
                for resolved_batch in resolved_batches
                for memory in runtime.memory_deltas_from_event_batch(resolved_batch, instance)
            ]:
                try:
                    await dependencies.apply_memory_delta(
                        instance.memory_namespace, {"add": [memory]},
                        int(getattr(instance, "round_number", 0) or 0),
                    )
                except Exception:
                    # Long-term memory is a derived projection of the persisted
                    # EventBatch. A projection failure must not roll back or
                    # contradict the already-saved authoritative campaign state.
                    logger.exception("D&D chapter-summary memory projection failed")
        return _response(
            dependencies, rule, runtime, instance, effective_requester,
            requester_is_gm=requester_is_gm,
            result={
                **applied,
                "replayed": bool(resolved.get("replayed", False)),
                "pending_decision": deepcopy(resolved.get("pending_decision")),
                "automatic_event_batches": automatic_batches,
                "automatic_results": automatic_results,
                "resolved_event_batches": resolved_batches,
            },
        )
