"""HTTP endpoints for the image-generation provider plugin."""

from __future__ import annotations

from aiohttp import web

from src.imagegen import ImageGenError
from src.webui.routes._common import _get_api
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY


async def api_test_imagegen(request: web.Request) -> web.Response:
    denied = _require_imagegen_admin(request)
    if denied is not None:
        return denied
    body = await request.json() if request.can_read_body else {}
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"ok": False, "error": "请填写测试画面描述"}, status=400)
    api = _get_api(request)
    from src.webui.services import imagegen as service
    result = await service.test_generation(api, prompt)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_game_images(request: web.Request) -> web.Response:
    api = _get_api(request)
    from src.webui.services import imagegen as service
    try:
        images = service.collect_game_images(
            api, request.match_info["game_key"], request.get("user_id", ""),
        )
    except KeyError:
        return web.json_response({"error": "游戏不存在"}, status=404)
    except PermissionError:
        return web.json_response({"error": "未加入本局，无法查看画廊"}, status=403)
    return web.json_response({"images": images})


async def api_map_background_from_scene(request: web.Request) -> web.Response:
    api = _get_api(request)
    from src.webui.services import imagegen as service
    body = await request.json() if request.can_read_body else {}
    try:
        result = await service.set_map_background_from_scene(
            api,
            request.match_info["game_key"],
            request.get("user_id", ""),
            str(body.get("asset_id") or ""),
        )
    except ImageGenError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result, status=200 if result.get("ok") else 400)


def _require_imagegen_admin(request: web.Request) -> web.Response | None:
    query = getattr(request, "query", {})
    if query.get("user") or query.get("share"):
        return web.json_response({"ok": False, "error": "玩家分享页不可测试图像生成"}, status=403)
    if request.get(ACCESS_PASSWORD_CONFIGURED_KEY, False) and not request.get("owner_authenticated", False):
        return web.json_response({"ok": False, "error": "仅管理员可以测试图像生成"}, status=403)
    return None


def register_imagegen(app: web.Application) -> None:
    app.router.add_post("/api/imagegen/test", api_test_imagegen)
    app.router.add_get("/api/games/{game_key}/images", api_game_images)
    app.router.add_post(
        "/api/games/{game_key}/map-background-from-scene",
        api_map_background_from_scene,
    )
