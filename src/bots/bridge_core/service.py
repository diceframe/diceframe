"""Shared text-command business dispatcher for DiceFrame bridge adapters."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any

from src.bots.bridge_core.client import DiceFrameClient, build_join_link
from src.bots.bridge_core.commands import (
    advance_force,
    away_target_query,
    is_advance,
    is_ai_character_create,
    is_away,
    is_character_create,
    is_help,
    is_invite,
    is_map,
    is_payment_list,
    is_private_log,
    is_recap,
    is_return,
    luck_decision,
    luck_index,
    payment_decision,
    payment_index,
)
from src.bots.bridge_core.errors import DiceFrameHTTPError
from src.bots.bridge_core.language import bridge_is_english, bridge_language, bridge_text, infer_command_language
from src.bots.bridge_core.models import BridgeInput, BridgeResult
from src.bots.bridge_core.presenters import (
    bound_help_text,
    character_creation_lines,
    character_creation_text,
    format_action_result,
    map_lines,
    map_text,
    match_roster_character,
    payment_line,
    recap_text,
    roster_names,
)
from src.bots.bridge_core.store import JsonBridgeStore
from src.bots.bridge_core.triggers import TriggerConfig, should_trigger, strip_prefix


@dataclass
class BridgeServiceConfig:
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    command_dedup_window_sec: float = 3
    max_reply_chars: int = 1800
    public_base_url: str = ""
    action_source: str = "bridge"
    command_prefix: str = "跑团"
    advance_allowed_users: set[str] = field(default_factory=set)
    default_language: str = "zh-CN"


class DiceFrameBridgeService:
    def __init__(self, client: DiceFrameClient, store: JsonBridgeStore, config: BridgeServiceConfig | None = None) -> None:
        self.client = client
        self.store = store
        self.config = config or BridgeServiceConfig()

    async def handle(self, message: BridgeInput) -> BridgeResult:
        before = await self._apply_extensions("before_message", {
            "platform": message.platform or "bridge",
            "kind": "command",
            "scope": "stream",
            "stream_id": message.stream_id,
            "user_id": message.platform_user_id,
            "text": message.text,
            "mentioned_bot": message.mentioned_bot,
            "language": message.language,
        })
        if before.get("handled"):
            outputs = before.get("outputs") if isinstance(before.get("outputs"), list) else []
            return BridgeResult(
                handled=True,
                intercept=True,
                replies=self._output_fallback_text(outputs),
                outputs=outputs,
                metadata={"extensions": before.get("applied", [])},
            )
        before_payload = before.get("payload")
        if isinstance(before_payload, dict):
            message = replace(
                message,
                text=str(before_payload.get("text") or message.text),
                language=str(before_payload.get("language") or message.language),
            )
        if not should_trigger(message.text, mentioned_bot=message.mentioned_bot, config=self.config.trigger):
            return BridgeResult(handled=False, intercept=False)

        command_text = strip_prefix(message.text, self.config.trigger.prefixes).strip()
        language = self._language(message, command_text)
        normalized = re.sub(r"\s+", " ", command_text)
        signature = f"{message.stream_id}:{message.platform_user_id}:{normalized}"
        if not await self.store.remember_command(signature, self.config.command_dedup_window_sec):
            return BridgeResult(handled=True, intercept=True)

        try:
            reply = await self._dispatch(command_text, message)
        except DiceFrameHTTPError as exc:
            reply = bridge_text(language, "DiceFrame 请求失败：{error}", "DiceFrame request failed: {error}", error=exc)
        except Exception as exc:
            reply = bridge_text(language, "DiceFrame Bridge 处理失败：{error}", "DiceFrame Bridge failed: {error}", error=exc)
        payload = {
            "platform": message.platform or "bridge",
            "kind": "text",
            "scope": "stream",
            "stream_id": message.stream_id,
            "user_id": message.platform_user_id,
            "text": reply,
            "language": language,
        }
        after = await self._apply_extensions("after_result", payload)
        payload = after.get("payload") if isinstance(after.get("payload"), dict) else payload
        if after.get("handled"):
            outputs = after.get("outputs") if isinstance(after.get("outputs"), list) else []
            return BridgeResult(
                handled=True,
                intercept=True,
                replies=self._output_fallback_text(outputs),
                outputs=outputs,
                metadata={"extensions": after.get("applied", [])},
            )
        rendered = await self._apply_extensions("render", payload)
        payload = rendered.get("payload") if isinstance(rendered.get("payload"), dict) else payload
        outputs = rendered.get("outputs") if isinstance(rendered.get("outputs"), list) else []
        if rendered.get("handled"):
            return BridgeResult(
                handled=True,
                intercept=True,
                replies=self._output_fallback_text(outputs),
                outputs=outputs,
                metadata={"extensions": rendered.get("applied", [])},
            )
        return BridgeResult(
            handled=True,
            intercept=True,
            replies=self._split_reply(str(payload.get("text") or reply), language),
            metadata={"extensions": [*after.get("applied", []), *rendered.get("applied", [])]},
        )

    async def _apply_extensions(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        apply_extensions = getattr(self.client, "apply_bridge_extensions", None)
        if not callable(apply_extensions):
            return {"handled": False, "payload": payload, "outputs": [], "applied": []}
        try:
            return await apply_extensions(stage, payload)
        except Exception:
            logging.getLogger("trpg.bridge").warning(
                "Bot Bridge 扩展阶段调用失败，使用内置逻辑: stage=%s",
                stage,
                exc_info=True,
            )
            return {"handled": False, "payload": payload, "outputs": [], "applied": []}

    @staticmethod
    def _output_fallback_text(outputs: list[dict[str, Any]]) -> list[str]:
        replies: list[str] = []
        for output in outputs:
            if not isinstance(output, dict):
                continue
            if output.get("type") == "text" and str(output.get("text") or "").strip():
                replies.append(str(output["text"]))
                continue
            fallback = str(output.get("fallback_text") or "").strip()
            if fallback:
                replies.append(fallback)
        return replies

    async def _dispatch(self, text: str, message: BridgeInput) -> str:
        language = self._language(message, text)
        if not text or is_help(text):
            group = self.store.group(message.stream_id)
            return self._bound_help_text(group, language) if group else self._unbound_text(language)

        bind_match = re.match(r"^(?:绑定|bind)\s+(\S+)\s+(\S+)\s*$", text, re.IGNORECASE)
        if bind_match:
            return await self._bind(message, bind_match.group(1), bind_match.group(2))

        verb, rest = self._verb_and_rest(text)
        aliases = {
            "unbind": "解绑",
            "join": "加入",
            "status": "状态",
            "recap": "前情",
            "summary": "前情",
            "map": "地图",
            "roll": "掷骰",
            "advance": "推进",
            "next": "推进",
            "下一轮": "推进",
            "pay": "支付",
            "确认支付": "支付",
            "rejectpay": "拒绝支付",
            "log": "感知",
            "sense": "感知",
            "invite": "邀请",
            "away": "暂离",
            "return": "回来",
            "back": "回来",
            "ping": "连接测试",
            "character": "新建角色",
            "create": "新建角色",
            "payment": "支付",
            "payments": "支付",
            "perception": "感知",
            "luck": "幸运",
            "spendluck": "幸运",
            "noluck": "不用幸运",
            "declineluck": "不用幸运",
            "keepfailure": "不用幸运",
        }
        verb = aliases.get(verb.lower(), verb)

        if verb == "连接测试":
            data = await self.client.ping()
            return bridge_text(
                language,
                "DiceFrame 连接正常。当前服务中共有 {total} 个对局。",
                "DiceFrame is connected. The service currently has {total} games.",
                total=data.get("total", 0),
            )
        if verb == "解绑":
            await self.store.unbind_group(message.stream_id)
            return bridge_text(language, "当前聊天流已解除 DiceFrame 绑定。", "This chat is no longer bound to DiceFrame.")
        if verb == "邀请" or is_invite(text):
            return await self._invite(message)
        if verb in {"新建角色", "车卡", "AI车卡", "ai车卡"} or is_character_create(text) or is_ai_character_create(text):
            return await self._character_guide(message, ai=(verb.lower() == "ai车卡" or is_ai_character_create(text)))
        if verb == "加入":
            return await self._join(message, rest)
        if verb == "状态":
            return await self._status(message)
        if verb == "前情" or is_recap(text):
            return await self._recap(message)
        if verb == "地图" or is_map(text):
            return await self._map(message)
        if verb == "感知" or is_private_log(text):
            return await self._private_log(message)
        if verb == "掷骰":
            return await self._action(message, "", confirm=True)
        luck = luck_decision(text)
        if verb in {"幸运", "不用幸运"} or luck is not None:
            return await self._luck(message, text, spend=(verb == "幸运") if luck is None else luck)
        if verb == "推进" or is_advance(text):
            return await self._advance(message, text)
        if verb == "暂离" or is_away(text):
            return await self._away(message, text, away=True)
        if verb == "回来" or is_return(text):
            return await self._away(message, text, away=False)
        decision = payment_decision(text)
        if verb == "支付":
            return await self._payment(message, text, accepted=True if rest else None)
        if is_payment_list(text) or decision is not None:
            return await self._payment(message, text, accepted=decision)
        if verb in {"行动", "做"}:
            return await self._action(message, rest)
        return await self._action(message, text)

    async def _bind(self, message: BridgeInput, game_key: str, bind_token: str) -> str:
        result = await self.client.bind_game(game_key, bind_token)
        language = bridge_language(result.get("language") or self._language(message))
        players = result.get("players") if isinstance(result.get("players"), list) else []
        await self.store.bind_group(
            message.stream_id,
            str(result.get("game_key") or game_key),
            message.platform_user_id,
            str(result.get("gm_uid") or ""),
            players,
            language,
        )
        world = str(result.get("world_name") or result.get("game_key") or game_key)
        if bridge_is_english(language):
            return (
                f"Bound to DiceFrame game “{world}”.\n"
                "The current user is mapped as GM.\n"
                f"Available characters: {roster_names({'roster': players}, language)}\n"
                f"Next, players use {self._cmd('join Character Name')}, then submit actions such as "
                f"{self._cmd('I inspect the area')}."
            )
        return (
            f"已绑定 DiceFrame 对局《{world}》。\n"
            "GM 已映射为当前用户。\n"
            f"可认领角色：{roster_names({'roster': players})}\n"
            f"下一步：玩家发送 {self._cmd('加入 角色名')}，然后用 {self._cmd('我调查四周')} 提交行动。"
        )

    async def _invite(self, message: BridgeInput) -> str:
        group, game_key, actor = self._require_actor_or_group_gm(message)
        language = self._group_language(group)
        detail = await self.client.detail(game_key, actor)
        world = str(detail.get("world_name") or game_key)
        link = await self._join_link(game_key)
        if bridge_is_english(language):
            return f"Player link for DiceFrame “{world}”: {link}" if link else f"DiceFrame “{world}” is bound."
        return f"DiceFrame《{world}》玩家入口：{link}" if link else f"DiceFrame《{world}》已绑定。"

    async def _character_guide(self, message: BridgeInput, *, ai: bool) -> str:
        group, game_key, actor = self._require_actor_or_group_gm(message)
        language = self._group_language(group)
        data = await self.client.characters(game_key, actor)
        link = await self._join_link(game_key)
        lines = character_creation_lines(data, command_prefix=self.config.command_prefix, language=language)
        if ai:
            lines.insert(0, bridge_text(
                language,
                "AI 辅助车卡请在网页入口选择 AI 生成，或按群聊适配器的私聊向导继续。",
                "Use AI generation in the web character creator, or continue through the adapter’s private-message guide.",
            ))
        return character_creation_text(lines, link, language)

    async def _join(self, message: BridgeInput, name: str) -> str:
        language = self._language(message)
        if not name:
            return bridge_text(language, "请发送：{cmd}", "Please send: {cmd}", cmd=self._cmd("join <Character Name>" if bridge_is_english(language) else "加入 <角色名>"))
        group, game_key, gm_uid = self._require_group(message.stream_id, language)
        language = self._group_language(group)
        roster = await self._refresh_roster(message.stream_id, group, gm_uid)
        matches = match_roster_character(roster, name)
        if len(matches) != 1:
            return bridge_text(language, "没有找到唯一匹配的角色，请输入完整角色名。", "Could not find one unique character. Please enter the full character name.")
        user_id = str(matches[0].get("user_id") or "")
        if not user_id:
            return bridge_text(language, "匹配到的角色缺少 user_id，无法认领。", "The matched character has no user ID and cannot be claimed.")
        ok = await self.store.bind_player(message.stream_id, message.platform_user_id, user_id)
        if not ok:
            return bridge_text(language, "该角色已被其他成员认领。", "That character has already been claimed by another member.")
        return bridge_text(language, "已认领角色：{name}", "Character claimed: {name}", name=matches[0].get("character_name") or name)

    async def _status(self, message: BridgeInput) -> str:
        group, game_key, actor = self._require_actor(message)
        language = self._group_language(group)
        data = await self.client.characters(game_key, actor)
        player = next((item for item in data.get("players", []) if isinstance(item, dict) and str(item.get("user_id") or "") == actor), None)
        if not player:
            return bridge_text(language, "未找到当前角色。", "Current character not found.")
        return await self._format_status(player, group, language)

    async def _recap(self, message: BridgeInput) -> str:
        group, game_key, actor = self._require_actor_or_group_gm(message)
        detail = await self.client.detail(game_key, actor)
        return recap_text(detail, self._group_language(group))

    async def _map(self, message: BridgeInput) -> str:
        group, game_key, actor = self._require_actor_or_group_gm(message)
        data = await self.client.map(game_key, actor)
        language = self._group_language(group)
        return map_text(map_lines(data, language), language)

    async def _private_log(self, message: BridgeInput) -> str:
        group, game_key, actor = self._require_actor(message)
        language = self._group_language(group)
        data = await self.client.private_log(game_key, actor)
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        lines = [
            (
                f"R{item.get('round', '?')}: {str(item.get('text') or '').strip()}"
                if bridge_is_english(language) else
                f"R{item.get('round', '?')}：{str(item.get('text') or '').strip()}"
            )
            for item in messages[-6:]
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        if lines:
            return ("Private character information:\n" if bridge_is_english(language) else "角色感知：\n") + "\n".join(lines)
        return bridge_text(language, "暂无专属于你的角色感知。", "There is no private information for your character yet.")

    async def _action(self, message: BridgeInput, text: str, *, confirm: bool = False) -> str:
        language = self._language(message)
        if not text and not confirm:
            return bridge_text(language, "请描述你的行动。", "Please describe your action.")
        group, game_key, actor = self._require_actor(message)
        language = self._group_language(group)
        result = await self.client.action(game_key, actor, text, confirm=confirm, source=self.config.action_source)
        return format_action_result(result, language)

    async def _advance(self, message: BridgeInput, text: str) -> str:
        group, game_key, gm_uid = self._require_group(message.stream_id, self._language(message))
        language = self._group_language(group)
        if not self._can_advance(group, message.platform_user_id):
            return bridge_text(language, "只有绑定本局的 GM 或配置中的授权用户可以推进。", "Only the bound GM or an authorized user can advance the game.")
        result = await self.client.advance(game_key, gm_uid, force=advance_force(text))
        return self._format_advance_response(result, language)

    async def _luck(self, message: BridgeInput, text: str, *, spend: bool) -> str:
        group, game_key, actor = self._require_actor(message)
        language = self._group_language(group)
        detail = await self.client.detail(game_key, actor)
        pending = [
            check for check in (detail.get("pending_luck_decisions") or [])
            if isinstance(check, dict) and str(check.get("actor_uid") or "") == actor
        ]
        if not pending:
            return bridge_text(language, "当前没有等待你处理的幸运选择。", "There is no Luck decision waiting for you.")
        index = luck_index(text)
        if index > len(pending):
            return bridge_text(language, "没有第 {index} 个幸运选择。", "There is no Luck decision #{index}.", index=index)
        check = pending[index - 1]
        result = await self.client.resolve_luck(
            game_key,
            actor,
            str(check.get("check_id") or ""),
            spend=spend,
        )
        cost = int(check.get("luck_cost", 0) or 0)
        prefix = (
            f"Spent {cost} Luck; the check is now a regular success."
            if spend else "Luck was not spent; the failure is kept."
        ) if bridge_is_english(language) else (
            f"已消耗 {cost} 点幸运，本次检定改为普通成功。"
            if spend else "未使用幸运，本次检定保留失败。"
        )
        narration = str(result.get("narration") or "").strip()
        if narration:
            return prefix + "\n" + narration
        remaining = result.get("pending_luck_decisions") if isinstance(result.get("pending_luck_decisions"), list) else []
        if remaining:
            suffix = " Waiting for other Luck decisions." if bridge_is_english(language) else " 仍在等待其他角色选择幸运。"
            return prefix + suffix
        return prefix

    async def _away(self, message: BridgeInput, text: str, *, away: bool) -> str:
        group, game_key, actor = self._require_actor(message)
        language = self._group_language(group)
        target_uid = actor
        query = away_target_query(text)
        if query:
            if not self._can_advance(group, message.platform_user_id):
                return bridge_text(language, "只有 GM 或授权账号可以切换其他角色的暂离状态。", "Only the GM or an authorized user can change another character’s away status.")
            matches = match_roster_character(group.get("roster", []), query)
            if len(matches) == 1:
                target_uid = str(matches[0].get("user_id") or actor)
        api_actor = actor if target_uid == actor else str(group.get("gm_uid") or actor)
        result = await self.client.set_player_away(game_key, api_actor, target_uid, away=away)
        name = str(result.get("character_name") or target_uid)
        if bridge_is_english(language):
            return f"{name} is now {'away' if away else 'back'}."
        return f"{name} 已{'暂离' if away else '回来'}。"

    async def _payment(self, message: BridgeInput, text: str, accepted: bool | None) -> str:
        group, game_key, actor = self._require_actor(message)
        language = self._group_language(group)
        payments = await self._pending_payments(game_key, actor)
        if accepted is None:
            if not payments:
                return bridge_text(language, "当前没有待处理的支付请求。", "There are no pending payment requests.")
            lines = ["Pending payments:" if bridge_is_english(language) else "待处理支付："]
            for index, payment in enumerate(payments, 1):
                lines.append(payment_line(payment, index, language))
            lines.append(
                f"Confirm: {self._cmd('confirm pay 1')}; reject: {self._cmd('reject pay 1')}"
                if bridge_is_english(language) else
                f"确认：{self._cmd('确认支付 1')}；拒绝：{self._cmd('拒绝支付 1')}"
            )
            return "\n".join(lines)
        if not payments:
            return bridge_text(language, "当前没有待处理的支付请求。", "There are no pending payment requests.")
        index = payment_index(text)
        if index < 1 or index > len(payments):
            return (
                f"There is no pending payment #{index}; use “{self._cmd('pay')}” to view the list."
                if bridge_is_english(language) else
                f"没有第 {index} 笔待支付；发送“{self._cmd('支付')}”查看列表。"
            )
        payment = payments[index - 1]
        result = await self.client.resolve_payment(game_key, actor, str(payment.get("id") or ""), accepted)
        if result.get("ok") is False:
            return str(result.get("error") or ("Payment failed" if bridge_is_english(language) else "支付处理失败"))
        amount = int(payment.get("amount", 0) or 0)
        if bridge_is_english(language):
            return f"Payment of {amount} gold {'confirmed' if accepted else 'rejected'}."
        return f"已{'确认' if accepted else '拒绝'}支付 {amount} 金币。"

    def _require_group(self, stream_id: str, language: str = "") -> tuple[dict[str, Any], str, str]:
        group = self.store.group(stream_id)
        if not group:
            raise DiceFrameHTTPError(self._unbound_text(language or self.config.default_language))
        language = self._group_language(group)
        game_key = str(group.get("game_key") or "")
        gm_uid = str(group.get("gm_uid") or "")
        if not game_key or not gm_uid:
            raise DiceFrameHTTPError(bridge_text(language, "当前绑定信息不完整，请重新绑定。", "The binding is incomplete. Please bind this chat again."))
        return group, game_key, gm_uid

    def _require_actor(self, message: BridgeInput) -> tuple[dict[str, Any], str, str]:
        group, game_key, _gm_uid = self._require_group(message.stream_id, self._language(message))
        language = self._group_language(group)
        player = self.store.player(message.stream_id, message.platform_user_id)
        if not player:
            command = self._cmd("join Character Name" if bridge_is_english(language) else "加入 角色名")
            raise DiceFrameHTTPError(bridge_text(language, "你还没有认领角色。请先发送 {cmd}。", "You have not claimed a character yet. First send {cmd}.", cmd=command))
        actor = str(player.get("user_id") or "")
        if not actor:
            raise DiceFrameHTTPError(bridge_text(language, "你的角色映射不完整，请重新加入角色。", "Your character mapping is incomplete. Please claim the character again."))
        return group, game_key, actor

    def _require_actor_or_group_gm(self, message: BridgeInput) -> tuple[dict[str, Any], str, str]:
        group, game_key, gm_uid = self._require_group(message.stream_id, self._language(message))
        player = self.store.player(message.stream_id, message.platform_user_id)
        actor = str((player or {}).get("user_id") or gm_uid)
        return group, game_key, actor

    async def _refresh_roster(self, stream_id: str, group: dict[str, Any], actor: str) -> list[dict[str, Any]]:
        game_key = str(group.get("game_key") or "")
        data = await self.client.characters(game_key, actor)
        players = data.get("players") if isinstance(data.get("players"), list) else []
        roster = [item for item in players if isinstance(item, dict)]
        await self.store.update_roster(stream_id, roster)
        group["roster"] = roster
        return roster

    async def _pending_payments(self, game_key: str, actor: str) -> list[dict[str, Any]]:
        detail = await self.client.detail(game_key, actor)
        payments = detail.get("pending_payments") if isinstance(detail.get("pending_payments"), list) else []
        gm_uid = str(detail.get("gm_uid") or "")
        return [
            item for item in payments
            if isinstance(item, dict)
            and item.get("status", "pending") == "pending"
            and (actor == gm_uid or str(item.get("uid") or "") == actor)
        ]

    async def _format_status(self, player: dict[str, Any], group: dict[str, Any], language: str) -> str:
        english = bridge_is_english(language)
        name = str(player.get("character_name") or player.get("user_id") or ("Character" if english else "角色"))
        sheet = player.get("character_sheet") if isinstance(player.get("character_sheet"), dict) else {}
        lines = [f"{name} status" if english else f"{name} 状态"]
        hp = sheet.get("hp")
        max_hp = sheet.get("max_hp")
        if hp is not None or max_hp is not None:
            lines.append(f"HP: {hp}/{max_hp}" if english else f"HP：{hp}/{max_hp}")
        if sheet.get("gold") is not None:
            lines.append(f"Gold: {sheet.get('gold')}" if english else f"金币：{sheet.get('gold')}")
        attrs = sheet.get("attributes_display") or self._format_attrs(sheet.get("attributes"), language)
        if attrs:
            lines.append(f"Attributes: {attrs}" if english else f"属性：{attrs}")
        skills = self._format_skills(sheet.get("skills"), language)
        if skills:
            lines.append(f"Skills: {skills}" if english else f"技能：{skills}")
        status = sheet.get("status")
        if status:
            lines.append(f"Condition: {status}" if english else f"状态：{status}")
        link = await self._join_link(str(group.get("game_key") or ""), str(player.get("user_id") or ""))
        if link:
            lines.append(f"Web page: {link}" if english else f"网页入口：{link}")
        return "\n".join(lines)

    def _format_advance_response(self, result: dict[str, Any], language: str) -> str:
        english = bridge_is_english(language)
        lines: list[str] = []
        narration = str(result.get("narration") or result.get("message") or "").strip()
        if narration:
            lines.append(narration)
        forced = result.get("forced_waiting") if isinstance(result.get("forced_waiting"), list) else []
        if forced:
            lines.append(
                "Default actions added for: " + ", ".join(str(item) for item in forced)
                if english else
                "已为未行动角色补默认行动：" + "、".join(str(item) for item in forced)
            )
        auto_rolls = result.get("auto_rolls") if isinstance(result.get("auto_rolls"), list) else []
        if auto_rolls:
            roll_text = (", " if english else "、").join(f"{item.get('user_id')}={item.get('value')}" for item in auto_rolls if isinstance(item, dict))
            lines.append(("Pending rolls resolved: " if english else "已自动处理待掷骰：") + roll_text)
        pending = result.get("pending_payments") if isinstance(result.get("pending_payments"), list) else []
        if pending:
            lines.append(
                f"Pending payments are available; send {self._cmd('pay')} to review them."
                if english else
                f"有待处理支付，发送 {self._cmd('支付')} 查看。"
            )
        quick_actions = result.get("quick_actions") if isinstance(result.get("quick_actions"), list) else []
        if quick_actions:
            lines.append(
                "Suggested actions: " + "; ".join(str(item) for item in quick_actions[:4])
                if english else
                "可选行动：" + "；".join(str(item) for item in quick_actions[:4])
            )
        return "\n".join(lines).strip() or ("Game advanced." if english else "推进完成。")

    def _bound_help_text(self, group: dict[str, Any] | None, language: str = "") -> str:
        if not group:
            return self._unbound_text(language)
        language = self._group_language(group)
        return bound_help_text(group, command_prefix=self.config.command_prefix, language=language)

    def _unbound_text(self, language: str = "") -> str:
        language = bridge_language(language or self.config.default_language)
        if bridge_is_english(language):
            return (
                "This chat is not bound to a DiceFrame game yet.\n"
                "The GM should generate a one-time Bot binding token in DiceFrame, then send:\n"
                f"{self._cmd('bind <game_key> <one-time-token>')}"
            )
        return (
            "当前聊天流尚未绑定 DiceFrame 对局。\n"
            "GM 请在 DiceFrame 网页生成一次性 Bot 绑定凭证，然后发送：\n"
            f"{self._cmd('绑定 <game_key> <一次性凭证>')}"
        )

    def _cmd(self, command: str) -> str:
        prefix = str(self.config.command_prefix or "").strip()
        command = str(command or "").strip()
        if prefix == "跑团" and command and not re.search(r"[\u3400-\u9fff]", command):
            prefix = "/df"
        return f"{prefix} {command}".strip() if command else prefix

    def _can_advance(self, group: dict[str, Any], platform_user_id: str) -> bool:
        user = str(platform_user_id or "").strip()
        if user and user == str(group.get("gm_platform_id") or "").strip():
            return True
        allowed = {item.strip().lower() for item in self.config.advance_allowed_users if item.strip()}
        return user.lower() in allowed

    async def _join_link(self, game_key: str, user: str = "") -> str:
        if not game_key:
            return ""
        override = str(self.config.public_base_url or "").strip()
        if override:
            return build_join_link(override, game_key, user)
        return await self.client.build_join_link(game_key, user)

    def _split_reply(self, reply: str, language: str = "") -> list[str]:
        max_chars = max(200, int(self.config.max_reply_chars or 1800))
        text = str(reply or "").strip() or bridge_text(
            language or self.config.default_language,
            "DiceFrame Bridge 没有返回内容。",
            "DiceFrame Bridge returned no content.",
        )
        return [text[index:index + max_chars] for index in range(0, len(text), max_chars)][:4]

    @staticmethod
    def _verb_and_rest(text: str) -> tuple[str, str]:
        parts = str(text or "").strip().split(maxsplit=1)
        return (parts[0], parts[1].strip()) if len(parts) > 1 else (parts[0] if parts else "", "")

    @staticmethod
    def _format_attrs(attrs: Any, language: str = "zh-CN") -> str:
        if not isinstance(attrs, dict) or not attrs:
            return ""
        return (", " if bridge_is_english(language) else "、").join(f"{key}:{value}" for key, value in list(attrs.items())[:8])

    @staticmethod
    def _format_skills(skills: Any, language: str = "zh-CN") -> str:
        if not isinstance(skills, list) or not skills:
            return ""
        names: list[str] = []
        for item in skills[:8]:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                value = item.get("value")
                if name:
                    names.append(f"{name}{f' {value}' if value not in (None, '') else ''}")
            else:
                value = str(item).strip()
                if value:
                    names.append(value)
        return (", " if bridge_is_english(language) else "、").join(names)

    def _language(self, message: BridgeInput, text: str = "") -> str:
        group = self.store.group(message.stream_id)
        if group:
            return self._group_language(group)
        if message.language:
            return bridge_language(message.language)
        return infer_command_language(text or message.text, self.config.default_language)

    def _group_language(self, group: dict[str, Any] | None) -> str:
        return bridge_language((group or {}).get("language") or self.config.default_language)
