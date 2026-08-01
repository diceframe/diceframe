"""回合应用服务：统一 Web、Bot 共用的行动、幸运与推进流程。

HTTP routes 只负责读取请求和绑定 SSE 回调；回合状态机、骰子结算、
幸运暂停与响应 DTO 都在这里保持单一实现。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypedDict

from src.engine.game_instance import GameState
from src.webui.services._common import MAX_ACTIONS_PER_TURN

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance
    from src.webui.api import WebAPI

logger = logging.getLogger("trpg")

NarrationDelta = Callable[[str], Awaitable[None]]
NarrationReset = Callable[[], Awaitable[None]]


class TurnResult(TypedDict):
    """应用服务结果；status 只供 route 映射 HTTP 状态，不进入 JSON。"""

    payload: dict[str, Any]
    status: int


def _result(payload: dict[str, Any], status: int = 200) -> TurnResult:
    return {"payload": payload, "status": status}


def _pending_payments(instance: "GameInstance") -> list[dict[str, Any]]:
    return [
        payment
        for payment in instance.pending_payments
        if isinstance(payment, dict) and payment.get("status") == "pending"
    ]


def _pending_luck_payload(
    instance: "GameInstance",
    *,
    roll: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "phase": "luck",
        "advanced": False,
        "message": "检定已完成，请选择是否消耗幸运后再继续叙事",
        "check_result": instance.last_check,
        "check_results": list(instance.last_checks),
        "pending_luck_decisions": instance.pending_luck_checks(),
        "multiplayer": instance.multiplayer_status(),
    }
    if roll:
        payload["roll"] = roll
    return payload


def _round_payload(
    instance: "GameInstance",
    narration: str,
    *,
    phase: str | None = None,
    ok: bool | None = None,
    include_recap: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "narration": narration,
        "quick_actions": list(instance.quick_actions),
        "pending_payments": _pending_payments(instance),
        "check_result": instance.last_check,
        "check_results": list(instance.last_checks),
    }
    if phase is not None:
        payload["phase"] = phase
    if ok is not None:
        payload["ok"] = ok
    if include_recap:
        payload["recap"] = instance.last_state_update
    return payload


def _luck_error_status(code: str) -> int:
    if code in {"GAME_NOT_FOUND", "CHECK_NOT_FOUND"}:
        return 404
    if code == "LUCK_FORBIDDEN":
        return 403
    if code in {"LUCK_ALREADY_RESOLVED", "LUCK_NOT_PENDING"}:
        return 409
    return 400


async def submit_action(
    api: "WebAPI",
    game_key: str,
    actor_uid: str,
    text: str,
    *,
    confirm: bool = False,
    d20: Any = None,
    server_roll: bool = False,
    selected_attribute: str = "",
    selected_skill: str = "",
    target_text: str = "",
    source: str = "",
    on_delta: NarrationDelta | None = None,
    on_reset: NarrationReset | None = None,
) -> TurnResult:
    """提交一次行动，并在满足推进条件时完成判定与叙事。"""
    instance = api._reg.get(api._parse_key(game_key))
    if not instance:
        return _result({"error": "游戏不存在，请刷新页面重新开始"}, 404)
    if actor_uid not in instance.players:
        return _result({"error": "未加入本局，请先通过邀请链接加入"}, 403)
    if instance.is_dead(actor_uid):
        return _result({"error": "角色已死亡，无法提交行动"}, 403)
    if instance.state == GameState.ACTIVE_JUDGMENT:
        return _result({"error": "本轮正在推进剧情，请等待下一轮开始", "phase": "processing"}, 409)

    existing_action = next(
        (action for action in instance.action_queue if action.get("user_id") == actor_uid),
        None,
    )
    existing_pending_roll = bool(existing_action and existing_action.get("dice_pending"))
    if instance.solo_mode:
        action_count = sum(1 for action in instance.action_queue if action.get("user_id") == actor_uid)
        if action_count >= MAX_ACTIONS_PER_TURN:
            return _result({"error": f"本回合已达行动上限（{MAX_ACTIONS_PER_TURN} 条）"}, 400)
    elif (
        existing_action
        and int(existing_action.get("revision_count", 1) or 1) >= 3
        and not (confirm and existing_pending_roll)
    ):
        return _result({"error": "本轮行动已修改 3 次，请等待其他玩家或 GM 推进"}, 400)

    if instance.state == GameState.PAUSED:
        if instance.round_number <= 0:
            await instance.start_round()
        else:
            await instance.resume()

    check_request = api.check_request_for_action(
        game_key,
        actor_uid,
        text,
        selected_attribute,
        selected_skill,
        target_text,
    )
    need_check = bool(check_request)
    dice_system = str((check_request or {}).get("dice_system") or "")

    roll_payload: dict[str, Any] | None = None
    if confirm and existing_pending_roll:
        resolved = await api.resolve_pending_dice_for_game(game_key, actor_uid, "player")
        if not resolved.get("ok"):
            return _result(resolved, 400)
        roll_payload = resolved.get("roll")
    elif need_check and not confirm:
        await instance.add_action(
            actor_uid,
            text,
            selected_attribute,
            selected_skill,
            target_text,
            source=source,
            dice_pending=True,
            dice_system=dice_system,
            check_request=check_request,
        )
        return _result({
            "phase": "dice",
            "message": f"需要{check_request.get('label') or '掷骰判定'}",
            "check_request": check_request,
            "advanced": False,
            "multiplayer": instance.multiplayer_status(),
        })
    elif confirm and d20 is None and server_roll:
        roll_payload = api.roll_for_game(game_key)
        if not roll_payload.get("ok"):
            return _result(roll_payload, 400)
        d20 = roll_payload["value"]

    if not (confirm and existing_pending_roll):
        action_text = text
        if confirm and d20 is not None:
            action_text = f"{text}\n(系统掷骰: {dice_system}={d20})"
        await instance.add_action(
            actor_uid,
            action_text,
            selected_attribute,
            selected_skill,
            target_text,
            source=source,
            check_request=check_request,
        )

    if await instance.try_advance():
        api._handler.prepare_round_checks(instance)
        if instance.pending_luck_checks():
            await api._reg.save(instance)
            return _result(_pending_luck_payload(instance, roll=roll_payload))
        narration, _ = await api._handler.process_round(
            instance,
            on_delta=on_delta,
            on_reset=on_reset,
        )
        payload = _round_payload(
            instance,
            narration,
            phase="done",
            include_recap=True,
        )
        payload["advanced"] = True
        if roll_payload:
            payload["roll"] = roll_payload
        return _result(payload)

    multiplayer = instance.multiplayer_status()
    waiting_names = [
        player.get("character_name") or player.get("user_id")
        for player in multiplayer.get("waiting_players", [])
    ]
    waiting_text = "、".join(str(name) for name in waiting_names if name)
    message = f"行动已公开，等待 {waiting_text} 行动" if waiting_text else "行动已公开，等待系统推进"
    payload = {
        "narration": message,
        "advanced": False,
        "phase": "done",
        "multiplayer": multiplayer,
    }
    if roll_payload:
        payload["roll"] = roll_payload
    return _result(payload)


async def resolve_luck_and_continue(
    api: "WebAPI",
    game_key: str,
    check_id: str,
    actor_uid: str,
    spend: bool,
    *,
    on_delta: NarrationDelta | None = None,
    on_reset: NarrationReset | None = None,
) -> TurnResult:
    """原子处理幸运选择；所有选择完成后继续生成叙事。"""
    decision = await api.resolve_luck_decision(game_key, check_id, actor_uid, spend)
    if not decision.get("ok"):
        return _result(decision, _luck_error_status(str(decision.get("code") or "")))
    if decision.get("round_already_resolved"):
        return _result({**decision, "phase": "done", "advanced": True})
    if not decision.get("ready_to_resolve"):
        return _result({**decision, "advanced": False})

    instance = api._reg.get(api._parse_key(game_key))
    if not instance:
        return _result({"ok": False, "error": "游戏不存在"}, 404)
    narration, _ = await api._handler.process_round(
        instance,
        on_delta=on_delta,
        on_reset=on_reset,
    )
    payload = {
        **decision,
        **_round_payload(instance, narration, phase="done"),
        "advanced": True,
    }
    return _result(payload)


async def advance_round(
    api: "WebAPI",
    game_key: str,
    actor_uid: str,
    *,
    force: bool = False,
    on_delta: NarrationDelta | None = None,
    on_reset: NarrationReset | None = None,
) -> TurnResult:
    """GM 推进回合，统一处理卡死恢复、待掷骰和待幸运选择。"""
    instance = api._reg.get(api._parse_key(game_key))
    if not instance:
        return _result({"error": "not found"}, 404)
    if actor_uid != instance.gm_uid:
        return _result({"ok": False, "error": "仅 GM 可推进"}, 403)

    if instance.state == GameState.ACTIVE_JUDGMENT and instance.action_queue:
        api._handler.prepare_round_checks(instance)
        pending_luck = instance.pending_luck_checks()
        if pending_luck and not force:
            return _result(_pending_luck_payload(instance), 409)
        advanced_declined_luck: list[dict[str, Any]] = []
        if pending_luck:
            declined = await api.decline_pending_luck(game_key)
            advanced_declined_luck = list(declined.get("declined_luck_decisions") or [])
        logger.warning("检测到卡死状态，自动恢复 process_round - game_key=%s", game_key)
        narration, _ = await api._handler.process_round(
            instance,
            on_delta=on_delta,
            on_reset=on_reset,
        )
        payload = _round_payload(instance, narration)
        if advanced_declined_luck:
            payload["declined_luck_decisions"] = advanced_declined_luck
        return _result(payload)

    if not instance.can_accept_actions():
        return _result({"ok": False, "narration": "当前不能推进"})

    auto_rolls: list[dict[str, Any]] = []
    if instance.has_pending_dice():
        if not force:
            return _result({
                "ok": False,
                "narration": "仍有玩家行动等待掷骰",
                "multiplayer": instance.multiplayer_status(),
            })
        resolved = await api.resolve_pending_dice_for_game(game_key, source="system")
        if not resolved.get("ok"):
            return _result(resolved, 400)
        auto_rolls = list(resolved.get("resolved") or [])

    forced_waiting: list[str] = []
    if force and not instance.should_advance():
        multiplayer = instance.multiplayer_status()
        waiting = multiplayer.get("waiting_players", [])
        if not instance.action_queue:
            return _result({"ok": False, "narration": "还没有任何玩家行动，无法推进"})
        for player in waiting:
            uid = str(player.get("user_id", "") or "")
            name = str(player.get("character_name", "") or uid)
            if uid:
                await instance.add_action(uid, "本轮暂不行动，保持警戒。")
                forced_waiting.append(name)

    advanced = await instance.advance_round() if force else await instance.try_advance()
    if advanced:
        api._handler.prepare_round_checks(instance)
        pending_luck = instance.pending_luck_checks()
        if pending_luck and not force:
            await api._reg.save(instance)
            return _result(_pending_luck_payload(instance))
        declined_luck: list[dict[str, Any]] = []
        if pending_luck:
            declined = await api.decline_pending_luck(game_key)
            declined_luck = list(declined.get("declined_luck_decisions") or [])
        narration, _ = await api._handler.process_round(
            instance,
            on_delta=on_delta,
            on_reset=on_reset,
        )
        payload = _round_payload(instance, narration, ok=True)
        if forced_waiting:
            payload["forced_waiting"] = forced_waiting
        if auto_rolls:
            payload["auto_rolls"] = auto_rolls
        if declined_luck:
            payload["declined_luck_decisions"] = declined_luck
        return _result(payload)

    multiplayer = instance.multiplayer_status()
    waiting_names = [
        player.get("character_name") or player.get("user_id")
        for player in multiplayer.get("waiting_players", [])
    ]
    waiting_text = "、".join(str(name) for name in waiting_names if name)
    message = f"推进失败：仍在等待 {waiting_text} 行动" if waiting_text else "推进失败：当前状态不能推进"
    return _result({"ok": False, "narration": message, "multiplayer": multiplayer})
