"""主游戏流程处理器 —— 回合推进 + LLM 调用 + 结果播报。"""

from __future__ import annotations

import logging
from pathlib import Path

from src.engine.game_instance import GameInstance, GameRegistry
from src.llm.client import LLMClient
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.store import LorebookStore
from src.memory.delta import MemoryStore
from src.commands.combat_resolver import CombatResolver
from src.commands.dice_resolver import DiceResolver
from src.commands.game_factory import GameFactory
from src.commands.game_lifecycle import GameLifecycle
from src.commands.progression_resolver import ProgressionResolver
from src.commands.prompt_composer import PromptComposer
from src.commands.puzzle_processor import PuzzleProcessor
from src.commands.round_helpers import (
    default_quick_actions_by_class,
    should_multi_step,
    validate_dice_constraint,
)
from src.commands.round_processor import RoundProcessor
from src.commands.state_update_applier import StateUpdateApplier
from src.commands.state_recap import (
    build_state_change_messages as _build_state_change_messages,
    snapshot_public_player_state as _snapshot_public_player_state,
)
from src.commands.swipe_generator import SwipeGenerator
from src.engine.language import DEFAULT_LANGUAGE

logger = logging.getLogger("trpg")


class GameHandler:
    """游戏流程处理器 —— 连接所有模块的粘合层。"""

    def __init__(
        self,
        registry: GameRegistry,
        llm_client: LLMClient,
        lorebook_matcher: KeywordMatcher,
        lorebook_store: LorebookStore | None = None,
        memory_store: MemoryStore | None = None,
        prompts_dir: Path | None = None,
        rules_dir: Path | None = None,
        worlds_dir: Path | None = None,
        narrative_max_tokens: int = 1024,
        summary_max_tokens: int = 400,
        brief_max_tokens: int = 300,
        analysis_max_tokens: int = 512,
    ):
        self.registry = registry
        self._combat = CombatResolver()
        self._dice = DiceResolver()
        self._puzzles = PuzzleProcessor()
        self.llm_client = llm_client
        self.matcher = lorebook_matcher
        self.lorebook_store = lorebook_store
        self.memory_store = memory_store
        self.prompts_dir = prompts_dir or Path(".")
        self.rules_dir = rules_dir or Path(".")
        self._prompt = PromptComposer(self.prompts_dir, self.rules_dir, self.memory_store)
        self.worlds_dir = worlds_dir or (Path(__file__).parent.parent.parent / "templates" / "worlds")
        self._factory = GameFactory(self.registry, self.lorebook_store, self.worlds_dir)
        self._state_applier = StateUpdateApplier(self.rules_dir, self.worlds_dir, self._load_world_template)
        self._progression = ProgressionResolver(self.rules_dir, self.worlds_dir)
        self._last_matcher_world_id: str | None = None
        self._round_processor = RoundProcessor(
            self.registry,
            self.llm_client,
            self.matcher,
            self.lorebook_store,
            self.memory_store,
            self._prompt,
            self._dice,
            self._combat,
            self._puzzles,
            self._state_applier,
            self._progression,
            self._load_world_template,
            self._ensure_matcher_for_world,
            narrative_max_tokens,
            summary_max_tokens,
            analysis_max_tokens,
        )
        self._swipe_generator = SwipeGenerator(
            self.llm_client,
            self.matcher,
            self._prompt,
            self._state_applier,
            self._load_world_template,
            self._ensure_matcher_for_world,
            narrative_max_tokens,
        )
        self._lifecycle = GameLifecycle(
            self.registry,
            self.llm_client,
            self._prompt,
            self._state_applier,
            self._ensure_matcher_for_world,
            self.create_game,
            self._load_world_template,
            narrative_max_tokens,
            brief_max_tokens,
        )
        self.narrative_max_tokens = narrative_max_tokens
        self.summary_max_tokens = summary_max_tokens
        self.brief_max_tokens = brief_max_tokens
        self.analysis_max_tokens = analysis_max_tokens

    def _ensure_matcher_for_world(self, world_id: str) -> None:
        """确保关键词匹配器已加载当前世界的条目。"""
        if world_id and world_id != self._last_matcher_world_id and self.lorebook_store:
            entries = self.lorebook_store.list_entries(world_id)
            self.matcher.build(entries)
            self._last_matcher_world_id = world_id

    # ---- 新建游戏 ----

    async def create_game(
        self, game_key: tuple, world_id: str, world_name: str,
        group_name: str, rule_id: str = "freeform_fantasy",
        seed_code: str = "", difficulty: str = "标准",
        language: str = DEFAULT_LANGUAGE,
    ) -> GameInstance:
        """兼容旧入口；实际逻辑已拆到 GameFactory。"""
        return await self._factory.create_game(
            game_key, world_id, world_name, group_name,
            rule_id=rule_id, seed_code=seed_code, difficulty=difficulty,
            language=language,
        )

    def _load_world_template(self, world_id: str) -> dict | None:
        """兼容旧内部调用；实际逻辑已拆到 GameFactory。"""
        return self._factory.load_world_template(world_id)

    async def _init_world_from_template(self, world_id: str, template: dict) -> None:
        """兼容旧内部调用；实际逻辑已拆到 GameFactory。"""
        await self._factory.init_world_from_template(world_id, template)

    # ---- 开始游戏 ----

    async def start_game(self, instance: GameInstance) -> str:
        """兼容旧入口；实际逻辑已拆到 GameLifecycle。"""
        return await self._lifecycle.start_game(instance)

    # ---- 智能续接 ----

    async def resume_game(self, instance: GameInstance) -> str:
        """兼容旧入口；实际逻辑已拆到 GameLifecycle。"""
        return await self._lifecycle.resume_game(instance)

    # ---- 重置游戏 ----

    async def reset_game(self, instance: GameInstance) -> GameInstance:
        """兼容旧入口；实际逻辑已拆到 GameLifecycle。"""
        return await self._lifecycle.reset_game(instance)

    async def restart_game(self, instance: GameInstance) -> GameInstance:
        """兼容旧入口；实际逻辑已拆到 GameLifecycle。"""
        return await self._lifecycle.restart_game(instance)

    # ---- 处理单轮 ----

    async def process_round(self, instance: GameInstance) -> tuple[str, dict | None]:
        """处理完整的一轮判定：context 拼接 → LLM 调用 → 解析 → 更新状态 → 播报。

        Returns: (narration: str, info_asymmetry: dict | None)
            info_asymmetry 格式: {"user_qq_xxx": "仅该玩家可见的消息", ...}
        """
        return await self._round_processor.process_round(instance)

    async def _process_round_impl(self, instance: GameInstance) -> tuple[str, dict | None]:
        """兼容旧内部调用；实际逻辑已拆到 RoundProcessor。"""
        return await self._round_processor.process_round_impl(instance)

    def _skill_growth_checks(self, instance: GameInstance, growth_skills: list[dict]) -> None:
        """兼容旧内部调用；实际逻辑已拆到 ProgressionResolver。"""
        self._progression.skill_growth_checks(instance, growth_skills)

    @staticmethod
    def _calc_xp_to_level(level: int) -> int:
        """兼容旧内部调用；实际逻辑已拆到 ProgressionResolver。"""
        return ProgressionResolver.calc_xp_to_level(level)

    def _try_level_up(self, instance: GameInstance, uid: str) -> list[str]:
        """兼容旧内部调用；实际逻辑已拆到 ProgressionResolver。"""
        return self._progression.try_level_up(instance, uid)

    def _apply_state_update(self, instance: GameInstance, update: dict) -> None:
        """兼容旧内部调用；实际逻辑已拆到 StateUpdateApplier。"""
        self._state_applier.apply_state_update(instance, update)

    def _tick_madness(self, instance: GameInstance) -> None:
        """兼容旧内部调用；实际逻辑已拆到 StateUpdateApplier。"""
        self._state_applier.tick_madness(instance)

    async def generate_swipe(self, instance: GameInstance, round_num: int) -> str | None:
        """兼容旧内部调用；实际逻辑已拆到 SwipeGenerator。"""
        return await self._swipe_generator.generate(instance, round_num)

    @staticmethod
    def _should_multi_step(instance: GameInstance, actions_text: str) -> bool:
        """兼容旧内部调用；实际逻辑已拆到 round_helpers。"""
        return should_multi_step(instance, actions_text)

    @staticmethod
    def _validate_dice_constraint(dice_block: str, narration: str) -> bool:
        """兼容旧内部调用；实际逻辑已拆到 round_helpers。"""
        return validate_dice_constraint(dice_block, narration)

    @staticmethod
    def _default_quick_actions_by_class(class_name: str) -> list[str]:
        """兼容旧内部调用；实际逻辑已拆到 round_helpers。"""
        return default_quick_actions_by_class(class_name)
