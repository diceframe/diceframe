"""Thin authenticated transport for Keeper-approved Push decisions."""
from aiohttp import web

from src.webui.routes._common import _get_api
from src.webui.routes.game_route_common import _narration_callbacks
from src.webui.services.check_decisions import refusal
from src.webui.viewer import viewer_for


async def push(request: web.Request) -> web.Response:
    api, key = _get_api(request), request.match_info["game_key"]
    inst = api.get_game_instance(key)
    if inst is None:
        return web.json_response(refusal("GAME_NOT_FOUND"), status=404)
    if request.get("player_preview") and not request.get("player_delegate"):
        return web.json_response(refusal("PREVIEW_READ_ONLY"), status=403)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response(refusal("PUSH_INVALID_REQUEST"), status=400)
    on_delta, on_reset = _narration_callbacks(request, key)
    result = await api.resolve_push_and_continue(key, request.match_info["check_id"], viewer_for(request, inst),
        request.match_info["operation"], body, on_delta=on_delta, on_reset=on_reset)
    pool = request.app.get("connection_pool")
    if pool and result["payload"].get("ok"):
        await pool.broadcast(key, {"type": "table_settings_changed"})
    return web.json_response(result["payload"], status=result["status"])


def register(app: web.Application) -> None:
    app.router.add_post("/api/games/{game_key}/checks/{check_id}/push/{operation:request|offer|reject|decline|confirm}", push)
