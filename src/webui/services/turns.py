"""回合应用服务：统一 Web、Bot 共用的行动、幸运与推进流程。

HTTP routes 只负责读取请求和绑定 SSE 回调；回合状态机、骰子结算、
幸运暂停与响应 DTO 都在这里保持单一实现。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from src.engine.economy import (
    has_blocking_economy_decision,
    blocking_economy_proposals,
    is_auto_settleable_reward,
    pending_effect_groups,
    pending_economy_proposals,
)
from src.engine.memory_outbox import pending_memory_deliveries, pending_memory_reversals
from src.engine.game_instance import GameState
from src.webui.services._common import MAX_ACTIONS_PER_TURN

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance
    from src.rulesets.registry import RulesetRuntimeRegistry

logger = logging.getLogger("trpg")


NarrationDelta = Callable[[str], Awaitable[None]]
NarrationReset = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class TurnDependencies:
    get_instance: Callable[[tuple[str, ...]], Any | None]
    parse_game_key: Callable[[str], tuple[str, ...]]
    ruleset_registry: "RulesetRuntimeRegistry"
    load_rule_for_game: Callable[[Any], Any | None]
    prepare_round_checks_ai: Callable[[Any], Awaitable[Any]] | None
    prepare_round_checks: Callable[[Any], Any] | None
    resolve_pending_dice: Callable[..., Awaitable[dict[str, Any]]]
    roll_for_game: Callable[[str], dict[str, Any]]
    save_instance: Callable[[Any], Awaitable[None]]
    process_round: Callable[..., Awaitable[tuple[str, Any]]] | None
    resolve_luck_decision: Callable[..., Awaitable[dict[str, Any]]]
    decline_pending_luck: Callable[..., Awaitable[dict[str, Any]]]
    drain_economy_outbox: Callable[[Any], Awaitable[bool]] | None = None
    # 奖励自动结算：settings 返回 (enabled, gold_cap)；resolve_reward 复用
    # 标准支付确认服务（交易流水 + 效果组提交 + 存档），只是免掉 GM 点击。
    economy_auto_reward_settings: Callable[[], tuple[bool, int]] | None = None
    resolve_reward: Callable[[str, str, str], Awaitable[dict[str, Any]]] | None = None


class TurnResult(TypedDict):
    """应用服务结果；status 只供 route 映射 HTTP 状态，不进入 JSON。"""

    payload: dict[str, Any]
    status: int


def _result(payload: dict[str, Any], status: int = 200) -> TurnResult:
    return {"payload": payload, "status": status}


def _visible_economy_proposals(instance: "GameInstance", viewer_uid: str = "") -> list[dict[str, Any]]:
    return [
        proposal
        for proposal in pending_economy_proposals(instance)
        if (
            not viewer_uid
            or viewer_uid == instance.gm_uid
            or proposal.get("visibility") == "party"
            or viewer_uid == str(proposal.get("payer_uid") or proposal.get("uid") or "")
            or viewer_uid in {
                str(item.get("uid") or "")
                for item in (proposal.get("contributors") or [])
                if isinstance(item, dict)
            }
        )
    ]


def economy_decision_pending_payload(
    instance: "GameInstance",
    viewer_uid: str = "",
) -> dict[str, Any]:
    """Build a non-leaking progression barrier response for one viewer."""

    unresolved = blocking_economy_proposals(instance)
    visible = [
        proposal
        for proposal in unresolved
        if (
            not viewer_uid
            or viewer_uid == instance.gm_uid
            or proposal.get("visibility") == "party"
            or viewer_uid
            == str(proposal.get("payer_uid") or proposal.get("uid") or "")
            or viewer_uid
            in {
                str(item.get("uid") or "")
                for item in (proposal.get("contributors") or [])
                if isinstance(item, dict)
            }
        )
    ]
    return {
        "ok": False,
        "error_code": "ECONOMY_DECISION_PENDING",
        "error": "请先处理待确认的经济提案，再继续本局叙事",
        "pending_count": (
            len(unresolved)
            or len(pending_effect_groups(instance))
            or len(pending_memory_deliveries(instance))
            or len(pending_memory_reversals(instance))
        ),
        "economy_proposals": visible,
    }


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
    viewer_uid: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "narration": narration,
        "quick_actions": list(instance.quick_actions),
        "economy_proposals": _visible_economy_proposals(instance, viewer_uid),
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
    if code in {"LUCK_ALREADY_RESOLVED", "LUCK_NOT_PENDING", "REWRITE_IN_PROGRESS", "STALE_RUN"}:
        return 409
    return 400


async def _retry_external_economy_effects(
    dependencies: TurnDependencies,
    instance: "GameInstance",
) -> None:
    drain = dependencies.drain_economy_outbox
    if drain is not None:
        await drain(instance)


async def _prepare_checks(
    dependencies: TurnDependencies,
    instance: "GameInstance",
) -> list[dict[str, Any]]:
    """调用两阶段处理器；为外部/测试处理器保留同步兼容入口。"""
    ai_prepare = dependencies.prepare_round_checks_ai
    if ai_prepare:
        return list((await ai_prepare(instance)) or [])
    legacy_prepare = dependencies.prepare_round_checks
    return list((legacy_prepare(instance) if legacy_prepare else []) or [])


async def _process_round(
    dependencies: TurnDependencies,
    instance: "GameInstance",
    *,
    game_key: str,
    on_delta: NarrationDelta | None,
    on_reset: NarrationReset | None,
) -> tuple[str, Any]:
    if dependencies.process_round is None:
        raise RuntimeError("round processor is not available")
    narration, response = await dependencies.process_round(
        instance,
        on_delta=on_delta,
        on_reset=on_reset,
    )
    await _auto_settle_rewards(dependencies, instance, game_key)
    return narration, response


async def _auto_settle_rewards(
    dependencies: TurnDependencies,
    instance: "GameInstance",
    game_key: str,
) -> None:
    """Auto-settle qualifying narrative reward proposals after a round.

    A pending kind=reward proposal blocks the table until the GM confirms it,
    which turns the GM into a clerk for ordinary rewards ("击杀怪物，获得 12
    金币").  Qualifying rewards settle through the standard payment-confirm
    service (ledger + deferred effects + save), so the authority path is
    identical to a manual confirmation — only the click is removed.  Anything
    over the cap or with the switch off stays pending for the GM.

    每个 reward proposal 独立结算：单个失败（服务返回非 ok 或异常）只影响
    该提案（保持 pending 交还 GM），不中断其余提案，也不保证整批原子提交。
    """

    resolve_reward = dependencies.resolve_reward
    settings = dependencies.economy_auto_reward_settings
    if resolve_reward is None or settings is None:
        return
    try:
        enabled, gold_cap = settings()
    except Exception:
        logger.warning("读取奖励自动结算配置失败，本轮跳过自动结算", exc_info=True)
        return
    if not enabled or gold_cap < 1:
        return
    economy = getattr(instance, "economy", {})
    proposals = economy.get("proposals", []) if isinstance(economy, dict) else []
    for proposal in list(proposals):
        if not is_auto_settleable_reward(instance, proposal, gold_cap=gold_cap):
            continue
        proposal_id = str(proposal.get("id") or "")
        if not proposal_id:
            continue
        try:
            result = await resolve_reward(game_key, proposal_id, str(instance.gm_uid or ""))
            if not result.get("ok"):
                logger.warning(
                    "奖励自动结算未成功，保留待确认: game=%s proposal=%s code=%s",
                    game_key, proposal_id, result.get("code"),
                )
        except Exception:
            logger.warning(
                "奖励自动结算异常，保留待确认: game=%s proposal=%s",
                game_key, proposal_id, exc_info=True,
            )


def _auto_reward_cap(dependencies: TurnDependencies) -> int | None:
    """Gold cap for auto-settleable rewards; None when the switch is off.

    Passed into the barrier check so a plain reward within the cap does not
    block the table: it will settle automatically when the round completes.
    """

    settings = dependencies.economy_auto_reward_settings
    if settings is None:
        return None
    try:
        enabled, gold_cap = settings()
    except Exception:
        return None
    return gold_cap if enabled and gold_cap >= 1 else None


async def submit_action(
    dependencies: TurnDependencies,
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
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance:
        return _result({"error": "游戏不存在，请刷新页面重新开始"}, 404)
    if actor_uid not in instance.players:
        return _result({"error": "未加入本局，请先通过邀请链接加入"}, 403)
    rule = dependencies.load_rule_for_game(instance)
    if rule is not None:
        try:
            runtime = dependencies.ruleset_registry.resolve(rule.template)
        except ValueError as exc:
            return _result({
                "ok": False,
                "error_code": "RULESET_RUNTIME_UNAVAILABLE",
                "error": str(exc),
            }, 409)
        state = getattr(instance, "ruleset_state", {})
        combat = state.get("combat") if isinstance(state, dict) else None
        combat_active = isinstance(combat, dict) and combat.get("status") == "active"
        if runtime.capabilities.authoritative_intents and (
            not runtime.capabilities.narrative_turns or combat_active
        ):
            return _result({
                "ok": False,
                "error_code": "STRUCTURED_INTENT_REQUIRED",
                "error": "当前处于权威战斗，请在专业战斗工具中选择合法动作",
            }, 409)
    if instance.is_dead(actor_uid):
        return _result({"error": "角色已死亡，无法提交行动"}, 403)
    await _retry_external_economy_effects(dependencies, instance)
    if has_blocking_economy_decision(
        instance, auto_reward_gold_cap=_auto_reward_cap(dependencies),
    ):
        return _result(economy_decision_pending_payload(instance, actor_uid), 409)
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

    roll_payload: dict[str, Any] | None = None
    if confirm and existing_pending_roll:
        resolved = await dependencies.resolve_pending_dice(
            game_key, actor_uid, "player",
        )
        if not resolved.get("ok"):
            status = 409 if resolved.get("code") == "REWRITE_IN_PROGRESS" else 400
            return _result(resolved, status)
        roll_payload = resolved.get("roll")
    elif confirm and d20 is None and server_roll:
        roll_payload = dependencies.roll_for_game(game_key)
        if not roll_payload.get("ok"):
            return _result(roll_payload, 400)
        d20 = roll_payload["value"]

    if not (confirm and existing_pending_roll):
        action_text = text
        action_added = await instance.add_action(
            actor_uid,
            action_text,
            selected_attribute,
            selected_skill,
            target_text,
            source=source,
        )
        process_lock = getattr(instance, "_process_lock", None)
        if (
            not action_added
            and process_lock is not None
            and process_lock.locked()
        ):
            return _result({
                "ok": False,
                "error_code": "REWRITE_IN_PROGRESS",
                "error": "GM 正在重写历史回合，请等待完成后再提交行动",
            }, 409)
        # Purchase requests are recorded together with the action. Persisting
        # here keeps a request recoverable even when another player has not yet
        # submitted an action and no narrative round has started.
        if action_added:
            try:
                await dependencies.save_instance(instance)
            except Exception:
                logger.exception("保存行动/购买请求失败: game=%s", game_key)

    if await instance.try_advance():
        await _prepare_checks(dependencies, instance)
        if instance.pending_luck_checks():
            await dependencies.save_instance(instance)
            return _result(_pending_luck_payload(instance, roll=roll_payload))
        narration, _ = await _process_round(
            dependencies, instance, game_key=game_key, on_delta=on_delta, on_reset=on_reset,
        )
        payload = _round_payload(
            instance,
            narration,
            phase="done",
            include_recap=True,
            viewer_uid=actor_uid,
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
    dependencies: TurnDependencies,
    game_key: str,
    check_id: str,
    actor_uid: str,
    spend: bool,
    *,
    on_delta: NarrationDelta | None = None,
    on_reset: NarrationReset | None = None,
) -> TurnResult:
    """原子处理幸运选择；所有选择完成后继续生成叙事。"""
    decision = await dependencies.resolve_luck_decision(
        game_key, check_id, actor_uid, spend,
    )
    if not decision.get("ok"):
        return _result(decision, _luck_error_status(str(decision.get("code") or "")))
    if decision.get("round_already_resolved"):
        return _result({**decision, "phase": "done", "advanced": True})
    if not decision.get("ready_to_resolve"):
        return _result({**decision, "advanced": False})

    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance:
        return _result({"ok": False, "error": "游戏不存在"}, 404)
    await _retry_external_economy_effects(dependencies, instance)
    if has_blocking_economy_decision(
        instance, auto_reward_gold_cap=_auto_reward_cap(dependencies),
    ):
        return _result(economy_decision_pending_payload(instance, actor_uid), 409)
    narration, _ = await _process_round(
        dependencies, instance, game_key=game_key, on_delta=on_delta, on_reset=on_reset,
    )
    payload = {
        **decision,
        **_round_payload(instance, narration, phase="done", viewer_uid=actor_uid),
        "advanced": True,
    }
    return _result(payload)


async def advance_round(
    dependencies: TurnDependencies,
    game_key: str,
    actor_uid: str,
    *,
    force: bool = False,
    on_delta: NarrationDelta | None = None,
    on_reset: NarrationReset | None = None,
) -> TurnResult:
    """GM 推进回合，统一处理卡死恢复、待掷骰和待幸运选择。"""
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance:
        return _result({"error": "not found"}, 404)
    if actor_uid != instance.gm_uid:
        return _result({"ok": False, "error": "仅 GM 可推进"}, 403)
    await _retry_external_economy_effects(dependencies, instance)
    if has_blocking_economy_decision(
        instance, auto_reward_gold_cap=_auto_reward_cap(dependencies),
    ):
        return _result(economy_decision_pending_payload(instance, actor_uid), 409)

    if instance.state == GameState.ACTIVE_JUDGMENT and instance.action_queue:
        await _prepare_checks(dependencies, instance)
        pending_luck = instance.pending_luck_checks()
        if pending_luck and not force:
            return _result(_pending_luck_payload(instance), 409)
        advanced_declined_luck: list[dict[str, Any]] = []
        if pending_luck:
            declined = await dependencies.decline_pending_luck(game_key)
            advanced_declined_luck = list(declined.get("declined_luck_decisions") or [])
        logger.warning("检测到卡死状态，自动恢复 process_round - game_key=%s", game_key)
        narration, _ = await _process_round(
            dependencies, instance, game_key=game_key, on_delta=on_delta, on_reset=on_reset,
        )
        payload = _round_payload(instance, narration, viewer_uid=actor_uid)
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
        resolved = await dependencies.resolve_pending_dice(
            game_key, source="system",
        )
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
        await _prepare_checks(dependencies, instance)
        pending_luck = instance.pending_luck_checks()
        if pending_luck and not force:
            await dependencies.save_instance(instance)
            return _result(_pending_luck_payload(instance))
        declined_luck: list[dict[str, Any]] = []
        if pending_luck:
            declined = await dependencies.decline_pending_luck(game_key)
            declined_luck = list(declined.get("declined_luck_decisions") or [])
        narration, _ = await _process_round(
            dependencies, instance, game_key=game_key, on_delta=on_delta, on_reset=on_reset,
        )
        payload = _round_payload(instance, narration, ok=True, viewer_uid=actor_uid)
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
