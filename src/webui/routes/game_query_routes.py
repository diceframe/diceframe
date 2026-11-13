"""Read-only game and scene query routes."""

from __future__ import annotations

import logging

from aiohttp import web

from src.webui.routes._common import (
    _get_api,
)
from src.webui.access_control import WebAccessControl
from src.webui.services.game_queries import LOBBY_NARRATIVE_FIELDS, join_characters, lobby_detail
from src.webui.viewer import viewer_for

logger = logging.getLogger("trpg")
from src.webui.routes.game_route_common import _gm_only_inst
from src.engine import module_hooks
from src.engine.modules import room_access


async def api_games(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_games())


async def api_detail(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    instance = api.get_game_instance(game_key)
    viewer = viewer_for(request, instance)
    d = api.game_detail(game_key, viewer.uid, viewer_is_gm=viewer.is_gm)
    if not d:
        return web.json_response({"error": "not found"}, status=404)
    if not viewer.is_member:
        # Visitors (no seat, revoked binding, or no valid seat token) get the
        # lobby only: no uids, GM identity, plot, recap, luck or economy data.
        lobby = lobby_detail(d)
        if (
            room_access.has_room_password(instance)
            and not WebAccessControl.request_room_token_ok(instance, request)
        ):
            # Story text stays behind the room password.
            for key in LOBBY_NARRATIVE_FIELDS:
                lobby.pop(key, None)
        return web.json_response(lobby)
    d["viewer"] = {"kind": viewer.kind, "uid": viewer.uid}
    return web.json_response(d)


async def api_game_adventure_projection(request: web.Request) -> web.Response:
    """Game-scoped adventure read model; player output is projected server-side."""

    api = _get_api(request)
    game_key = request.match_info["game_key"]
    instance = api.get_game_instance(game_key)
    viewer = viewer_for(request, instance)
    result = api.game_adventure_projection(game_key, viewer_is_gm=viewer.is_gm, viewer_uid=viewer.uid)
    return (
        web.json_response(result)
        if result is not None
        else web.json_response({"error": "not found"}, status=404)
    )


async def api_gameplay_view(request: web.Request) -> web.Response:
    """Serve an explicitly registered gameplay owner projection."""

    api = _get_api(request)
    game_key = request.match_info["game_key"]
    instance = api.get_game_instance(game_key)
    viewer = viewer_for(request, instance)
    if instance is None:
        return web.json_response({"ok": False, "code": "GAME_NOT_FOUND"}, status=404)
    if not viewer.is_member:
        return web.json_response({"ok": False, "code": "GAME_MEMBER_REQUIRED"}, status=403)
    result = api.gameplay_view(game_key, viewer.uid, viewer.is_gm)
    status = 200 if result.get("ok") else (409 if result.get("code") == "UNSUPPORTED_GAMEPLAY" else 422)
    return web.json_response(result, status=status)


async def api_gameplay_action(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    instance = api.get_game_instance(game_key)
    viewer = viewer_for(request, instance)
    if instance is None:
        return web.json_response({"ok": False, "code": "GAME_NOT_FOUND"}, status=404)
    if not viewer.is_member:
        return web.json_response({"ok": False, "code": "GAME_MEMBER_REQUIRED"}, status=403)
    action = str(request.match_info.get("action") or "").strip()
    if not action or len(action) > 64 or not all(ch.isalnum() or ch in "_.-" for ch in action):
        return web.json_response({"ok": False, "code": "INVALID_GAMEPLAY_ACTION"}, status=400)
    try:
        if request.content_length and request.content_length > 16 * 1024:
            return web.json_response({"ok": False, "code": "ACTION_BODY_TOO_LARGE"}, status=413)
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "code": "INVALID_ACTION_BODY"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "code": "INVALID_ACTION_BODY"}, status=400)
    result = await api.gameplay_action(
        game_key, viewer.uid, viewer.is_gm, action, body,
    )
    status = 200 if result.get("ok") else 409
    return web.json_response(result, status=status)


async def api_gameplay_slot(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    instance = api.get_game_instance(game_key)
    viewer = viewer_for(request, instance)
    if instance is None:
        return web.json_response({"ok": False, "code": "GAME_NOT_FOUND"}, status=404)
    if not viewer.is_member:
        return web.json_response({"ok": False, "code": "GAME_MEMBER_REQUIRED"}, status=403)
    slot = str(request.match_info.get("slot") or "").strip()
    if slot not in {"create", "character", "gm"}:
        return web.json_response({"ok": False, "code": "INVALID_GAMEPLAY_SLOT"}, status=400)
    result = api.gameplay_slot(game_key, viewer.uid, viewer.is_gm, slot)
    status = 200 if result.get("ok") else 409
    return web.json_response(result, status=status)


async def api_game_scene_image_file(request: web.Request) -> web.StreamResponse:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    inst = api.get_game_instance(game_key)
    if not inst:
        return web.json_response({"error": "not found"}, status=404)
    use_default = request.query.get("default", "").lower() in {"1", "true", "yes"}
    reference = (
        api.resolve_default_scene_image(inst.world_id, inst.rule_id)
        if use_default
        else (module_hooks.read_value(inst, module_hooks.SCENE_IMAGE) or {})
        or api.resolve_default_scene_image(inst.world_id, inst.rule_id)
    )
    path = api.resolve_scene_image_file(reference)
    if path is None:
        return web.json_response({"error": "scene image not found"}, status=404)
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=300"})


async def api_game_scene_image_update(request: web.Request) -> web.Response:
    game_key = request.match_info["game_key"]
    _inst, denied = _gm_only_inst(request, game_key)
    if denied is not None:
        return denied
    body = await request.json()
    reference = body.get("scene_image")
    if body.get("file_data"):
        upload = _get_api(request).save_scene_image_upload(
            str(body["file_data"]), str(body.get("file_name") or "")
        )
        if not upload.get("ok"):
            return web.json_response(upload, status=400)
        reference = upload.get("scene_image")
    result = await _get_api(request).update_scene_image(
        game_key,
        reference,
        use_default=bool(body.get("use_default", False)),
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_chars(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    viewer = viewer_for(request, api.get_game_instance(game_key))
    characters = api.list_characters(game_key, viewer_is_gm=viewer.is_gm)
    if not viewer.is_member:
        # A visitor joining needs the public ruleset bootstrap, never the roster.
        return web.json_response(join_characters(characters))
    return web.json_response(characters)


async def api_log(request: web.Request) -> web.Response:
    try:
        page = max(1, int(request.query.get("page", "1")))
        per_page = max(1, min(200, int(request.query.get("per_page", "50"))))
    except (TypeError, ValueError):
        return web.json_response(
            {"ok": False, "error": "分页参数必须是整数"}, status=400
        )
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    inst = api.get_game_instance(game_key)
    viewer = viewer_for(request, inst)
    include_internal = viewer.is_gm
    if include_internal:
        return web.json_response(api.get_log(game_key, page, per_page, True))
    return web.json_response(api.get_log(game_key, page, per_page))


async def api_player_context(request: web.Request) -> web.Response:
    return web.json_response(
        _get_api(request).player_context(
            preview=bool(request.get("player_preview", False)),
            delegate=bool(request.get("player_delegate", False)),
            user_id=request.get("user_id", ""),
        )
    )
