"""平台 Bot 服务入口：绑定验证与 GM 获取绑定凭证。"""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api
from src.version import APP_NAME, __version__


async def api_bot_ping(request: web.Request) -> web.Response:
    games = _get_api(request).list_games()
    bridge_extensions = _get_api(request).bot_extension_capabilities()
    return web.json_response({
        "ok": True,
        "app_name": APP_NAME,
        "version": __version__,
        "total": int(games.get("total", 0)),
        "bridge_extensions": bridge_extensions,
    })


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


async def api_apply_bridge_extensions(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "请求体必须是对象"}, status=400)
    payload = body.get("payload")
    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "error": "payload 必须是对象"}, status=400)
    caller = request.get("plugin_authenticated")
    payload = dict(payload)
    payload["_caller"] = {
        "plugin_id": str((caller or {}).get("plugin_id") or "external"),
        "managed": bool(caller),
    }
    result = await _get_api(request).apply_bot_extensions(str(body.get("stage") or ""), payload)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_bridge_plugin_asset(request: web.Request) -> web.StreamResponse:
    try:
        path = _get_api(request).bot_extension_asset_path(
            request.match_info["plugin_id"],
            request.match_info["relative_path"],
        )
    except (KeyError, ValueError):
        raise web.HTTPNotFound(text="Bot Bridge 图片不存在")
    response = web.FileResponse(path)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def register_bot(app: web.Application) -> None:
    app.router.add_get("/api/bot/ping", api_bot_ping)
    app.router.add_post("/api/bot/bind-game", api_bind_game)
    app.router.add_post("/api/bot/bridge/extensions", api_apply_bridge_extensions)
    app.router.add_get("/api/bot/plugin-assets/{plugin_id}/{relative_path:.+}", api_bridge_plugin_asset)
    app.router.add_post("/api/games/{game_key}/bot-bind-token", api_get_bind_token)
