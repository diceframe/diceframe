"""游戏实例创建与世界模板初始化。"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any
from collections.abc import Callable

from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.engine.modules import game_config
from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.engine.world_template import load_world_template
from src.lorebook.bootstrap import ensure_world_from_template
from src.rules.loader import RuleBundleLoader
from src.rules.rule_system import RuleSystem
from src.engine.currency.validation import (
    declared_currency_display_label,
    validate_declared_currency_system,
)

logger = logging.getLogger("trpg")


_SEED_ADJECTIVES = [
    "brave", "dark", "golden", "silver", "crimson", "ancient", "crystal", "shadow",
    "storm", "frost", "ember", "iron", "silent", "wild", "mystic", "hollow",
    "azure", "scarlet", "jade", "onyx", "amber", "pearl", "obsidian", "celestial",
    "wandering", "blazing", "whispering", "thundering", "forgotten", "eternal",
    "frozen", "burning", "shimmering", "twilight", "dawn", "dusk", "hidden",
    "lone", "sacred", "fallen", "restless", "enchanted", "arcane", "dire",
]
_SEED_NOUNS = [
    "dragon", "sword", "phoenix", "wolf", "griffin", "knight", "wizard", "throne",
    "crown", "forest", "mountain", "ocean", "star", "moon", "sun", "river",
    "tower", "castle", "temple", "gate", "dream", "legend", "journey", "quest",
    "spirit", "flame", "blade", "saga", "fate", "destiny", "relic", "riddle",
    "echo", "shadow", "harbinger", "oracle", "seer", "wanderer", "prophecy",
    "guardian", "sentinel", "serpent", "raven", "lotus", "cipher",
]


def generate_seed_code() -> str:
    adj = random.choice(_SEED_ADJECTIVES)
    noun = random.choice(_SEED_NOUNS)
    num = random.randint(100, 999)
    return f"{adj}-{noun}-{num}"


class GameFactory:
    """负责创建 GameInstance，并按世界模板初始化世界书。"""

    def __init__(
        self,
        registry: GameRegistry,
        lorebook_store: Any,
        worlds_dir: Path,
        rules_dir: Path | None = None,
    ):
        self.registry = registry
        self.lorebook_store = lorebook_store
        self.worlds_dir = worlds_dir
        self.rules_dir = rules_dir
        self._defaults_provider: Callable[[], dict] = lambda: {}

    def set_defaults_provider(self, provider: Callable[[], dict]) -> None:
        self._defaults_provider = provider

    async def create_game(
        self, game_key: tuple, world_id: str, world_name: str,
        group_name: str, rule_id: str = "",
        seed_code: str = "", difficulty: str = "标准",
        language: str = DEFAULT_LANGUAGE,
        fresh_instance: bool = False,
    ) -> GameInstance:
        is_new = fresh_instance or self.registry.get(game_key) is None
        instance = GameInstance(game_key=game_key) if fresh_instance else self.registry.get_or_create(game_key)
        world_data = self.load_world_template(world_id, language)
        if world_data:
            rule_id = rule_id or world_data.get("default_rule", "")
        rule_id = rule_id or "freeform_fantasy"
        currency_label = ""
        currency_system: dict[str, Any] | None = None
        if self.rules_dir:
            rule = self._load_rule(rule_id, language)
            if rule is not None:
                currency_label = rule.currency
                currency_system = rule.currency_system
        if isinstance(world_data, dict):
            declared = world_data.get("currency_system")
            if declared is not None:
                validate_declared_currency_system(declared)
                currency_system = declared
                currency_label = declared_currency_display_label(
                    declared, currency_label,
                )
            if isinstance(world_data.get("currency"), str) and world_data["currency"].strip():
                currency_label = world_data["currency"].strip()
        if not is_new:
            currency_label = game_config.currency_label(instance)
            currency_system = game_config.currency_system(instance)
        async with instance._lock:
            instance.configure_game(
                world_id=world_id,
                rule_id=rule_id,
                world_name=world_name,
                group_name=group_name,
                state=GameState.WAITING,
                seed_code=seed_code or generate_seed_code(),
                difficulty=difficulty,
                language=normalize_language(language),
                currency_label=currency_label,
                currency_system=currency_system,
            )
            if is_new:
                from src.engine.game_preferences import apply
                from src.game_preferences import validate_preferences
                apply(instance, validate_preferences(self._defaults_provider()))

        if world_data:
            # Persist the canonical starter lore. Localized display text is
            # materialized per game when matching/building prompts.
            canonical_world = self.load_world_template(world_id) or world_data
            await self.init_world_from_template(world_id, canonical_world)

        return instance

    def _load_rule(self, rule_id: str, language: str) -> RuleSystem | None:
        if self.rules_dir is None:
            return None
        core = self.rules_dir / f"{rule_id}.json"
        if core.exists():
            return RuleSystem(RuleBundleLoader().load_rule(self.rules_dir, rule_id, language))
        localized = RuleSystem.path_for(self.rules_dir, rule_id, language)
        return RuleSystem.load(localized) if localized.exists() else None

    def load_world_template(self, world_id: str, language: str = "") -> dict | None:
        """加载世界模板 JSON 文件。"""
        return load_world_template(self.worlds_dir, world_id, language)

    async def init_world_from_template(self, world_id: str, template: dict) -> None:
        """从模板初始化世界书条目。"""
        ensure_world_from_template(self.lorebook_store, world_id, template)
