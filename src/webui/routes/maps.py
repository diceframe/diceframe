"""HTTP routes for location maps and per-game background selection."""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api


async def api_map_locations(request: web.Request) -> web.Response:
    result = _get_api(request).get_map_locations(request.match_info["game_key"])
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
        return web.json_response({"error": "地图背景不存在"}, status=404)
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


async def api_map_background_update(request: web.Request) -> web.Response:
    game_key = request.match_info["game_key"]
    api = _get_api(request)
    detail = api.game_detail(game_key)
    if not detail:
        return web.json_response({"error": "游戏不存在"}, status=404)
    if request.get("user_id", "") != detail.get("gm_uid", ""):
        return web.json_response({"error": "仅 GM 可修改地图背景"}, status=403)

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


def register_maps(app: web.Application) -> None:
    app.router.add_post("/api/map-backgrounds", api_map_background_upload)
    app.router.add_get("/api/games/{game_key}/map", api_map_locations)
    app.router.add_get(
        "/api/games/{game_key}/map-background-asset/{asset_id}",
        api_game_map_background_file,
    )
    app.router.add_post("/api/games/{game_key}/map-background", api_map_background_update)
