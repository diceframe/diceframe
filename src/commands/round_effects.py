"""回合解析结果的附加效果应用。"""

from __future__ import annotations

import logging
from typing import Any

from src.commands.round_helpers import default_quick_actions_by_class
from src.commands.state_recap import build_state_change_messages
from src.engine.character_utils import revive_character
from src.engine.game_instance import GameInstance
from src.engine.health import record_health_event
from src.engine.puzzle import PuzzleState

logger = logging.getLogger("trpg")


def apply_confirmed_items(instance: GameInstance, data: dict) -> None:
    confirmed = data.get("confirmed", [])
    if not confirmed:
        return
    existing = set(instance.confirmed_items)
    for item in confirmed:
        if item not in existing:
            instance.confirmed_items.append(item)
            existing.add(item)
    if len(instance.confirmed_items) > 50:
        del instance.confirmed_items[:len(instance.confirmed_items) - 50]


def apply_puzzle_updates(instance: GameInstance, data: dict) -> None:
    puzzle_updates = data.get("puzzle_updates", {})
    if not puzzle_updates or not instance.puzzle_manager:
        return
    for puzzle_id, new_state in puzzle_updates.items():
        puzzle = instance.puzzle_manager.get_puzzle(puzzle_id)
        if not puzzle:
            continue
        if new_state == "solved":
            puzzle.solve()
        elif new_state == "failed":
            puzzle.state = PuzzleState.FAILED
        elif new_state == "hint_given":
            puzzle.hint_given = True
        logger.info("谜题状态更新: %s → %s", puzzle_id, new_state)


def apply_combat_command(instance: GameInstance, data: dict) -> None:
    combat_cmd = data.get("combat_command", "")
    if combat_cmd == "end" and instance.combat_state == "active":
        instance.combat_state = "none"
        instance.combat_active = False
        instance.initiative_order.clear()
        instance.initiative_current = 0
        logger.info("战斗结束 (round=%d)", instance.round_number)


def apply_revive_commands(instance: GameInstance, data: dict) -> None:
    revive_commands = data.get("revive_commands", [])
    for cmd in revive_commands:
        uid = cmd["uid"]
        method = cmd.get("method", "法术")
        if uid not in instance.players:
            continue
        character_sheet = instance.get_character_sheet(uid)
        if not revive_character(character_sheet, method):
            continue
        instance.set_character_sheet(uid, character_sheet)
        logger.info("复活: %s method=%s hp=%d",
                    instance.players[uid].get("character_name", uid),
                    method, character_sheet["hp"])


def apply_growth_rewards(instance: GameInstance, data: dict, response: Any, rule: Any, progression: Any) -> None:
    growth_system = rule.growth_system if rule else "xp_level"
    if growth_system == "skill_improvement":
        progression.skill_growth_checks(instance, data.get("growth_skills", []))
        return

    xp_rewards: dict[str, int] = data.get("xp_rewards", {})
    level_up_msgs: list[str] = []
    for uid in instance.alive_players:
        bonus_xp = xp_rewards.get(uid, 0)
        total_xp = 10 + bonus_xp
        character_sheet = instance.get_character_sheet(uid)
        character_sheet["xp"] = character_sheet.get("xp", 0) + total_xp
        instance.set_character_sheet(uid, character_sheet)
        up_msgs = progression.try_level_up(instance, uid)
        level_up_msgs.extend(up_msgs)
    if level_up_msgs:
        for msg in level_up_msgs:
            logger.info("%s %s", instance.game_key, msg)
        addon = "\n\n" + "\n".join(level_up_msgs)
        response.narration = (response.narration or "") + addon


def update_quick_actions(instance: GameInstance, data: dict) -> None:
    quick_actions = data.get("quick_actions", [])
    if not quick_actions:
        first_uid = next(iter(instance.alive_players), "")
        class_name = instance.get_character_sheet(first_uid).get("class", "")
        quick_actions = default_quick_actions_by_class(class_name)
    instance.quick_actions = quick_actions

async def apply_memory_delta(instance: GameInstance, response: Any, memory_store: Any) -> None:
    if response.memory_delta and memory_store:
        try:
            await memory_store.apply_delta(
                str(instance.game_key), response.memory_delta, instance.round_number,
            )
        except Exception:
            logger.exception("记忆写入失败 (round=%d)", instance.round_number)
            record_health_event(
                instance,
                component="memory",
                code="MEMORY_WRITE_FAILED",
                severity="warning",
                title="记忆写入失败",
                message="本轮 memory_delta 未能写入长期记忆。",
                impact="长团连续性可能下降，AI 之后可能忘记本轮关键事实。",
                fallback="skip_memory_delta",
                repair_hint="检查 memory 数据库、embedding 配置或手动记录关键事实。",
            )


def apply_plot_update(instance: GameInstance, response: Any) -> None:
    if response.plot_update and instance.plot_tracker:
        try:
            changes = instance.plot_tracker.apply_update(
                response.plot_update, instance.round_number,
            )
            if changes:
                logger.info("剧情更新: round=%d, changes=%s", instance.round_number, changes)
        except Exception:
            logger.exception("剧情更新异常，已跳过 (round=%d)", instance.round_number)


def store_private_messages(instance: GameInstance, response: Any) -> None:
    info_asym = response.info_asymmetry or {}
    for uid, msg in info_asym.items():
        instance.private_log.setdefault(uid, []).append({
            "round": instance.round_number,
            "text": msg,
        })


def append_state_change_messages(
    instance: GameInstance,
    response: Any,
    public_state_before: dict[str, dict],
    data: dict,
) -> list[str]:
    state_change_msgs = build_state_change_messages(instance, public_state_before, data)
    if not state_change_msgs:
        return []
    addon = "\n\n" + "\n".join(state_change_msgs)
    response.narration = (response.narration or "") + addon
    for msg in state_change_msgs:
        logger.info("%s %s", instance.game_key, msg)
    return state_change_msgs
