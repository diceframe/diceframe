"""HTTP routes for the built-in image-generation system."""

from __future__ import annotations

from aiohttp import web

from src.imagegen import IMAGE_PURPOSES, ImageGenerationError, game_image_owner_id
from src.webui.routes._common import _get_api
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY


async def api_image_generation_status(request: web.Request) -> web.Response:
    service = getattr(_get_api(request), "_imagegen", None)
    return web.json_response(service.public_config() if service is not None else {
        "enabled": False,
        "available": False,
        "provider": "",
        "model": "",
        "auto_scene": False,
    })


async def api_generate_image(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "生图请求必须是 JSON 对象"}, status=400)
    purpose = str(body.get("purpose") or "freeform").strip().lower()
    if purpose not in IMAGE_PURPOSES:
        return web.json_response({"ok": False, "error": "不支持的图片用途"}, status=400)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"ok": False, "error": "请填写画面描述"}, status=400)
    api = _get_api(request)
    game_key = str(request.match_info.get("game_key") or body.get("game_key") or "").strip()
    owner_type = "library"
    owner_id = str(request.get("user_id", "") or "local")
    if game_key:
        inst = api._reg.get(api._parse_key(game_key))
        if inst is None:
            return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
        user_id = str(request.get("user_id", "") or "")
        is_gm = bool(user_id and user_id == inst.gm_uid)
        is_member = bool(is_gm or user_id in inst.players)
        if not is_member:
            return web.json_response({"ok": False, "error": "未加入本局，无法生成图片"}, status=403)
        if purpose in {"scene", "map", "item"} and not is_gm:
            return web.json_response({"ok": False, "error": "仅 GM 可生成该类型图片"}, status=403)
        owner_type = "game"
        owner_id = game_image_owner_id(inst.game_key)
    elif request.get(ACCESS_PASSWORD_CONFIGURED_KEY, False) and not request.get("owner_authenticated", False):
        return web.json_response({"ok": False, "error": "仅管理员可以生成系统图片"}, status=403)
    from src.webui.services import generated_images as service
    try:
        result = await service.generate_image(
            api,
            prompt=prompt,
            purpose=purpose,
            owner_type=owner_type,
            owner_id=owner_id,
            aspect_ratio=str(body.get("aspect_ratio") or ""),
            style=str(body.get("style") or ""),
            context=body.get("context") if isinstance(body.get("context"), dict) else {},
        )
    except ImageGenerationError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result)


async def api_generated_image_file(request: web.Request) -> web.StreamResponse:
    api = _get_api(request)
    game_key = str(request.match_info.get("game_key") or "").strip()
    if game_key:
        inst = api._reg.get(api._parse_key(game_key))
        if inst is None:
            return web.json_response({"error": "游戏不存在"}, status=404)
        user_id = str(request.get("user_id", "") or "")
        if not user_id or (user_id != inst.gm_uid and user_id not in inst.players):
            return web.json_response({"error": "未加入本局，无法查看生成图片"}, status=403)
    path = api.generated_image_file(request.match_info["asset_id"])
    if path is None:
        return web.json_response({"error": "生成图片不存在"}, status=404)
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def api_game_generated_images(request: web.Request) -> web.Response:
    from src.webui.services import generated_images as service
    try:
        images = service.list_game_images(
            _get_api(request),
            request.match_info["game_key"],
            str(request.get("user_id", "") or ""),
            purpose=str(request.query.get("purpose") or "").strip().lower(),
        )
    except KeyError:
        return web.json_response({"error": "游戏不存在"}, status=404)
    except PermissionError:
        return web.json_response({"error": "未加入本局，无法查看生成历史"}, status=403)
    return web.json_response({"images": images})


async def api_generated_image_as_map_background(request: web.Request) -> web.Response:
    from src.webui.services import generated_images as service
    result = await service.use_as_map_background(
        _get_api(request),
        request.match_info["game_key"],
        str(request.get("user_id", "") or ""),
        request.match_info["asset_id"],
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


def register_generated_images(app: web.Application) -> None:
    app.router.add_get("/api/image-generation", api_image_generation_status)
    app.router.add_post("/api/generated-images", api_generate_image)
    app.router.add_get("/api/generated-images/{asset_id}", api_generated_image_file)
    app.router.add_get("/api/games/{game_key}/generated-images", api_game_generated_images)
    app.router.add_post("/api/games/{game_key}/generated-images", api_generate_image)
    app.router.add_get(
        "/api/games/{game_key}/generated-images/{asset_id}",
        api_generated_image_file,
    )
    app.router.add_post(
        "/api/games/{game_key}/generated-images/{asset_id}/map-background",
        api_generated_image_as_map_background,
    )
