"""Character portrait upload and serving routes."""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api


async def api_avatar_upload(request: web.Request) -> web.Response:
    body = await request.json()
    result = _get_api(request).save_avatar_upload(
        file_data=body.get("file_data", ""),
        file_name=body.get("file_name", ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_avatar_file(request: web.Request) -> web.StreamResponse:
    path = _get_api(request).avatar_file(request.match_info["asset_id"])
    if path is None:
        return web.json_response({"error": "头像不存在"}, status=404)
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def api_avatar_list(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_user_avatars())


async def api_avatar_delete(request: web.Request) -> web.Response:
    result = _get_api(request).delete_avatar(request.match_info["asset_id"])
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_game_avatar_upload(request: web.Request) -> web.Response:
    api = _get_api(request)
    if not api.game_detail(request.match_info["game_key"]):
        return web.json_response({"error": "游戏不存在"}, status=404)
    body = await request.json()
    result = api.save_avatar_upload(
        file_data=body.get("file_data", ""),
        file_name=body.get("file_name", ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_game_avatar_file(request: web.Request) -> web.StreamResponse:
    api = _get_api(request)
    if not api.game_detail(request.match_info["game_key"]):
        return web.json_response({"error": "游戏不存在"}, status=404)
    path = api.avatar_file(request.match_info["asset_id"])
    if path is None:
        return web.json_response({"error": "头像不存在"}, status=404)
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def register_avatars(app: web.Application) -> None:
    app.router.add_get("/api/avatars", api_avatar_list)
    app.router.add_post("/api/avatars", api_avatar_upload)
    app.router.add_get("/api/avatars/{asset_id}", api_avatar_file)
    app.router.add_delete("/api/avatars/{asset_id}", api_avatar_delete)
    app.router.add_post("/api/games/{game_key}/avatars", api_game_avatar_upload)
    app.router.add_get("/api/games/{game_key}/avatars/{asset_id}", api_game_avatar_file)
