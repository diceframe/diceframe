"""Character creation and invite flows for the QQ bot adapter."""

from __future__ import annotations

import json
import re
import time
from typing import Any


WIZARD_TTL_SEC = 600


class QQCharacterFlowMixin:
    async def _send_character_creation_guide(
        self,
        group_id: str,
        platform_user_id: str,
        group: dict[str, Any],
        *,
        start_private_ai: bool = False,
    ) -> None:
        game_key = str(group.get("game_key") or "")
        gm_uid = str(group.get("gm_uid") or "")
        data: dict[str, Any] = {}
        if game_key and gm_uid:
            try:
                data = await self.api.characters(game_key, gm_uid)
            except Exception:
                self.logger.warning("获取角色创建规则失败", exc_info=True)
        link = await self._join_link(game_key)
        lines = self._character_creation_lines(data)
        await self._send_group_card(
            group_id,
            title="新建角色 / 车卡",
            subtitle="按这些内容填写",
            lines=lines,
            fallback=self._character_creation_text(lines, link if self._link_reminders_enabled() else ""),
            link_text=self._link_text("网页建卡入口", link),
        )
        if start_private_ai and self._ai_character_creation_enabled():
            await self._start_character_creation_wizard(group_id, platform_user_id, game_key, link, lines)
        elif start_private_ai:
            await self._send_group_text(group_id, "当前已关闭 AI 辅助车卡；请按群里的清单或网页入口填写。")

    async def _start_character_creation_wizard(self, group_id: str, platform_user_id: str,
                                               game_key: str, link: str, guide_lines: list[str]) -> None:
        self.character_wizards[platform_user_id] = {
            "group_id": group_id,
            "game_key": game_key,
            "link": link,
            "guide_lines": guide_lines,
            "state": "awaiting_prompt",
            "updated_at": time.time(),
        }
        try:
            await self._send_private_text(
                platform_user_id,
                "我可以帮你 AI 辅助车卡。\n"
                "直接告诉我你想玩什么角色，例如：落魄剑修，嘴硬心软，擅长御剑和医毒。\n"
                "我会按本局规则生成草稿；你确认后，我会把公开版发到群里给大家看。"
                + self._link_suffix("网页建卡入口", link),
            )
        except Exception:
            self.character_wizards.pop(platform_user_id, None)
            self.logger.warning("QQ 主动私聊 AI 车卡失败", exc_info=True)
            await self._send_group_text(
                group_id,
                f"@{platform_user_id} 我暂时无法私聊你。可以添加 Bot 好友或开启临时会话；也可以直接用网页建卡入口填写。",
            )

    async def _handle_character_wizard_private(self, platform_user_id: str, text: str) -> None:
        wizard = self.character_wizards.get(platform_user_id)
        if not wizard:
            return
        wizard["updated_at"] = time.time()
        normalized = re.sub(r"\s+", "", text.strip().lower())
        if normalized in {"取消", "退出", "不车了", "停止", "cancel", "stop"}:
            self.character_wizards.pop(platform_user_id, None)
            await self._send_private_text(platform_user_id, "已取消 AI 辅助车卡。你也可以随时在群里重新发送 @我 车卡。")
            return
        if wizard.get("state") == "draft" and normalized in {"确认", "提交", "就这个", "可以", "发群里", "公示", "ok", "yes"}:
            await self._publish_character_draft(platform_user_id, wizard)
            self.character_wizards.pop(platform_user_id, None)
            return

        prompt = text.strip()
        if not prompt:
            await self._send_private_text(platform_user_id, "直接描述你想玩的角色就行，例如：落魄剑修，嘴硬心软，擅长御剑和医毒。")
            return

        if wizard.get("state") == "draft":
            previous = json.dumps(wizard.get("draft") or {}, ensure_ascii=False)
            prompt = (
                "请根据玩家修改意见重做角色草稿。\n"
                f"上一版草稿：{previous}\n"
                f"修改意见：{prompt}"
            )

        await self._send_private_text(platform_user_id, "收到，AI 正在生成角色草稿中，请稍等…")
        result = await self.api.generate_character(prompt, game_key=str(wizard.get("game_key") or ""))
        if not result.get("ok"):
            await self._send_private_text(platform_user_id, f"AI 车卡失败：{result.get('error') or '返回内容不可用'}\n你可以换个描述再试，或直接打开网页建卡。")
            return
        draft = result.get("character") if isinstance(result.get("character"), dict) else {}
        if not draft:
            await self._send_private_text(platform_user_id, "AI 没有生成可用草稿。你可以换个描述再试。")
            return
        wizard["state"] = "draft"
        wizard["draft"] = draft
        lines = self._character_draft_lines(draft)
        link = str(wizard.get("link") or "")
        await self._send_private_card(
            platform_user_id,
            title="AI 角色草稿",
            subtitle="回复“确认”后发到群里公示",
            lines=[
                *lines,
                "想改就直接说修改意见；不满意可回“取消”。",
            ],
            fallback=self._character_draft_text("AI 角色草稿", lines, link if self._link_reminders_enabled() else "")
            + "\n回复“确认”后发到群里公示；想改就直接说修改意见。",
            link_text=self._link_text("网页建卡入口", link),
        )

    @staticmethod
    def _wizard_stale(wizard: dict[str, Any]) -> bool:
        last = wizard.get("updated_at") or wizard.get("created_at")
        if not last:
            return False
        return (time.time() - float(last)) > WIZARD_TTL_SEC

    async def _publish_character_draft(self, platform_user_id: str, wizard: dict[str, Any]) -> None:
        group_id = str(wizard.get("group_id") or "")
        draft = wizard.get("draft") if isinstance(wizard.get("draft"), dict) else {}
        if not group_id or not draft:
            await self._send_private_text(platform_user_id, "当前草稿状态不完整，请在群里重新发送 @我 车卡。")
            return
        lines = [
            f"玩家：{platform_user_id}",
            *self._character_public_lines(draft),
            "如需调整，GM 可在网页角色页直接修改。",
        ]
        await self._send_group_card(
            group_id,
            title="新角色草稿",
            subtitle=str(draft.get("character_name") or "未命名角色"),
            lines=lines,
            fallback=self._character_draft_text("新角色草稿", lines),
        )
        await self._send_private_text(platform_user_id, "已把公开版发到群里。你可以按网页建卡入口继续填写或微调。")

    async def _send_invite_link(
        self,
        group_id: str,
        group: dict[str, Any],
        *,
        target_user_ids: list[str] | None = None,
    ) -> None:
        game_key = str(group.get("game_key") or "")
        link = await self._join_link(game_key)
        if not link:
            await self._send_group_text(group_id, "暂时生成不了邀请链接，请检查网页端公开地址配置。")
            await self._send_player_tutorial_card(group_id)
            return
        await self._send_group_text(
            group_id,
            "邀请链接：\n"
            f"　　{link}\n"
            "　　玩家打开后可创建/认领角色；如果群里已绑定 Bot，也可以回来发送：@我 加入 角色名",
        )
        await self._send_player_tutorial_card(group_id)
        for target_user_id in target_user_ids or []:
            try:
                await self._send_private_invite(target_user_id, link)
                await self._send_group_text(group_id, f"已私聊邀请 QQ {target_user_id}。")
            except Exception:
                self.logger.warning("QQ 私聊邀请失败: user=%s", target_user_id, exc_info=True)
                await self._send_group_text(
                    group_id,
                    f"暂时无法私聊邀请 QQ {target_user_id}，请让 TA 添加 Bot 好友或开启临时会话；"
                    "也可以直接使用上面的邀请链接。",
                )

    async def _send_private_invite(self, user_id: str, link: str) -> None:
        lines = [
            "你被邀请加入一局 DiceFrame 跑团。",
            "打开网页入口创建/认领角色。",
            "回到群聊后可发送：@我 加入 角色名。",
            "不会玩也没关系：群里发送 @我 帮助。",
        ]
        fallback = "\n".join(lines) + self._link_suffix("网页入口", link)
        await self._send_private_card(
            user_id,
            title="群聊跑团邀请",
            subtitle="打开链接就能加入",
            lines=lines,
            fallback=fallback,
            link_text=self._link_text("网页入口", link),
        )

    async def _send_player_tutorial_card(self, group_id: str) -> None:
        lines = self._player_tutorial_lines()
        await self._send_group_card(
            group_id,
            title="群聊跑团新玩家一图流",
            subtitle="进群先看这 5 步",
            lines=lines,
            fallback=self._player_tutorial_text(lines),
        )
