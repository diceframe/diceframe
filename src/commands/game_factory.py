"""游戏实例创建与世界模板初始化。"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.engine.world_template import load_world_template

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

    def __init__(self, registry: GameRegistry, lorebook_store: Any, worlds_dir: Path):
        self.registry = registry
        self.lorebook_store = lorebook_store
        self.worlds_dir = worlds_dir

    async def create_game(
        self, game_key: tuple, world_id: str, world_name: str,
        group_name: str, rule_id: str = "freeform_fantasy",
        seed_code: str = "", difficulty: str = "标准",
        language: str = DEFAULT_LANGUAGE,
    ) -> GameInstance:
        instance = self.registry.get_or_create(game_key)
        async with instance._lock:
            instance.world_id = world_id
            instance.world_name = world_name
            instance.group_name = group_name
            instance.state = GameState.WAITING
            instance.seed_code = seed_code or generate_seed_code()
            instance.difficulty = difficulty
            instance.language = normalize_language(language)

        world_data = self.load_world_template(world_id)
        if world_data:
            rule_id = world_data.get("default_rule", rule_id)
            await self.init_world_from_template(world_id, world_data)

        return instance

    def load_world_template(self, world_id: str) -> dict | None:
        """加载世界模板 JSON 文件。"""
        try:
            return load_world_template(self.worlds_dir, world_id)
        except Exception:
            logger.exception("世界模板加载失败: %s", world_id)
            return None

    async def init_world_from_template(self, world_id: str, template: dict) -> None:
        """从模板初始化世界书条目。"""
        if not self.lorebook_store:
            return
        world = self.lorebook_store.get_world(world_id)
        if not world:
            self.lorebook_store.create_world(
                world_id,
                template.get("world_name", world_id),
                description=template.get("description", ""),
            )
        for entry in template.get("starter_lorebook", []):
            entry["world_id"] = world_id
            existing = self.lorebook_store.get_entry(entry["id"])
            if existing and existing.get("world_id") == world_id:
                continue  # 同世界已存在，跳过
            self.lorebook_store.add_entry(entry)
