"""OneBot 11 群消息到 TRPG HTTP API 的 P0 适配。"""

from __future__ import annotations

import logging
import re
import asyncio
from typing import Any, Protocol

from src.bots.qq.api_client import TRPGBotAPI
from src.bots.qq.card_renderer import render_card_png
from src.bots.qq.character_flow import QQCharacterFlowMixin
from src.bots.qq.command_matchers import (
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
    is_private_character_creation_request,
    is_private_log,
    is_recap,
    is_return,
    payment_decision,
    payment_index,
)
from src.bots.qq.config import QQBotConfig
from src.bots.qq.delivery import QQDeliveryMixin
from src.bots.qq.game_commands import QQGameCommandsMixin
from src.bots.qq.message_utils import invite_target_user_ids, mentions_self, message_text
from src.bots.qq.presenters import (
    background_lines,
    bind_success_text,
    bound_help_text,
    character_creation_lines,
    character_creation_text,
    character_draft_lines,
    character_draft_text,
    character_public_lines,
    format_action_result,
    format_character_attrs,
    format_character_items,
    format_character_skills,
    is_current_location,
    map_lines,
    map_text,
    match_roster_character,
    normalize_summary_line,
    payment_line,
    player_tutorial_lines,
    player_tutorial_text,
    recap_text,
    roster_name_by_uid,
    roster_names,
    text_contains_summary_line,
    unbound_group_text,
    unclaimed_player_text,
)
from src.bots.qq.store import QQSessionStore
from src.bots.qq.web_sync import QQWebSyncMixin


class GroupSender(Protocol):
    async def send_group_text(self, group_id: str, text: str) -> dict[str, Any]: ...
    async def send_private_text(self, user_id: str, text: str) -> dict[str, Any]: ...
    async def send_group_image(self, group_id: str, image_path: str, caption: str = "") -> dict[str, Any]: ...
    async def send_private_image(self, user_id: str, image_path: str, caption: str = "") -> dict[str, Any]: ...


class QQTRPGAdapter(QQDeliveryMixin, QQWebSyncMixin, QQCharacterFlowMixin, QQGameCommandsMixin):
    _mentions_self = staticmethod(mentions_self)
    _text = staticmethod(message_text)
    _invite_target_user_ids = staticmethod(invite_target_user_ids)

    _is_help = staticmethod(is_help)
    _is_character_create = staticmethod(is_character_create)
    _is_ai_character_create = staticmethod(is_ai_character_create)
    _is_invite = staticmethod(is_invite)
    _is_private_character_creation_request = staticmethod(is_private_character_creation_request)
    _is_private_log = staticmethod(is_private_log)
    _is_recap = staticmethod(is_recap)
    _is_map = staticmethod(is_map)
    _is_away = staticmethod(is_away)
    _is_return = staticmethod(is_return)
    _away_target_query = staticmethod(away_target_query)
    _is_advance = staticmethod(is_advance)
    _advance_force = staticmethod(advance_force)
    _is_payment_list = staticmethod(is_payment_list)
    _payment_decision = staticmethod(payment_decision)
    _payment_index = staticmethod(payment_index)

    _format_action_result = staticmethod(format_action_result)
    _recap_text = staticmethod(recap_text)
    _map_lines = staticmethod(map_lines)
    _is_current_location = staticmethod(is_current_location)
    _map_text = staticmethod(map_text)
    _normalize_summary_line = staticmethod(normalize_summary_line)
    _text_contains_summary_line = staticmethod(text_contains_summary_line)
    _payment_line = staticmethod(payment_line)
    _roster_names = staticmethod(roster_names)
    _roster_name_by_uid = staticmethod(roster_name_by_uid)
    _match_roster_character = staticmethod(match_roster_character)
    _bind_success_text = staticmethod(bind_success_text)
    _unbound_group_text = staticmethod(unbound_group_text)
    _unclaimed_player_text = staticmethod(unclaimed_player_text)
    _bound_help_text = staticmethod(bound_help_text)
    _character_creation_lines = staticmethod(character_creation_lines)
    _character_creation_text = staticmethod(character_creation_text)
    _character_draft_lines = staticmethod(character_draft_lines)
    _background_lines = staticmethod(background_lines)
    _character_public_lines = staticmethod(character_public_lines)
    _character_draft_text = staticmethod(character_draft_text)
    _format_character_attrs = staticmethod(format_character_attrs)
    _format_character_skills = staticmethod(format_character_skills)
    _format_character_items = staticmethod(format_character_items)
    _player_tutorial_lines = staticmethod(player_tutorial_lines)
    _player_tutorial_text = staticmethod(player_tutorial_text)

    @staticmethod
    def _render_card_png(*args, **kwargs):
        return render_card_png(*args, **kwargs)

    def __init__(self, api: TRPGBotAPI, store: QQSessionStore, sender: GroupSender,
                 config: QQBotConfig | None = None) -> None:
        self.api = api
        self.store = store
        self.sender = sender
        self.self_id = ""
        self.pending_dice: dict[str, str] = {}
        self.logger = logging.getLogger("trpg.qq.adapter")
        self.config = config
        config_data_path = getattr(config, "data_path", None)
        self.card_dir = ((config_data_path.parent if config_data_path else store.path.parent) / "cards")
        self.character_wizards: dict[str, dict[str, Any]] = {}
        self._web_sync_last_round: dict[str, int] = {}
        self._web_sync_seen_actions: dict[str, set[str]] = {}
        self._group_action_inflight: dict[str, bool] = {}

    async def handle_payload(self, payload: dict[str, Any]) -> None:
        if payload.get("post_type") == "meta_event" and payload.get("self_id"):
            self.self_id = str(payload["self_id"])
            return
        message_type = str(payload.get("message_type") or "")
        if payload.get("post_type") != "message" or message_type not in {"group", "private"}:
            return
        if str(payload.get("user_id") or "") == str(payload.get("self_id") or self.self_id):
            return
        message_id = str(payload.get("message_id") or "")
        if not await self.store.remember_message(message_id):
            return
        segments = payload.get("message") if isinstance(payload.get("message"), list) else []
        self_id = str(payload.get("self_id") or self.self_id)
        if self_id:
            self.self_id = self_id
        if message_type == "group" and not self._mentions_self(segments, self_id):
            return
        group_id = str(payload.get("group_id") or "")
        platform_user_id = str(payload.get("user_id") or "")
        if not await self._message_allowed(group_id, platform_user_id, message_type == "private"):
            return
        text = self._text(segments).strip()
        if not await self._remember_command_signature(message_type, group_id, platform_user_id, text):
            self.logger.info("QQ 重复命令已忽略: type=%s group=%s user=%s text=%s",
                             message_type, group_id, platform_user_id, text[:40])
            return
        # 私聊一律走 _handle_private：群临时会话的 private 消息也带 group_id，
        # 用 group_id 判断会让车卡描述漏进 _handle_command 被当成对局行动。
        if message_type == "private":
            if platform_user_id:
                try:
                    await self._handle_private(platform_user_id, text)
                except Exception as exc:
                    self.logger.exception("QQ 私聊指令处理失败")
                    if getattr(self.sender, "send_private_text", None):
                        await self._send_private_text(platform_user_id, f"服务暂时不可用：{exc}")
            return
        if not group_id or not platform_user_id:
            return
        try:
            await self._handle_command(group_id, platform_user_id, text, segments)
        except Exception as exc:
            self.logger.exception("QQ 指令处理失败")
            await self._send_group_text(group_id, f"服务暂时不可用：{exc}")

    async def _message_allowed(self, group_id: str, user_id: str, private: bool = False) -> bool:
        config = self.config
        if not config:
            return True
        if user_id in config.blocked_users:
            self.logger.warning("全局屏蔽用户 %s 的消息已丢弃", user_id)
            return False
        if config.block_official_bots and group_id:
            checker = getattr(self.sender, "is_official_bot", None)
            if checker and await checker(group_id, user_id):
                self.logger.warning("QQ 官方机器人消息已丢弃")
                return False
        if not config.chat_filter_enabled:
            return True
        configured = config.private_list if private else config.group_list
        mode = config.private_list_mode if private else config.group_list_mode
        target = user_id if private else group_id
        allowed = target in configured
        if mode == "blacklist":
            allowed = not allowed
        if not allowed and config.show_dropped_logs:
            self.logger.warning("%s %s 未通过聊天名单过滤", "私聊" if private else "群聊", target)
        return allowed

    async def _handle_private(self, platform_user_id: str, text: str) -> None:
        sender = getattr(self.sender, "send_private_text", None)
        if not sender:
            return
        wizard = self.character_wizards.get(platform_user_id)
        if wizard is not None:
            if self._wizard_stale(wizard):
                self.character_wizards.pop(platform_user_id, None)
            else:
                await self._handle_character_wizard_private(platform_user_id, text)
                return
        bindings = self.store.bindings_for_platform(platform_user_id)
        if not bindings:
            await self._send_private_text(platform_user_id, "尚未在任何已绑定群中认领角色。")
            return
        if len(bindings) > 1:
            await self._send_private_text(platform_user_id, "你在多个群中拥有角色，请在对应群内 @Bot 操作。")
            return
        group_id, player = bindings[0]
        game_key, actor = player["game_key"], player["user_id"]
        pending_key = QQSessionStore.player_key(group_id, platform_user_id)
        payment_decision = self._payment_decision(text)
        if payment_decision is not None:
            await self._resolve_payment_private(platform_user_id, game_key, actor, payment_decision, text)
            return
        if self._is_payment_list(text):
            await self._send_payment_list_private(platform_user_id, game_key, actor)
            return
        if self._is_private_log(text):
            await self._send_private_log_private(platform_user_id, game_key, actor)
            return
        if self._is_recap(text):
            await self._send_recap_private(platform_user_id, game_key, actor)
            return
        if text in {"状态", "我的状态"}:
            data = await self.api.characters(game_key, actor)
            found = next((item for item in data.get("players", []) if item.get("user_id") == actor), None)
            if not found:
                await self._send_private_text(platform_user_id, "未找到当前角色。")
                return
            sheet = found.get("character_sheet", {})
            await self._send_private_card(
                platform_user_id,
                title=str(found.get("character_name") or actor),
                subtitle="角色状态",
                lines=[
                    f"HP {sheet.get('hp',0)}/{sheet.get('max_hp',0)}  金币 {sheet.get('gold',0)}",
                ],
                fallback=f"{found.get('character_name') or actor}  HP {sheet.get('hp',0)}/{sheet.get('max_hp',0)}  金币 {sheet.get('gold',0)}",
            )
            return
        if text in {"掷骰", "骰子", "roll"}:
            pending = self.pending_dice.get(pending_key)
            if not pending:
                await self._send_private_text(platform_user_id, "当前没有等待确认的骰子。")
                return
            result = await self._action_with_private_thinking(platform_user_id, game_key, actor, pending, confirm=True)
            self.pending_dice.pop(pending_key, None)
            await self._send_private_card(
                platform_user_id,
                title="行动结果",
                subtitle=str(result.get("phase") or "done"),
                lines=self._format_action_result(result).splitlines(),
                fallback=self._format_action_result(result),
            )
            return
        if not text or text in {"帮助", "help", "?", "？"}:
            await self._send_private_card(
                platform_user_id,
                title="DiceFrame 私聊帮助",
                subtitle="功能入口，不提交正式行动",
                lines=["私聊不会进入正式对局", "发送“状态”查看角色", "发送“前情”查看公开提要", "发送“掷骰”确认检定", "发送“感知”查看私密信息", "发送“支付”处理待付款", "车卡请回群聊发 @我 车卡 / @我 AI车卡"],
                fallback="私聊不会进入正式对局。可发送“状态”“前情”“掷骰”“感知”“支付”。车卡请回群聊发 @我 车卡 / @我 AI车卡。",
            )
            return
        if self._is_private_character_creation_request(text):
            await self._send_private_text(
                platform_user_id,
                "车卡不要在普通私聊里直接说，避免误进剧情。\n"
                "请回群聊发送：@我 车卡 / @我 新建角色。\n"
                "如果想用 AI 辅助车卡，请在群聊发送：@我 AI车卡；之后我会私聊引导你。",
            )
            return
        await self._send_private_text(
            platform_user_id,
            "我在。私聊 Bot 只做功能操作，不会把对话送进正式对局。\n"
            "可用：状态 / 前情 / 感知 / 支付 / 掷骰。\n"
            "车卡请回群聊发：@我 车卡；AI 辅助车卡发：@我 AI车卡。\n"
            "正式行动请在群聊 @Bot 发送，或到网页游玩界面提交。",
        )

    async def _handle_command(
        self,
        group_id: str,
        platform_user_id: str,
        text: str,
        segments: list[dict[str, Any]] | None = None,
    ) -> None:
        bind_match = re.match(r"^绑定\s+(\S+)\s+(\S+)\s*$", text)
        if bind_match:
            result = await self.api.bind_game(bind_match.group(1), bind_match.group(2))
            await self.store.bind_group(
                group_id,
                result["game_key"],
                platform_user_id,
                result["gm_uid"],
                result.get("players", []),
            )
            link = await self._join_link(result["game_key"])
            await self._send_group_card(
                group_id,
                title="DiceFrame 已绑定",
                subtitle=str(result.get("world_name") or result["game_key"]),
                lines=[
                    "GM 身份已确认。",
                    "下一步：玩家发送“@我 加入 角色名”。",
                    f"可认领：{self._roster_names({'roster': result.get('players', [])})}",
                    "开始玩：@我 我调查四周。",
                    "常用：@我 状态 / @我 前情 / @我 地图 / @我 掷骰 / @我 帮助",
                ],
                fallback=self._bind_success_text(result),
                link_text=self._link_text("网页入口", link),
            )
            return

        group = self.store.group(group_id)
        if not group:
            await self._send_group_text(group_id, self._unbound_group_text())
            return
        player = self.store.player(group_id, platform_user_id)
        if self._is_ai_character_create(text):
            await self._send_character_creation_guide(
                group_id, platform_user_id, group, start_private_ai=True
            )
            return
        if self._is_character_create(text):
            await self._send_character_creation_guide(group_id, platform_user_id, group)
            return
        if self._is_invite(text):
            target_user_ids = self._invite_target_user_ids(segments or [], self.self_id, platform_user_id, text)
            await self._send_invite_link(group_id, group, target_user_ids=target_user_ids)
            return
        if self._is_recap(text):
            await self._send_recap_group(group_id, group)
            return
        if self._is_map(text):
            await self._send_map_group(group_id, group)
            return
        if self._is_advance(text):
            await self._advance_group(group_id, platform_user_id, group, text)
            return
        if self._is_away(text) or self._is_return(text):
            await self._set_away_group(group_id, platform_user_id, group, text, away=self._is_away(text))
            return
        if self._is_help(text):
            await self._send_group_card(
                group_id,
                title="DiceFrame 群聊新手指南",
                subtitle="按这 3 步玩",
                lines=[
                    "1. 认领角色：@我 加入 角色名",
                    f"可认领：{self._roster_names(group)}",
                    "没有角色：@我 新建角色 / 车卡；想 AI 辅助：@我 AI车卡",
                    "邀请玩家：@我 邀请",
                    "补前情：@我 前情",
                    "看地图：@我 地图",
                    "2. 描述行动：@我 我观察四周",
                    "3. 需要检定时：@我 掷骰",
                    "GM 推进：@我 推进 / @我 下一轮",
                    "临时离开：@我 暂离；回来：@我 回来",
                    "随时查看：@我 状态",
                ],
                fallback=self._bound_help_text(group),
            )
            return
        if text.startswith("加入"):
            await self._join_character(group_id, platform_user_id, text[2:].strip(), group)
            return
        if not player:
            await self._send_group_text(group_id, self._unclaimed_player_text(group))
            return
        actor = player["user_id"]
        game_key = group["game_key"]
        pending_key = QQSessionStore.player_key(group_id, platform_user_id)
        payment_decision = self._payment_decision(text)

        if self._is_help(text):
            await self._send_group_card(
                group_id,
                title="DiceFrame 群聊帮助",
                subtitle="群内轻量跑团入口",
                lines=[
                    "@我 加入 <角色名>",
                    "@我 新建角色 / 车卡",
                    "@我 邀请",
                    "@我 <自然语言行动>",
                    "@我 掷骰",
                    "@我 状态",
                    "@我 前情",
                    "@我 地图",
                    "@我 感知（私聊发送）",
                    "@我 支付 / 确认支付 / 拒绝支付",
                    "@我 推进 / 下一轮（GM 或授权账号）",
                    "@我 暂离 / 回来",
                    "每轮最多修改行动 3 次，AI 只读取最后一版。",
                ],
                fallback="@我 加入 <角色名>\n@我 新建角色 / 车卡\n@我 邀请\n@我 <自然语言行动>\n@我 掷骰\n@我 状态\n@我 前情\n@我 地图\n@我 感知\n@我 支付 / 确认支付 / 拒绝支付\n@我 推进 / 下一轮（GM 或授权账号）\n@我 暂离 / 回来\n每轮最多修改行动 3 次，AI 只读取最后一版。",
            )
            return
        if text in {"加点", "属性"}:
            await self._send_attributes_card(group_id, game_key, actor)
            return
        allocate_match = re.match(r"^加\s+(\S+)\s+(\d+)\s*$", text)
        if allocate_match:
            await self._allocate_attribute(group_id, game_key, actor, allocate_match.group(1), int(allocate_match.group(2)))
            return
        if payment_decision is not None:
            await self._resolve_payment_group(group_id, platform_user_id, game_key, actor, payment_decision, text)
            return
        if self._is_payment_list(text):
            await self._send_payment_list_group(group_id, platform_user_id, game_key, actor)
            return
        if self._is_private_log(text):
            await self._send_private_log_group(group_id, platform_user_id, game_key, actor)
            return
        if self._is_recap(text):
            await self._send_recap_group(group_id, group)
            return
        if self._is_map(text):
            await self._send_map_group(group_id, group)
            return
        if self._is_away(text) or self._is_return(text):
            await self._set_away_group(group_id, platform_user_id, group, text, away=self._is_away(text), actor_uid=actor)
            return
        if text in {"状态", "我的状态"}:
            await self._send_status(group_id, platform_user_id, game_key, actor)
            return
        if text in {"掷骰", "骰子", "roll"}:
            pending_text = self.pending_dice.get(pending_key)
            if not pending_text:
                detail = await self.api.game_detail(game_key, actor)
                submitted = (detail.get("multiplayer") or {}).get("submitted_actions") or []
                own_action = next(
                    (
                        item for item in submitted
                        if item.get("user_id") == actor and item.get("dice_pending")
                    ),
                    None,
                )
                if own_action:
                    pending_text = str(own_action.get("text") or "")
            if not pending_text:
                await self._send_group_text(group_id, "当前没有等待确认的骰子。")
                return
            self._group_action_inflight[game_key] = True
            try:
                result = await self._action_with_group_thinking(group_id, game_key, actor, pending_text, confirm=True)
                self.pending_dice.pop(pending_key, None)
                await self._send_action_result(group_id, game_key, actor, result)
            finally:
                self._group_action_inflight[game_key] = False
            return
        if not text:
            await self._send_group_text(group_id, "请在 @我 后描述行动，或发送“帮助”。")
            return

        self._group_action_inflight[game_key] = True
        try:
            result = await self._action_with_group_thinking(group_id, game_key, actor, text)
            if result.get("phase") == "dice":
                self.pending_dice[pending_key] = text
                await self._send_group_text(group_id, f"@{platform_user_id} 这次行动需要掷骰。回复 @我 掷骰，或重新描述行动。")
                return
            await self._send_action_result(group_id, game_key, actor, result)
        finally:
            self._group_action_inflight[game_key] = False

    async def _action_with_group_thinking(self, group_id: str, game_key: str, actor: str,
                                          text: str, *, confirm: bool = False) -> dict[str, Any]:
        show_thinking = await self._will_trigger_gm_thinking(game_key, actor)
        task = asyncio.create_task(self.api.action(game_key, actor, text, confirm=confirm, source="group"))
        if show_thinking:
            await self._send_group_thinking(group_id)
        return await task

    async def _action_with_private_thinking(self, platform_user_id: str, game_key: str, actor: str,
                                            text: str, *, confirm: bool = False) -> dict[str, Any]:
        show_thinking = await self._will_trigger_gm_thinking(game_key, actor)
        task = asyncio.create_task(self.api.action(game_key, actor, text, confirm=confirm, source="group"))
        if show_thinking:
            await self._send_private_thinking(platform_user_id)
        return await task

    async def _will_trigger_gm_thinking(self, game_key: str, actor: str) -> bool:
        try:
            detail = await self.api.game_detail(game_key, actor)
        except Exception:
            self.logger.warning("获取行动前多人状态失败，跳过 QQ 思考提示", exc_info=True)
            return False
        multiplayer = detail.get("multiplayer") if isinstance(detail.get("multiplayer"), dict) else {}
        if detail.get("solo_mode") or multiplayer.get("solo_mode"):
            return True
        waiting = multiplayer.get("waiting_players") if isinstance(multiplayer.get("waiting_players"), list) else []
        waiting_ids = {
            str(item.get("user_id") or "")
            for item in waiting
            if isinstance(item, dict) and str(item.get("user_id") or "")
        }
        return waiting_ids == {actor}

    async def _send_group_thinking(self, group_id: str) -> None:
        await self._send_group_text(group_id, "GM 正在思考中，生成下一段剧情…")

    async def _send_private_thinking(self, platform_user_id: str) -> None:
        await self._send_private_text(platform_user_id, "GM 正在思考中，生成下一段剧情…")

    async def _join_character(self, group_id: str, platform_user_id: str, name: str,
                              group: dict[str, Any]) -> None:
        if not name:
            await self._send_group_text(group_id, "请发送：@我 加入 <角色名>")
            return
        group = await self._refresh_group_roster(group_id, group)
        matches = self._match_roster_character(group.get("roster", []), name)
        if len(matches) != 1:
            await self._send_group_text(group_id, "没有找到唯一匹配的角色，请输入完整角色名。")
            return
        if not await self.store.bind_player(group_id, platform_user_id, str(matches[0]["user_id"])):
            await self._send_group_text(group_id, "该角色已被其他群成员认领。")
            return
        await self._send_group_text(group_id, f"已认领角色：{matches[0].get('character_name') or name}")

    async def _refresh_group_roster(self, group_id: str, group: dict[str, Any]) -> dict[str, Any]:
        game_key = str(group.get("game_key") or "")
        gm_uid = str(group.get("gm_uid") or "")
        if not game_key or not gm_uid:
            return group
        try:
            data = await self.api.characters(game_key, gm_uid)
        except Exception:
            self.logger.warning("刷新群聊角色名单失败", exc_info=True)
            return group
        players = data.get("players") if isinstance(data.get("players"), list) else None
        if players is None:
            return group
        roster_by_key: dict[str, dict[str, Any]] = {}
        for item in group.get("roster", []):
            if isinstance(item, dict):
                key = str(item.get("user_id") or item.get("character_name") or "")
                if key:
                    roster_by_key[key] = item
        for item in players:
            if isinstance(item, dict):
                key = str(item.get("user_id") or item.get("character_name") or "")
                if key:
                    roster_by_key[key] = item
        roster = list(roster_by_key.values())
        await self.store.update_group_roster(group_id, roster)
        fresh = dict(group)
        fresh["roster"] = roster
        return fresh

    async def _send_action_result(self, group_id: str, game_key: str, actor: str,
                                  result: dict[str, Any]) -> None:
        roll = result.get("roll") or {}
        advanced = bool(result.get("advanced"))
        narration = str(result.get("narration") or "").strip()
        quick_actions = result.get("quick_actions") if isinstance(result.get("quick_actions"), list) else []
        pending = result.get("pending_payments") if isinstance(result.get("pending_payments"), list) else []

        if advanced:
            # 群行动触发推进：拉 game_detail 拿真实 recap（result.recap 是 last_state_update，无轮次字段，
            # 直接读会让 round_number=0、去重失效，_poll 又发第二张）。发合并卡后标记该轮已群发。
            round_number = 0
            state_changes: list = []
            try:
                detail = await self.api.game_detail(game_key, actor)
                round_number = int(detail.get("round_number") or 0)
                recap = detail.get("recap") or {}
                recent = recap.get("recent_rounds") if isinstance(recap.get("recent_rounds"), list) else []
                if recent and isinstance(recent[-1], dict):
                    latest = recent[-1]
                    gm = str(latest.get("gm_response") or "").strip()
                    if gm:
                        narration = gm
                    sc = latest.get("state_changes")
                    if isinstance(sc, list):
                        state_changes = sc
                qa = detail.get("quick_actions")
                if isinstance(qa, list):
                    quick_actions = qa
            except Exception:
                self.logger.warning("群聊行动推进后拉取回合详情失败", exc_info=True)
            await self._send_round_summary_card(
                group_id,
                round_number,
                narration,
                state_changes,
                roll=roll,
                pending_payments=pending,
                quick_actions=quick_actions,
            )
            if game_key and round_number:
                self._web_sync_last_round[game_key] = round_number
                self._web_sync_seen_actions.setdefault(game_key, set()).clear()
            return

        # 未推进：行动结果简短卡（roll + 等待提示 + pending）
        text = self._format_action_result(result)
        lines: list = []
        if roll and roll.get("value") is not None:
            lines.append(f"🎲 {str(roll.get('dice_system', '')).upper()} = {roll.get('value')}")
        if narration:
            lines.extend(narration.splitlines()[:8])
        if pending:
            lines.append("")
            lines.append("-- 待确认支付 --")
            for p in pending:
                if not isinstance(p, dict):
                    continue
                amount = int(p.get("amount", 0) or 0)
                reason = str(p.get("reason") or "GM 建议支付").strip()
                lines.append(f"{reason}：{amount} 金币")
            lines.append("发 @我 支付 查看并确认/拒绝")
        await self._send_group_card(
            group_id,
            title="行动结果",
            subtitle=str(result.get("phase") or "done"),
            lines=lines or [text],
            fallback=text,
        )

    async def _send_round_summary_card(
        self,
        group_id: str,
        round_number: int,
        gm_response: str,
        state_changes,
        *,
        roll: dict | None = None,
        pending_payments=None,
        quick_actions: list[str] | None = None,
    ) -> None:
        """推进后合并卡：GM 叙事正文 + 状态变动 + 可选行动（一张图，不分发多张）。"""
        lines: list[str] = []
        if roll and roll.get("value") is not None:
            ds = str(roll.get("dice_system") or "").upper()
            lines.append(f"🎲 {ds} = {roll.get('value')}")
        gm_response = str(gm_response or "").strip()
        if gm_response:
            paras = [p.strip() for p in re.split(r"\n\s*\n", gm_response) if p.strip()]
            if not paras:
                paras = [p.strip() for p in gm_response.splitlines() if p.strip()]
            for i, p in enumerate(paras):
                if lines:
                    lines.append("")
                lines.append(p)
        if isinstance(state_changes, list) and state_changes:
            new_state_lines = [
                str(s) for s in state_changes
                if not self._text_contains_summary_line(gm_response, str(s))
            ]
            if new_state_lines:
                if lines:
                    lines.append("")
                lines.extend(new_state_lines)
        if pending_payments:
            if lines:
                lines.append("")
            lines.append("-- 待确认支付 --")
            for p in pending_payments:
                if not isinstance(p, dict):
                    continue
                amount = int(p.get("amount", 0) or 0)
                reason = str(p.get("reason") or "GM 建议支付").strip()
                lines.append(f"{reason}：{amount} 金币")
            lines.append("发 @我 支付 查看并确认/拒绝")
        # 可选行动两列（最下面，footer 前）：一行两个选项
        hint_pairs: list[tuple[str, str]] = []
        if isinstance(quick_actions, list) and quick_actions:
            items = [str(qa).strip() for qa in quick_actions[:4] if str(qa).strip()]
            for i in range(0, len(items), 2):
                left = items[i]
                right = items[i + 1] if i + 1 < len(items) else ""
                hint_pairs.append((left, right))
        if not lines and not hint_pairs:
            return
        subtitle = f"第 {round_number} 轮" if round_number else ""
        fallback_lines = [ln if isinstance(ln, str) else ln[0] for ln in lines if ln]
        fallback_lines.extend(f"{l}　{r}".strip() for l, r in hint_pairs if (l or r))
        await self._send_group_card(
            group_id,
            title="GM 叙事",
            subtitle=subtitle,
            lines=lines,
            fallback="\n".join(fallback_lines),
            hint=hint_pairs or None,
        )

    async def _join_link(self, game_key: str, user: str = "") -> str:
        builder = getattr(self.api, "build_join_link", None)
        if not builder:
            return ""
        try:
            return await builder(game_key, user)
        except Exception:
            self.logger.warning("生成网页入口失败", exc_info=True)
            return ""

    def _link_reminders_enabled(self) -> bool:
        return bool(getattr(self.config, "link_reminder_enabled", True))

    def _ai_character_creation_enabled(self) -> bool:
        return bool(getattr(self.config, "ai_character_creation_enabled", True))

    def _link_text(self, label: str, link: str) -> str:
        if not self._link_reminders_enabled() or not link:
            return ""
        return f"{label}：{link}"

    def _link_suffix(self, label: str, link: str) -> str:
        text = self._link_text(label, link)
        return f"\n{text}" if text else ""

    async def _remember_command_signature(self, message_type: str, group_id: str,
                                          platform_user_id: str, text: str) -> bool:
        window = float(getattr(self.config, "command_dedup_window_sec", 0) or 0) if self.config else 0
        normalized_text = re.sub(r"\s+", " ", text or "").strip()
        scope = group_id if message_type == "group" else f"private:{platform_user_id}"
        signature = f"{message_type}:{scope}:{platform_user_id}:{normalized_text}"
        return await self.store.remember_command(signature, window)
