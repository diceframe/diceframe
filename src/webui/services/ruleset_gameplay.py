"""Authenticated Web/API boundary for authoritative ruleset gameplay intents."""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.webui.ruleset_draft_validation import validate_draft_shape
from src.adventures import binding_matches
from src.engine import progression
from src.engine.modules import session_stats
from src.engine.action_gate import (
    GateRequest, ROUND_PROCESSING, SOURCE_INTENT, STRUCTURED_INTENT_POLICY,
    check_not_judging, check_seat_exists, evaluate,
)
from src.rulesets.automation import (
    advance_automatic_intents,
    append_public_timeline_entry,
    is_public_story_milestone,
)
from src.rulesets.contracts import (
    AdventureBindingMigrationRuntime,
    AuthoritativeIntentHooks,
    AutomaticIntentRuntime,
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
    resolve_adventure_binding: Callable[..., dict[str, Any]]
    save_instance: Callable[[Any], Awaitable[None]]
    apply_memory_delta: Callable[[str, dict[str, Any], int], Awaitable[Any]] | None
    # AI 临时遭遇等只读 LLM 提案使用；None 表示环境没有可用的 AI 服务商。
    resolve_llm_client: Callable[[], Any | None] | None = None
    # Existing authoritative-intent HTTP seam for Adventure v2 completion.
    # The callback is explicit so this service never reaches through WebAPI.
    complete_adventure_node: Callable[[Any, str, str, str], dict[str, Any]] | None = None


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": message}


def _intent_write_error(
    instance: Any, requester_id: str, requester_is_gm: bool = False,
) -> dict[str, Any] | None:
    """Check live admission under the state lock, before any transaction writes."""
    code = evaluate(
        instance,
        GateRequest(actor_uid=requester_id, source=SOURCE_INTENT, requester_is_gm=requester_is_gm),
        STRUCTURED_INTENT_POLICY,
    )
    if not code:
        return None
    return _error(
        code, "回合正在处理中，请稍后重试" if code == ROUND_PROCESSING else "当前玩家不在本局中",
    )


def _seat_actor_id(uid: str) -> str:
    """Map a seat uid to its authoritative combat actor identity.

    ``player:<uid>`` is the actor identity convention the engine and the ruleset
    layer already share (``src/engine/checks.py`` and
    ``src/webui/services/combat_extension.py`` build the same string).  Generic
    code must not import the concrete ruleset, so this one string convention is
    kept here and used only to answer "is it this seat's turn right now?".
    """

    return f"player:{uid}"


def _active_combat(instance: Any) -> dict[str, Any] | None:
    state = getattr(instance, "ruleset_state", None)
    combat = state.get("combat") if isinstance(state, dict) else None
    if isinstance(combat, dict) and combat.get("status") == "active":
        return combat
    return None


def _project_public_batch(
    runtime: Any, instance: Any, batch: dict[str, Any], applied: dict[str, Any],
) -> None:
    if applied.get("applied") and is_public_story_milestone(runtime, batch):
        append_public_timeline_entry(runtime, instance, batch)


def _automatic_segment(
    runtime: Any, instance: Any, rng: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The bounded server-owned automation ladder, or nothing without one."""

    if not isinstance(runtime, AutomaticIntentRuntime):
        return [], []
    return advance_automatic_intents(
        runtime, instance, rng,
        on_applied=lambda batch, applied: _project_public_batch(
            runtime, instance, batch, applied,
        ),
    )


async def _project_batch_memory(
    dependencies: RulesetGameplayDependencies,
    runtime: Any,
    instance: Any,
    batches: list[dict[str, Any]],
) -> None:
    """Best-effort long-term memory projection of already-persisted batches."""

    if not dependencies.apply_memory_delta:
        return
    memories = [
        memory
        for batch in batches
        for memory in runtime.memory_deltas_from_event_batch(batch, instance)
    ]
    for memory in memories:
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
    code = evaluate(
        instance,
        GateRequest(actor_uid=requester_id, source=SOURCE_INTENT, requester_is_gm=requester_is_gm),
        # Shared by queries too. Write admission runs under the state lock,
        # after application-command authorization, to avoid a stale phase check.
        (check_seat_exists,),
    )
    if code:
        return instance, None, None, "", _error(code, "当前玩家不在本局中")
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
        try:
            expected = dependencies.resolve_adventure_binding(
                str(binding.get("adventure_id") or ""),
                runtime,
                str(getattr(instance, "world_id", "") or ""),
                str(getattr(instance, "language", "") or ""),
                str(binding.get("source_kind") or ""),
                str(binding.get("source_id") or ""),
            )
        except TypeError:
            # Older standalone harnesses expose the original four-argument
            # resolver.  Production composition always uses the source-aware
            # signature above; this compatibility branch cannot select a
            # source in a real WebAPI process.
            expected = dependencies.resolve_adventure_binding(
                str(binding.get("adventure_id") or ""),
                runtime,
                str(getattr(instance, "world_id", "") or ""),
                str(getattr(instance, "language", "") or ""),
            )
    except ValueError as exc:
        return _error("INCOMPATIBLE_ADVENTURE", str(exc))
    # FIX-02 §4.2：形状感知比较——旧存档的 5 字段绑定与重新解析出的来源感知
    # 绑定视为同一包；只有包身份真的变了才进入迁移/失败路径。
    if binding_matches(binding, expected):
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
        view_data = view if isinstance(view, dict) else {}
        access = view_data.get("encounter_access")
        combat = view_data.get("combat")
        request = view_data.get("encounter_request")
        if isinstance(combat, dict) and combat.get("status") == "active":
            return _error("COMBAT_ACTIVE", "战斗进行中，无法生成临时遭遇")
        request_pending = (
            isinstance(request, dict)
            and str(request.get("status") or "") == "pending"
        )
        # 标准自由对局没有叙事层 encounter_request；GM 从战斗工具
        # 明确点击 AI 生成时，sandbox capability 本身就是授权条件。
        # 活动冒险的未准备状态仍必须有待处理敌情，不能静默绕过剧情门槛。
        access_mode = str(access.get("mode") or "") if isinstance(access, dict) else ""
        if not request_pending and access_mode != "sandbox":
            return _error(
                "NO_PENDING_ENCOUNTER",
                "当前没有可生成临时遭遇的敌情",
            )
        if not isinstance(access, dict) or str(access.get("mode") or "") == "story":
            # 正式剧情遭遇永远优先：有绑定 preset 时绝不能用临时生成绕过。
            return _error(
                "STORY_ENCOUNTER_BOUND",
                "当前剧情已绑定正式遭遇，不能覆盖为临时遭遇",
            )
        requested_preset_id = (
            str(request.get("encounter_preset_id") or "")
            if isinstance(request, dict) and request_pending else ""
        )
        available_preset_ids = {
            str(item.get("id") or "")
            for item in (view_data.get("encounter_presets") or [])
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
    # Adventure v2 completion is a GM-authorized application command that
    # deliberately reuses the normal authenticated intent endpoint.  It is not
    # a ruleset runtime event and therefore must be dispatched before the
    # concrete runtime validates its own intent vocabulary.
    if isinstance(body, dict) and body.get("type") == "adventure.node.complete":
        if not requester_is_gm:
            return _error("GM_ONLY", "只有 GM 可以确认冒险节点完成")
        node_id = str(body.get("node_id") or "").strip()
        if not node_id:
            return _error("INVALID_ADVENTURE_NODE", "node_id is required")
        if dependencies.complete_adventure_node is None:
            return _error("ADVENTURE_RUNTIME_UNAVAILABLE", "Adventure v2 runtime is unavailable")
        async with instance._lock:
            admission_error = _intent_write_error(instance, requester_id, requester_is_gm)
            if admission_error:
                return admission_error
            binding_error = await _ensure_compatible_adventure_binding(
                dependencies, runtime, instance,
            )
            if binding_error:
                return binding_error
            before = {
                "world_state": deepcopy(getattr(instance, "world_state", None)),
                "adventure_progress": deepcopy(getattr(instance, "adventure_progress", None)),
                "economy": deepcopy(getattr(instance, "economy", None)),
            }
            try:
                completed = dependencies.complete_adventure_node(
                    instance, node_id, effective_requester, effective_requester,
                )
                await dependencies.save_instance(instance)
            except (ValueError, KeyError, TypeError) as exc:
                for field, value in before.items():
                    if value is not None:
                        setattr(instance, field, value)
                return _error("ADVENTURE_NODE_REJECTED", str(exc))
            except Exception:
                for field, value in before.items():
                    if value is not None:
                        setattr(instance, field, value)
                logger.exception("Adventure node transaction failed and was restored")
                return _error(
                    "ADVENTURE_NODE_FAILED",
                    "冒险节点结算失败，权威状态已恢复，请重试",
                )
            return _response(
                dependencies, rule, runtime, instance, effective_requester,
                requester_is_gm=True,
                result={"adventure_node": completed},
            )

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
        admission_error = _intent_write_error(instance, requester_id, requester_is_gm)
        if admission_error:
            return admission_error
        progression.require_writable(instance)
        session_stats.require_writable(instance)
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
            _project_public_batch(runtime, instance, batch, applied)
            automatic_batches, automatic_results = _automatic_segment(
                runtime, instance, rng,
            )
            session_stats.touch(instance)
            await dependencies.save_instance(instance)
        except (ValueError, KeyError, TypeError) as exc:
            instance.restore_ruleset_transaction(before)
            return _error("INTENT_REJECTED", str(exc))
        except Exception:
            instance.restore_ruleset_transaction(before)
            raise
        await _project_batch_memory(
            dependencies, runtime, instance, [batch, *automatic_batches],
        )
        return _response(
            dependencies, rule, runtime, instance, effective_requester,
            requester_is_gm=requester_is_gm,
            result={
                **applied,
                "replayed": bool(resolved.get("replayed", False)),
                "pending_decision": deepcopy(resolved.get("pending_decision")),
                "automatic_event_batches": automatic_batches,
                "automatic_results": automatic_results,
                "resolved_event_batches": [batch, *automatic_batches],
            },
        )


async def resume_authoritative_combat(
    dependencies: RulesetGameplayDependencies,
    game_key: str,
    seat_uid: str,
) -> dict[str, Any]:
    """控制权变更后，立刻把该席位的权威回合走完。

    这是 B8 的唯一入口：``next_automatic_intent()`` 已经能识别
    「此刻由 AI 托管的 ``player:<uid>``」，这里只补上触发时机——控制权刚写入
    就复用同一条确定性自动阶梯，直到轮到真人、战斗结束或没有自动意图为止，
    而不是等某个人再点一次攻击。

    ``handled=False`` 表示本局不是权威意图运行时（例如自由文本规则），调用方
    应当继续走探索回合的推进边界。**绝不**替不属于该席位的 actor 出手：只有
    ``next_automatic_intent()`` 现在给出的 actor 就是该席位时才继续，所以
    "当前 actor 不是这个 PC" 时不会有任何状态变化（B17 第二种情形）。

    失败时回滚整段事务并返回结构化错误，不半提交（B19）。
    """

    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if instance is None:
        return {
            "ok": False, "handled": True, "resumed": False,
            "error_code": "GAME_NOT_FOUND", "error": "游戏不存在",
        }
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        # 没有规则就没有权威意图；交给探索回合的边界处理。
        return {"ok": True, "handled": False, "resumed": False, "reason": "no_rule"}
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except ValueError as exc:
        return {
            "ok": False, "handled": True, "resumed": False,
            "error_code": "RULESET_RUNTIME_UNAVAILABLE", "error": str(exc),
        }
    if not runtime.capabilities.authoritative_intents or _active_combat(instance) is None:
        return {
            "ok": True, "handled": False, "resumed": False,
            "reason": "not_authoritative_combat",
        }
    if not isinstance(runtime, AutomaticIntentRuntime):
        return {
            "ok": True, "handled": True, "resumed": False,
            "reason": "no_automatic_intent_runtime",
        }

    async with instance._lock:
        # Server-owned automation keeps its existing actor checks below; only
        # narrative judgment is a new restriction, including after lock waits.
        code = check_not_judging(instance, GateRequest(actor_uid=seat_uid, source=SOURCE_INTENT))
        if code:
            return {
                "ok": False, "handled": True, "resumed": False,
                "error_code": code, "error": "回合正在处理中，请稍后重试",
            }
        progression.require_writable(instance)
        session_stats.require_writable(instance)
        binding_error = await _ensure_compatible_adventure_binding(
            dependencies, runtime, instance,
        )
        if binding_error:
            return {**binding_error, "handled": True, "resumed": False}
        # 复核与推进在同一个持锁区段内：飞行期间回合可能已经易主，此时
        # "轮到谁"必须以写入那一刻的状态为准。
        pending = runtime.next_automatic_intent(instance)
        if pending is None or str(pending.get("actor_id") or "") != _seat_actor_id(seat_uid):
            return {
                "ok": True, "handled": True, "resumed": False,
                "reason": "not_this_seat",
            }
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
            automatic_batches, automatic_results = _automatic_segment(
                runtime, instance, rng,
            )
            session_stats.touch(instance)
            await dependencies.save_instance(instance)
        except (ValueError, KeyError, TypeError) as exc:
            instance.restore_ruleset_transaction(before)
            logger.warning(
                "控制权变更后的权威回合推进失败，已回滚: game=%s uid=%s",
                game_key, seat_uid, exc_info=True,
            )
            return {
                "ok": False, "handled": True, "resumed": False,
                "error_code": "AUTOMATIC_TURN_FAILED", "error": str(exc),
            }
        except Exception:
            instance.restore_ruleset_transaction(before)
            raise
        await _project_batch_memory(
            dependencies, runtime, instance, automatic_batches,
        )
    return {
        "ok": True,
        "handled": True,
        "resumed": True,
        "automatic_event_batches": automatic_batches,
        "automatic_results": automatic_results,
    }

