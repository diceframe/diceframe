"""Multiplayer coordination, dice, luck, and session control transactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.engine.checks import build_check_request, roll_check_request
from src.engine.dice import d20_critical_thresholds, roll
from src.engine.game_instance import GameState
from src.engine.health import mark_health_event
from src.rules.rule_system import RuleSystem

GameKey = tuple[str, ...]


@dataclass(frozen=True)
class GameControlDependencies:
    parse_game_key: Callable[[str], GameKey]
    get_instance: Callable[[GameKey], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule: Callable[[Any], RuleSystem | None]


class GameControlService:
    """State-changing game controls with an explicit persistence boundary."""

    def __init__(self, dependencies: GameControlDependencies) -> None:
        self._dependencies = dependencies

    def _instance(self, game_key: str) -> Any | None:
        return self._dependencies.get_instance(
            self._dependencies.parse_game_key(game_key)
        )

    async def set_player_away(
        self, game_key: str, user_id: str, away: bool,
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        if user_id not in instance.players:
            return {"ok": False, "error": "玩家不存在"}
        ok = await instance.set_player_away(user_id, away)
        if not ok:
            return {"ok": False, "error": "无法切换该玩家状态"}
        await self._dependencies.save_instance(instance)
        return {
            "ok": True,
            "user_id": user_id,
            "character_name": (
                instance.players.get(user_id, {}).get("character_name")
                or user_id
            ),
            "away": bool(away),
            "multiplayer": instance.multiplayer_status(),
        }

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
            "player_access_open": instance.player_access_open,
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
            "solo_mode": instance.solo_mode,
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
            "narrative_perspective": instance.narrative_perspective,
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
            "gm_style_override": instance.gm_style_override,
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
