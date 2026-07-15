"""平台 Bot 服务入口：绑定验证与 GM 获取绑定凭证。"""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api


async def api_bind_game(request: web.Request) -> web.Response:
    body = await request.json()
    result = await _get_api(request).verify_bot_bind_game(
        str(body.get("game_key") or ""),
        str(body.get("bind_token") or ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 403)


async def api_get_bind_token(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    detail = api.game_detail(game_key)
    if not detail:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
    if request.get("user_id", "") != detail.get("gm_uid"):
        return web.json_response({"ok": False, "error": "仅 GM 可获取绑定凭证"}, status=403)
    body = await request.json() if request.content_length else {}
    result = await api.get_bot_bind_token(game_key, bool(body.get("rotate")))
    return web.json_response(result, status=200 if result.get("ok") else 400)


def register_bot(app: web.Application) -> None:
    app.router.add_post("/api/bot/bind-game", api_bind_game)
    app.router.add_post("/api/games/{game_key}/bot-bind-token", api_get_bind_token)
