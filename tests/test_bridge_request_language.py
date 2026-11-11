"""Bot Bridge 把会话语言作为 Accept-Language 发给 DiceFrame，服务端提示随之本地化。"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.bots.bridge_core.client import DiceFrameClient, request_language
from src.bots.bridge_core.errors import DiceFrameHTTPError
from src.webui.errors import error_code_middleware
from src.webui.request_language import request_language_middleware


def _server(seen: list[str | None]) -> TestServer:
    async def ping(request: web.Request) -> web.Response:
        seen.append(request.headers.get("Accept-Language"))
        return web.json_response({"ok": True})

    async def missing(request: web.Request) -> web.Response:
        seen.append(request.headers.get("Accept-Language"))
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)

    app = web.Application(middlewares=[request_language_middleware, error_code_middleware])
    app.router.add_get("/api/bot/ping", ping)
    app.router.add_get("/api/games/{game_key}", missing)
    return TestServer(app)


@pytest.mark.asyncio
async def test_requests_carry_the_conversation_language_only_inside_the_scope() -> None:
    seen: list[str | None] = []
    async with _server(seen) as server:
        client = DiceFrameClient(str(server.make_url("")), "token")
        try:
            await client.ping()
            with request_language("en"):
                await client.ping()
                with request_language("ja"):
                    await client.ping()
                await client.ping()
            await client.ping()
        finally:
            await client.close()
    assert seen == [None, "en", "ja", "en", None]


@pytest.mark.asyncio
async def test_server_errors_reach_the_bot_in_the_conversation_language() -> None:
    seen: list[str | None] = []
    async with _server(seen) as server:
        client = DiceFrameClient(str(server.make_url("")), "token")
        try:
            with pytest.raises(DiceFrameHTTPError) as chinese:
                await client.detail("web|room|bot")
            with request_language("en"), pytest.raises(DiceFrameHTTPError) as english:
                await client.detail("web|room|bot")
        finally:
            await client.close()
    assert "游戏不存在" in str(chinese.value)
    assert "Game not found" in str(english.value)
