"""GM-only AI seat policy and recovery routes."""
from aiohttp import web

from src.webui.routes._common import _get_api
from src.webui.viewer import viewer_for


def _denied(request: web.Request) -> web.Response | None:
    inst = _get_api(request).get_game_instance(request.match_info["game_key"])
    if inst is None:
        return web.json_response({"ok": False, "error_code": "game_missing"}, status=404)
    if not viewer_for(request, inst).is_gm:
        return web.json_response({"ok": False, "error": "GM only", "error_code": "gm_only"}, status=403)
    return None


async def read(request: web.Request) -> web.Response:
    denied = _denied(request)
    if denied is not None:
        return denied
    return web.json_response(_get_api(request).ai_seat_settings(request.match_info["game_key"]))


async def update(request: web.Request) -> web.Response:
    denied = _denied(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error_code": "invalid_settings"}, status=400)
    result = await _get_api(request).update_ai_seat_settings(
        request.match_info["game_key"], body, request.match_info.get("user_id"),
    )
    if result.get("ok"):
        await _broadcast(request)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def retry(request: web.Request) -> web.Response:
    denied = _denied(request)
    if denied is not None:
        return denied
    result = await _get_api(request).retry_ai_seat(request.match_info["game_key"], request.match_info["user_id"])
    await _broadcast(request)
    return web.json_response(result, status=200 if result.get("ok") else 409)


async def _broadcast(request: web.Request) -> None:
    pool = request.app.get("connection_pool")
    if pool is not None:
        await pool.broadcast(request.match_info["game_key"], {"type": "table_settings_changed"})


def register(app: web.Application) -> None:
    app.router.add_get("/api/games/{game_key}/ai-seats", read)
    app.router.add_put("/api/games/{game_key}/ai-seats/defaults", update)
    app.router.add_put("/api/games/{game_key}/ai-seats/seats/{user_id}", update)
    app.router.add_post("/api/games/{game_key}/ai-seats/seats/{user_id}/retry", retry)
