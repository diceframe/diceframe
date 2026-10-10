"""Multiplayer coordination, dice, luck, and session control transactions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.engine.checks import build_check_request, roll_check_request
from src.engine.dice import d20_critical_thresholds, roll
from src.engine.game_instance import GameState
from src.engine.health import mark_health_event
from src.engine.modules import room_access, table_settings
from src.engine.player_control import (
    PlayerControlError,
    away_control_policy,
    control_mode,
    get_control,
    set_away_control_policy,
)
from src.rules.rule_system import RuleSystem

logger = logging.getLogger("trpg")

GameKey = tuple[str, ...]


@dataclass(frozen=True)
class GameControlDependencies:
    parse_game_key: Callable[[str], GameKey]
    get_instance: Callable[[GameKey], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule: Callable[[Any], RuleSystem | None]
    # 控制权写入成功之后的即时接管（src/webui/services/turns.py）：席位刚变成
    # AI，如果当前已经具备推进条件就立刻继续，不必等真人再发一句话。
    # None 表示该运行时没有配置这条能力（行为与以前一致）。
    resume_after_control_change: (
        Callable[[str, str], Awaitable[dict[str, Any]]] | None
    ) = None


class GameControlService:
    """State-changing game controls with an explicit persistence boundary."""

    def __init__(self, dependencies: GameControlDependencies) -> None:
        self._dependencies = dependencies

    def _instance(self, game_key: str) -> Any | None:
        return self._dependencies.get_instance(
            self._dependencies.parse_game_key(game_key)
        )

    async def _resume_after_control_change(
        self, game_key: str, user_id: str,
    ) -> dict[str, Any] | None:
        """控制权已经落盘后，立即唤醒现有推进边界；失败不改变控制权结果。

        控制权本身已经写入并保存，所以即时接管的任何失败都不能把这次切换
        报成失败——那会让 GM 以为切换没生效而重复操作。失败只记录并作为
        ``resume`` 字段回传，UI 因此能看到"仍未推进"而不是伪造成功。

        ``resume_after_control_change`` 与其它回合入口一样返回应用服务的
        ``TurnResult``；这里只把它的 payload 摊平进响应，让 route / 前端拿到的是
        一个可读的结果对象（``resumed`` / ``reason`` / ``advanced`` / ``narration``），
        而不是内部的 ``{payload, status}`` 信封。控制权切换本身的状态码不变。
        """
        resume = self._dependencies.resume_after_control_change
        if resume is None:
            return None
        fallback = {"ok": False, "resumed": False, "error_code": "RESUME_FAILED"}
        try:
            outcome = await resume(game_key, user_id)
        except Exception:
            logger.exception(
                "控制权变更后的即时接管失败: game=%s uid=%s", game_key, user_id,
            )
            return fallback
        payload = outcome.get("payload") if isinstance(outcome, dict) else None
        return payload if isinstance(payload, dict) else fallback

    async def set_player_away(
        self, game_key: str, user_id: str, away: bool,
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        if user_id not in instance.players:
            return {"ok": False, "error": "玩家不存在"}
        # 暂离是否把角色交给服务器 AI，由房间设置决定；默认 pause 保持旧行为。
        # 安全边界复核、在场状态与控制权转换由聚合在**一个** transaction 内完成，
        # 因此外部永远观察不到「已暂离但仍由真人控制」这种没人负责的中间态。
        code = await instance.apply_away_transition(
            user_id, away, policy=away_control_policy(instance),
        )
        if code:
            return {
                "ok": False,
                "error_code": code,
                "error": {
                    "UNKNOWN_PLAYER": "无法切换该玩家状态",
                }.get(code, "正在推进剧情，请等待本轮结束后再切换暂离状态"),
            }
        await self._dependencies.save_instance(instance)
        result = {
            "ok": True,
            "user_id": user_id,
            "character_name": (
                instance.players.get(user_id, {}).get("character_name")
                or user_id
            ),
            "away": bool(away),
            "control": get_control(instance, user_id),
            "multiplayer": instance.multiplayer_status(),
        }
        # 暂离把席位交给 AI（ai_takeover）同样是一次 human→ai：如果此刻已经
        # 具备推进条件，服务器立即接手，而不是等下一次真人提交。"回来"不会触发
        # 接管（它只会把席位还给真人），所以这里同时要求 ``away``。
        if away and control_mode(instance, user_id) == "ai":
            resume = await self._resume_after_control_change(game_key, user_id)
            if resume is not None:
                result["resume"] = resume
                result["multiplayer"] = instance.multiplayer_status()
        return result

    async def set_player_control(
        self, game_key: str, user_id: str, mode: str,
    ) -> dict[str, Any]:
        """GM 托管控件：把席位交给 AI，或停止 AI 托管交回真人。

        只改 Control Contract：不复制角色、不动 Web 身份 / Bot 绑定、不重置
        ready、不重置 HP、不重建战斗 actor。安全边界复核与写入由聚合在同一
        transaction 内完成。写入成功后，如果席位此刻由 AI 负责，就立刻唤醒现有
        推进边界（探索补行动 / 权威战斗自动回合），不需要真人再提交一次。
        """

        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        if user_id not in instance.players:
            return {"ok": False, "error": "玩家不存在"}
        if mode not in ("ai", "human"):
            return {
                "ok": False,
                "error_code": "CONTROL_MODE_UNSUPPORTED",
                "error": "只能设为 AI 托管或交回真人",
            }
        code = await instance.apply_player_control_change(user_id, mode)
        if code:
            return {
                "ok": False,
                "error_code": code,
                "error": {
                    "UNKNOWN_PLAYER": "玩家不存在",
                    "CONTROL_REJECTED": "该角色无法切换托管状态",
                }.get(code, "正在推进剧情，请等待本轮结束后再切换托管状态"),
            }
        await self._dependencies.save_instance(instance)
        result = {
            "ok": True,
            "user_id": user_id,
            "mode": control_mode(instance, user_id),
            "control": get_control(instance, user_id),
            "multiplayer": instance.multiplayer_status(),
        }
        if control_mode(instance, user_id) == "ai":
            resume = await self._resume_after_control_change(game_key, user_id)
            if resume is not None:
                result["resume"] = resume
                result["multiplayer"] = instance.multiplayer_status()
        return result

    async def set_away_control_policy(
        self, game_key: str, policy: str,
    ) -> dict[str, Any]:
        """房间设置：暂离语义 pause（默认）/ ai_takeover。"""

        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        try:
            value = set_away_control_policy(instance, policy)
        except PlayerControlError:
            return {
                "ok": False,
                "error_code": "AWAY_POLICY_UNSUPPORTED",
                "error": f"未知的暂离策略：{policy!r}",
            }
        await self._dependencies.save_instance(instance)
        return {"ok": True, "away_control_policy": value}

    async def set_player_access(
        self, game_key: str, open_access: bool,
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        instance.set_player_access(open_access)
        await self._dependencies.save_instance(instance)
        return {
            "ok": True,
            "player_access_open": room_access.player_access_open(instance),
        }

    def check_request_for_action(
        self,
        game_key: str,
        user_id: str,
        text: str,
        selected_attribute: str = "",
        selected_skill: str = "",
        target_text: str = "",
    ) -> dict[str, Any] | None:
        instance = self._instance(game_key)
        if not instance or user_id not in instance.players:
            return None
        action = {
            "user_id": user_id,
            "text": text,
            "selected_attribute": selected_attribute,
            "selected_skill": selected_skill,
            "target_text": target_text,
        }
        return build_check_request(
            instance, action, self._dependencies.load_rule(instance)
        )

    def roll_for_game(self, game_key: str) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        rule = self._dependencies.load_rule(instance)
        dice_system = str(rule.dice_system if rule else "d20").lower()
        if dice_system == "none":
            return {"ok": False, "error": "当前规则不需要掷骰"}
        formula = "d100" if dice_system == "d100" else "d20"
        result = roll(formula)
        if dice_system == "d20":
            crit_on, fumble_on = d20_critical_thresholds(rule)
            critical = crit_on is not None and crit_on <= result.natural <= 20
            fumble = fumble_on is not None and 1 <= result.natural <= fumble_on
        else:
            critical = result.natural == 1
            fumble = result.natural == 100
        return {
            "ok": True,
            "dice_system": formula,
            "value": result.natural,
            "critical": critical,
            "fumble": fumble,
        }

    async def resolve_pending_dice(
        self,
        game_key: str,
        user_id: str = "",
        source: str = "system",
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        async with instance.authoritative_write() as write_entered:
            if not write_entered:
                return {
                    "ok": False,
                    "code": "REWRITE_IN_PROGRESS",
                    "error": "GM 正在重写历史回合，请等待完成后重试",
                }
            pending = instance.pending_dice_actions(user_id or None)
            if not pending:
                return {"ok": True, "resolved": []}
            rule = self._dependencies.load_rule(instance)
            resolved: list[dict[str, Any]] = []
            for action in pending:
                actor_id = str(action.get("user_id") or "")
                request = action.get("check_request")
                if not isinstance(request, dict):
                    request = build_check_request(instance, action, rule)
                if not request:
                    continue
                action["check_request"] = request
                payload = roll_check_request(request, rule)
                applied = await instance.apply_action_roll(
                    actor_id,
                    payload["dice_system"],
                    payload["value"],
                    rolls=payload["rolls"],
                    source=source,
                )
                if not applied:
                    continue
                payload.update({"user_id": actor_id, "source": source})
                resolved.append(payload)
            return {
                "ok": True,
                "resolved": resolved,
                "roll": resolved[0] if resolved else None,
            }

    async def resolve_luck_decision(
        self,
        game_key: str,
        check_id: str,
        actor_uid: str,
        spend: bool,
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {
                "ok": False,
                "code": "GAME_NOT_FOUND",
                "error": "游戏不存在",
            }
        async with instance.authoritative_write() as entered:
            if not entered:
                return {"ok": False, "code": "REWRITE_IN_PROGRESS", "error": "GM 正在重写历史回合，请等待完成后重试"}
            if self._instance(game_key) is not instance:
                return {"ok": False, "code": "STALE_RUN", "error": "对局已重开，请刷新后重试"}
            assert instance is not None
            result = await instance.resolve_luck_decision(check_id, actor_uid, spend, rule=self._dependencies.load_rule(instance), allow_gm=True)
            if not result.get("ok"):
                return result
            await self._dependencies.save_instance(instance)
            pending = instance.pending_luck_checks()
            round_already_resolved = instance.state != GameState.ACTIVE_JUDGMENT
            return {**result, "phase": "done" if round_already_resolved else ("luck" if pending else "ready"), "pending_luck_decisions": pending, "ready_to_resolve": not pending and not round_already_resolved, "round_already_resolved": round_already_resolved}

    async def decline_pending_luck(self, game_key: str) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {
                "ok": False,
                "code": "GAME_NOT_FOUND",
                "error": "游戏不存在",
            }
        async with instance.authoritative_write() as entered:
            if not entered:
                return {"ok": False, "code": "REWRITE_IN_PROGRESS", "error": "GM 正在重写历史回合，请等待完成后重试"}
            if self._instance(game_key) is not instance:
                return {"ok": False, "code": "STALE_RUN", "error": "对局已重开，请刷新后重试"}
            declined = await instance.decline_pending_luck()
            if declined:
                await self._dependencies.save_instance(instance)
            return {"ok": True, "declined_luck_decisions": declined}

    async def set_solo_mode(
        self, game_key: str, solo: bool,
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        instance.set_solo_mode(solo)
        await self._dependencies.save_instance(instance)
        return {
            "ok": True,
            "solo_mode": table_settings.solo_mode(instance),
            "multiplayer": instance.multiplayer_status(),
        }

    async def set_narrative_perspective(
        self, game_key: str, perspective: str,
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        try:
            instance.set_narrative_perspective(perspective)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        await self._dependencies.save_instance(instance)
        return {
            "ok": True,
            "narrative_perspective": table_settings.narrative_perspective(instance),
        }

    async def set_gm_style(self, game_key: str, raw: Any) -> dict[str, Any]:
        """当前对局 GM 叙事风格覆盖：None=恢复跟随世界，dict=显式覆盖。"""
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        if raw is not None and not isinstance(raw, dict):
            return {"ok": False, "error": "GM 叙事风格设置无效"}
        try:
            instance.set_gm_style_override(raw)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        await self._dependencies.save_instance(instance)
        return {
            "ok": True,
            "gm_style_override": table_settings.gm_style_override(instance),
        }

    async def mark_health_event(
        self,
        game_key: str,
        event_id: str,
        *,
        resolved: bool = False,
        ignored: bool = False,
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "game not found"}
        if not mark_health_event(
            instance, event_id, resolved=resolved, ignored=ignored,
        ):
            return {"ok": False, "error": "health event not found"}
        await self._dependencies.save_instance(instance)
        return {
            "ok": True,
            "event_id": event_id,
            "resolved": resolved,
            "ignored": ignored,
        }
