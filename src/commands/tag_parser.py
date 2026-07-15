"""LLM 标签 / JSON 输出解析工具。

负责把 GM 回复中的 `---` 后状态标签解析为结构化 state_update、plot_update、
memory_delta 等数据；同时提供 JSON 回退解析与标签摘要。

parse_tag_state 按标签类型分派到 tag_handlers 中的 typed handler：
玩家字段、世界/剧情、战利品、行动/谜题。
"""

from __future__ import annotations

from src.commands.tag_handlers import (
    ACTION_TAGS,
    KNOWN_TAGS,
    LIMITS_BY_COMBAT_MODEL,
    LOOT_TAGS,
    PLAYER_TAGS,
    WORLD_TAGS,
    parse_action_tag,
    parse_loot_tag,
    parse_player_tag,
    parse_world_tag,
)


def _new_result() -> dict:
    return {
        "state_update": {"players": {}, "npcs": {}, "scene_change": "", "loot": []},
        "memory_delta": {"add": [], "update": [], "forget": []},
        "info_asymmetry": {},
        "plot_update": {"quests": [], "relations": [], "decisions": []},
        "xp_rewards": {},  # uid -> xp_amount (LLM 可选标签)
        "growth_skills": [],  # [{uid, skill}] 用于 CoC 技能成长检定
    }


def _extract_tag_lines(text: str, result: dict) -> list[str]:
    """切出标签行；只有 `---` 之后的内容会被当作可执行标签。"""
    if "---" in text:
        parts = text.split("---", 1)
        tag_block = parts[1].strip()
        if tag_block.upper().startswith("NONE"):
            return []
        return tag_block.split("\n")
    result.setdefault("_missing_tag_separator", True)
    return []


def _split_tag_line(line: str) -> tuple[str | None, str | None]:
    """解析单行 'TAG: value'；无效或未知标签返回 (None, None)。"""
    line = line.strip()
    if not line or ":" not in line:
        return None, None
    tag, _, value = line.partition(":")
    tag, value = tag.strip().upper(), value.strip()
    if tag not in KNOWN_TAGS or not value:
        return None, None
    return tag, value


def parse_tag_state(text: str, combat_model: str = "hp_based") -> dict:
    """从 LLM 输出的标签格式中提取结构化状态。
    根据规则体系（combat_model）动态调整数值上限。
    """
    limits = LIMITS_BY_COMBAT_MODEL.get(combat_model, LIMITS_BY_COMBAT_MODEL["hp_based"])
    result = _new_result()
    if not text:
        return result

    for line in _extract_tag_lines(text, result):
        tag, value = _split_tag_line(line)
        if tag is None:
            continue
        if tag in PLAYER_TAGS:
            parse_player_tag(tag, value, result, limits)
        elif tag in WORLD_TAGS:
            parse_world_tag(tag, value, result)
        elif tag in LOOT_TAGS:
            parse_loot_tag(tag, value, result)
        elif tag in ACTION_TAGS:
            parse_action_tag(tag, value, result)

    return result
