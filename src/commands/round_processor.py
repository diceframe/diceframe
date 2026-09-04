"""完整回合推进流程。"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from src.commands.economy_effects import (
    discard_unearned_reward_proposals,
    defer_narrative_effects,
    has_economy_proposal,
    pending_decision_notice,
    currency_labels_for_rule,
    unbacked_purchase_notice,
    unbacked_payment_notice,
    unearned_reward_notice,
    should_warn_unbacked_payment,
)
from src.commands.round_effects import (
    append_state_change_messages,
    apply_combat_command,
    apply_confirmed_items,
    apply_growth_rewards,
    apply_memory_delta,
    apply_plot_update,
    apply_puzzle_updates,
    apply_revive_commands,
    apply_ruleset_combat_signal,
    store_private_messages,
    update_quick_actions,
)
from src.commands.check_planner import (
    plan_round_checks,
    price_unpriced_purchase_intents,
)
from src.engine.economy import (
    economy_changes_are_resolutions_only,
    economy_fingerprint,
    queue_purchase_offer,
)
from src.commands.death_save_tracker import resolve_round_death_saves
from src.commands.round_llm import (
    append_multistep_analysis,
    apply_parsed_data_to_response,
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
from src.commands.state_recap import snapshot_public_player_state
from src.commands.state_update_applier import discard_unresolved_player_damage
from src.commands.tag_summary import summarize_tags
from src.engine.economy import (
    has_blocking_economy_decision,
    queue_effect_group,
)
from src.engine.economy import filter_unconfirmed_purchase_grants, has_pending_identical_purchase
from src.engine.game_instance import GameInstance, GameState, _snapshot_players
from src.engine.language import localized_text
from src.imagegen import (
    ImageGenerationError,
    ImageGenerationRequest,
    game_image_owner_id,
)
from src.memory.summarizer import needs_summary, summarize
from src.rulesets.contracts import (
    NarrativeCheckPolicyRuntime,
    NarrativeDirectorPlanningRuntime,
    NarrativeDirectorRuntime,
    NarrativeStatePolicyRuntime,
)
from src.rulesets.automation import apply_director_automation, summarize_automation_batches

logger = logging.getLogger("trpg")


def overreach_guard_enabled() -> bool:
    """顺带裁判开关：默认关，TRPG_OVERREACH_GUARD=1 启用。"""
    return os.environ.get("TRPG_OVERREACH_GUARD", "") == "1"


def _last_scene_image_prompt(instance: GameInstance) -> str:
    """最近一张场景图的画面描述；用于无场景切换时的重复生成节流。"""
    for entry in reversed(instance.log):
        record = entry.get("scene_image")
        if isinstance(record, dict) and record.get("status") == "ready":
            return str(record.get("prompt") or "")
    return ""


def format_overreach_block(instance: GameInstance) -> str:
    """把本轮裁判的越权标注组装为可信裁定块（服务端组装，玩家不可注入）。"""
    notes = list(getattr(instance, "last_overreach", []) or [])
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
    })
    return f"{heading}\n" + "\n".join(lines)


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
    ):
        self.registry = registry
        self.llm_client = llm_client
        self.matcher = matcher
        self.lorebook_store = lorebook_store
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
        # 后台摘要任务引用持有，避免被 GC 中断
        self._pending_summary_tasks: set = set()
        self._image_generation = None
        # 每局同一时间只允许一个生图任务：连续快速推进时跳过新请求
        self._scene_image_tasks: dict[tuple[tuple[str, ...], str], asyncio.Task] = {}

    def set_image_generation_service(self, service) -> None:
        self._image_generation = service

    def _ruleset_runtime(self, instance: GameInstance) -> Any | None:
        binding = dict(getattr(instance, "ruleset_runtime", {}) or {})
        runtime_id = str(binding.get("id") or "")
        ruleset_registry = getattr(self._prompt, "ruleset_registry", None)
        if ruleset_registry is None or not runtime_id:
            return None
        return ruleset_registry.get(
            runtime_id, minimum_version=int(binding.get("version", 1) or 1),
        )

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

    def prepare_round_checks(self, instance: GameInstance) -> list[dict]:
        """离线兼容路径：模型工具不可用时按旧规则意图结算检定。"""
        if instance.round_checks_prepared:
            return list(instance.last_checks)
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
        for check in instance.last_checks:
            if not check.get("check_id"):
                check["check_id"] = uuid.uuid4().hex
            if check.get("luck_spend_available"):
                check["luck_decision"] = "pending"
        instance.complete_round_check_preparation()
        return list(instance.last_checks)

    async def prepare_round_checks_ai(self, instance: GameInstance) -> list[dict]:
        """阶段 1：由 GM 模型统一规划检定，再由服务端一次性掷骰结算。"""
        if instance.round_checks_prepared:
            return list(instance.last_checks)
        if instance.state != GameState.ACTIVE_JUDGMENT:
            return []
        actions_text = collect_actions_text(instance)
        rule_ctx = self._prompt.load_rule_context(instance, self._load_world_template)
        instance.reset_round_checks()
        deferred_indexes = self._deferred_check_indexes(instance)
        expected_run_id = instance.run_id
        expected_economy_fingerprint = economy_fingerprint(instance)

        def planning_target_is_current() -> bool:
            current = self.registry.get(instance.game_key)
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
            planned, metadata = await plan_round_checks(instance, rule_ctx.rule, self.llm_client)
            if not planning_target_is_current():
                logger.info(
                    "丢弃过期检定规划: game=%s run=%s",
                    instance.game_key,
                    expected_run_id,
                )
                instance.reset_round_checks()
                return []
            logger.info(
                "检定规划: 完成 (round=%d, 耗时=%dms)",
                instance.round_number,
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
                            offer["payer_uid"], offer["target"], instance.round_number,
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
                        source_ref=(
                            f"ai:{instance.run_id}:{instance.round_number}:"
                            f"{offer['payer_uid']}:{offer['target']}:{quantity}:"
                            f"{offer.get('amount_scope') or 'total'}:{offer['amount']}"
                        ),
                    )
                except Exception:
                    logger.warning(
                        "AI 报价提案创建失败，已跳过: %s", offer, exc_info=True,
                    )
            # 无价购买意图留在实例回合内存中：叙事后复检一次是否有口述价格，
            # 并在结算阶段拦截同轮模型授予（从不持久化、不产生金额）。
            instance.round_unpriced_purchase_intents = list(
                metadata.get("unpriced_purchase_intents") or [],
            )
            if not metadata.get("available"):
                logger.warning("模型工具不可用，进入离线检定兼容路径: %s", instance.game_key)
                return self.prepare_round_checks(instance)
            deferred_actions = {
                id(instance.action_queue[index]) for index in deferred_indexes
            }
            planned = [
                (action, request) for action, request in planned
                if id(action) not in deferred_actions
            ]
            for action, request in planned:
                action["check_request"] = request
            if not metadata.get("skipped"):
                instance.record_llm_usage(int(metadata.get("total_tokens", 0) or 0), calls=1)
            errors = metadata.get("errors") or []
            if errors:
                logger.warning("部分 AI 检定参数被拒绝: %s", "; ".join(str(item) for item in errors))
            instance.last_overreach = list(metadata.get("overreach") or [])
            build_dice_constraint_block(
                instance,
                actions_text,
                rule_ctx.rule,
                rule_ctx.dice_system,
                self._dice,
                planned_only=True,
                skip_action_indexes=deferred_indexes,
            )
        except Exception:
            logger.exception("AI 检定规划失败，进入离线检定兼容路径: %s", instance.game_key)
            if not planning_target_is_current():
                instance.reset_round_checks()
                return []
            return self.prepare_round_checks(instance)
        for check in instance.last_checks:
            if not check.get("check_id"):
                check["check_id"] = uuid.uuid4().hex
            if check.get("luck_spend_available"):
                check["luck_decision"] = "pending"
        instance.complete_round_check_preparation()
        return list(instance.last_checks)

    async def process_round(self, instance: GameInstance, *, on_delta=None, on_reset=None) -> tuple[str, dict | None]:
        instance = self.registry.get(instance.game_key)
        if not instance or instance.state != GameState.ACTIVE_JUDGMENT:
            return "", None
        if has_blocking_economy_decision(instance):
            logger.info("等待经济提案结算，暂不生成叙事: %s", instance.game_key)
            return "", None
        await self.prepare_round_checks_ai(instance)
        if instance.pending_luck_checks():
            logger.info("等待幸运选择，暂不生成叙事: %s", instance.game_key)
            self._schedule_luck_timeouts(instance)
            return "", None
        if instance._process_lock.locked():
            logger.warning("process_round 已在处理中，跳过并发调用: %s", instance.game_key)
            return "", None
        async with instance._process_lock:
            return await self.process_round_impl(instance, on_delta=on_delta, on_reset=on_reset)

    def _schedule_luck_timeouts(self, instance: GameInstance) -> None:
        """为每条 pending 幸运检定挂独立超时；到点只 decline 该条，全清则重新推进回合。

        每玩家每轮只有一条主检定，故 per-check 即 per-player。已在计时的不重复挂。
        luck_timeout_seconds=0 时禁用（异步局可设 0 让幸运选择无限等待）。
        """
        timeout = int(getattr(instance, "luck_timeout_seconds", 60) or 0)
        if timeout <= 0:
            return
        for check in instance.pending_luck_checks():
            check_id = str(check.get("check_id") or "")
            if not check_id:
                continue
            existing = instance._luck_timers.get(check_id)
            if existing and not existing.done():
                continue
            task = asyncio.create_task(self._luck_timeout(instance.game_key, check_id, timeout))
            instance._luck_timers[check_id] = task

    async def _luck_timeout(self, game_key, check_id: str, timeout: int) -> None:
        """单条幸运检定的超时回调：到点按失败继续，若是最后一条则重新生成叙事。"""
        try:
            await asyncio.sleep(timeout)
            instance = self.registry.get(game_key)
            if not instance:
                return
            result = await instance.system_decline_luck(check_id)
            if not result.get("ok"):
                return  # 玩家已手动决定，或检定已过期
            await self.registry.save(instance)
            if result.get("declined_all") and instance.state == GameState.ACTIVE_JUDGMENT:
                logger.info("幸运超时全部决定，继续生成叙事: %s", game_key)
                await self.process_round(instance)
        except Exception:
            logger.exception("幸运超时处理失败: %s check=%s", game_key, check_id)
        finally:
            inst = self.registry.get(game_key)
            if inst is not None:
                inst._luck_timers.pop(check_id, None)

    async def _summarize_background(self, instance: GameInstance, gm_prompt: Any, round_number: int) -> None:
        """后台执行摘要压缩，不阻塞回合返回。

        叙事已在 finish_judgment 推送，用户无需等待摘要。asyncio 单线程下
        instance.summary 的赋值在 await 间原子，下轮读到旧/新摘要均合法；
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
        task = asyncio.create_task(self._summarize_background(instance, gm_prompt, instance.round_number))
        self._pending_summary_tasks.add(task)
        task.add_done_callback(self._pending_summary_tasks.discard)
        return task

    def _maybe_schedule_scene_image(self, instance: GameInstance, data: dict) -> asyncio.Task | None:
        """按 GM 的 SCENE_IMAGE 标签调度后台生图；能力关闭或节流命中时返回 None。"""
        service = self._image_generation
        if service is None or not service.available or not service.auto_scene:
            return None
        prompt = str(data.get("scene_image_prompt") or "").strip()
        if not prompt:
            return None
        completed_round = int(instance.round_number) - 1
        if completed_round < 1:
            return None
        scene_change = str((data.get("state_update") or {}).get("scene_change") or "").strip()
        # 场景切换时即使描述与上一张相同也重新生成（场景确实变了）；
        # 否则与上一张相同的描述视为模型复读，跳过。
        return self.schedule_scene_image(instance, prompt, completed_round, force=bool(scene_change))

    def schedule_deferred_scene_image(
        self,
        instance: GameInstance,
        payload: dict[str, Any],
    ) -> asyncio.Task | None:
        """Public boundary for a deferred prompt whose settlement is persisted."""

        return self._maybe_schedule_scene_image(instance, payload)

    def schedule_scene_image(
        self,
        instance: GameInstance,
        prompt: str,
        completed_round: int,
        *,
        force: bool = False,
    ) -> asyncio.Task | None:
        """为指定回合调度一次场景图生成（叙事已推送，生图在后台进行）。"""
        service = self._image_generation
        prompt = str(prompt or "").strip()
        if service is None or not service.available or not service.auto_scene or not prompt:
            return None
        if not force and prompt == _last_scene_image_prompt(instance):
            return None
        game_key = instance.game_key
        expected_run_id = instance.run_id
        task_key = (game_key, expected_run_id)
        existing = self._scene_image_tasks.get(task_key)
        if existing is not None and not existing.done():
            return None
        task = asyncio.create_task(
            self._generate_scene_image_background(
                game_key, expected_run_id, completed_round, prompt,
            )
        )
        self._scene_image_tasks[task_key] = task
        task.add_done_callback(
            lambda completed, key=task_key: (
                self._scene_image_tasks.pop(key, None)
                if self._scene_image_tasks.get(key) is completed
                else None
            )
        )
        return task

    async def _generate_scene_image_background(
        self,
        game_key: tuple[str, ...],
        expected_run_id: str,
        round_number: int,
        prompt: str,
    ) -> None:
        try:
            current = self.registry.get(game_key)
            if current is None or current.run_id != expected_run_id:
                return
            if not any(
                item.get("round") == round_number for item in current.log
            ):
                return  # 该回合已被回滚删除，放弃本次生图
            if self._image_generation is None:
                return
            result = await self._image_generation.generate(ImageGenerationRequest(
                prompt=prompt,
                purpose="scene",
                owner_type="game",
                owner_id=game_image_owner_id(game_key),
                aspect_ratio="16:9",
                context={"round": round_number, "run_id": expected_run_id},
            ))
            # 重开/重置可能发生在生图 await 期间。旧任务不得写入新一局，
            # 同一局的 swipe 也可能已经删除或替换目标回合。
            current = self.registry.get(game_key)
            if current is None or current.run_id != expected_run_id:
                return
            entry = next(
                (item for item in current.log if item.get("round") == round_number),
                None,
            )
            if entry is None:
                return  # 该回合已被回滚删除，放弃本次生图
            reference = {"kind": "generated", "asset_id": result.asset_id}
            current.set_scene_image(reference)
            entry["scene_image"] = {
                "reference": reference,
                "generation_id": result.generation_id,
                "prompt": prompt,
                "revised_prompt": result.revised_prompt,
                "status": "ready",
                "swipe_index": int(entry.get("current_swipe") or 0),
            }
            await self.registry.save(current)
            logger.info("场景图已生成 (round=%d, asset=%s)", round_number, result.asset_id)
        except ImageGenerationError as exc:
            logger.warning("场景图生成失败 (round=%d): %s", round_number, exc)
        except Exception:
            logger.exception("场景图后台任务异常 (round=%d)", round_number)

    async def process_round_impl(self, instance: GameInstance, *, on_delta=None, on_reset=None) -> tuple[str, dict | None]:
        """实际的判定处理逻辑。"""
        expected_run_id = instance.run_id
        if not instance.round_checks_prepared:
            await self.prepare_round_checks_ai(instance)
        # The planning phase may create an explicitly-authorized table offer.
        # Capture the narrative-start snapshot only after planning completes;
        # confirmations of this snapshot may resolve while the LLM streams,
        # but proposals created afterwards must invalidate the response.
        expected_economy_fingerprint = economy_fingerprint(instance)
        # 只保留最近一轮的短期展示状态，避免旧提示或战斗结果常驻。
        instance.begin_round_processing()

        ensure_round_managers(instance)
        actions_text = collect_actions_text(instance)

        if instance.world_id:
            self._ensure_matcher_for_world(instance.world_id, instance.language)
        lorebook_matches = self.matcher.match_with_recursive(
            actions_text, timed_state=instance.lorebook_timed_state)

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
        )

        # 昏迷角色的死亡豁免先于战斗/叙事结算，文本并入行动块供 GM 遵循。
        death_text = resolve_round_death_saves(instance, rule)
        if death_text:
            actions_text = death_text + "\n" + actions_text

        dice_block = format_check_results_constraint(instance, list(instance.last_checks))
        if dice_block:
            actions_text += dice_block

        explicit_attack = any(
            isinstance(action.get("check_request"), dict)
            and str(action["check_request"].get("kind") or "") == "attack"
            for action in instance.action_queue
            if action.get("user_id") in instance.players
        )
        authoritative_combat = combat_model == "authoritative_event_batch"
        if not authoritative_combat and (instance.combat_state != "none" or explicit_attack):
            combat_text = self._combat.resolve_combat(instance, actions_text, combat_model, rule)
            if combat_text:
                actions_text = combat_text + "\n" + actions_text
                if instance.combat_state == "none" and instance.combat_enemies:
                    init_text = self._combat.initiate_combat(instance)
                    actions_text = init_text + "\n" + actions_text

        puzzle_text = self._puzzles.process_puzzles(instance, actions_text)
        if puzzle_text:
            actions_text = puzzle_text + "\n\n" + actions_text
        gm_directives_text, consumed_directive_ids = collect_gm_directives_text(instance)
        # 指令不再拼进玩家块：走独立可信通道，避免被“玩家发言不可信”标注误伤，
        # 也杜绝玩家在行动文本里仿冒【GM私密指令】标题。
        overreach_text = (
            format_overreach_block(instance) if overreach_guard_enabled() else ""
        )

        gm_prompt = self._prompt.compose_gm_prompt(instance, rule_appendix, world_data=world_data)
        provider_name = self.llm_client.default if self.llm_client else ""
        context = await self._prompt.build_user_context(
            instance, gm_prompt, lorebook_matches, actions_text,
            provider_name=provider_name, world_data=world_data,
            directives_text=gm_directives_text, overreach_text=overreach_text)

        context = await append_multistep_analysis(
            self.llm_client, instance, gm_prompt, context, actions_text, self.analysis_max_tokens)
        response, data = await call_llm_with_tag_retry(
            self.llm_client, instance, gm_prompt, context, combat_model,
            dice_block, self.narrative_max_tokens, actions_text,
            on_delta=on_delta, on_reset=on_reset)
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
            return "", None
        runtime = self._ruleset_runtime(instance)
        if isinstance(runtime, NarrativeStatePolicyRuntime):
            data["state_update"] = runtime.filter_narrative_state_update(
                instance, dict(data.get("state_update") or {}),
            )
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
        dropped_rewards = discard_unearned_reward_proposals(instance, data, response.narration)
        if dropped_rewards:
            # The response object was populated before the economy gate; keep
            # the authoritative state-update view in sync with the filtered
            # proposal list so it cannot be queued through the old reference.
            response.state_update = data.get("state_update") or {}
            system_changes.append(unearned_reward_notice(instance.language))
        if should_warn_unbacked_payment(
            response.narration, data, instance.language,
            currency_labels=currency_labels,
        ):
            system_changes.append(unbacked_payment_notice(instance.language))
        # Purchase authority is explicit GM order + payer confirmation. Never
        # infer a price or create a chargeable proposal from narration text;
        # the same-round pass below may only report numbers a human verbatim
        # stated in this narration (same provenance contract as the planner).
        unpriced_purchase_intents = list(instance.round_unpriced_purchase_intents)
        if unpriced_purchase_intents:
            try:
                late_offers, unpriced_purchase_intents = await price_unpriced_purchase_intents(
                    instance, self.llm_client, response.narration,
                    unpriced_purchase_intents,
                )
            except Exception:
                logger.warning(
                    "同期购买价格复检失败，无价意图保持拦截: game=%s round=%d",
                    instance.game_key, instance.round_number, exc_info=True,
                )
                late_offers = []
            for offer in late_offers:
                try:
                    if has_pending_identical_purchase(
                        instance, str(offer["payer_uid"]), str(offer["target"]),
                    ):
                        logger.info(
                            "同商品购买已待确认，跳过复检重复报价: payer=%s target=%s round=%d",
                            offer["payer_uid"], offer["target"], instance.round_number,
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
                        source_ref=(
                            f"ai:{instance.run_id}:{instance.round_number}:"
                            f"{offer['payer_uid']}:{offer['target']}:{quantity}:"
                            f"{offer.get('amount_scope') or 'total'}:{offer['amount']}"
                        ),
                    )
                except Exception:
                    logger.warning(
                        "复检报价提案创建失败，已跳过: %s", offer, exc_info=True,
                    )
            instance.round_unpriced_purchase_intents = list(unpriced_purchase_intents)
        dropped_purchase_items = filter_unconfirmed_purchase_grants(
            instance, data,
            unpriced_purchase_intents=unpriced_purchase_intents,
        )
        if dropped_purchase_items:
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

        queued_proposals: list[dict[str, Any]] = []
        allowed_uids: set | None = None
        if response.state_update:
            # 多人局权威白名单：状态标签只允许作用于本轮行动者/参战者。
            if len(instance.players) > 1:
                allowed_uids = {
                    str(action.get("user_id"))
                    for action in instance.action_queue
                    if action.get("user_id") in instance.players
                }
                if str(getattr(instance, "combat_state", "none") or "none") != "none":
                    allowed_uids |= set(instance.alive_players)
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
                    instance.round_number,
                    len(queued_proposals),
                )
        instance.set_state_update_recap(response.state_update)

        apply_confirmed_items(instance, data)
        apply_puzzle_updates(instance, data)
        apply_combat_command(instance, data)
        combat_requested = apply_ruleset_combat_signal(
            instance, data, runtime, director_proposal_data,
        )
        automation_batches: list[dict[str, Any]] = []
        if director_proposal_data and runtime is not None:
            try:
                automation_batches = apply_director_automation(
                    runtime, instance, director_proposal_data, random.SystemRandom(),
                )
            except (ValueError, KeyError, TypeError):
                # The advisory encounter request remains visible for GM review.
                logger.exception("D&D Director automation was rejected; waiting for GM")
        apply_revive_commands(instance, data)
        system_changes.extend(apply_growth_rewards(
            instance, data, response, rule, self._progression, runtime,
        ))
        update_quick_actions(instance, data)
        await apply_memory_delta(instance, response, self.memory_store)
        # 消费待处理 embedding 队列，让新记忆在运行中也能获得向量（此前从未被调用）
        if self.memory_store and self.memory_store.embedding_client:
            try:
                await self.memory_store.flush_pending_embeddings()
            except Exception:
                logger.warning("flush_pending_embeddings 失败 (round=%d)", instance.round_number, exc_info=True)
        apply_plot_update(instance, response)
        store_private_messages(instance, response)
        state_msgs = append_state_change_messages(instance, response, public_state_before, data)
        state_msgs.extend(system_changes)
        request = (
            instance.ruleset_state.get("encounter_request")
            if isinstance(getattr(instance, "ruleset_state", None), dict)
            else None
        )
        if combat_requested and isinstance(request, dict) and request.get("status") == "pending":
            request_note = localized_text(instance.language, {
                "en": "Combat is ready. Waiting for the GM to confirm initiative.",
                "zh-CN": "战斗准备已就绪，等待 GM 确认进入先攻。",
                "ja": "戦闘準備が整いました。GM のイニシアチブ開始確認を待っています。",
            })
            state_msgs.append(request_note)
        if automation_batches:
            automation_note = summarize_automation_batches(
                automation_batches,
                chinese=not str(getattr(instance, "language", "") or "").lower().startswith("en"),
            )
            state_msgs.append(automation_note)

        instance.consume_gm_directives(set(consumed_directive_ids))
        await instance.finish_judgment(response.narration, pre_state_snapshot=round_pre_snapshot, state_changes=state_msgs)
        instance.set_latest_log_tags_summary(summarize_tags(data))
        instance.record_llm_usage(response.total_tokens, calls=0)

        self._state_applier.tick_madness(instance)

        # 摘要压缩较慢（LLM 调用，每 10 回合），延后到后台：叙事已推送，用户无需等待。
        self._maybe_schedule_summary(instance, gm_prompt)
        # 场景图生成慢（生图 API 10-60s），同样延后到后台，完成后再推给前端。
        self._maybe_schedule_scene_image(instance, data)

        try:
            await self.registry.save(instance)
            instance.record_save_success()
        except Exception:
            count = instance.record_save_failure()
            logger.exception("存档失败(连续%d次) (round=%d)", count, instance.round_number)

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
                instance.round_number,
            )
