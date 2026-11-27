"""完整回合推进流程。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import uuid
from collections.abc import Callable
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from src.commands.economy_effects import (
    defer_narrative_effects,
    infer_narrative_reward_candidates,
    merge_narrative_reward_candidates,
    has_economy_proposal,
    pending_decision_notice,
    currency_labels_for_rule,
    unbacked_purchase_notice,
    unpriced_purchase_notice,
    unbacked_payment_notice,
    should_warn_unbacked_payment,
)
from src.commands.round_effects import (
    append_state_change_messages,
    apply_confirmed_items,
    apply_growth_rewards,
    apply_plot_update,
    apply_puzzle_updates,
    apply_revive_commands,
    apply_ruleset_combat_signal,
    store_private_messages,
    update_quick_actions,
)
from src.engine.economy import (
    economy_changes_are_resolutions_only,
    economy_fingerprint,
    purchase_grant_protection_snapshot,
)
from src.commands.death_save_tracker import resolve_round_death_saves
from src.commands.narrative_application import (
    apply_narrative_effects,
    apply_parsed_data_to_response,
)
from src.commands.narrative_quality import (
    BufferedNarrativeStream,
    build_quality_gate,
    quality_repair_instruction as build_quality_repair_instruction,
)
from src.commands.round_llm import (
    append_multistep_analysis,
    call_llm_with_tag_retry,
)
from src.commands.round_actions import (
    build_dice_constraint_block,
    collect_actions_text,
    collect_gm_directives_text,
    ensure_round_managers,
    format_check_results_constraint,
    initialize_puzzles_from_lorebook,
)
from src.commands import round_checks
from src.commands.round_checks import (
    CheckModelNotConfigured,
    CheckPreparationError,
    InvalidCheckPlan,
)
from src.commands import round_media
from src.commands.round_helpers import log_round, should_semantic_review
from src.commands.state_recap import snapshot_public_player_state
from src.commands.state_update_applier import discard_unresolved_player_damage
from src.commands.tag_summary import summarize_tags
from src.engine.economy import (
    has_blocking_economy_decision,
    queue_effect_group,
)
from src.engine.economy import (
    filter_unconfirmed_purchase_grants,
    item_names_match,
    purchase_grant_ambiguity_snapshot,
)
from src.engine import combat_narrative, module_hooks, progression
from src.engine.game_instance import GameInstance, GameState, _snapshot_players
from src.engine.module_state import ModuleStateError
from src.engine.modules import adventure_runtime_state, checks, progression_state, round_safety, ruleset_runtime, session_stats
from src.engine.language import localized_text
from src.engine.world_state import WorldStateError
from src.llm.world_prompt import (
    format_world_events_block,
    format_world_legality_block,
    format_world_state_block,
)
from src.llm.context_builder import lore_char_budget
from src.llm.parser import sanitize_narration
from src.lorebook.retrieval import LoreRetriever
from src.imagegen import (
    normalize_scene_panels,
    storyboard_source_revision,
    storyboard_panel_metadata,
)
from src.memory.summarizer import needs_summary, summarize
from src.rulesets.contracts import (
    NarrativeCheckPolicyRuntime,
    NarrativeDirectorPlanningRuntime,
    NarrativeDirectorRuntime,
)
from src.rulesets.automation import apply_director_automation, summarize_automation_batches
from src.rulesets.narrative_checks import resolver_for

logger = logging.getLogger("trpg")


def overreach_guard_enabled() -> bool:
    """顺带裁判开关：默认关，TRPG_OVERREACH_GUARD=1 启用。"""
    return os.environ.get("TRPG_OVERREACH_GUARD", "") == "1"


def _build_recent_public_narration_context(
    instance: GameInstance,
    *,
    rounds: int = 3,
    max_chars: int = 2400,
) -> str:
    """收集最近若干回合已公开给玩家的 GM 正文，供叙事二次压缩阶段核对 QUICK_ACTIONS（#272）。

    数据源只有 instance.log 的 gm_response（玩家已看到的公开内容），经
    sanitize_narration 清理；绝不接入完整 context / 世界书 / 记忆 / 私密日志——
    QUICK_ACTIONS 的知识边界以玩家已知为限。无历史时返回空串。总长超限时
    丢弃更旧的回合，保留最近内容。
    """
    chunks: list[str] = []
    for entry in list(getattr(instance, "log", None) or [])[-rounds:]:
        text = sanitize_narration(str(entry.get("gm_response", "") or "")).strip()
        if text:
            chunks.append(f"Round {entry.get('round', '?')}:\n{text}")
    while chunks and sum(len(chunk) for chunk in chunks) + 2 * (len(chunks) - 1) > max_chars:
        chunks.pop(0)
    if not chunks:
        return ""
    return "\n\n".join(chunks)


def format_overreach_block(instance: GameInstance) -> str:
    """把本轮裁判的越权标注组装为可信裁定块（服务端组装，玩家不可注入）。"""
    notes = list(module_hooks.read(instance, module_hooks.OVERREACH))
    if not notes:
        return ""
    lines = []
    for note in notes:
        uid = str(note.get("player") or "")
        name = (instance.players.get(uid) or {}).get("character_name", uid) if uid else ""
        lines.append(f"- {name or uid}: {note.get('reason', '')}")
    heading = localized_text(instance.language, {
        "en": (
            "## Authority Adjudication · Must Follow\n"
            "The referee flagged the following declarations as overreach. Narrate them as attempts and the "
            "world's reaction; never accept them as world facts, and never let them change checks or state:"
        ),
        "zh-CN": (
            "【权限裁定·必须遵循】\n"
            "以下声明被裁判标记为越权：请叙述为尝试与世界的反应，不得接受为世界事实，"
            "不得因其改变检定或状态："
        ),
        "ja": (
            "【権限裁定・必ず従うこと】\n"
            "以下の宣言はレフェリーにより権限越えと判定された。試みと世界の反応として叙述し、"
            "世界事実として受け入れず、これにより判定や状態を変更してはならない："
        ),
        "de": (
            "## Autoritätsentscheid · Muss befolgt werden\n"
            "Der Schiedsrichter hat die folgenden Aussagen als Kompetenzüberschreitung markiert. "
            "Erzähle sie als Versuche und die Reaktion der Welt; akzeptiere sie niemals als Welttatsachen, "
            "und lass sie niemals Proben oder Status verändern:"
        ),
    })
    return f"{heading}\n" + "\n".join(lines)


class RoundNotProcessed(RuntimeError):
    """本轮判定没有被执行，也没有产生叙事。

    调用方 MUST NOT 把它当成「回合已成功处理」：它覆盖实例已被替换、运行已
    过期、状态不在判定阶段、经济/幸运等待，以及其他任务正持有
    ``_process_lock``（``reason="busy"``）等「未处理」情形。

    ``reason`` 供调用方区分文案与状态码；可能取值：
    ``not_judging`` / ``economy_pending`` / ``luck_pending`` / ``busy`` / ``stale``。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RoundProcessingFailure(RuntimeError):
    """本轮判定执行中失败；``rolled_back`` 表示实例是否已退回行动阶段。

    处理边界（``RoundProcessor.process_round``）在抛出前已完成回滚、落盘与
    重置广播，因此任何入口——Web 回合服务、幸运超时、CLI——都不会把对局留在
    ``ACTIVE_JUDGMENT``。
    """

    def __init__(self, message: str, *, rolled_back: bool) -> None:
        super().__init__(message)
        self.rolled_back = rolled_back


class RoundProcessor:
    """处理完整的一轮判定：context 拼接 → LLM 调用 → 解析 → 更新状态 → 播报。"""

    def __init__(
        self,
        registry: Any,
        llm_client: Any,
        matcher: Any,
        lorebook_store: Any,
        memory_store: Any,
        prompt: Any,
        dice: Any,
        combat: Any,
        puzzles: Any,
        state_applier: Any,
        progression: Any,
        load_world_template: Callable[[str, str], dict | None],
        ensure_matcher_for_world: Callable[[str, str], None],
        narrative_max_tokens: int,
        summary_max_tokens: int,
        analysis_max_tokens: int,
        lore_retriever: Any | None = None,
        content_projection: Any | None = None,
        advance_adventure_world: Callable[[GameInstance], dict[str, Any]] | None = None,
        check_model_enabled: bool = False,
        check_llm_client: Any | None = None,
    ):
        self.registry = registry
        self.llm_client = llm_client
        self.check_model_enabled = check_model_enabled
        self.check_llm_client = check_llm_client
        self.matcher = matcher
        self.lore_retriever = lore_retriever or LoreRetriever(matcher)
        self.lorebook_store = lorebook_store
        self.content_projection = content_projection
        self.memory_store = memory_store
        self._prompt = prompt
        self._dice = dice
        self._combat = combat
        self._puzzles = puzzles
        self._state_applier = state_applier
        self._progression = progression
        self._load_world_template = load_world_template
        self._ensure_matcher_for_world = ensure_matcher_for_world
        self.narrative_max_tokens = narrative_max_tokens
        self.summary_max_tokens = summary_max_tokens
        self.analysis_max_tokens = analysis_max_tokens
        # The application composition root injects this optional callback.  The
        # generic round processor owns world-time settlement but must not import
        # the Adventure application service or a concrete ruleset.
        self._advance_adventure_world = advance_adventure_world
        self._defer_hosted_players = False
        # 后台摘要任务引用持有，避免被 GC 中断
        self._pending_summary_tasks: set = set()
        self._image_generation = None
        # 每局同一时间只允许一个生图任务：连续快速推进时跳过新请求
        self._scene_image_tasks: dict[tuple[tuple[str, ...], str], asyncio.Task] = {}
        # GM 漏掉 SCENE_PANEL 时的后台补分镜任务。只保存公开分镜草稿，不触发生图。
        self._storyboard_draft_tasks: dict[
            tuple[tuple[str, ...], str, int, int, str], asyncio.Task
        ] = {}

    def set_image_generation_service(self, service) -> None:
        self._image_generation = service

    def set_defer_hosted_players(self, enabled: bool) -> None:
        self._defer_hosted_players = enabled

    def set_adventure_world_advance(
        self, callback: Callable[[GameInstance], dict[str, Any]] | None,
    ) -> None:
        """Attach the v2 gate reevaluation at the existing time-authority seam."""

        self._advance_adventure_world = callback

    def _ruleset_runtime(self, instance: GameInstance) -> Any | None:
        binding = dict(ruleset_runtime.binding(instance) or {})
        runtime_id = str(binding.get("id") or "")
        ruleset_registry = getattr(self._prompt, "ruleset_registry", None)
        if ruleset_registry is None or not runtime_id:
            return None
        return ruleset_registry.get(
            runtime_id, minimum_version=int(binding.get("version", 1) or 1),
        )

    @staticmethod
    def _allowed_narrative_player_uids(instance: GameInstance) -> set[str] | None:
        """Return the server-owned player authority boundary for this round."""

        if len(instance.players) <= 1:
            return None
        allowed = {
            str(action.get("user_id"))
            for action in instance.action_queue
            if action.get("user_id") in instance.players
        }
        if str(module_hooks.combat_view(instance)["combat_state"] or "none") != "none":
            allowed |= set(instance.alive_players)
        return allowed

    def _deferred_check_indexes(self, instance: GameInstance) -> set[int]:
        runtime = self._ruleset_runtime(instance)
        if not isinstance(runtime, NarrativeCheckPolicyRuntime):
            return set()
        try:
            action_ids = set(runtime.deferred_narrative_check_action_ids(instance))
        except Exception:
            logger.exception("Ruleset narrative check policy failed; keeping normal checks")
            return set()
        return {
            index for index in range(len(instance.action_queue))
            if f"action:{index}" in action_ids
        }

    def _narrative_check_resolver(self, instance: GameInstance, rule: Any) -> Any | None:
        """Free-text checks only need a runtime when the core cannot roll them.

        A rule with d20 or d100 keeps the core path untouched; only a no-dice
        rule whose runtime declares ``narrative_checks`` hands its dice over.
        """

        if rule is None or str(getattr(rule, "dice_system", "") or "").lower() != "none":
            return None
        return resolver_for(self._ruleset_runtime(instance))

    def prepare_round_checks(self, instance: GameInstance) -> list[dict]:
        """离线兼容路径：模型工具不可用时按旧规则意图结算检定。"""
        progression.require_writable(instance)
        checks.require_writable(instance)
        if checks.round_checks_prepared(instance):
            return list(checks.last_checks(instance))
        if instance.state != GameState.ACTIVE_JUDGMENT:
            return []
        actions_text = collect_actions_text(instance)
        rule_ctx = self._prompt.load_rule_context(instance, self._load_world_template)
        instance.reset_round_checks()
        deferred_indexes = self._deferred_check_indexes(instance)
        build_dice_constraint_block(
            instance,
            actions_text,
            rule_ctx.rule,
            rule_ctx.dice_system,
            self._dice,
            skip_action_indexes=deferred_indexes,
        )
        for check in checks.last_checks(instance):
            if not check.get("check_id"):
                check["check_id"] = uuid.uuid4().hex
            if check.get("luck_spend_available"):
                check["luck_decision"] = "pending"
        from src.engine import check_decisions

        check_decisions.prepare(instance, rule_ctx.rule)
        instance.complete_round_check_preparation()
        return list(checks.last_checks(instance))

    def _check_context(self) -> round_checks.CheckPreparationContext:
        """The check-preparation pipeline's dependencies, read fresh on every call.

        The callables are the processor's own bound methods, so ruleset
        resolution, deferred-check admission and the offline compatibility
        fallback keep behaving exactly as before.
        """

        return round_checks.CheckPreparationContext(
            registry=self.registry,
            llm_client=self.llm_client,
            check_llm_client=self.check_llm_client,
            check_model_enabled=self.check_model_enabled,
            _dice=self._dice,
            _prompt=self._prompt,
            _load_world_template=self._load_world_template,
            _narrative_check_resolver=self._narrative_check_resolver,
            _advance_adventure_world=self._advance_adventure_world,
            _deferred_check_indexes=self._deferred_check_indexes,
            prepare_round_checks=self.prepare_round_checks,
        )

    async def prepare_round_checks_ai(self, instance: GameInstance) -> list[dict]:
        """Stage runtime work away from live authority; retain the core path."""
        # Module admission guards stay ahead of context construction: the
        # transaction-preflight contract rejects an unsupported module schema
        # before any other processor state is touched.
        progression.require_writable(instance)
        session_stats.require_writable(instance)
        checks.require_writable(instance)
        return await round_checks.prepare_round_checks_ai(self._check_context(), instance)

    async def _prepare_round_checks_ai(
        self, instance: GameInstance, *, isolated_runtime: bool = False,
    ) -> list[dict]:
        return await round_checks._prepare_round_checks_ai(
            self._check_context(), instance, isolated_runtime=isolated_runtime,
        )

    async def process_round(self, instance: GameInstance, *, on_delta=None, on_reset=None) -> tuple[str, dict | None]:
        """执行一轮判定与叙事。

        Raises:
            RoundNotProcessed: 本轮没有执行（并发占用 / 状态已变化 /
                等待幸运或经济）；调用方必须按"未处理"上报，不得当作成功。
            RoundProcessingFailure: 执行中失败，对局已（尝试）退回行动阶段。
        """
        instance = self.registry.get(instance.game_key)
        if not instance or instance.state != GameState.ACTIVE_JUDGMENT:
            raise RoundNotProcessed("not_judging")
        progression.require_writable(instance)
        session_stats.require_writable(instance)
        checks.require_writable(instance)
        round_safety.require_writable(instance)
        adventure_runtime_state.require_writable(instance)
        ruleset_runtime.require_writable(instance)
        module_hooks.preflight(instance, module_hooks.NARRATIVE_VIEW)
        if has_blocking_economy_decision(instance):
            logger.info("等待经济提案结算，暂不生成叙事: %s", instance.game_key)
            raise RoundNotProcessed("economy_pending")
        try:
            await self.prepare_round_checks_ai(instance)
        except CheckPreparationError as exc:
            # Direct callers (luck timeout/CLI) may enter process_round without
            # the turns service's preparation boundary.  Never leave those
            # callers in ACTIVE_JUDGMENT after a dedicated provider failure.
            rolled_back = await self._recover_failed_round(instance, on_reset=on_reset)
            if not rolled_back:
                raise type(exc)(
                    str(exc), rolled_back=False, rollback_failed=True,
                ) from exc
            raise type(exc)(str(exc), rolled_back=True) from exc
        from src.engine import check_decisions

        if check_decisions.pending_any(instance):
            logger.info("等待幸运选择，暂不生成叙事: %s", instance.game_key)
            self._schedule_luck_timeouts(instance)
            raise RoundNotProcessed("luck_pending")
        if instance._process_lock.locked():
            # 不能返回空叙事当作成功：那会让调用方以为本轮已经处理完，
            # 而对局实际仍停在判定阶段（生产事故 2026-09-10 22:10:55）。
            logger.warning("process_round 已在处理中，本轮不重复处理: %s", instance.game_key)
            raise RoundNotProcessed("busy")
        try:
            async with instance._process_lock:
                async with instance.track_round_processing():
                    return await self.process_round_impl(instance, on_delta=on_delta, on_reset=on_reset)
        except asyncio.CancelledError:
            # 被取消（GM 抢占 / 关服 / 断连）同样不能把对局留在判定阶段。
            logger.info("回合处理被取消，回滚到行动阶段: game=%s", instance.game_key)
            await self._recover_failed_round(instance, on_reset=on_reset)
            if instance.consume_preempt_request():
                # 显式抢占：转成结构化"未处理"，让被中止的请求照常返回客户端。
                raise RoundNotProcessed("preempted") from None
            raise
        except RoundNotProcessed:
            raise
        except Exception as exc:
            # 判定/叙事失败不允许把对局留在 ACTIVE_JUDGMENT：那会让玩家永远
            # 看到"正在生成剧情"（生产事故 2026-09-10）。回滚到行动阶段后
            # 再向调用方报错，恢复对本函数的所有入口一视同仁。
            logger.exception("回合处理失败，回滚到行动阶段: game=%s", instance.game_key)
            rolled_back = await self._recover_failed_round(instance, on_reset=on_reset)
            raise RoundProcessingFailure(
                str(exc) or exc.__class__.__name__, rolled_back=rolled_back,
            ) from exc

    async def _recover_failed_round(self, instance: GameInstance, *, on_reset=None) -> bool:
        """共享恢复边界：回滚失败回合并落盘。

        幸运超时、CLI 与 Web 回合服务都从 ``process_round`` 进来，恢复必须放在
        这里而不是各调用点，否则绕过服务层的入口（如 ``_luck_timeout``）失败后
        仍会把对局永久留在判定阶段。

        返回是否真的回滚成功（回合已过提交点时 ``abort_round_processing`` 是
        空操作）。清理自身的异常只记日志，不吞掉原始异常。
        """
        try:
            rolled_back = bool(await instance.abort_round_processing())
            if rolled_back and on_reset is not None:
                await on_reset()
            await self.registry.save(instance)
            return rolled_back
        except Exception:
            logger.exception("回滚失败回合状态异常: game=%s", instance.game_key)
            return False

    def schedule_decision_timeouts(self, instance: GameInstance) -> None:
        """Arm the timeout of every open post-check choice (Luck and Push).

        Public entry for the service layer, which pauses a round for these
        choices without calling ``process_round``.
        """
        self._schedule_luck_timeouts(instance)

    def _schedule_luck_timeouts(self, instance: GameInstance) -> None:
        """为每条待决的检定选择挂独立超时；到点只按失败关闭该条，全清则重新推进回合。

        幸运待选的检定挂幸运超时（同时关闭它的孤注一掷）；只剩孤注一掷待决的
        检定挂 Push 超时。两者共用 ``_luck_timers`` 与取消路径：任何一次手动
        决定都会取消该条计时，仍待决时由服务层重新挂上，所以 Push 每一步
        （申请 → 告知风险 → 确认）都有完整的等待时长。已在计时的不重复挂。
        默认实时单人局为 60 秒，多人局自动放宽到 180 秒；显式配置仍优先，
        luck_timeout_seconds=0 时禁用（异步局可设 0 让检定选择无限等待）。
        """
        effective_timeout = getattr(instance, "effective_luck_timeout_seconds", None)
        timeout = int(
            effective_timeout() if callable(effective_timeout)
            else module_hooks.check_presentation(instance)["luck_timeout_seconds"] or 0
        )
        if timeout <= 0:
            return
        from src.engine import check_decisions

        luck_ids = [str(check.get("check_id") or "") for check in instance.pending_luck_checks()]
        push_ids = [
            str(check.get("check_id") or "") for check in check_decisions.pending(instance)
        ]
        for check_id, push in [(cid, False) for cid in luck_ids] + [
            (cid, True) for cid in push_ids if cid not in luck_ids
        ]:
            if not check_id:
                continue
            existing = instance._luck_timers.get(check_id)
            if existing and not existing.done():
                continue
            task = asyncio.create_task(
                self._luck_timeout(instance.game_key, check_id, timeout, push=push)
            )
            instance._luck_timers[check_id] = task

    async def _luck_timeout(self, game_key, check_id: str, timeout: int, *, push: bool = False) -> None:
        """单条检定选择的超时回调：到点按失败继续，若是最后一条则重新生成叙事。"""
        unsupported_progression = False
        try:
            await asyncio.sleep(timeout)
            instance = self.registry.get(game_key)
            if not instance:
                return
            result = await (
                instance.system_decline_push(check_id) if push
                else instance.system_decline_luck(check_id)
            )
            if not result.get("ok"):
                return  # 玩家已手动决定，或检定已过期
            await self.registry.save(instance)
            if result.get("declined_all") and instance.state == GameState.ACTIVE_JUDGMENT:
                logger.info("检定选择超时全部决定，继续生成叙事: %s", game_key)
                try:
                    await self.process_round(instance)
                except RoundNotProcessed as exc:
                    # 并发推进或状态已变化属于正常竞争，不是超时处理失败。
                    logger.info(
                        "幸运超时后本轮未处理: game=%s reason=%s", game_key, exc.reason,
                    )
                except RoundProcessingFailure as exc:
                    # 回滚已由 process_round 这个共享边界完成，这里只避免重复
                    # 记录整段堆栈；对局不会留在"生成中"。
                    logger.warning(
                        "幸运超时后的推进失败，已回滚到行动阶段: game=%s rolled_back=%s",
                        game_key, exc.rolled_back,
                    )
        except ModuleStateError:
            unsupported_progression = True
            logger.exception("幸运超时拒绝不支持的模块状态: %s check=%s", game_key, check_id)
        except Exception:
            logger.exception("幸运超时处理失败: %s check=%s", game_key, check_id)
        finally:
            inst = self.registry.get(game_key)
            if inst is not None and not unsupported_progression:
                # A decision may have re-armed this check while the callback ran;
                # never drop a newer live timer.
                current = inst._luck_timers.get(check_id)
                if current is None or current is asyncio.current_task() or current.done():
                    inst._luck_timers.pop(check_id, None)

    async def _summarize_background(self, instance: GameInstance, gm_prompt: Any, round_number: int) -> None:
        """后台执行摘要压缩，不阻塞回合返回。

        叙事已在 finish_judgment 推送，用户无需等待摘要。asyncio 单线程下
        narrative_notes.summary 的赋值在 await 间原子，下轮读到旧/新摘要均合法；
        关机 save_all_active 会落盘。round_number 在调度时快照，避免日志读到被推进的值。
        """
        try:
            await summarize(instance, self.llm_client, gm_prompt, self.summary_max_tokens)
        except Exception:
            logger.exception("摘要压缩失败，已跳过 (round=%d)", round_number)

    def _maybe_schedule_summary(self, instance: GameInstance, gm_prompt: Any) -> asyncio.Task | None:
        """若到摘要周期（每 10 回合），调度后台摘要任务并返回；否则返回 None。"""
        if not needs_summary(instance):
            return None
        task = asyncio.create_task(self._summarize_background(instance, gm_prompt, progression_state.round_value(instance)))
        self._pending_summary_tasks.add(task)
        task.add_done_callback(self._pending_summary_tasks.discard)
        return task

    def _media_context(self) -> round_media.SceneMediaContext:
        """The media pipeline's dependencies, read fresh on every call.

        ``_image_generation`` can be replaced or cleared by the composition root
        at any time, so the context is built per call instead of cached.  The two
        task dicts stay the processor's own: sharing them by reference is what
        keeps the one-task-per-game throttle working across calls.
        """

        return round_media.SceneMediaContext(
            service=self._image_generation,
            scene_tasks=self._scene_image_tasks,
            draft_tasks=self._storyboard_draft_tasks,
            llm_client=self.llm_client,
            registry=self.registry,
        )

    def _maybe_schedule_scene_image(self, instance: GameInstance, data: dict) -> asyncio.Task | None:
        """Schedule scene art only when the GM emits an explicit SCENE_IMAGE."""

        return round_media.maybe_schedule_scene_image(self._media_context(), instance, data)

    def _maybe_schedule_storyboard_draft(
        self, instance: GameInstance, data: dict,
    ) -> asyncio.Task | None:
        """Fill a missing GM storyboard in the background without generating art."""

        return round_media.maybe_schedule_storyboard_draft(self._media_context(), instance, data)

    def schedule_opening_scene_image(self, instance: GameInstance) -> asyncio.Task | None:
        """Schedule an opening image only when round zero explicitly requested one."""

        return round_media.schedule_opening_scene_image(self._media_context(), instance)

    def schedule_deferred_scene_image(
        self, instance: GameInstance, payload: dict[str, Any],
    ) -> asyncio.Task | None:
        """Public boundary for a deferred prompt whose settlement is persisted."""

        return round_media.schedule_deferred_scene_image(self._media_context(), instance, payload)

    def schedule_scene_image(
        self, instance: GameInstance, prompt: str, completed_round: int,
        *, force: bool = False, panels: list[dict[str, Any]] | None = None,
        compressed_count: int = 0,
    ) -> asyncio.Task | None:
        """为指定回合调度一次场景图生成（叙事已推送，生图在后台进行）。"""

        return round_media.schedule_scene_image(
            self._media_context(), instance, prompt, completed_round,
            force=force, panels=panels, compressed_count=compressed_count,
        )

    async def process_round_impl(self, instance: GameInstance, *, on_delta=None, on_reset=None) -> tuple[str, dict | None]:
        """实际的判定处理逻辑。"""
        progression.require_writable(instance)
        session_stats.require_writable(instance)
        checks.require_writable(instance)
        round_safety.require_writable(instance)
        adventure_runtime_state.require_writable(instance)
        ruleset_runtime.require_writable(instance)
        module_hooks.preflight(instance, module_hooks.NARRATIVE_VIEW)
        expected_run_id = instance.run_id
        if not checks.round_checks_prepared(instance):
            await self.prepare_round_checks_ai(instance)
        # The planning phase may create an explicitly-authorized table offer.
        # Capture the narrative-start snapshot only after planning completes;
        # confirmations of this snapshot may resolve while the LLM streams,
        # but proposals created afterwards must invalidate the response.
        expected_economy_fingerprint = economy_fingerprint(instance)
        protected_purchase_items = purchase_grant_protection_snapshot(instance)
        protected_ambiguous_payers = purchase_grant_ambiguity_snapshot(instance)
        # 只保留最近一轮的短期展示状态，避免旧提示或战斗结果常驻。
        instance.begin_round_processing()
        pending_combat_event_ids = combat_narrative.pending_event_ids(instance)
        pending_combat_events_text = combat_narrative.format_pending_events(instance)

        ensure_round_managers(instance)
        actions_text = collect_actions_text(instance)

        if instance.world_id:
            self._ensure_matcher_for_world(instance.world_id, instance.language)
        # 统一走通用 LoreRetriever（锚点 + 关键词 + 可选语义）：正常回合是 GM 视角，
        # 沿用既有计时器语义（匹配到的 sticky/cooldown/delay 会写回实例）。
        action_actor_uids = sorted({
            str(action.get("user_id") or "")
            for action in instance.action_queue
            if str(action.get("user_id") or "") in instance.players
        })
        # 整体 lore 预算由既有 context 预算派生（唯一的 context-window authority），
        # 在检索阶段就收口，避免把远超预算的条目一路带到 composer 再裁。
        provider_name = self.llm_client.default if self.llm_client else ""
        lorebook_matches = await self.lore_retriever.retrieve(
            instance, actions_text, action_actor_uids=action_actor_uids,
            overall_budget=lore_char_budget(provider_name),
        )

        rule_ctx = self._prompt.load_rule_context(instance, self._load_world_template)
        rule_appendix = rule_ctx.rule_appendix
        combat_model = rule_ctx.combat_model
        dice_system = rule_ctx.dice_system
        world_data = rule_ctx.world_data
        rule = rule_ctx.rule
        currency_labels = currency_labels_for_rule(rule)

        initialize_puzzles_from_lorebook(
            instance,
            self.lorebook_store,
            world_data=world_data,
            content_projection=self.content_projection,
        )

        # 昏迷角色的死亡豁免先于战斗/叙事结算，文本并入行动块供 GM 遵循。
        death_text = resolve_round_death_saves(instance, rule)
        if death_text:
            actions_text = death_text + "\n" + actions_text

        dice_block = format_check_results_constraint(instance, list(checks.last_checks(instance)))
        if dice_block:
            actions_text += dice_block

        explicit_attack = any(
            isinstance(action.get("check_request"), dict)
            and str(action["check_request"].get("kind") or "") == "attack"
            for action in instance.action_queue
            if action.get("user_id") in instance.players
        )
        authoritative_combat = combat_model == "authoritative_event_batch"
        if not authoritative_combat and (module_hooks.combat_view(instance)["combat_state"] != "none" or explicit_attack):
            combat_text = self._combat.resolve_combat(instance, actions_text, combat_model, rule)
            if combat_text:
                actions_text = combat_text + "\n" + actions_text

        puzzle_text = self._puzzles.process_puzzles(instance, actions_text)
        if puzzle_text:
            actions_text = puzzle_text + "\n\n" + actions_text
        gm_directives_text, consumed_directive_ids = collect_gm_directives_text(instance)
        # 指令不再拼进玩家块：走独立可信通道，避免被“玩家发言不可信”标注误伤，
        # 也杜绝玩家在行动文本里仿冒【GM私密指令】标题。
        overreach_text = (
            format_overreach_block(instance) if overreach_guard_enabled() else ""
        )
        # 权威世界真相与行动合法性：都是服务端组装的可信块，玩家文本无法注入。
        # GM 视角包含 gm 私有事实（并标注玩家不可见）。
        world_state_text = format_world_state_block(instance, viewer_is_gm=True)
        world_legality_text = format_world_legality_block(instance)
        world_events_text = format_world_events_block(instance)

        gm_prompt = self._prompt.compose_gm_prompt(instance, rule_appendix, world_data=world_data)
        context = await self._prompt.build_user_context(
            instance, gm_prompt, lorebook_matches, actions_text,
            provider_name=provider_name, world_data=world_data,
            directives_text=gm_directives_text, overreach_text=overreach_text,
            world_state_text=world_state_text,
            world_legality_text=world_legality_text,
            world_events_text=world_events_text,
            authoritative_events_text=pending_combat_events_text)

        context = await append_multistep_analysis(
            self.llm_client, instance, gm_prompt, context, actions_text, self.analysis_max_tokens)
        runtime = self._ruleset_runtime(instance)
        allowed_uids = self._allowed_narrative_player_uids(instance)
        semantic_review_enabled = should_semantic_review(instance, actions_text)
        public_narration_context = _build_recent_public_narration_context(instance)
        quality_gate = build_quality_gate(
            instance,
            self.llm_client,
            runtime=runtime,
            actions_text=actions_text,
            semantic_review=semantic_review_enabled,
            public_narration_context=public_narration_context,
            allowed_player_uids=allowed_uids,
        )
        buffered_stream = (
            BufferedNarrativeStream(on_delta)
            if semantic_review_enabled and on_delta is not None else None
        )

        async def quality_reset() -> None:
            if buffered_stream is not None:
                buffered_stream.clear()
            if on_reset is not None:
                await on_reset()

        response, data = await call_llm_with_tag_retry(
            self.llm_client, instance, gm_prompt, context, combat_model,
            list(checks.last_checks(instance)), self.narrative_max_tokens, actions_text,
            on_delta=buffered_stream or on_delta,
            on_reset=quality_reset if buffered_stream is not None else on_reset,
            public_narration_context=public_narration_context,
            quality_gate=quality_gate,
            quality_repair_instruction=lambda issues: build_quality_repair_instruction(
                instance.language, issues,
            ),
        )
        if buffered_stream is not None:
            await buffered_stream.flush()
        current_instance = self.registry.get(instance.game_key)
        if (
            current_instance is not instance
            or instance.run_id != expected_run_id
            # 生成期间玩家确认/拒绝既有提案不判过期；回滚等仍会丢弃。
            or not economy_changes_are_resolutions_only(
                expected_economy_fingerprint, economy_fingerprint(instance),
            )
        ):
            logger.warning(
                "丢弃过期叙事响应: game=%s expected_run=%s current_run=%s",
                instance.game_key,
                expected_run_id,
                getattr(current_instance, "run_id", "missing"),
            )
            # 叙事已作废（回滚/重置/运行替换），本轮不会提交：必须让调用方
            # 知道"没有处理"，而不是收到一个空正文的成功回合。
            raise RoundNotProcessed("stale")
        director_proposal_data: dict[str, Any] = {}
        if authoritative_combat and isinstance(runtime, NarrativeDirectorRuntime):
            try:
                director_result = runtime.director_proposal(instance)
            except Exception:
                logger.exception("D&D Director proposal failed; keeping narrative turn")
                director_result = {}
            candidate = director_result.get("proposal") if isinstance(director_result, dict) else {}
            if isinstance(candidate, dict):
                director_proposal_data = candidate
        if isinstance(runtime, NarrativeDirectorPlanningRuntime):
            try:
                semantic_proposal = await runtime.plan_director_turn(instance, self.llm_client)
            except Exception:
                logger.exception("D&D semantic Director planning failed; keeping deterministic proposal")
                semantic_proposal = None
            if isinstance(semantic_proposal, dict):
                director_proposal_data = semantic_proposal
        if authoritative_combat and explicit_attack:
            data["combat_command"] = "start"
        # D&D Director may recognize an explicit hostile action even when the
        # model omitted the legacy combat marker. This only creates the
        # advisory tool signal; combat.start still validates the encounter.
        if authoritative_combat and not data.get("combat_command"):
            if (
                director_proposal_data.get("kind") == "combat"
                and director_proposal_data.get("mode") != "manual"
                and float(director_proposal_data.get("confidence", 0) or 0) >= 0.85
            ):
                data["combat_command"] = "start"
        discard_unresolved_player_damage(instance, data.get("state_update", {}))
        initial_budget = int(getattr(response, "token_budget_initial", 0) or 0)
        used_budget = int(getattr(response, "token_budget_used", 0) or 0)
        instance.set_token_budget_bump(initial_budget, used_budget)
        apply_parsed_data_to_response(instance, response, data)
        system_changes: list[str] = list(
            getattr(response, "system_notices", []) or [],
        )
        if should_warn_unbacked_payment(
            response.narration, data, instance.language,
            currency_labels=currency_labels,
        ):
            system_changes.append(unbacked_payment_notice(instance.language))
        inferred_rewards = infer_narrative_reward_candidates(instance, response.narration, rule)
        if inferred_rewards:
            merge_narrative_reward_candidates(data, inferred_rewards)
            response.state_update = data.get("state_update")
        # Purchase authority is explicit GM order + payer confirmation. Never
        # infer a price or create a chargeable proposal from narration text.
        # Unpriced purchase intents from planning stay memory-only and feed
        # the same-round grant filter below: a purchase whose price nobody
        # has stated cannot be delivered through narrative LOOT either
        # (ADR 0002). A price the GM narrates this round becomes quotable by
        # the planner next round via recent_narration, or the GM can issue
        # the offer immediately through the manual purchase composer.
        unpriced_purchase_intents = list(instance.round_unpriced_purchase_intents)
        free_grant_markers = [
            marker for marker in (data.get("state_update", {}).get("free_grants", [])
                                  if isinstance(data.get("state_update"), dict) else [])
            if isinstance(marker, dict)
        ]

        def _unpriced_without_explicit_free_grant(intent: dict[str, Any]) -> bool:
            uid = str(intent.get("payer_uid") or "").strip()
            target = str(intent.get("target") or "").strip()
            if not uid or not target:
                return False
            if intent.get("ambiguous_target") is True:
                return not any(
                    str(marker.get("player") or "").strip() == uid
                    for marker in free_grant_markers
                )
            return not any(
                str(marker.get("player") or "").strip() == uid
                and item_names_match(
                    {str(marker.get("item") or "").strip()}, target,
                )
                for marker in free_grant_markers
            )

        dropped_purchase_items = filter_unconfirmed_purchase_grants(
            instance, data,
            unpriced_purchase_intents=unpriced_purchase_intents,
            protected_purchase_items=protected_purchase_items,
            protected_ambiguous_payers=protected_ambiguous_payers,
        )
        if any(
            _unpriced_without_explicit_free_grant(intent)
            for intent in unpriced_purchase_intents
            if isinstance(intent, dict)
        ):
            # The planner recognized a purchase intent but could not form a
            # canonical, chargeable offer. Make the fail-closed decision
            # visible so the player knows how to obtain a real order.
            system_changes.append(unpriced_purchase_notice(instance.language))
        elif dropped_purchase_items:
            system_changes.append(unbacked_purchase_notice(instance.language))
        economy_pending = has_economy_proposal(data)
        deferred_effects = defer_narrative_effects(
            data, response,
            defer_state_update=True,
        )
        if economy_pending:
            system_changes.append(pending_decision_notice(instance.language))

        public_state_before = snapshot_public_player_state(instance)
        round_pre_snapshot = _snapshot_players(instance)
        round_pre_combat_snapshot = module_hooks.read_value(instance, module_hooks.COMBAT_EXTENSION_SNAPSHOT)

        queued_proposals: list[dict[str, Any]] = []
        if response.state_update:
            # 多人局权威白名单：状态标签只允许作用于本轮行动者/参战者。
            queued_proposals = self._state_applier.apply_state_update(
                instance, response.state_update, allowed_player_uids=allowed_uids,
            )
        if deferred_effects:
            deferred_effects["allowed_player_uids"] = (
                sorted(allowed_uids) if allowed_uids is not None else None
            )
            group = queue_effect_group(instance, queued_proposals, deferred_effects)
            if group is None:
                logger.warning(
                    "经济提案未能建立叙事效果决策屏障，已 fail closed: game=%s round=%d proposals=%d",
                    instance.game_key,
                    progression_state.round_value(instance),
                    len(queued_proposals),
                )
        instance.set_state_update_recap(response.state_update)

        apply_confirmed_items(instance, data)
        apply_puzzle_updates(instance, data)
        combat_requested = apply_ruleset_combat_signal(
            instance, data, runtime, director_proposal_data,
        )
        automation_batches: list[dict[str, Any]] = []
        if director_proposal_data and runtime is not None:
            try:
                automation_batches = apply_director_automation(
                    runtime, instance, director_proposal_data, random.SystemRandom(),
                    defer_hosted_players=self._defer_hosted_players,
                )
            except (ValueError, KeyError, TypeError):
                # The advisory encounter request remains visible for GM review.
                logger.exception("D&D Director automation was rejected; waiting for GM")
        apply_revive_commands(instance, data, runtime)
        # 通用尾部链（成长奖励 → 快捷行动 → 记忆 → 剧情 → 私信）已泛化到
        # narrative_application（GAL 轨道 P5），供链外叙事消费方复用。
        system_changes.extend(await apply_narrative_effects(
            instance, data, response,
            rule=rule, progression=self._progression, runtime=runtime,
            memory_store=self.memory_store,
        ))
        state_msgs = append_state_change_messages(instance, response, public_state_before, data)
        state_msgs.extend(system_changes)
        request = (
            ruleset_runtime.state(instance).get("encounter_request")
            if isinstance(ruleset_runtime.state(instance), dict)
            else None
        )
        if combat_requested and isinstance(request, dict) and request.get("status") == "pending":
            request_note = localized_text(instance.language, {
                "en": "Combat is ready. Waiting for the GM to confirm initiative.",
                "zh-CN": "战斗准备已就绪，等待 GM 确认进入先攻。",
                "ja": "戦闘準備が整いました。GM のイニシアチブ開始確認を待っています。",
                "de": "Der Kampf ist bereit. Es wird auf die Bestätigung der Initiative durch den GM gewartet.",
            })
            state_msgs.append(request_note)
        if automation_batches:
            automation_note = summarize_automation_batches(
                automation_batches,
                chinese=not str(getattr(instance, "language", "") or "").lower().startswith("en"),
            )
            state_msgs.append(automation_note)

        instance.consume_gm_directives(set(consumed_directive_ids))
        await instance.finish_judgment(
            response.narration,
            pre_state_snapshot=round_pre_snapshot,
            state_changes=state_msgs,
            pre_combat_extension_snapshot=round_pre_combat_snapshot,
            story=getattr(response, "story", None),
            adventure_completion_candidate=(
                data.get("plot_update", {}).get("adventure_completion")
                if isinstance(data.get("plot_update"), dict) else None
            ),
        )
        # Keep the GM's public image directives with the completed round so
        # manual generation and later restores use the same source.
        if bool(getattr(self._image_generation, "auto_storyboard", False)):
            completed = next((item for item in reversed(instance.log)
                              if log_round(item, -1) == int(progression_state.round_value(instance)) - 1), None)
            if isinstance(completed, dict):
                completed["scene_panels"] = normalize_scene_panels(
                    data.get("scene_panels"), merge_same_location=False,
                )[0]
                completed["scene_image_prompt"] = str(data.get("scene_image_prompt") or "")[:300]
                completed["scene_panel_meta"] = storyboard_panel_metadata(
                    completed["scene_panels"],
                    narration=str(completed.get("gm_response") or ""),
                    actions=completed.get("actions") or [],
                    current_scene=str(module_hooks.narrative_view(instance)["scene"] or ""),
                    source_revision=storyboard_source_revision(completed),
                )
        combat_narrative.consume_pending_events(instance, pending_combat_event_ids)
        instance.set_latest_log_tags_summary(summarize_tags(data))
        instance.record_llm_usage(response.total_tokens, calls=0)

        self._state_applier.tick_madness(instance)

        # 摘要压缩较慢（LLM 调用，每 10 回合），延后到后台：叙事已推送，用户无需等待。
        self._maybe_schedule_summary(instance, gm_prompt)
        # 场景图生成慢（生图 API 10-60s），同样延后到后台，完成后再推给前端。
        self._maybe_schedule_scene_image(instance, data)
        # 如果 GM 漏了 SCENE_PANEL，自动分镜仍可在后台补一份可编辑草稿。
        # 这不改变 SCENE_IMAGE 触发规则，也不新增图像调用。
        self._maybe_schedule_storyboard_draft(instance, data)

        try:
            await self.registry.save(instance)
            instance.record_save_success()
        except Exception:
            count = instance.record_save_failure()
            logger.exception("存档失败(连续%d次) (round=%d)", count, progression_state.round_value(instance))

        return response.narration, response.info_asymmetry

    async def commit_deferred_economy_effects(
        self,
        instance: GameInstance,
        effects: dict[str, Any],
    ) -> None:
        """Apply one persisted effect group after its economic commit."""

        payload = deepcopy(dict(effects or {}))
        state_update = dict(payload.get("state_update") or {})
        allowed_raw = payload.get("allowed_player_uids")
        allowed_uids = (
            {str(uid) for uid in allowed_raw if str(uid)}
            if isinstance(allowed_raw, list)
            else None
        )
        if state_update:
            self._state_applier.apply_state_update(
                instance,
                state_update,
                allowed_player_uids=allowed_uids,
                authorized_receipt_id=str(
                    getattr(instance, "_economy_authorized_receipt_id", "") or ""
                ),
                settlement_effects=True,
            )
            instance.set_state_update_recap(state_update)
        response = SimpleNamespace(
            narration="",
            memory_delta=dict(payload.get("memory_delta") or {}),
            info_asymmetry=dict(payload.get("info_asymmetry") or {}),
            plot_update=dict(payload.get("plot_update") or {}),
        )
        apply_confirmed_items(instance, payload)
        if any(
            payload.get(key)
            for key in ("xp_rewards", "growth_skills", "milestone_grants")
        ):
            rule_ctx = self._prompt.load_rule_context(
                instance, self._load_world_template,
            )
            apply_growth_rewards(
                instance,
                payload,
                response,
                rule_ctx.rule,
                self._progression,
                self._ruleset_runtime(instance),
                include_base=False,
            )
        if payload.get("quick_actions"):
            update_quick_actions(instance, payload)
        apply_plot_update(instance, response)
        store_private_messages(instance, response)
        # Deferred economy effects participate in the settlement transaction.
        # Unlike normal round memory (which is best-effort), a failure here must
        # abort the staged aggregate so the proposal remains retryable.
        if response.memory_delta and self.memory_store:
            await self.memory_store.apply_delta(
                instance.memory_namespace,
                response.memory_delta,
                progression_state.round_value(instance),
            )
