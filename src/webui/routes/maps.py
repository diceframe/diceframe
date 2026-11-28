"""HTTP routes for location maps and per-game background selection."""

from __future__ import annotations

import json

from aiohttp import web

from src.webui.errors import declared_error_code
from src.webui.routes._common import _get_api
from src.webui.viewer import viewer_for
from src.localization.messages import t

_CONVERSION_STATUS = {
    "map_background_forbidden": 403,
    "map_background_not_found": 404,
    "map_background_unconfirmed": 400,
    "map_background_unresolvable": 422,
    "map_background_conversion_conflict": 409,
    "tactical_scene_forbidden": 403,
    "tactical_scene_not_found": 404,
    "tactical_scene_invalid_payload": 422,
    "tactical_scene_unsupported_schema": 422,
    "tactical_scene_stale_revision": 409,
    "rewrite_in_progress": 409,
    "stale_run": 409,
}


async def api_map_locations(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    viewer = viewer_for(request, api.get_game_instance(game_key))
    result = api.get_map_locations(game_key, viewer=viewer)
    return web.json_response(result)


async def api_map_background_upload(request: web.Request) -> web.Response:
    body = await request.json()
    result = _get_api(request).save_map_background_upload(
        file_data=body.get("file_data", ""),
        file_name=body.get("file_name", ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_game_map_background_file(request: web.Request) -> web.StreamResponse:
    path = _get_api(request).map_background_asset(
        request.match_info["game_key"],
        str(request.match_info["asset_id"] or ""),
    )
    if path is None:
        return web.json_response({"error": t("webui.routes.maps.background_missing")}, status=404)
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


async def api_map_background_update(request: web.Request) -> web.Response:
    game_key = request.match_info["game_key"]
    api = _get_api(request)
    detail = api.game_detail(game_key)
    if not detail:
        return web.json_response({"error": "游戏不存在"}, status=404)
    if request.get("user_id", "") != detail.get("gm_uid", ""):
        return web.json_response({"error": t("webui.routes.maps.background_gm_only")}, status=403)

    body = await request.json()
    selection = body.get("map_background")
    if body.get("file_data"):
        upload = api.save_map_background_upload(
            str(body["file_data"]),
            str(body.get("file_name") or ""),
        )
        if not upload.get("ok"):
            return web.json_response(upload, status=400)
        selection = upload.get("map_background")
    result = await api.update_map_background(game_key, selection)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_map_background_to_tactical_scene(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    viewer = viewer_for(request, api.get_game_instance(game_key))
    raw = await request.text()
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return web.json_response(
            {"ok": False, "error_code": "map_background_unconfirmed",
             "error": "Confirm the conversion and its grid before creating a tactical scene."},
            status=400,
        )
    pool = request.app.get("connection_pool")
    result = await api.convert_map_background_to_tactical_scene(
        game_key, viewer, intent=body,
        broadcast=pool.broadcast if pool is not None else None,
    )
    status = 200 if result.get("ok") else _CONVERSION_STATUS.get(declared_error_code(result), 400)
    return web.json_response(result, status=status)


def register_maps(app: web.Application) -> None:
    app.router.add_post("/api/map-backgrounds", api_map_background_upload)
    app.router.add_get("/api/games/{game_key}/map", api_map_locations)
    app.router.add_get(
        "/api/games/{game_key}/map-background-asset/{asset_id}",
        api_game_map_background_file,
    )
    app.router.add_post("/api/games/{game_key}/map-background", api_map_background_update)
    app.router.add_post(
        "/api/games/{game_key}/tactical-scenes/from-map-background",
        api_map_background_to_tactical_scene,
    )
