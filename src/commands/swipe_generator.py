"""Swipe 候选叙事生成。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from src.commands.economy_effects import (
    currency_labels_for_rule,
    discard_unearned_reward_proposals,
    defer_narrative_effects,
    has_economy_proposal,
    pending_decision_notice,
    unearned_reward_notice,
    unbacked_purchase_notice,
    unbacked_payment_notice,
    should_warn_unbacked_payment,
)
from src.engine.economy import filter_unconfirmed_purchase_grants
from src.commands.protocol_repair import repair_malformed_protocol_response
from src.commands.round_actions import format_check_results_constraint
from src.commands.state_update_applier import StateUpdateApplier, discard_unresolved_player_damage
from src.commands.tag_parser import parse_tag_state
from src.engine.game_instance import GameInstance, restore_players
from src.engine.economy import queue_effect_group, reconcile_rollback_snapshot, reverse_round_economy
from src.llm.parser import normalize_tag_protocol, sanitize_narration

logger = logging.getLogger("trpg")


class SwipeGenerator:
    """为指定历史轮次重新生成一个候选叙事。"""

    def __init__(
        self,
        llm_client: Any,
        matcher: Any,
        prompt: Any,
        state_applier: StateUpdateApplier,
        load_world_template: Callable[[str, str], dict | None],
        ensure_matcher_for_world: Callable[[str, str], None],
        narrative_max_tokens: int,
        get_instance: Callable[[tuple], GameInstance | None] | None = None,
        save_instance: Callable[[GameInstance], Any] | None = None,
    ):
        self.llm_client = llm_client
        self.matcher = matcher
        self.prompt = prompt
        self.state_applier = state_applier
        self.load_world_template = load_world_template
        self.ensure_matcher_for_world = ensure_matcher_for_world
        self.narrative_max_tokens = narrative_max_tokens
        self.get_instance = get_instance
        self.save_instance = save_instance
        # 生图调度回调（GameHandler 注入）：swipe 叙事带新 SCENE_IMAGE 时重新生成该回合图片
        self._scene_image_hook = None

    def set_scene_image_hook(self, hook) -> None:
        self._scene_image_hook = hook

    async def generate(self, instance: GameInstance, round_num: int) -> str | None:
        """Run the complete historical rewrite under the shared process barrier."""

        if instance._process_lock.locked():
            logger.warning(
                "process_round/swipe 进行中，跳过并发 generate_swipe: %s",
                instance.game_key,
            )
            return None
        async with instance.historical_rewrite() as rewrite_entered:
            if not rewrite_entered:
                return None
            async with instance._process_lock:
                expected_run_id = instance.run_id
                before = type(instance).from_dict(deepcopy(instance.to_dict()))
                before.log = deepcopy(instance.log)
                staged = type(instance).from_dict(deepcopy(instance.to_dict()))
                staged.log = deepcopy(instance.log)
                narration, scene_payload = await self._generate_locked(staged, round_num)
                if narration is None:
                    return None
                if self.get_instance:
                    current = self.get_instance(instance.game_key)
                    if current is not instance or getattr(current, "run_id", None) != expected_run_id:
                        return None
                instance.replace_persisted_state_from(staged)
                try:
                    if self.save_instance is not None:
                        await self.save_instance(instance)
                except Exception:
                    instance.replace_persisted_state_from(before)
                    raise
                if scene_payload and self._scene_image_hook:
                    try:
                        self._scene_image_hook(instance, str(scene_payload.get("prompt") or ""), int(scene_payload.get("round", round_num) or round_num), force=True)
                    except Exception:
                        logger.exception("Swipe 场景图调度失败 (round=%d)", round_num)
                return narration

    async def _generate_locked(
        self, instance: GameInstance, round_num: int,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """为指定轮生成一个新 swipe（最多 5 个）。"""
        target_entry = None
        target_idx = -1
        for i, entry in enumerate(instance.log):
            if entry.get("round") == round_num:
                target_entry = entry
                target_idx = i
                break
        if not target_entry:
            return None, None

        swipes = target_entry.get("swipes", [])
        if not swipes:
            swipes = [target_entry.get("gm_response", "")]
            target_entry["swipes"] = swipes
        if len(swipes) >= 5:
            logger.warning("Swipe 已达上限 (5), round=%d", round_num)
            return None, None

        snapshot = target_entry.get("pre_state_snapshot", {})
        if snapshot:
            reverse_round_economy(instance, round_num)
            restore_players(instance, reconcile_rollback_snapshot(instance, snapshot, round_num))
            logger.info("Swipe: 已恢复 pre-state snapshot (round=%d)", round_num)

        actions_text = "; ".join(
            a.get("text", "") for a in target_entry.get("actions", [])
            if a.get("user_id") in instance.players
        )
        actions_text += format_check_results_constraint(
            instance, list(target_entry.get("check_results") or [])
        )
        if instance.world_id:
            self.ensure_matcher_for_world(instance.world_id, instance.language)
        lorebook_matches = self.matcher.match_with_recursive(
            actions_text, timed_state=instance.lorebook_timed_state)

        rule_ctx = self.prompt.load_swipe_rule_context(instance, self.load_world_template)
        combat_model_s = rule_ctx.combat_model
        world_data = rule_ctx.world_data
        currency_labels = currency_labels_for_rule(rule_ctx.rule)

        gm_prompt = self.prompt.compose_gm_prompt(
            instance, rule_ctx.rule_appendix, world_data=rule_ctx.world_data,
        )

        # 构建上下文（仅使用目标轮之前的日志），不临时改写共享 instance.log。
        provider_name = self.llm_client.default if self.llm_client else ""
        context = await self.prompt.build_user_context(
            instance,
            gm_prompt,
            lorebook_matches,
            actions_text,
            provider_name=provider_name,
            world_data=world_data,
            history_override=instance.log[:target_idx],
        )

        response = await self.llm_client.call(
            system_prompt=gm_prompt,
            user_message=context,
            temperature=0.9,
            max_tokens=self.narrative_max_tokens,
        )
        response = await repair_malformed_protocol_response(
            self.llm_client,
            response,
            system_prompt=gm_prompt,
            user_message=context,
            language=instance.language,
            temperature=0.9,
            max_tokens=self.narrative_max_tokens,
        )
        response.content = normalize_tag_protocol(response.content)

        narration = response.content
        if "---" in response.content:
            narration = response.content.split("---", 1)[0].strip()
        narration = sanitize_narration(narration)
        data = parse_tag_state(response.content, combat_model_s)
        system_changes: list[str] = []
        if should_warn_unbacked_payment(
            narration, data, instance.language,
            currency_labels=currency_labels,
        ):
            system_changes.append(unbacked_payment_notice(instance.language))
        dropped_rewards = discard_unearned_reward_proposals(
            instance, data, narration,
        )
        if dropped_rewards:
            system_changes.append(unearned_reward_notice(instance.language))
        dropped_purchase_items = filter_unconfirmed_purchase_grants(instance, data)
        if dropped_purchase_items:
            system_changes.append(unbacked_purchase_notice(instance.language))
        deferred_effects = defer_narrative_effects(
            data, response,
            defer_state_update=True,
        )
        economy_pending = has_economy_proposal(data)
        if economy_pending:
            system_changes.append(pending_decision_notice(instance.language))

        queued_proposals: list[dict[str, Any]] = []
        if data.get("state_update"):
            discard_unresolved_player_damage(instance, data.get("state_update", {}))
            queued_proposals = self.state_applier.apply_state_update(
                instance, data.get("state_update", {}),
            )
        if deferred_effects:
            deferred_effects["allowed_player_uids"] = None
            queue_effect_group(instance, queued_proposals, deferred_effects)
        if data.get("plot_update") and instance.plot_tracker:
            try:
                instance.plot_tracker.apply_update(
                    data.get("plot_update", {}), instance.round_number)
            except Exception:
                logger.exception("Swipe 剧情更新异常，已跳过 (round=%d)", round_num)

        await instance.finish_judgment_with_swipe(
            narration, round_num,
            state_changes=system_changes if system_changes else None,
        )
        scene_payload = None
        swipe_prompt = str(data.get("scene_image_prompt") or "").strip()
        if swipe_prompt:
            scene_payload = {"prompt": swipe_prompt, "round": round_num}
        logger.info("Swipe 生成: round=%d swipe=%d/%d", round_num,
                    len(swipes) + 1, len(swipes) + 1)
        return narration, scene_payload
