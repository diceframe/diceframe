"""Turn-state / action-queue helpers for :class:`GameInstance`.

本模块处理：

- ready / waiting 投影与多人协调状态（``multiplayer_status`` 等 query）；
- 行动队列细节（追加 / 替换 / 骰值附着）；
- 回合 readiness 查询与推进判断；
- **持锁的**回合状态 mutation detail（``*_locked``）。

边界：

- 本模块**不获取任何锁**。所有 ``*_locked`` 函数的前置条件是调用方已经持有
  ``GameInstance`` 的 ``_lock``（及所需 authority gate）；锁仍由 GameInstance
  的 public / authority 方法持有。
- Human gate 语义不变：``human_actions_ready()`` 只回答"真人一侧是否交齐"，
  它不是"AI 是否可以生成行动"；AI 的最终 fill gate 属于
  ``src/commands/ai_player.py``。
- 不 runtime import ``src.engine.game_instance``（仅 ``TYPE_CHECKING`` 类型引用），
  依赖方向保持 ``game_instance → turn_state``。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.engine.contracts import ActionRecord
from src.engine import progression
from src.engine.game_state import GameState
from src.engine.player_control import (
    ai_controlled_players,
    away_control_policy,
    unclaimed_players,
)
from src.engine.round_snapshots import snapshot_players

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance

logger = logging.getLogger("trpg")


# ---------- readiness / 协调状态 query ----------------------


def all_alive_ready(instance: GameInstance) -> bool:
    """多人模式下，所有未暂离的存活真人席位都提交行动后才自动推进。

    AI 托管与未认领的席位不参与等待：它们不是"还没交行动的真人"。
    """
    active = instance.active_human_players
    if not active:
        return False
    return active.issubset(instance.ready_players)


def human_actions_ready(instance: GameInstance) -> bool:
    """真人一侧是否已经交齐，可以轮到服务器 AI 补行动。

    这是 AI 托管席位补行动的唯一闸门（``src/commands/ai_player.py``）：AI
    只在真人行动齐了之后出手，绝不与尚未提交的真人并行。没有真人席位、
    还没有人提交、或真人行动仍在等掷骰时都是 ``False``——"AI 不该现在行动"
    与"这一轮不能推进"是两件事，因此 ``should_advance()`` 的语义保持不变。
    """
    if instance.has_pending_dice():
        return False
    active = instance.active_human_players
    if not active:
        return False
    return active.issubset(instance.ready_players)


def multiplayer_status(instance: GameInstance) -> dict:
    """返回多人协调所需的轻量状态。

    ready / waiting 只统计真人席位（``active_human_players``），因此 AI
    托管与未认领的席位不会出现在 ``waiting_players`` 里、也不会阻塞推进；
    它们分别在 ``ai_players`` / ``unclaimed_players`` 中列出，说明"还差谁"
    之外的那部分席位由谁负责。``active_count`` 仍是"在场存活"席位总数。
    """
    alive = instance.alive_players
    active = instance.active_alive_players
    human_active = instance.active_human_players
    ready = human_active.intersection(instance.ready_players)
    waiting = human_active.difference(instance.ready_players)
    away = alive.intersection(instance.away_players)
    ai_hosted = ai_controlled_players(instance)
    unclaimed = unclaimed_players(instance)

    def player_label(uid: str) -> str:
        return instance.players.get(uid, {}).get("character_name") or uid

    return {
        "state": instance.state.value,
        "round_number": instance.round_number,
        "solo_mode": instance.solo_mode,
        "player_count": len(instance.players),
        "max_players": instance.max_players,
        "ready_count": len(ready),
        "alive_count": len(alive),
        "active_count": len(active),
        "away_count": len(away),
        "ready_players": [
            {"user_id": uid, "character_name": player_label(uid)}
            for uid in sorted(ready)
        ],
        "waiting_players": [
            {"user_id": uid, "character_name": player_label(uid)}
            for uid in sorted(waiting)
        ],
        "away_players": [
            {"user_id": uid, "character_name": player_label(uid)}
            for uid in sorted(away)
        ],
        "ai_players": [
            {"user_id": uid, "character_name": player_label(uid)}
            for uid in ai_hosted
        ],
        "unclaimed_players": [
            {"user_id": uid, "character_name": player_label(uid)}
            for uid in unclaimed
        ],
        "ai_count": len(ai_hosted),
        "unclaimed_count": len(unclaimed),
        "can_accept_actions": instance.can_accept_actions(),
        "can_advance": instance.can_accept_actions() and bool(instance.action_queue),
        "action_count": len(instance.action_queue),
        "submitted_actions": [
            {
                "user_id": a.get("user_id", ""),
                "character_name": player_label(a.get("user_id", "")),
                "text": a.get("text", ""),
                "revision_count": int(a.get("revision_count", 1) or 1),
                "dice_pending": bool(a.get("dice_pending")),
                "dice_system": str(a.get("dice_system", "") or ""),
                "dice_roll_source": str(a.get("dice_roll_source", "") or ""),
                **({"check_request": a.get("check_request")} if a.get("check_request") else {}),
            }
            for a in instance.action_queue
            if a.get("user_id") in instance.players
        ],
        "pending_action_count": len(instance.pending_actions),
        "gm_uid": instance.gm_uid,
        "player_access_open": instance.player_access_open,
        "away_control_policy": away_control_policy(instance),
    }


def should_advance(instance: GameInstance) -> bool:
    """任一满足即推进：所有存活玩家已就绪，或单人模式下任一玩家已行动。"""
    if instance.has_pending_dice():
        return False
    if instance.solo_mode and instance.action_queue:
        return True
    return instance.all_alive_ready()


def has_action_from_source(
    instance: GameInstance, user_id: str, round_number: int, source: str,
) -> bool:
    """这一轮该席位是否已有一条来自 ``source`` 的行动（持锁与只读都安全）。"""
    return any(
        str(action.get("user_id") or "") == user_id
        and isinstance(action.get("metadata"), Mapping)
        and str(action["metadata"].get("source") or "") == source
        and int(action["metadata"].get("generated_for_round", -1) or -1) == round_number
        for action in instance.action_queue
    )


def ai_player_action_stale_reason(
    instance: GameInstance,
    user_id: str,
    *,
    expected_run_id: str,
    expected_round_number: int,
    expected_control_revision: int,
) -> str:
    """Why an in-flight hosted-seat result must be discarded, else ``""``.

    Owned by the aggregate so there is exactly one definition of "this result
    still belongs to the seat it was produced for"; callers must invoke it
    inside the same boundary as the write it guards.
    """
    from src.engine.action_gate import AI_SEAT_STALE_POLICY, GateRequest, SOURCE_AI_SEAT, evaluate

    return evaluate(
        instance,
        GateRequest(
            actor_uid=user_id,
            source=SOURCE_AI_SEAT,
            expected_run_id=expected_run_id,
            expected_round_number=expected_round_number,
            expected_control_revision=expected_control_revision,
        ),
        AI_SEAT_STALE_POLICY,
    )


def has_pending_dice(instance: GameInstance, user_id: str | None = None) -> bool:
    return any(
        action.get("dice_pending")
        and (user_id is None or action.get("user_id") == user_id)
        for action in instance.action_queue
    )


def pending_dice_actions(
    instance: GameInstance, user_id: str | None = None,
) -> list[dict]:
    return [
        action for action in instance.action_queue
        if action.get("dice_pending")
        and (user_id is None or action.get("user_id") == user_id)
    ]


# ---------- 持锁 mutation detail（调用方已持锁）--------------


def add_action_locked(
    instance: GameInstance,
    user_id: str,
    action_text: str,
    *,
    selected_attribute: str = "",
    selected_skill: str = "",
    target_text: str = "",
    source: str = "",
    dice_pending: bool = False,
    dice_system: str = "",
    check_request: dict | None = None,
    count_revision: bool = True,
    action_metadata: dict | None = None,
    defer_out_of_phase: bool = True,
) -> bool:
    """``add_action`` 的持锁实现（调用方必须已持有写权限与 ``_lock``）。

    单独抽出来是为了让需要「先复核再写入」的调用方能在**同一个** boundary
    内完成两件事：自己在锁内复核，再调用这里追加，而不是写两层加锁。
    """
    from src.engine.modules import session_stats

    session_stats.require_writable(instance)
    if user_id in instance.players:
        cs = instance.get_character_sheet(user_id)
        if cs.get("deceased"):
            return False  # 死亡玩家不能行动
        instance.away_players.discard(user_id)
    action_entry: ActionRecord = {
        "user_id": user_id, "text": action_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "selected_attribute": selected_attribute,
        "selected_skill": selected_skill,
        "target_text": target_text,
        "source": source,
    }
    if action_metadata:
        action_entry["metadata"] = dict(action_metadata)
    if dice_pending:
        action_entry["dice_pending"] = True
        action_entry["dice_system"] = dice_system or "d20"
    if check_request:
        action_entry["check_request"] = dict(check_request)
    if not instance.can_accept_actions():
        if not defer_out_of_phase:
            return False
        instance.pending_actions.append(action_entry)
        return False
    # 切换行动时替换同玩家的旧条目（solo 与多人一致）：
    # 避免 solo 模式反复追加堆积多条行动、触发 3 条上限，
    # 也让未掷骰的旧检定随替换作废，不再卡住掷骰。
    existing_index = next(
        (index for index, action in enumerate(instance.action_queue)
         if action.get("user_id") == user_id),
        None,
    )
    if existing_index is not None:
        existing = instance.action_queue[existing_index]
        old_roll = next(
            (line for line in str(existing.get("text", "")).splitlines()
             if line.startswith("(系统掷骰:") and line.endswith(")")),
            "",
        )
        if old_roll:
            clean_text = "\n".join(
                line for line in str(action_text).splitlines()
                if not (line.startswith("(系统掷骰:") and line.endswith(")"))
            ).rstrip()
            action_entry["text"] = f"{clean_text}\n{old_roll}"
            action_entry["dice_pending"] = False
            action_entry["dice_system"] = existing.get("dice_system", "")
            action_entry["dice_roll_source"] = existing.get("dice_roll_source", "")
            action_entry["dice_value"] = existing.get("dice_value")
            action_entry["dice_rolls"] = list(existing.get("dice_rolls") or [])
            action_entry["check_request"] = existing.get("check_request")
        old_revision = int(existing.get("revision_count", 1) or 1)
        action_entry["revision_count"] = old_revision + 1 if count_revision else old_revision
        instance.action_queue[existing_index] = action_entry
    else:
        action_entry["revision_count"] = 1
        instance.action_queue.append(action_entry)
    instance.ready_players.add(user_id)
    session_stats.touch(instance)
    return True


def start_round_locked(instance: GameInstance) -> None:
    """``start_round`` 的持锁实现（调用方必须已持有 ``_lock``）。"""
    from src.engine.modules import session_stats

    session_stats.require_writable(instance)
    progression.open_next_round(instance)
    current = str(instance.round_number)
    instance.death_save_outcomes = {
        current: instance.death_save_outcomes.get(current, {})
    }
    instance.state = GameState.ACTIVE_ACTION
    instance.round_checks_prepared = False
    instance.round_start_snapshot.clear()
    instance.round_entity_snapshot.clear()
    instance.action_queue.clear()
    instance.ready_players.clear()
    if instance.pending_actions:
        instance.action_queue.extend(instance.pending_actions)
        instance.pending_actions.clear()
    session_stats.touch(instance)
    logger.info("Round %d 开始 - game_key=%s", instance.round_number, instance.game_key)


def apply_action_roll_locked(
    instance: GameInstance,
    user_id: str,
    dice_system: str,
    value: int,
    *,
    rolls: list[int] | None = None,
    source: str = "player",
) -> bool:
    """``apply_action_roll`` 的持锁实现（调用方必须已持有写权限与 ``_lock``）。"""
    action = next(
        (
            item for item in instance.action_queue
            if item.get("user_id") == user_id and item.get("dice_pending")
        ),
        None,
    )
    if not action:
        return False
    from src.engine.modules import session_stats

    session_stats.require_writable(instance)
    clean_text = "\n".join(
        line for line in str(action.get("text", "")).splitlines()
        if not (line.startswith("(系统掷骰:") and line.endswith(")"))
    ).rstrip()
    system = dice_system or str(action.get("dice_system") or "d20")
    action["text"] = f"{clean_text}\n(系统掷骰: {system}={int(value)})"
    action["dice_pending"] = False
    action["dice_system"] = system
    action["dice_roll_source"] = source
    action["dice_value"] = int(value)
    action["dice_rolls"] = [int(item) for item in (rolls or [value])]
    instance.ready_players.add(user_id)
    session_stats.touch(instance)
    return True


def set_player_away_locked(instance: GameInstance, user_id: str, away: bool) -> bool:
    """``set_player_away`` 的持锁实现（调用方必须已持有 ``_lock``）。

    单独抽出来是为了让「暂离」和它可能触发的控制权转换能在**同一个**
    transaction 内完成：``_lock`` 不可重入，所以调用方不能在持锁时再调
    ``set_player_away``。
    """
    if user_id not in instance.players or not instance.is_alive(user_id):
        return False
    from src.engine.modules import session_stats

    session_stats.require_writable(instance)
    if away:
        instance.away_players.add(user_id)
        instance.ready_players.discard(user_id)
    else:
        instance.away_players.discard(user_id)
    session_stats.touch(instance)
    return True


def do_advance_locked(instance: GameInstance) -> bool:
    """在锁内执行推进（调用方需持锁）。"""
    if instance.state != GameState.ACTIVE_ACTION:
        return False
    for uid in instance.alive_players:
        instance.ready_players.add(uid)
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.round_checks_prepared = False
    instance.round_start_snapshot = snapshot_players(instance)
    instance.capture_round_entity_snapshot()
    logger.info("进入判定阶段 - game_key=%s, actions=%d",
                instance.game_key, len(instance.action_queue))
    return True
