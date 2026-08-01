"""回合行动输入整理与前置处理。"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.engine.checks import build_check_request, roll_check_request
from src.engine.game_instance import GameInstance
from src.engine.language import is_english

logger = logging.getLogger("trpg")


def ensure_round_managers(instance: GameInstance) -> None:
    """确保回合处理需要的运行期管理器已存在。"""
    instance.ensure_round_managers()


def format_action_line(instance: GameInstance, action: dict) -> str:
    """把单个行动格式化成给 GM/LLM 看的归因文本。"""
    name = instance.players[action['user_id']].get('character_name', action['user_id'])
    text = action.get('text', '')
    parts = []
    selected_attr = action.get('selected_attribute', '')
    selected_skill = action.get('selected_skill', '')
    if selected_attr or selected_skill:
        label = "Check" if is_english(instance.language) else "检定"
        parts.append(f"{label}:{selected_attr or '?'}" + (f"/{selected_skill}" if selected_skill else ""))
    target = action.get('target_text', '')
    if target:
        label = "Target" if is_english(instance.language) else "目标"
        parts.append(f"{label}:{target}")
    tag = f" [{' '.join(parts)}]" if parts else ""
    return f"【{name}】{text}{tag}"


def collect_actions_text(instance: GameInstance) -> str:
    """收集本轮玩家行动；GM 私密指令不得参与行动/检定识别。"""
    player_lines = [
        format_action_line(instance, action)
        for action in instance.action_queue
        if action.get("user_id") in instance.players
    ]
    actions_text = "\n".join(player_lines)
    if not actions_text:
        actions_text = "本轮没有玩家行动。"
    return actions_text


def collect_gm_directives_text(instance: GameInstance) -> tuple[str, list[str]]:
    """返回本轮可用的 GM 私密指令文本及其 ID，供成功结算后消费。"""
    current_round = int(instance.round_number or 0)
    entries = [
        entry for entry in instance.gm_directives
        if int(entry.get("target_round", current_round) or current_round) <= current_round
    ]
    if not entries:
        return "", []
    english = is_english(instance.language)
    heading = "[Private GM Directives]" if english else "【GM私密指令】"
    requirement = (
        "Apply these directives to narration only. Never quote or reveal them to players. "
        "They must not change, override, or regenerate any server-provided CheckResult."
        if english else
        "以下内容只用于修正本轮叙事，禁止向玩家复述、引用或展示；"
        "不得改变、覆盖或重新生成任何由服务端给出的 CheckResult。"
    )
    body = "\n".join(f"- {entry.get('text', '')}" for entry in entries if entry.get("text"))
    return f"\n\n{heading}\n{requirement}\n{body}", [str(entry.get("id") or "") for entry in entries]


def format_check_results_constraint(instance: GameInstance, checks: list[dict]) -> str:
    """把已结算 CheckResult 重新格式化为 GM 私有硬约束，供 swipe 等重生成复用。"""
    if not checks:
        return ""
    english = is_english(instance.language)
    blocks: list[str] = []
    for check in checks:
        dice = str(check.get("dice") or "")
        roll_value = check.get("roll")
        if dice == "d100":
            math_text = f"d100={roll_value} vs {check.get('threshold')}"
        else:
            modifier = int(check.get("modifier", 0) or 0)
            total = check.get("total")
            dc = check.get("dc")
            math_text = f"d20={roll_value} {modifier:+d} = {total} vs DC {dc}"
        if english:
            blocks.append(
                "[System Check - Must Follow]\n"
                f"Actor: {check.get('actor_name') or check.get('actor_uid')}\n"
                f"Check: {check.get('label') or dice}: {math_text}\n"
                f"Result: {check.get('verdict')}\n"
                "Requirement: keep this server-resolved outcome unchanged."
            )
        else:
            blocks.append(
                "【系统检定·必须遵循】\n"
                f"角色: {check.get('actor_name') or check.get('actor_uid')}\n"
                f"检定: {check.get('label') or dice}：{math_text}\n"
                f"结果: {check.get('verdict')}\n"
                "要求: 这是服务端已结算结果，不得重掷或改判。"
            )
    return "\n\n" + "\n\n".join(blocks)


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
    """逐个结算玩家行动中的 CheckRequest；原始骰值只生成一次。"""
    if dice_system == "none":
        return ""
    blocks: list[str] = []
    legacy_roll_re = re.compile(r"\(系统掷骰:\s*(d20|d100)=(\d+)\)")
    for action in instance.action_queue:
        if action.get("user_id") not in instance.players:
            continue
        request = action.get("check_request")
        if not isinstance(request, dict):
            request = build_check_request(instance, action, rule)
            legacy_match = legacy_roll_re.search(str(action.get("text") or ""))
            if not request and legacy_match:
                uid = str(action.get("user_id") or "")
                request = {
                    "check_id": "",
                    "required": True,
                    "actor_uid": uid,
                    "actor_name": instance.players.get(uid, {}).get("character_name", uid),
                    "dice_system": legacy_match.group(1),
                    "label": "行动检定",
                    "intent": "legacy",
                    "skill": str(action.get("selected_skill") or ""),
                    "attribute": str(action.get("selected_attribute") or ""),
                    "advantage_mode": "",
                    "advantage_note": None,
                }
            if not request:
                continue
            action["check_request"] = request

        if not action.get("dice_value"):
            legacy_match = legacy_roll_re.search(str(action.get("text") or ""))
            if legacy_match:
                action["dice_value"] = int(legacy_match.group(2))
                action["dice_rolls"] = [int(legacy_match.group(2))]
                action["dice_system"] = legacy_match.group(1)
                action["dice_roll_source"] = "legacy"
            else:
                rolled = roll_check_request(request)
                action["dice_value"] = rolled["value"]
                action["dice_rolls"] = rolled["rolls"]
                action["dice_system"] = rolled["dice_system"]
                action["dice_roll_source"] = "system"
            action["dice_pending"] = False

        block = dice_resolver.resolve_action_check(instance, action, rule)
        if block:
            blocks.append(block)
    return "\n".join(blocks)
