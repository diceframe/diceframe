"""回合行动输入整理与前置处理。"""

from __future__ import annotations

import logging
from typing import Any

from src.engine.constants import ACTION_KEYWORDS
from src.engine.dice import roll as dice_roll
from src.engine.game_instance import GameInstance

logger = logging.getLogger("trpg")


def ensure_round_managers(instance: GameInstance) -> None:
    """确保回合处理需要的运行期管理器已存在。"""
    if not hasattr(instance, "plot_tracker") or instance.plot_tracker is None:
        from src.engine.plot_tracker import PlotTracker
        instance.plot_tracker = PlotTracker()

    if not hasattr(instance, "puzzle_manager") or instance.puzzle_manager is None:
        from src.engine.puzzle import PuzzleManager
        instance.puzzle_manager = PuzzleManager()


def format_action_line(instance: GameInstance, action: dict) -> str:
    """把单个行动格式化成给 GM/LLM 看的归因文本。"""
    name = instance.players[action['user_id']].get('character_name', action['user_id'])
    text = action.get('text', '')
    parts = []
    selected_attr = action.get('selected_attribute', '')
    selected_skill = action.get('selected_skill', '')
    if selected_attr or selected_skill:
        parts.append(f"检定:{selected_attr or '?'}" + (f"/{selected_skill}" if selected_skill else ""))
    target = action.get('target_text', '')
    if target:
        parts.append(f"目标:{target}")
    tag = f" [{' '.join(parts)}]" if parts else ""
    return f"【{name}】{text}{tag}"


def collect_actions_text(instance: GameInstance) -> str:
    """收集本轮所有行动文本（含角色名归属 + 结构化归因标注）。"""
    player_lines = [
        format_action_line(instance, action)
        for action in instance.action_queue
        if action.get("user_id") in instance.players
    ]
    system_lines = [
        action.get("text", "")
        for action in instance.action_queue
        if action.get("user_id") == "system" and action.get("text")
    ]
    actions_text = "\n".join(player_lines + system_lines)
    if not actions_text:
        actions_text = "; ".join(action.get("text", "") for action in instance.action_queue)
    return actions_text


def initialize_puzzles_from_lorebook(instance: GameInstance, lorebook_store: Any) -> None:
    """从世界书初始化谜题（仅新增未注册的谜题）。"""
    if not instance.world_id or not lorebook_store or not instance.puzzle_manager:
        return

    all_entries = lorebook_store.list_entries(instance.world_id)
    for entry in all_entries:
        if entry.get("type") != "puzzle":
            continue
        puzzle_id = entry.get("id", "")
        if instance.puzzle_manager.get_puzzle(puzzle_id):
            continue
        from src.engine.puzzle import create_puzzle_from_lorebook
        puzzle = create_puzzle_from_lorebook(entry)
        if puzzle:
            instance.puzzle_manager.add_puzzle(puzzle)
            logger.info("谜题初始化: %s (%s)", puzzle.name, puzzle_id)


def build_dice_constraint_block(
    instance: GameInstance,
    actions_text: str,
    rule: Any,
    dice_system: str,
    dice_resolver: Any,
) -> str:
    """按行动文本和规则生成系统骰子约束块；不需要检定时返回空字符串。"""
    if dice_system == "none":
        return ""
    already_rolled = "【系统检定·必须遵循】" in actions_text
    need_check = not already_rolled and any(keyword in actions_text for keyword in ACTION_KEYWORDS)
    if not need_check:
        return ""

    if rule:
        return dice_resolver.roll_rule_check(instance, actions_text, rule)
    if dice_system == "d100":
        return dice_resolver.roll_d100_check(instance, actions_text)

    d20_result = dice_roll("d20")
    if d20_result.natural == 20:
        verdict = "大成功 (d20=20)"
    elif d20_result.natural == 1:
        verdict = "大失败 (d20=1)"
    else:
        verdict = f"掷出 d20={d20_result.natural}"
    return (
        f"\n【系统检定·必须遵循】\n"
        f"检定结果: {verdict}\n"
        f"要求: GM叙事必须严格体现此掷骰结果。大成功=额外的叙事奖励，大失败=严重后果，"
        f"普通值由GM根据DC判定成败\n"
    )
