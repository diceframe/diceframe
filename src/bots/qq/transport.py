"""项目原生 NapCat 正向 WebSocket 客户端。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from src.bots.qq.config import QQBotConfig


class NapCatTransport:
    def __init__(self, config: QQBotConfig, on_payload: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self.config = config
        self.on_payload = on_payload
        self.logger = logging.getLogger("trpg.qq.transport")
        self._stop = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._send_lock = asyncio.Lock()
        self._official_bot_cache: dict[str, bool] = {}

    async def run(self) -> None:
        self._stop = False
        headers = {"Authorization": f"Bearer {self.config.napcat_token}"} if self.config.napcat_token else {}
        while not self._stop:
            try:
                timeout = aiohttp.ClientTimeout(total=None, connect=10)
                async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                    async with session.ws_connect(
                        self.config.ws_url,
                        heartbeat=self.config.heartbeat_sec or None,
                    ) as websocket:
                        self._ws = websocket
                        self.logger.info("NapCat 已连接: %s", self.config.ws_url)
                        async for message in websocket:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                await self._dispatch(message.data)
                            elif message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                                break
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("NapCat 连接中断")
            finally:
                self._ws = None
                self._fail_pending("NapCat 连接已断开")
            if not self._stop:
                await asyncio.sleep(self.config.reconnect_delay_sec)

    async def stop(self) -> None:
        self._stop = True
        if self._ws and not self._ws.closed:
            await self._ws.close()

    async def call(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._ws or self._ws.closed:
            raise RuntimeError("NapCat 尚未连接")
        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        try:
            async with self._send_lock:
                await self._ws.send_json({"action": action, "params": params, "echo": echo})
            result = await asyncio.wait_for(future, timeout=self.config.action_timeout_sec)
            self._raise_if_failed(action, result)
            return result
        finally:
            self._pending.pop(echo, None)

    async def send_group_text(self, group_id: str, text: str) -> dict[str, Any]:
        return await self.call("send_group_msg", {
            "group_id": int(group_id) if str(group_id).isdigit() else group_id,
            "message": [{"type": "text", "data": {"text": text}}],
        })

    async def send_private_text(self, user_id: str, text: str) -> dict[str, Any]:
        return await self.call("send_private_msg", {
            "user_id": int(user_id) if str(user_id).isdigit() else user_id,
            "message": [{"type": "text", "data": {"text": text}}],
        })

    async def send_group_image(self, group_id: str, image_path: str, caption: str = "") -> dict[str, Any]:
        message = [{"type": "image", "data": {"file": self._image_as_base64(image_path)}}]
        if caption:
            message.append({"type": "text", "data": {"text": "\n" + caption}})
        return await self.call("send_group_msg", {
            "group_id": int(group_id) if str(group_id).isdigit() else group_id,
            "message": message,
        })

    async def send_private_image(self, user_id: str, image_path: str, caption: str = "") -> dict[str, Any]:
        message = [{"type": "image", "data": {"file": self._image_as_base64(image_path)}}]
        if caption:
            message.append({"type": "text", "data": {"text": "\n" + caption}})
        return await self.call("send_private_msg", {
            "user_id": int(user_id) if str(user_id).isdigit() else user_id,
            "message": message,
        })

    @staticmethod
    def _image_as_base64(image_path: str) -> str:
        data = base64.b64encode(open(image_path, "rb").read()).decode("ascii")
        return "base64://" + data

    async def is_official_bot(self, group_id: str, user_id: str) -> bool:
        key = f"{group_id}:{user_id}"
        if key in self._official_bot_cache:
            return self._official_bot_cache[key]
        try:
            result = await self.call("get_group_member_info", {
                "group_id": int(group_id) if str(group_id).isdigit() else group_id,
                "user_id": int(user_id) if str(user_id).isdigit() else user_id,
                "no_cache": True,
            })
            data = result.get("data") if isinstance(result, dict) else {}
            is_robot = bool((data or {}).get("is_robot"))
        except Exception:
            self.logger.warning("无法确认用户是否为官方机器人，默认放行", exc_info=True)
            is_robot = False
        self._official_bot_cache[key] = is_robot
        return is_robot

    @staticmethod
    def _raise_if_failed(action: str, payload: dict[str, Any]) -> None:
        """把 OneBot/NapCat 的失败响应转成异常，避免上层误判“发送成功”。"""
        if not isinstance(payload, dict):
            return
        status = str(payload.get("status") or "").lower()
        retcode = payload.get("retcode")
        failed = status in {"failed", "fail", "error"} or (
            retcode not in (None, 0, "0")
        )
        if not failed:
            return
        message = (
            payload.get("message")
            or payload.get("wording")
            or payload.get("msg")
            or payload.get("errMsg")
            or payload.get("error")
            or ""
        )
        data = payload.get("data")
        if not message and isinstance(data, dict):
            message = data.get("message") or data.get("wording") or data.get("errMsg") or ""
        raise RuntimeError(f"NapCat action {action} failed: {message or payload}")

    async def _dispatch(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.logger.warning("忽略非法 NapCat JSON")
            return
        echo = str(payload.get("echo") or "")
        if echo:
            future = self._pending.get(echo)
            if future and not future.done():
                future.set_result(payload)
            return
        task = asyncio.create_task(self.on_payload(payload))
        task.add_done_callback(self._log_task_error)

    def _fail_pending(self, message: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError(message))
        self._pending.clear()

    def _log_task_error(self, task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and task.exception():
            self.logger.error("NapCat 消息处理失败", exc_info=task.exception())
