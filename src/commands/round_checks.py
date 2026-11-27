"""Check planning and settlement preparation for the round's judgment stage.

``RoundProcessor`` owns the round lifecycle; this module owns the first stage
of a judged round: the model plans the checks, stale plans are fenced against
the live instance, the plan's structured side effects settle (economy offers,
world legality, world time), and the server-side dice constraint block is
prepared for settlement.  The offline compatibility path stays the processor's
own ``prepare_round_checks`` and is reached through the context.

Every entry point takes an explicit :class:`CheckPreparationContext` rather
than the processor, so nothing here can reach back into round state it does
not own.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.commands.check_planner import plan_round_checks
from src.commands.round_actions import build_dice_constraint_block, collect_actions_text
from src.engine import module_hooks, progression
from src.engine.economy import (
    economy_changes_are_resolutions_only,
    economy_fingerprint,
    has_pending_identical_purchase,
    queue_purchase_offer,
)
from src.engine.game_instance import GameInstance, GameState
from src.engine.modules import adventure_runtime_state, checks, progression_state, session_stats
from src.engine.world_events import advance_world_time
from src.engine.world.memory_projection import queue_world_memory
from src.engine.world_legality import evaluate_world_requirements
from src.llm.client import LLMToolCallError
from src.rulesets.narrative_checks import resolve_planned_checks

logger = logging.getLogger("trpg")


class CheckPreparationError(RuntimeError):
    """Failure before check settlement; callers own the rollback of the turn."""

    def __init__(
        self,
        message: str,
        *,
        rolled_back: bool | None = None,
        rollback_failed: bool = False,
    ) -> None:
        super().__init__(message)
        # ``prepare_round_checks_ai`` raises this before it owns progression;
        # ``process_round`` fills these fields when it performs recovery for a
        # direct caller (luck timeout/CLI).
        self.rolled_back = rolled_back
        self.rollback_failed = rollback_failed


class CheckModelNotConfigured(CheckPreparationError):
    """独立检定模型已启用但无法构建客户端，检定规划无法进行。

    这是可恢复的配置错误而不是程序缺陷：调用方必须翻译成稳定的
    ``error_code``（``CHECK_MODEL_NOT_CONFIGURED``）与非 2xx 响应，并把对局
    状态完整退回（见回合服务的推进回滚），绝不让它冒泡成 500 或被当成
    成功回合。``error_code`` 是对外契约，不得随文案改动。
    """

    error_code = "CHECK_MODEL_NOT_CONFIGURED"


class InvalidCheckPlan(CheckPreparationError):
    """The configured check model did not produce a usable plan."""


@dataclass(frozen=True, slots=True)
class CheckPreparationContext:
    """The processor state the check-preparation pipeline is allowed to touch.

    Built fresh on every call by ``RoundProcessor._check_context``.  The
    callables are the processor's own bound methods, so ruleset resolution,
    deferred-check admission and the offline compatibility fallback keep
    behaving exactly as before.
    """

    registry: Any
    llm_client: Any
    check_llm_client: Any | None
    check_model_enabled: bool
    _dice: Any
    _prompt: Any
    _load_world_template: Callable[[str, str], dict | None]
    _narrative_check_resolver: Callable[[GameInstance, Any], Any | None]
    _advance_adventure_world: Callable[[GameInstance], dict[str, Any]] | None
    _deferred_check_indexes: Callable[[GameInstance], set[int]]
    prepare_round_checks: Callable[[GameInstance], list[dict]]


async def prepare_round_checks_ai(
    ctx: CheckPreparationContext, instance: GameInstance,
) -> list[dict]:
    """Stage runtime work away from live authority; retain the core path."""
    progression.require_writable(instance)
    session_stats.require_writable(instance)
    checks.require_writable(instance)
    if checks.round_checks_prepared(instance):
        return list(checks.last_checks(instance))
    if instance.state != GameState.ACTIVE_JUDGMENT:
        return []
    rule_ctx = ctx._prompt.load_rule_context(instance, ctx._load_world_template)
    if ctx._narrative_check_resolver(instance, rule_ctx.rule) is not None:
        from src.commands.runtime_check_preparation import prepare_runtime_checks

        async def prepare(candidate: GameInstance) -> list[dict]:
            return await _prepare_round_checks_ai(ctx, candidate, isolated_runtime=True)

        return await prepare_runtime_checks(instance, ctx.registry, prepare)
    return await _prepare_round_checks_ai(ctx, instance)


async def _prepare_round_checks_ai(
    ctx: CheckPreparationContext, instance: GameInstance, *, isolated_runtime: bool = False,
) -> list[dict]:
    """阶段 1：由 GM 模型统一规划检定，再由服务端一次性掷骰结算。"""
    progression.require_writable(instance)
    session_stats.require_writable(instance)
    checks.require_writable(instance)
    if checks.round_checks_prepared(instance):
        return list(checks.last_checks(instance))
    if instance.state != GameState.ACTIVE_JUDGMENT:
        return []
    if ctx.check_model_enabled and ctx.check_llm_client is None:
        raise CheckModelNotConfigured(
            "独立检定模型未配置，请在设置中选择有效服务商和模型，或关闭独立检定模型"
        )
    actions_text = collect_actions_text(instance)
    rule_ctx = ctx._prompt.load_rule_context(instance, ctx._load_world_template)
    narrative_resolver = ctx._narrative_check_resolver(instance, rule_ctx.rule)
    instance.reset_round_checks()
    deferred_indexes = ctx._deferred_check_indexes(instance)
    expected_run_id = instance.run_id
    expected_economy_fingerprint = economy_fingerprint(instance)

    def planning_target_is_current() -> bool:
        # A detached runtime candidate is fenced as a whole on publication.
        if isolated_runtime:
            return True
        current = ctx.registry.get(instance.game_key)
        return (
            current is instance
            and instance.run_id == expected_run_id
            # 玩家确认既有提案不使规划过期；回滚/状态篡改仍会。
            and economy_changes_are_resolutions_only(
                expected_economy_fingerprint, economy_fingerprint(instance),
            )
        )

    try:
        plan_started = time.perf_counter()
        try:
            planned, metadata = await plan_round_checks(
                instance, rule_ctx.rule,
                ctx.check_llm_client if ctx.check_model_enabled else ctx.llm_client,
                narrative_checks=narrative_resolver is not None,
            )
        except Exception as exc:
            if isinstance(exc, LLMToolCallError) or not ctx.check_model_enabled or not instance.action_queue:
                raise
            # Handle decoding/normalization failures after the stale-run fence.
            # A dedicated planner must not silently settle the round offline.
            # P3：程序性 bug 不能只留异常类名——保留完整堆栈便于排障。
            logger.exception("检定规划器抛出未预期异常: %s", type(exc).__name__)
            planned, metadata = [], {"available": False, "errors": [type(exc).__name__]}
        if not planning_target_is_current():
            logger.info(
                "丢弃过期检定规划: game=%s run=%s",
                instance.game_key,
                expected_run_id,
            )
            instance.reset_round_checks()
            return []
        if ctx.check_model_enabled and instance.action_queue and not metadata.get("available"):
            logger.warning("独立检定方案不可用: %s", metadata.get("errors"))
            # P3：被拒方案也是已计费调用——先入账再中止，实例侧用量不漏记。
            rejected_tokens = int(metadata.get("total_tokens", 0) or 0)
            if rejected_tokens > 0:
                instance.record_llm_usage(rejected_tokens, calls=1)
            raise InvalidCheckPlan(
                "独立检定模型未返回有效检定方案，请重试或检查模型配置",
            )
        logger.info(
            "检定规划: 完成 (round=%d, 耗时=%dms)",
            progression_state.round_value(instance),
            int((time.perf_counter() - plan_started) * 1000),
        )
        # AI 报价落库必须在过时检查之后：创建提案会推进 revision，
        # 提前创建会让 planning_target_is_current() 误判规划过期。
        for offer in metadata.get("economy_offers") or []:
            try:
                if has_pending_identical_purchase(
                    instance, str(offer["payer_uid"]), str(offer["target"]),
                ):
                    logger.info(
                        "同商品购买已待确认，跳过重复 AI 报价: payer=%s target=%s round=%d",
                        offer["payer_uid"], offer["target"], progression_state.round_value(instance),
                    )
                    continue
                quantity = max(1, min(8, int(offer.get("quantity", 1) or 1)))
                queue_purchase_offer(
                    instance,
                    payer_uid=str(offer["payer_uid"]),
                    amount=int(offer["amount"]),
                    items=[str(offer["target"])] * quantity,
                    reason=str(offer.get("note") or ""),
                    source="table_offer",
                    initial_due=(
                        int(offer["initial_due"])
                        if offer.get("initial_due") is not None else None
                    ),
                    remaining_due=(
                        int(offer["remaining_due"])
                        if offer.get("remaining_due") is not None else None
                    ),
                    quote_evidence=str(offer.get("evidence") or ""),
                    ambiguous_target=bool(offer.get("ambiguous_target")),
                    source_ref=(
                        f"ai:{instance.run_id}:{progression_state.round_value(instance)}:"
                        f"{offer['payer_uid']}:{offer['target']}:{quantity}:"
                        f"{offer.get('amount_scope') or 'total'}:{offer['amount']}:"
                        f"{offer.get('max_total') if offer.get('max_total') is not None else ''}:"
                        f"{offer.get('initial_due') if offer.get('initial_due') is not None else ''}:"
                        f"{offer.get('evidence') or ''}"
                    ),
                )
            except Exception:
                logger.warning(
                    "AI 报价提案创建失败，已跳过: %s", offer, exc_info=True,
                )
        # 无价购买意图留在实例回合内存中：结算阶段拦截同轮模型授予
        # （从不持久化、不产生金额；叙事后价格复检已按 ADR 0002 修订移除）。
        instance.round_unpriced_purchase_intents = list(
            metadata.get("unpriced_purchase_intents") or [],
        )
        if not metadata.get("available"):
            logger.warning("模型工具不可用，进入离线检定兼容路径: %s", instance.game_key)
            # The offline checker resets round-check state. Preserve the
            # economy-only blocker recovered above so an unavailable
            # planner cannot reopen the same-round free-grant path.
            recovered_unpriced = list(instance.round_unpriced_purchase_intents)
            prepared = ctx.prepare_round_checks(instance)
            instance.round_unpriced_purchase_intents = recovered_unpriced
            return prepared
        deferred_actions = {
            id(instance.action_queue[index]) for index in deferred_indexes
        }
        planned = [
            (action, request) for action, request in planned
            if id(action) not in deferred_actions
        ]
        if narrative_resolver is not None and planned:
            # Only detached candidates may cross this thread boundary.
            if not isolated_runtime:
                raise RuntimeError("runtime checks require isolated preparation")
            planned = await asyncio.to_thread(
                resolve_planned_checks, instance, narrative_resolver, planned,
            )
        for action, request in planned:
            action["check_request"] = request
        if not metadata.get("skipped"):
            instance.record_llm_usage(
                int(metadata.get("total_tokens", 0) or 0),
                calls=max(1, int(metadata.get("calls", 1) or 1)),
            )
        errors = metadata.get("errors") or []
        if errors:
            logger.warning("部分 AI 检定参数被拒绝: %s", "; ".join(str(item) for item in errors))
        module_hooks.record(
            instance, module_hooks.OVERREACH, list(metadata.get("overreach") or []),
        )
        # 世界合法性：模型只提议结构化地点，server 对照权威世界真相后才阻断或
        # 落库合法移动；空世界/未知地点一律不阻断（旧游戏行为不变）。
        world_result = evaluate_world_requirements(
            instance, metadata.get("world_requirements") or [],
        )
        module_hooks.record(
            instance, module_hooks.WORLD_LEGALITY, list(world_result.get("notes") or []),
        )
        # 逻辑世界时间：模型只报告本轮经过的时间，server 推进时钟并确定性
        # 结算到期事件（无后台 tick、无独立 scheduler）。
        time_advance = metadata.get("world_time_advance")
        if isinstance(time_advance, dict) and time_advance.get("minutes"):
            before_world = deepcopy(getattr(instance, "world_state", None))
            before_progress = deepcopy(adventure_runtime_state.progress(instance))
            try:
                outcome = advance_world_time(
                    instance, int(time_advance["minutes"]),
                    source_round=progression_state.round_value(instance),
                )
                if ctx._advance_adventure_world is not None:
                    ctx._advance_adventure_world(instance)
            except Exception as exc:
                # World settlement and its dependent Adventure gate update
                # are one in-memory authority transaction.  Restoring the
                # before-images makes a retry perform the same settlement
                # exactly once rather than advancing only half the state.
                instance.world_state = before_world
                adventure_runtime_state.replace_progress(instance, before_progress)
                logger.warning("世界时间推进被拒绝: %s", exc)
            else:
                module_hooks.record(instance, module_hooks.WORLD_EVENTS, [
                    {**item, "status": "applied"}
                    for item in outcome.get("applied") or []
                ] + [
                    {**item, "status": "failed"}
                    for item in outcome.get("failed") or []
                ])
                # World Memory 投影（母方案 §21/§22）：确定性地把白名单内的
                # WorldEvent receipts 排入 memory outbox（幂等，authoritative_
                # world 类）；只读 receipts、只写 memory 侧，不触碰世界真相。
                queue_world_memory(
                    instance, outcome.get("events") or [],
                    round_number=progression_state.round_value(instance),
                )
        build_dice_constraint_block(
            instance,
            actions_text,
            rule_ctx.rule,
            rule_ctx.dice_system,
            ctx._dice,
            planned_only=True,
            skip_action_indexes=deferred_indexes,
        )
    except CheckPreparationError:
        raise
    except LLMToolCallError as exc:
        if ctx.check_model_enabled:
            # A configured dedicated model is an explicit user choice.  If
            # every provider/tool call fails, falling back to the shared
            # offline planner would make that choice appear to work while
            # silently changing the check-model contract.
            logger.warning(
                "独立检定模型调用失败，不进入离线兼容路径: game=%s",
                instance.game_key,
                exc_info=True,
            )
            raise CheckModelNotConfigured(
                "独立检定模型调用失败，请检查服务商和模型配置，或关闭独立检定模型",
            ) from exc
        logger.exception("AI 检定规划失败，进入离线检定兼容路径: %s", instance.game_key)
        if not planning_target_is_current():
            instance.reset_round_checks()
            return []
        recovered_unpriced = list(instance.round_unpriced_purchase_intents)
        prepared = ctx.prepare_round_checks(instance)
        instance.round_unpriced_purchase_intents = recovered_unpriced
        return prepared
    except Exception:
        logger.exception("AI 检定规划失败，进入离线检定兼容路径: %s", instance.game_key)
        if not planning_target_is_current():
            instance.reset_round_checks()
            return []
        recovered_unpriced = list(instance.round_unpriced_purchase_intents)
        prepared = ctx.prepare_round_checks(instance)
        instance.round_unpriced_purchase_intents = recovered_unpriced
        return prepared
    for check in checks.last_checks(instance):
        if not check.get("check_id"):
            check["check_id"] = uuid.uuid4().hex
        if check.get("luck_spend_available"):
            check["luck_decision"] = "pending"
    from src.engine import check_decisions

    check_decisions.prepare(instance, rule_ctx.rule)
    instance.complete_round_check_preparation()
    return list(checks.last_checks(instance))
