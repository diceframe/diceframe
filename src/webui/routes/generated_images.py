"""HTTP routes for the built-in image-generation system."""

from __future__ import annotations

from aiohttp import web

from src.imagegen import IMAGE_PURPOSES, ImageGenerationError, game_image_owner_id
from src.webui.routes._common import _get_api, _require_confirmed_request
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY


def _effective_game_user_id(request: web.Request, instance) -> str:
    """Return the canonical game identity passed to image services.

    Browser sessions authenticated with the local access password have a
    ``web_<token>`` session id, which is intentionally different from the
    persisted game's ``gm_uid``.  Image services operate on the persisted
    game identity (and consequently reject that browser-only id).  Keep the
    transport/session id for ordinary players, but project an authenticated
    local owner onto the game's GM id before crossing the route/service
    boundary.  This mirrors the existing ``is_game_gm`` owner policy without
    teaching the image service about HTTP authentication.
    """
    user_id = str(request.get("user_id", "") or "").strip()
    # An owner can inspect a share link in ``player_preview`` mode.  Keep that
    # request's projected player identity so a preview cannot silently invoke
    # GM-only image operations; this mirrors the gameplay route policy.
    if (
        request.get("owner_authenticated", False)
        and not request.get("player_preview", False)
        and instance is not None
    ):
        gm_uid = str(getattr(instance, "gm_uid", "") or "").strip()
        if gm_uid:
            return gm_uid
    return user_id


async def api_image_generation_status(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).image_generation_status())


async def api_optimize_image_prompt(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    query = getattr(request, "query", {})
    if query.get("user") or query.get("share"):
        return web.json_response(
            {"ok": False, "error": "玩家分享页不可使用 AI 优化提示词"},
            status=403,
        )
    if request.get(ACCESS_PASSWORD_CONFIGURED_KEY, False) and not request.get(
        "owner_authenticated", False,
    ):
        return web.json_response(
            {"ok": False, "error": "仅管理员可以使用 AI 优化提示词"},
            status=403,
        )
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        return web.json_response(
            {"ok": False, "error": "优化请求必须是 JSON 对象"}, status=400,
        )
    try:
        result = await _get_api(request).optimize_image_prompt(
            field=str(body.get("field") or ""),
            text=str(body.get("text") or ""),
            language=str(body.get("language") or ""),
        )
    except ImageGenerationError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result)


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
        inst = api.get_game_instance(game_key)
        if inst is None:
            return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
        user_id = _effective_game_user_id(request, inst)
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
    try:
        result = await api.generate_generated_image(
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
        inst = api.get_game_instance(game_key)
        if inst is None:
            return web.json_response({"error": "游戏不存在"}, status=404)
        user_id = _effective_game_user_id(request, inst)
        if not user_id or (user_id != inst.gm_uid and user_id not in inst.players):
            return web.json_response({"error": "未加入本局，无法查看生成图片"}, status=403)
    path = api.generated_image_file(request.match_info["asset_id"])
    if path is None:
        return web.json_response({"error": "生成图片不存在"}, status=404)
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def api_game_generated_images(request: web.Request) -> web.Response:
    game_key = str(request.match_info["game_key"])
    instance = _get_api(request).get_game_instance(game_key)
    user_id = _effective_game_user_id(request, instance)
    try:
        images = _get_api(request).list_game_generated_images(
            game_key,
            user_id,
            purpose=str(request.query.get("purpose") or "").strip().lower(),
        )
    except KeyError:
        return web.json_response({"error": "游戏不存在"}, status=404)
    except PermissionError:
        return web.json_response({"error": "未加入本局，无法查看生成历史"}, status=403)
    return web.json_response({"images": images})


async def api_generated_image_as_map_background(request: web.Request) -> web.Response:
    game_key = str(request.match_info["game_key"])
    instance = _get_api(request).get_game_instance(game_key)
    result = await _get_api(request).use_generated_image_as_map_background(
        game_key,
        _effective_game_user_id(request, instance),
        request.match_info["asset_id"],
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_generate_current_round_image(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "生图请求必须是 JSON 对象"}, status=400)
    combine_references = body.get("combine_avatar_references", True)
    if not isinstance(combine_references, bool):
        return web.json_response({"ok": False, "error": "拼接头像选项必须是布尔值"}, status=400)
    try:
        round_number = int(body.get("round") or 0)
    except (TypeError, ValueError):
        round_number = 0
    panel_count = body.get("panel_count")
    if panel_count is not None and type(panel_count) is not int:
        return web.json_response({"ok": False, "error": "分镜格数必须是 1 到 6 的整数"}, status=400)
    if panel_count is not None and not 1 <= panel_count <= 6:
        return web.json_response({"ok": False, "error": "分镜格数必须在 1 到 6 之间"}, status=400)
    game_key = str(request.match_info.get("game_key") or "")
    instance = _get_api(request).get_game_instance(game_key)
    result = await _get_api(request).generate_current_round_image(
        game_key,
        _effective_game_user_id(request, instance),
        str(body.get("prompt") or ""), round_number,
        body.get("panels"), bool(body.get("use_avatar_references", False)), panel_count, combine_references,
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_storyboard_draft(request: web.Request) -> web.Response:
    game_key = str(request.match_info["game_key"])
    instance = _get_api(request).get_game_instance(game_key)
    result = _get_api(request).storyboard_draft(game_key, _effective_game_user_id(request, instance), int(request.query.get("round") or 0))
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_analyze_storyboard(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "分镜请求必须是 JSON 对象"}, status=400)
    requested_count = body.get("panel_count")
    if requested_count is not None and type(requested_count) is not int:
        return web.json_response({"ok": False, "error": "分镜格数必须是 1 到 6 的整数"}, status=400)
    if requested_count is not None and not 1 <= requested_count <= 6:
        return web.json_response({"ok": False, "error": "分镜格数必须在 1 到 6 之间"}, status=400)
    game_key = str(request.match_info["game_key"])
    instance = _get_api(request).get_game_instance(game_key)
    result = await _get_api(request).analyze_storyboard(game_key, _effective_game_user_id(request, instance), int((body or {}).get("round") or 0), requested_count)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_preview_image_prompt(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    body = body if isinstance(body, dict) else {}
    game_key = str(request.match_info["game_key"])
    instance = _get_api(request).get_game_instance(game_key)
    result = _get_api(request).preview_image_prompt(game_key, _effective_game_user_id(request, instance), str(body.get("prompt") or ""), body.get("panels"))
    return web.json_response(result, status=200 if result.get("ok") else 400)


def register_generated_images(app: web.Application) -> None:
    app.router.add_get("/api/image-generation", api_image_generation_status)
    app.router.add_post("/api/image-prompts/optimize", api_optimize_image_prompt)
    app.router.add_post("/api/generated-images", api_generate_image)
    app.router.add_get("/api/generated-images/{asset_id}", api_generated_image_file)
    app.router.add_get("/api/games/{game_key}/generated-images", api_game_generated_images)
    app.router.add_post("/api/games/{game_key}/generated-images", api_generate_image)
    app.router.add_post("/api/games/{game_key}/generated-images/current-round", api_generate_current_round_image)
    app.router.add_get("/api/games/{game_key}/generated-images/storyboard", api_storyboard_draft)
    app.router.add_post("/api/games/{game_key}/generated-images/storyboard/analyze", api_analyze_storyboard)
    app.router.add_post("/api/games/{game_key}/generated-images/prompt/preview", api_preview_image_prompt)
    app.router.add_get(
        "/api/games/{game_key}/generated-images/{asset_id}",
        api_generated_image_file,
    )
    app.router.add_post(
        "/api/games/{game_key}/generated-images/{asset_id}/map-background",
        api_generated_image_as_map_background,
    )
