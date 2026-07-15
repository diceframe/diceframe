"""Swipe 候选叙事生成。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.commands.state_update_applier import StateUpdateApplier
from src.commands.tag_parser import parse_tag_state
from src.engine.game_instance import GameInstance, restore_players

logger = logging.getLogger("trpg")


class SwipeGenerator:
    """为指定历史轮次重新生成一个候选叙事。"""

    def __init__(
        self,
        llm_client: Any,
        matcher: Any,
        prompt: Any,
        state_applier: StateUpdateApplier,
        load_world_template: Callable[[str], dict | None],
        ensure_matcher_for_world: Callable[[str], None],
        narrative_max_tokens: int,
    ):
        self.llm_client = llm_client
        self.matcher = matcher
        self.prompt = prompt
        self.state_applier = state_applier
        self.load_world_template = load_world_template
        self.ensure_matcher_for_world = ensure_matcher_for_world
        self.narrative_max_tokens = narrative_max_tokens

    async def generate(self, instance: GameInstance, round_num: int) -> str | None:
        """为指定轮生成一个新 swipe（最多 5 个）。"""
        if instance._process_lock.locked():
            logger.warning("process_round 进行中，跳过 generate_swipe: %s", instance.game_key)
            return None
        target_entry = None
        target_idx = -1
        for i, entry in enumerate(instance.log):
            if entry.get("round") == round_num:
                target_entry = entry
                target_idx = i
                break
        if not target_entry:
            return None

        swipes = target_entry.get("swipes", [])
        if not swipes:
            swipes = [target_entry.get("gm_response", "")]
            target_entry["swipes"] = swipes
        if len(swipes) >= 5:
            logger.warning("Swipe 已达上限 (5), round=%d", round_num)
            return None

        snapshot = target_entry.get("pre_state_snapshot", {})
        if snapshot:
            restore_players(instance, snapshot)
            logger.info("Swipe: 已恢复 pre-state snapshot (round=%d)", round_num)

        actions_text = "; ".join(a.get("text", "") for a in target_entry.get("actions", []))
        if instance.world_id:
            self.ensure_matcher_for_world(instance.world_id)
        lorebook_matches = self.matcher.match_with_recursive(
            actions_text, timed_state=instance.lorebook_timed_state)

        rule_ctx = self.prompt.load_swipe_rule_context(instance, self.load_world_template)
        combat_model_s = rule_ctx.combat_model
        world_data = rule_ctx.world_data

        gm_prompt = self.prompt.compose_gm_prompt(instance, rule_ctx.rule_appendix)

        # 构建上下文（仅使用目标轮之前的日志）
        saved_log = list(instance.log)
        instance.log = instance.log[:target_idx]
        try:
            provider_name = self.llm_client.default if self.llm_client else ""
            context = await self.prompt.build_user_context(
                instance, gm_prompt, lorebook_matches, actions_text,
                provider_name=provider_name, world_data=world_data)
        finally:
            instance.log = saved_log

        response = await self.llm_client.call(
            system_prompt=gm_prompt,
            user_message=context,
            temperature=0.9,
            max_tokens=self.narrative_max_tokens,
        )

        narration = response.content
        if "---" in response.content:
            narration = response.content.split("---", 1)[0].strip()
        data = parse_tag_state(response.content, combat_model_s)

        if data.get("state_update"):
            self.state_applier.apply_state_update(instance, data.get("state_update", {}))
        if data.get("plot_update") and instance.plot_tracker:
            try:
                instance.plot_tracker.apply_update(
                    data.get("plot_update", {}), instance.round_number)
            except Exception:
                logger.exception("Swipe 剧情更新异常，已跳过 (round=%d)", round_num)

        await instance.finish_judgment_with_swipe(narration, round_num)
        logger.info("Swipe 生成: round=%d swipe=%d/%d", round_num,
                    len(swipes) + 1, len(swipes) + 1)
        return narration
