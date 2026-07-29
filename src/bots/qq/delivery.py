"""Message delivery helpers for QQ bot adapters."""

from __future__ import annotations

import asyncio
import random
from typing import Any

from src.bots.qq.card_renderer import BRAND_FOOTER, cleanup_card_cache, render_card_png


class QQDeliveryMixin:
    async def _send_group_card(self, group_id: str, *, title: str, subtitle: str = "",
                               lines: list[str] | None = None, fallback: str = "",
                               link_text: str = "",
                               hint: list[tuple[str, str]] | None = None) -> None:
        payload = {
            "platform": "qq",
            "kind": "card",
            "scope": "group",
            "stream_id": group_id,
            "title": title,
            "subtitle": subtitle,
            "lines": list(lines or []),
            "fallback_text": fallback,
        }
        handled, payload, outputs = await self._apply_output_extensions(payload)
        if handled:
            await self._deliver_group_extension_outputs(group_id, outputs)
            if link_text:
                await self._send_group_text_raw(group_id, link_text)
            return
        title = str(payload.get("title") or title)
        subtitle = str(payload.get("subtitle") or subtitle)
        lines = payload.get("lines") if isinstance(payload.get("lines"), list) else list(lines or [])
        fallback = str(payload.get("fallback_text") or fallback)
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
        payload = {
            "platform": "qq",
            "kind": "card",
            "scope": "private",
            "stream_id": user_id,
            "title": title,
            "subtitle": subtitle,
            "lines": list(lines or []),
            "fallback_text": fallback,
        }
        handled, payload, outputs = await self._apply_output_extensions(payload)
        if handled:
            await self._deliver_private_extension_outputs(user_id, outputs)
            if link_text:
                await self._send_private_text_raw(user_id, link_text)
            return
        title = str(payload.get("title") or title)
        subtitle = str(payload.get("subtitle") or subtitle)
        lines = payload.get("lines") if isinstance(payload.get("lines"), list) else list(lines or [])
        fallback = str(payload.get("fallback_text") or fallback)
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
        payload = {
            "platform": "qq",
            "kind": "text",
            "scope": "group",
            "stream_id": group_id,
            "text": str(text or ""),
        }
        handled, payload, outputs = await self._apply_output_extensions(payload)
        if handled:
            return await self._deliver_group_extension_outputs(group_id, outputs)
        return await self._send_group_text_raw(group_id, str(payload.get("text") or text))

    async def _send_private_text(self, user_id: str, text: str) -> dict:
        payload = {
            "platform": "qq",
            "kind": "text",
            "scope": "private",
            "stream_id": user_id,
            "text": str(text or ""),
        }
        handled, payload, outputs = await self._apply_output_extensions(payload)
        if handled:
            return await self._deliver_private_extension_outputs(user_id, outputs)
        return await self._send_private_text_raw(user_id, str(payload.get("text") or text))

    async def _send_group_text_raw(self, group_id: str, text: str) -> dict:
        await self._reply_delay()
        return await self.sender.send_group_text(group_id, text)

    async def _send_private_text_raw(self, user_id: str, text: str) -> dict:
        await self._reply_delay()
        return await self.sender.send_private_text(user_id, text)

    async def _apply_output_extensions(
        self,
        payload: dict[str, Any],
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        apply_extensions = getattr(self.api, "apply_bridge_extensions", None)
        if not callable(apply_extensions):
            return False, payload, []
        try:
            after = await apply_extensions("after_result", payload)
            current = after.get("payload") if isinstance(after.get("payload"), dict) else payload
            if after.get("handled"):
                outputs = after.get("outputs") if isinstance(after.get("outputs"), list) else []
                return True, current, outputs
            rendered = await apply_extensions("render", current)
            current = rendered.get("payload") if isinstance(rendered.get("payload"), dict) else current
            outputs = rendered.get("outputs") if isinstance(rendered.get("outputs"), list) else []
            return bool(rendered.get("handled")), current, outputs
        except Exception:
            self.logger.warning("Bot Bridge 展示扩展调用失败，使用内置展示", exc_info=True)
            return False, payload, []

    async def _deliver_group_extension_outputs(
        self,
        group_id: str,
        outputs: list[dict[str, Any]],
    ) -> dict:
        result: dict = {}
        for output in outputs[:16]:
            try:
                result = await self._deliver_group_extension_output(group_id, output)
            except Exception:
                self.logger.warning("Bot Bridge 群聊扩展输出发送失败", exc_info=True)
                fallback = str(output.get("fallback_text") or "").strip()
                if fallback:
                    result = await self._send_group_text_raw(group_id, fallback)
        return result

    async def _deliver_private_extension_outputs(
        self,
        user_id: str,
        outputs: list[dict[str, Any]],
    ) -> dict:
        result: dict = {}
        for output in outputs[:16]:
            try:
                result = await self._deliver_private_extension_output(user_id, output)
            except Exception:
                self.logger.warning("Bot Bridge 私聊扩展输出发送失败", exc_info=True)
                fallback = str(output.get("fallback_text") or "").strip()
                if fallback:
                    result = await self._send_private_text_raw(user_id, fallback)
        return result

    async def _deliver_group_extension_output(self, group_id: str, output: dict[str, Any]) -> dict:
        output_type = str(output.get("type") or "")
        if output_type == "text":
            return await self._send_group_text_raw(group_id, str(output.get("text") or ""))
        if output_type == "image":
            image = await self.api.download_bridge_asset(str(output.get("asset_url") or ""), self.card_dir)
            await self._reply_delay()
            return await self.sender.send_group_image(group_id, str(image), str(output.get("caption") or ""))
        if output_type == "card":
            image = self._render_card_png(
                self.card_dir,
                title=str(output.get("title") or ""),
                subtitle=str(output.get("subtitle") or ""),
                lines=output.get("lines") if isinstance(output.get("lines"), list) else [],
                footer=BRAND_FOOTER,
            )
            await self._reply_delay()
            return await self.sender.send_group_image(group_id, str(image))
        raise ValueError(f"不支持的群聊扩展输出：{output_type}")

    async def _deliver_private_extension_output(self, user_id: str, output: dict[str, Any]) -> dict:
        output_type = str(output.get("type") or "")
        if output_type == "text":
            return await self._send_private_text_raw(user_id, str(output.get("text") or ""))
        if output_type == "image":
            image = await self.api.download_bridge_asset(str(output.get("asset_url") or ""), self.card_dir)
            await self._reply_delay()
            return await self.sender.send_private_image(user_id, str(image), str(output.get("caption") or ""))
        if output_type == "card":
            image = self._render_card_png(
                self.card_dir,
                title=str(output.get("title") or ""),
                subtitle=str(output.get("subtitle") or ""),
                lines=output.get("lines") if isinstance(output.get("lines"), list) else [],
                footer=BRAND_FOOTER,
            )
            await self._reply_delay()
            return await self.sender.send_private_image(user_id, str(image))
        raise ValueError(f"不支持的私聊扩展输出：{output_type}")

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
