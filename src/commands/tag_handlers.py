"""GM 状态标签的分域入口与协议分类。"""

from __future__ import annotations

from src.commands.player_tag_handlers import parse_player_tag
from src.commands.world_tag_handlers import parse_action_tag, parse_loot_tag, parse_world_tag
from src.llm.protocol import KNOWN_PROTOCOL_TAGS

KNOWN_TAGS = KNOWN_PROTOCOL_TAGS

LIMITS_BY_COMBAT_MODEL = {
    "lethal_narrative": {"hp_max": 20, "hp_heal": 10, "gold_max": 200, "gold_loss": 50, "weapon": 12},
    "narrative": {"hp_max": 30, "hp_heal": 15, "gold_max": 300, "gold_loss": 80, "weapon": 15},
    "hp_based": {"hp_max": 50, "hp_heal": 20, "gold_max": 500, "gold_loss": 100, "weapon": 15},
}

PLAYER_TAGS = frozenset({
    "HP", "PAY", "GOLD", "USE", "EQUIP", "WEAPON", "XP", "SAN", "SAN_CHECK",
    "LUCK", "SKILL_GROWTH", "PUSH", "MANA", "REVIVE",
})
WORLD_TAGS = frozenset({
    "CONFIRMED", "MEMORY", "SCENE", "NPC", "DECISION", "QUEST", "PRIVATE",
})
LOOT_TAGS = frozenset({"LOOT", "KEY_ITEM"})
ACTION_TAGS = frozenset({"PUZZLE", "SPELL", "QUICK_ACTIONS", "COMBAT"})

__all__ = [
    "ACTION_TAGS",
    "KNOWN_TAGS",
    "LIMITS_BY_COMBAT_MODEL",
    "LOOT_TAGS",
    "PLAYER_TAGS",
    "WORLD_TAGS",
    "parse_action_tag",
    "parse_loot_tag",
    "parse_player_tag",
    "parse_world_tag",
]
