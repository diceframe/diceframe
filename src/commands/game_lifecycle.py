"""游戏开始、恢复、重置与重开流程。"""

from __future__ import annotations

import asyncio
import logging
import copy
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from src.commands.protocol_repair import repair_malformed_protocol_response
from src.commands.economy_effects import (
    currency_labels_for_rule,
    discard_unearned_reward_proposals,
    defer_narrative_effects,
    has_economy_proposal,
    pending_decision_notice,
    should_warn_unbacked_payment,
    unbacked_purchase_notice,
    unbacked_payment_notice,
    unearned_reward_notice,
)
from src.engine.economy import filter_unconfirmed_purchase_grants
from src.commands.state_update_applier import StateUpdateApplier
from src.commands.tag_parser import (
    parse_tag_state,
)
from src.commands.tag_json import extract_narration_from_response
from src.commands.tag_summary import summarize_tags
from src.engine.character_utils import reset_character_for_restart
from src.engine.economy import queue_effect_group
from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.engine.language import localized_text, normalize_language
from src.engine.narrative_perspective import narrative_perspective_instruction
from src.llm.parser import normalize_tag_protocol, sanitize_narration
from src.rulesets.contracts import RunLifecycleRuntime

logger = logging.getLogger("trpg")


class GameLifecycle:
    """负责游戏生命周期操作，避免 GameHandler 承担所有流程细节。"""

    def __init__(
        self,
        registry: GameRegistry,
        llm_client: Any,
        prompt: Any,
        state_applier: StateUpdateApplier,
        ensure_matcher_for_world: Callable[[str, str], None],
        create_game: Callable[..., Awaitable[GameInstance]],
        load_world_template: Callable[[str, str], dict | None],
        narrative_max_tokens: int,
        brief_max_tokens: int,
        memory_store: Any | None = None,
    ):
        self.registry = registry
        self.llm_client = llm_client
        self.prompt = prompt
        self.state_applier = state_applier
        self.ensure_matcher_for_world = ensure_matcher_for_world
        self.create_game = create_game
        self.load_world_template = load_world_template
        self.narrative_max_tokens = narrative_max_tokens
        self.brief_max_tokens = brief_max_tokens
        self.memory_store = memory_store
        self._run_transition_locks: dict[tuple[str, ...], asyncio.Lock] = {}

    async def _clear_session_memory(self, instance: GameInstance) -> None:
        """Compatibility hook; run namespaces now provide memory isolation.

        Physical deletion is intentionally not part of the transition commit:
        a cleanup failure must not expose an earlier run or leave a half-reset
        aggregate. Old namespaces can be collected independently.
        """
        clear = getattr(self.memory_store, "clear_game", None)
        if not callable(clear):
            return
        try:
            namespaces = {instance.memory_namespace, str(instance.game_key)}
            for namespace in namespaces:
                result = clear(namespace)
                if hasattr(result, "__await__"):
                    await result
        except Exception:
            # Isolation is already guaranteed by the new namespace. Cleanup is
            # storage hygiene and may be retried without rolling back the run.
            logger.warning(
                "旧 run 记忆清理失败，已保持命名空间隔离: game=%s run=%s",
                instance.game_key,
                instance.run_id,
                exc_info=True,
            )

    async def _new_run_candidate(
        self,
        source: GameInstance,
        *,
        preserve_players: bool,
    ) -> GameInstance:
        candidate = await self.create_game(
            source.game_key,
            world_id=source.world_id,
            world_name=source.world_name,
            group_name=source.group_name,
            seed_code=source.seed_code,
            rule_id=source.rule_id,
            difficulty=source.difficulty,
            language=normalize_language(source.language),
            fresh_instance=True,
        )
        candidate.configure_session(
            solo_mode=source.solo_mode,
            entry_point=source.entry_point,
            room_password=source.room_password,
            gm_uid=source.gm_uid,
            luck_timeout_seconds=source.luck_timeout_seconds,
            narrative_perspective=source.narrative_perspective,
        )
        candidate.max_players = source.max_players
        candidate.player_access_open = source.player_access_open
        candidate.bot_bind_token = source.bot_bind_token
        candidate.room_token = source.room_token
        candidate.ruleset_runtime = copy.deepcopy(source.ruleset_runtime)
        candidate.ruleset_state = (
            {
                "state_schema_version": int(
                    source.ruleset_runtime.get("state_schema_version", 1) or 1
                )
            }
            if source.ruleset_runtime else {}
        )
        candidate.adventure_binding = copy.deepcopy(source.adventure_binding)
        if preserve_players:
            players = copy.deepcopy(source.players)
            for pdata in players.values():
                pdata["character_sheet"] = reset_character_for_restart(
                    pdata.get("character_sheet", {})
                )
            candidate.replace_players(players)
        self._initialize_ruleset_run(
            candidate,
            preserve_characters=preserve_players,
        )
        return candidate

    def _initialize_ruleset_run(
        self,
        candidate: GameInstance,
        *,
        preserve_characters: bool,
    ) -> None:
        binding = dict(candidate.ruleset_runtime or {})
        runtime_id = str(binding.get("id") or "")
        ruleset_registry = getattr(self.prompt, "ruleset_registry", None)
        if runtime_id and runtime_id != "core:legacy" and ruleset_registry is not None:
            runtime = ruleset_registry.get(
                runtime_id,
                minimum_version=int(binding.get("version", 1) or 1),
            )
            if isinstance(runtime, RunLifecycleRuntime):
                runtime.initialize_new_run(
                    candidate,
                    preserve_characters=preserve_characters,
                )

    async def start_game(
        self,
        instance: GameInstance,
        *,
        publish: bool = True,
        persist: bool = True,
    ) -> str:
        """激活游戏，生成开场叙事，进入第一轮。"""
        await instance.activate()
        await instance.start_round()
        if publish:
            self.registry.register(instance)

        if instance.world_id:
            self.ensure_matcher_for_world(instance.world_id, instance.language)

        rule_ctx = self.prompt.load_rule_context(instance, self.load_world_template)
        gm_prompt = self.prompt.compose_gm_prompt(
            instance, rule_ctx.rule_appendix, world_data=rule_ctx.world_data,
        )
        world_data = rule_ctx.world_data or {}
        currency_labels = currency_labels_for_rule(rule_ctx.rule)
        world_description = world_data.get("description", "")
        world_setting = world_data.get("world_setting", "")
        starter_scene = world_data.get("starter_scene", "")
        sandbox_world = bool(world_data.get("sandbox"))
        player_lines = []
        for pdata in instance.players.values():
            cs = pdata.get("character_sheet", {})
            player_lines.append(
                f"- {pdata.get('character_name', '冒险者')}："
                f"{cs.get('race', '人类')} {cs.get('class', '冒险者')}"
                f"；背景：{cs.get('background', '') or '未填写'}"
            )
        players_text = "\n".join(player_lines) if player_lines else "尚未创建角色"
        if sandbox_world:
            opening_instruction = localized_text(
                instance.language,
                {
                    "en": (
                        "This is an intentionally blank freeform sandbox with no preset canon, era, "
                        "location, factions, or NPCs. Use only the player character names and backgrounds "
                        "to establish a minimal opening situation, and leave room for the players to define "
                        "the world through play. Do not assume a tavern, medieval fantasy, or any other genre "
                        "unless a character concept establishes it.\n\n"
                        "Write a concise 100-150 word opening that offers an immediate choice."
                    ),
                    "zh-CN": (
                        "这是一个有意保持空白的自由沙盒，没有预设时代、地点、阵营、NPC 或固定世界观。"
                        "只根据玩家已经填写的角色姓名与背景建立最少的开场事实，并允许玩家在行动中继续定义世界。"
                        "除非角色设定明确提到，否则不得默认套用酒馆、中世纪奇幻或其他固定题材。"
                        "\n\n请用 100 至 150 字给出一个简洁、可立即行动的开场。"
                    ),
                    "ja": (
                        "これは意図的に空白のフリーフォームサンドボックスで、事前設定された世界観・時代・"
                        "場所・派閥・NPC はない。プレイヤーが入力したキャラクター名と背景だけを使って、"
                        "最小限のオープニング状況を構築し、プレイを通じて世界を定義していく余地を残すこと。"
                        "キャラクターコンセプトで明示されない限り、酒場や中世ファンタジーなどのジャンルを"
                        "勝手に想定しない。\n\n簡潔な 100〜150 字の、すぐ行動できるオープニングを書くこと。"
                    ),
                },
            )
        else:
            opening_instruction = localized_text(
                instance.language,
                {
                    "en": (
                        "The game has just started. As GM, strictly follow the world setting, era, "
                        "location, and genre above. Describe the opening scene, introduce the "
                        "current environment, and make clear where the player characters are. "
                        "Do not switch to another genre, city, or era without cause.\n\n"
                        "Write about 120-180 English words for the opening scene and naturally "
                        "mention the player character names."
                    ),
                    "zh-CN": (
                        "游戏刚刚开始，请作为 GM 严格沿用上面的世界设定、时代、地点和题材，"
                        "描述开场场景，介绍当前环境和玩家所在的位置。不得无端切换到其他题材、城市或时代。"
                        "\n\n请用 150 字左右描述开场场景，并自然点出玩家角色名。"
                    ),
                    "ja": (
                        "ゲームは始まったばかり。GM として上記の世界設定・時代・場所・ジャンルを厳密に守り、"
                        "オープニングシーンを描写し、現在の環境を紹介し、プレイヤーキャラクターの位置を明確にすること。"
                        "理由なく別のジャンル・都市・時代に切り替えてはならない。\n\n"
                        "120〜180 語程度のオープニングシーンを書き、プレイヤーキャラクター名を自然に言及すること。"
                    ),
                },
            )
        opening_instruction += "\n\n" + narrative_perspective_instruction(
            instance, instance.language,
        )
        welcome_context = (
            f"{gm_prompt}\n\n"
            f"【当前世界】\n"
            f"名称：{instance.world_name}\n"
            f"简介：{world_description or '无'}\n"
            f"世界设定：{world_setting or '无'}\n"
            f"模板开场：{starter_scene or '无'}\n\n"
            f"【玩家角色】\n{players_text}\n\n"
            f"【开场场景】\n"
            f"{opening_instruction}"
        )

        response = None
        try:
            response = await self.llm_client.call(
                system_prompt=gm_prompt,
                user_message=welcome_context,
                temperature=0.8,
                max_tokens=self.narrative_max_tokens,
            )
            response = await repair_malformed_protocol_response(
                self.llm_client,
                response,
                system_prompt=gm_prompt,
                user_message=welcome_context,
                language=instance.language,
                temperature=0.8,
                max_tokens=self.narrative_max_tokens,
            )
            response.content = normalize_tag_protocol(response.content)
            narration = extract_narration_from_response(response)
            if "---" in response.content:
                narration = response.content.split("---", 1)[0].strip()
            narration = sanitize_narration(narration)
            # 开场标签同样需要落地到 instance：NPC 登记、场景、首次战利品等。
            start_data = parse_tag_state(response.content, rule_ctx.combat_model)
        except Exception:
            logger.exception("开场叙事生成失败，已保存可继续的兜底开场")
            narration = localized_text(
                instance.language,
                {
                    "en": (
                        f"{instance.world_name} is ready. The opening narration could not be "
                        "generated, but your game and characters have been saved. Configure or "
                        "retry the model service, then continue when you are ready."
                    ),
                    "zh-CN": (
                        f"《{instance.world_name}》已经创建，角色与存档均已保存。"
                        "本次开场叙事生成失败；请检查模型服务后继续游戏或重试。"
                    ),
                    "ja": (
                        f"『{instance.world_name}』を作成し、キャラクターとセーブを保存しました。"
                        "オープニング生成に失敗したため、モデル設定を確認してから続行または再試行してください。"
                    ),
                },
            )
            start_data = {}
        # Opening narration goes through the same reward qualification gate as
        # normal rounds.  The model may describe an NPC promising payment for
        # a task that has not happened yet; such a GOLD tag must not become a
        # pending GM approval (or an eventual balance change) merely because
        # it appeared in the first response.
        system_changes: list[str] = []
        dropped_rewards = discard_unearned_reward_proposals(
            instance, start_data, narration,
        )
        if dropped_rewards:
            system_changes.append(unearned_reward_notice(instance.language))
        economy_pending = bool(response is not None and has_economy_proposal(start_data))
        if should_warn_unbacked_payment(
            narration, start_data, instance.language,
            currency_labels=currency_labels,
        ):
            system_changes.append(unbacked_payment_notice(instance.language))
        dropped_purchase_items = filter_unconfirmed_purchase_grants(instance, start_data)
        if dropped_purchase_items:
            system_changes.append(unbacked_purchase_notice(instance.language))
        deferred_effects = (
            defer_narrative_effects(start_data, response)
            if response is not None else {}
        )
        if economy_pending:
            system_changes.append(pending_decision_notice(instance.language))
        queued_proposals: list[dict[str, Any]] = []
        if start_data.get("state_update"):
            queued_proposals = self.state_applier.apply_state_update(
                instance, start_data["state_update"],
            )
        if deferred_effects:
            deferred_effects["allowed_player_uids"] = None
            queue_effect_group(instance, queued_proposals, deferred_effects)
        if start_data.get("plot_update") and instance.plot_tracker:
            try:
                instance.plot_tracker.apply_update(start_data["plot_update"], 0)
            except Exception:
                logger.exception("开场剧情更新异常，已跳过")
        if start_data.get("quick_actions"):
            instance.set_quick_actions(start_data["quick_actions"])
        scene = (start_data.get("state_update") or {}).get("scene_change", "")
        start_label = localized_text(
            getattr(instance, "language", ""),
            {"en": "Game Start", "zh-CN": "游戏开始", "ja": "ゲーム開始"},
        )
        instance.set_scene(scene or start_label)
        if response is not None:
            instance.record_llm_usage(response.total_tokens)
        instance.append_log_entry({
            "round": 0,
            "actions": [{"user_id": "system", "text": start_label}],
            "gm_response": narration,
            "state_changes": system_changes,
            "tags_summary": summarize_tags(start_data),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if persist:
            await self.registry.save(instance)
        return narration

    async def resume_game(self, instance: GameInstance) -> str:
        """从 PAUSED 状态恢复游戏，生成「上回说到」续接叙事。"""
        if instance.state != GameState.PAUSED:
            await instance.activate()
            return ""

        recent_log = instance.log[-5:] if instance.log else []
        if not recent_log:
            await instance.start_round()
            return ""

        gm_prompt = self.prompt.compose_gm_prompt(instance)
        history_text = "\n".join(
            f"Round {e.get('round','?')}: {sanitize_narration(e.get('gm_response',''))[:100]}"
            for e in recent_log
        )

        resume_prompt = localized_text(
            instance.language,
            {
                "en": (
                    "You are the GM of a TRPG game that has just resumed from pause. "
                    "Write a brief 'Previously on...' continuation in English, under 80 words. "
                    "Summarize the latest events and naturally lead into the current scene.\n\n"
                    f"Recent log:\n{history_text}\n\n"
                    f"Current scene: {instance.scene}\n"
                    f"Alive players: {', '.join(instance.alive_players) if instance.alive_players else 'none'}\n\n"
                    "Output narration only, without a JSON block."
                ),
                "zh-CN": (
                    f"你是 TRPG 的 GM，游戏刚刚从暂停中恢复。请生成一段不超过100字的「上回说到」续接叙事，"
                    f"概括最近发生的事情并自然推进到当前场景。\n\n"
                    f"最近日志：\n{history_text}\n\n"
                    f"当前场景：{instance.scene}\n"
                    f"存活玩家：{', '.join(instance.alive_players) if instance.alive_players else '无'}\n\n"
                    f"请直接输出叙事文本（不要 JSON 块）。"
                ),
                "ja": (
                    "あなたは TRPG の GM。ゲームは一時停止から再開したばかり。"
                    "「これまでのあらすじ」として、80 語以内の日本語の続きのナレーションを書くこと。"
                    "直近の出来事をまとめ、現在のシーンへ自然につなぐこと。\n\n"
                    f"最近のログ：\n{history_text}\n\n"
                    f"現在のシーン：{instance.scene}\n"
                    f"生存プレイヤー：{', '.join(instance.alive_players) if instance.alive_players else 'なし'}\n\n"
                    "ナレーションのみを出力し、JSON ブロックを付けないこと。"
                ),
            },
        )

        try:
            response = await self.llm_client.call(
                system_prompt=gm_prompt,
                user_message=resume_prompt,
                temperature=0.6,
                max_tokens=self.brief_max_tokens,
            )
            resume_narration = sanitize_narration(response.narration or response.content)
        except Exception:
            logger.exception("续接叙事生成失败")
            resume_narration = localized_text(
                instance.language,
                {
                    "en": f"The GM is back online. Current scene: {instance.scene}. Continue when ready.",
                    "zh-CN": f"GM 已重新上线。当前场景：{instance.scene}。输入 /go 继续冒险。",
                    "ja": f"GM は再起動した。現在のシーン：{instance.scene}。/go で冒険を続行。",
                },
            )

        await instance.start_round()
        await self.registry.save(instance)
        return resume_narration

    async def reset_game(self, instance: GameInstance) -> GameInstance:
        transition_lock = self._run_transition_locks.setdefault(
            instance.game_key, asyncio.Lock(),
        )
        async with transition_lock:
            current = self.registry.get(instance.game_key) or instance
            async with current.authoritative_write():
                async with current._process_lock:
                    async with current._lock:
                        return await self._replace_run(current, preserve_players=False)

    async def _replace_run(
        self,
        previous: GameInstance,
        *,
        preserve_players: bool,
    ) -> GameInstance:
        candidate = await self._new_run_candidate(
            previous, preserve_players=preserve_players,
        )
        await self._start_reset_instance(candidate)
        candidate.record_llm_usage(calls=1)
        if self.registry.get(previous.game_key) is not previous:
            raise RuntimeError("对局已在重开过程中发生变化，请刷新后重试")
        await self.registry.replace_current(previous, candidate)
        await self._clear_session_memory(previous)
        return candidate

    async def restart_game(self, instance: GameInstance) -> GameInstance:
        """重开世界：重置剧情/场景/日志，保留所有角色卡（回满 HP 和状态）。"""
        transition_lock = self._run_transition_locks.setdefault(
            instance.game_key, asyncio.Lock(),
        )
        async with transition_lock:
            current = self.registry.get(instance.game_key) or instance
            if not current.players:
                raise ValueError("重开世界需要至少 1 名角色")
            async with current.authoritative_write():
                async with current._process_lock:
                    # Freeze every old-run aggregate write through the atomic swap.
                    # A waiter holding a stale reference resumes afterwards and is
                    # rejected by its registry-identity fence.
                    async with current._lock:
                        return await self._replace_run(current, preserve_players=True)

    async def _start_reset_instance(self, instance: GameInstance) -> str:
        """Resume the gameplay stack already bound to this save."""

        runtime_id = str((instance.ruleset_runtime or {}).get("id") or "")
        if not runtime_id or runtime_id == "core:legacy":
            return await self.start_game(instance, publish=False, persist=False)
        await instance.activate()
        world = self.load_world_template(instance.world_id, instance.language) or {}
        initial_scene = str(world.get("starter_scene") or instance.world_name or "").strip()
        if initial_scene:
            instance.set_scene(initial_scene)
        return ""
