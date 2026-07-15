"""记忆检索路由 handler。"""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api, _require_confirmed_request


async def api_memories(request: web.Request) -> web.Response:
    """列出当前游戏的长期记忆，支持关键词过滤与分页。"""
    gk = request.match_info["game_key"]
    keyword = request.query.get("keyword", "")
    try:
        limit = min(int(request.query.get("limit", "50")), 200)
        offset = max(0, int(request.query.get("offset", "0")))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "分页参数必须是整数"}, status=400)
    result = _get_api(request).list_memories(gk, keyword, limit, offset)
    return web.json_response(result)


async def api_memory_update(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    result = await _get_api(request).update_memory(request.match_info["game_key"], int(request.match_info["entry_id"]), await request.json())
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_memory_delete(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    result = await _get_api(request).delete_memory(request.match_info["game_key"], int(request.match_info["entry_id"]))
    return web.json_response(result, status=200 if result.get("ok") else 404)


def register_memory(app: web.Application) -> None:
    app.router.add_get("/api/games/{game_key}/memories", api_memories)
    app.router.add_put("/api/games/{game_key}/memories/{entry_id}", api_memory_update)
    app.router.add_delete("/api/games/{game_key}/memories/{entry_id}", api_memory_delete)
