"""完整回合推进流程。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.commands.round_effects import (
    append_state_change_messages,
    apply_combat_command,
    apply_confirmed_items,
    apply_growth_rewards,
    apply_memory_delta,
    apply_plot_update,
    apply_puzzle_updates,
    apply_revive_commands,
    store_private_messages,
    update_quick_actions,
)
from src.commands.round_llm import (
    append_multistep_analysis,
    apply_parsed_data_to_response,
    call_llm_with_tag_retry,
)
from src.commands.round_actions import (
    build_dice_constraint_block,
    collect_actions_text,
    ensure_round_managers,
    initialize_puzzles_from_lorebook,
)
from src.commands.state_recap import snapshot_public_player_state
from src.commands.tag_summary import summarize_tags
from src.engine.constants import COMBAT_INTENT_KEYWORDS
from src.engine.game_instance import GameInstance, GameState, _snapshot_players
from src.memory.summarizer import needs_summary, summarize

logger = logging.getLogger("trpg")


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
        load_world_template: Callable[[str], dict | None],
        ensure_matcher_for_world: Callable[[str], None],
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

    async def process_round(self, instance: GameInstance) -> tuple[str, dict | None]:
        instance = self.registry.get(instance.game_key)
        if not instance or instance.state != GameState.ACTIVE_JUDGMENT:
            return "", None
        if instance._process_lock.locked():
            logger.warning("process_round 已在处理中，跳过并发调用: %s", instance.game_key)
            return "", None
        async with instance._process_lock:
            return await self.process_round_impl(instance)

    async def process_round_impl(self, instance: GameInstance) -> tuple[str, dict | None]:
        """实际的判定处理逻辑。"""
        instance.pending_combat_results.clear()
        instance.update_lorebook_timed_state()

        ensure_round_managers(instance)
        actions_text = collect_actions_text(instance)

        if instance.world_id:
            self._ensure_matcher_for_world(instance.world_id)
        lorebook_matches = self.matcher.match_with_recursive(
            actions_text, timed_state=instance.lorebook_timed_state)

        initialize_puzzles_from_lorebook(instance, self.lorebook_store)

        rule_ctx = self._prompt.load_rule_context(instance, self._load_world_template)
        rule_appendix = rule_ctx.rule_appendix
        combat_model = rule_ctx.combat_model
        dice_system = rule_ctx.dice_system
        world_data = rule_ctx.world_data
        rule = rule_ctx.rule

        dice_block = ""
        dice_block = build_dice_constraint_block(instance, actions_text, rule, dice_system, self._dice)
        if dice_block:
            actions_text += dice_block

        if any(kw in actions_text for kw in COMBAT_INTENT_KEYWORDS):
            combat_text = self._combat.resolve_combat(instance, actions_text, combat_model)
            if combat_text:
                actions_text = combat_text + "\n" + actions_text
                if instance.combat_state == "none" and instance.combat_enemies:
                    init_text = self._combat.initiate_combat(instance)
                    actions_text = init_text + "\n" + actions_text

        puzzle_text = self._puzzles.process_puzzles(instance, actions_text)
        if puzzle_text:
            actions_text = puzzle_text + "\n\n" + actions_text

        gm_prompt = self._prompt.compose_gm_prompt(instance, rule_appendix)
        provider_name = self.llm_client.default if self.llm_client else ""
        context = await self._prompt.build_user_context(
            instance, gm_prompt, lorebook_matches, actions_text,
            provider_name=provider_name, world_data=world_data)

        context = await append_multistep_analysis(
            self.llm_client, instance, gm_prompt, context, actions_text, self.analysis_max_tokens)
        response, data = await call_llm_with_tag_retry(
            self.llm_client, instance, gm_prompt, context, combat_model,
            dice_block, self.narrative_max_tokens, actions_text)
        apply_parsed_data_to_response(instance, response, data)

        public_state_before = snapshot_public_player_state(instance)
        round_pre_snapshot = _snapshot_players(instance)

        if response.state_update:
            self._state_applier.apply_state_update(instance, response.state_update)
        instance.last_state_update = response.state_update or None

        apply_confirmed_items(instance, data)
        apply_puzzle_updates(instance, data)
        apply_combat_command(instance, data)
        apply_revive_commands(instance, data)
        apply_growth_rewards(instance, data, response, rule, self._progression)
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

        await instance.finish_judgment(response.narration, pre_state_snapshot=round_pre_snapshot, state_changes=state_msgs)
        if instance.log:
            instance.log[-1]["tags_summary"] = summarize_tags(data)
        instance.total_tokens += response.total_tokens

        self._state_applier.tick_madness(instance)

        if needs_summary(instance):
            try:
                await summarize(instance, self.llm_client, gm_prompt, self.summary_max_tokens)
            except Exception:
                logger.exception("摘要压缩失败，已跳过 (round=%d)", instance.round_number)

        try:
            await self.registry.save(instance)
            instance._save_fail_count = 0
        except Exception:
            count = getattr(instance, "_save_fail_count", 0) + 1
            instance._save_fail_count = count
            logger.exception("存档失败(连续%d次) (round=%d)", count, instance.round_number)

        return response.narration, response.info_asymmetry
