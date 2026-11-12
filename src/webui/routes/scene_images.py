"""Adventure scene-image upload and serving routes."""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api
from src.localization.messages import t


async def api_scene_image_upload(request: web.Request) -> web.Response:
    body = await request.json()
    result = _get_api(request).save_scene_image_upload(
        file_data=body.get("file_data", ""),
        file_name=body.get("file_name", ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_scene_image_file(request: web.Request) -> web.StreamResponse:
    path = _get_api(request).scene_image_file(request.match_info["asset_id"])
    if path is None:
        return web.json_response({"error": t("webui.routes.scene_images.cover_missing")}, status=404)
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def register_scene_images(app: web.Application) -> None:
    app.router.add_post("/api/scene-images", api_scene_image_upload)
    app.router.add_get("/api/scene-images/{asset_id}", api_scene_image_file)
