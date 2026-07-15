"""Message delivery helpers for QQ bot adapters."""

from __future__ import annotations

import asyncio
import random

from src.bots.qq.card_renderer import BRAND_FOOTER, cleanup_card_cache, render_card_png


class QQDeliveryMixin:
    async def _send_group_card(self, group_id: str, *, title: str, subtitle: str = "",
                               lines: list[str] | None = None, fallback: str = "",
                               link_text: str = "",
                               hint: list[tuple[str, str]] | None = None) -> None:
        sender = getattr(self.sender, "send_group_image", None)
        if sender:
            try:
                image = self._render_card_png(self.card_dir, title=title, subtitle=subtitle, lines=lines or [], footer=BRAND_FOOTER, hint=hint)
                await self._cleanup_card_cache()
                await self._reply_delay()
                await sender(group_id, str(image))
                if link_text:
                    await self._send_group_text(group_id, link_text)
                return
            except Exception:
                self.logger.warning("群聊卡片发送失败，降级为文本", exc_info=True)
        await self._send_group_text(group_id, fallback or "\n".join([title, subtitle, *(lines or [])]).strip())

    async def _send_private_card(self, user_id: str, *, title: str, subtitle: str = "",
                                 lines: list[str] | None = None, fallback: str = "",
                                 link_text: str = "") -> None:
        sender = getattr(self.sender, "send_private_image", None)
        if sender:
            try:
                image = self._render_card_png(self.card_dir, title=title, subtitle=subtitle, lines=lines or [], footer=BRAND_FOOTER)
                await self._cleanup_card_cache()
                await self._reply_delay()
                await sender(user_id, str(image))
                if link_text:
                    await self.sender.send_private_text(user_id, link_text)
                return
            except Exception:
                self.logger.warning("QQ 私聊卡片发送失败，降级为文本", exc_info=True)
        await self._send_private_text(user_id, fallback or "\n".join([title, subtitle, *(lines or [])]).strip())

    async def _send_group_text(self, group_id: str, text: str) -> dict:
        await self._reply_delay()
        return await self.sender.send_group_text(group_id, text)

    async def _send_private_text(self, user_id: str, text: str) -> dict:
        await self._reply_delay()
        return await self.sender.send_private_text(user_id, text)

    async def _reply_delay(self) -> None:
        config = self.config
        if not config:
            return
        min_sec = max(0.0, float(getattr(config, "reply_delay_min_sec", 0) or 0))
        max_sec = max(0.0, float(getattr(config, "reply_delay_max_sec", min_sec) or 0))
        if max_sec < min_sec:
            max_sec = min_sec
        if max_sec <= 0:
            return
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    async def _cleanup_card_cache(self) -> None:
        config = self.config
        if not config:
            return
        try:
            result = cleanup_card_cache(
                self.card_dir,
                max_age_hours=float(getattr(config, "card_cache_max_age_hours", 24) or 0),
                max_files=int(getattr(config, "card_cache_max_files", 200) or 0),
            )
            if result["deleted"]:
                self.logger.info("QQ 卡片缓存已清理: %s", result)
        except Exception:
            self.logger.warning("QQ 卡片缓存清理失败", exc_info=True)

    def _render_card_png(self, *args, **kwargs):
        return render_card_png(*args, **kwargs)
