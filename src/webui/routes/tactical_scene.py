"""Scene read projection and GM write routes (B2 / scene-03)."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from src.webui.errors import declared_error_code
from src.webui.routes._common import _get_api
from src.webui.services import portable_scene_import
from src.webui.services.tactical_scene_ops import TacticalSceneOpDependencies
from src.webui.viewer import viewer_for

_STATUS_BY_CODE = {
    "tactical_scene_not_found": 404,
    "tactical_scene_placeable_not_found": 404,
    "tactical_scene_forbidden": 403,
    "tactical_scene_invalid_payload": 422,
    "tactical_scene_unsupported_schema": 422,
    "tactical_scene_stale_revision": 409,
    "rewrite_in_progress": 409,
    "stale_run": 409,
    "scene_import_forbidden": 403,
    "scene_import_not_found": 404,
    "scene_import_unconfirmed": 400,
    "scene_import_conflict": 409,
    "scene_import_name_conflict": 409,
    "save_failed": 500,
}


def _status(result: dict) -> int:
    if result.get("ok"):
        return 200
    return _STATUS_BY_CODE.get(declared_error_code(result), 400)


async def _json_body(request: web.Request) -> dict:
    """Read an optional JSON object; malformed bodies become an invalid payload."""

    raw = await request.text()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def api_game_tactical_scenes(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    viewer = viewer_for(request, api.get_game_instance(game_key))
    result = api.tactical_scene_list(game_key, viewer)
    return web.json_response(result, status=_status(result))


async def api_game_tactical_scene(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    viewer = viewer_for(request, api.get_game_instance(game_key))
    result = api.tactical_scene_read(game_key, viewer, request.match_info.get("scene_id"))
    return web.json_response(result, status=_status(result))


async def _apply(request: web.Request, op: dict, body: dict) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    viewer = viewer_for(request, api.get_game_instance(game_key))
    pool = request.app.get("connection_pool")
    expected = body.get("expected_revision", request.query.get("expected_revision"))
    if isinstance(expected, str) and expected.strip().lstrip("-").isdigit():
        expected = int(expected)
    result = await api.tactical_scene_apply(
        game_key, viewer, op,
        expected_revision=expected,
        broadcast=pool.broadcast if pool is not None else None,
    )
    return web.json_response(result, status=_status(result))


async def api_tactical_scene_create(request: web.Request) -> web.Response:
    body = await _json_body(request)
    return await _apply(request, {"kind": "tactical_scene_create", "scene": body.get("scene")}, body)


async def api_tactical_scene_update(request: web.Request) -> web.Response:
    body = await _json_body(request)
    op = {
        "kind": "tactical_scene_update",
        "scene_id": request.match_info["scene_id"],
        "scene": body.get("scene"),
    }
    return await _apply(request, op, body)


async def api_tactical_scene_delete(request: web.Request) -> web.Response:
    body = await _json_body(request)
    op = {"kind": "tactical_scene_delete", "scene_id": request.match_info["scene_id"]}
    return await _apply(request, op, body)


async def api_tactical_scene_activate(request: web.Request) -> web.Response:
    body = await _json_body(request)
    op = {"kind": "tactical_scene_set_active", "scene_id": request.match_info["scene_id"]}
    return await _apply(request, op, body)


async def api_tactical_scene_placeable_create(request: web.Request) -> web.Response:
    body = await _json_body(request)
    op = {
        "kind": "tactical_scene_placeable_create",
        "scene_id": request.match_info["scene_id"],
        "collection": request.match_info["collection"],
        "placeable": body.get("placeable"),
    }
    return await _apply(request, op, body)


async def api_tactical_scene_placeable_update(request: web.Request) -> web.Response:
    body = await _json_body(request)
    op = {
        "kind": "tactical_scene_placeable_update",
        "scene_id": request.match_info["scene_id"],
        "collection": request.match_info["collection"],
        "placeable_id": request.match_info["placeable_id"],
        "placeable": body.get("placeable"),
    }
    return await _apply(request, op, body)


async def api_tactical_scene_placeable_delete(request: web.Request) -> web.Response:
    body = await _json_body(request)
    op = {
        "kind": "tactical_scene_placeable_delete",
        "scene_id": request.match_info["scene_id"],
        "collection": request.match_info["collection"],
        "placeable_id": request.match_info["placeable_id"],
    }
    return await _apply(request, op, body)


async def api_tactical_scene_import_preview(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    viewer = viewer_for(request, api.get_game_instance(game_key))
    body = await _json_body(request)
    result = portable_scene_import.preview_scene_import(
        _portable_scene_dependencies(api), game_key, viewer,
        definition=body.get("definition"),
    )
    return web.json_response(result, status=_status(result))


async def api_tactical_scene_import(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    viewer = viewer_for(request, api.get_game_instance(game_key))
    body = await _json_body(request)
    pool = request.app.get("connection_pool")
    result = await portable_scene_import.import_scene_definition(
        _portable_scene_dependencies(api), game_key, viewer,
        definition=body.get("definition"),
        intent=body,
        broadcast=pool.broadcast if pool is not None else None,
    )
    return web.json_response(result, status=_status(result))


def _portable_scene_dependencies(api: Any) -> TacticalSceneOpDependencies:
    """Route-side seam: the import service gets explicit deps, never the facade."""

    return TacticalSceneOpDependencies(
        get_instance=api.get_game_instance,
        save_instance=api.save_game_instance,
    )


def register_tactical_scene(app: web.Application) -> None:
    app.router.add_post(
        "/api/games/{game_key}/tactical-scenes/import", api_tactical_scene_import,
    )
    app.router.add_post(
        "/api/games/{game_key}/tactical-scenes/import/preview",
        api_tactical_scene_import_preview,
    )
    app.router.add_get("/api/games/{game_key}/tactical-scenes", api_game_tactical_scenes)
    app.router.add_get("/api/games/{game_key}/tactical-scenes/{scene_id}", api_game_tactical_scene)
    app.router.add_get("/api/games/{game_key}/tactical-scene", api_game_tactical_scene)
    app.router.add_post("/api/games/{game_key}/tactical-scenes", api_tactical_scene_create)
    app.router.add_patch("/api/games/{game_key}/tactical-scenes/{scene_id}", api_tactical_scene_update)
    app.router.add_delete("/api/games/{game_key}/tactical-scenes/{scene_id}", api_tactical_scene_delete)
    app.router.add_post(
        "/api/games/{game_key}/tactical-scenes/{scene_id}/activate", api_tactical_scene_activate,
    )
    app.router.add_post(
        "/api/games/{game_key}/tactical-scenes/{scene_id}/placeables/{collection}",
        api_tactical_scene_placeable_create,
    )
    app.router.add_patch(
        "/api/games/{game_key}/tactical-scenes/{scene_id}/placeables/{collection}/{placeable_id}",
        api_tactical_scene_placeable_update,
    )
    app.router.add_delete(
        "/api/games/{game_key}/tactical-scenes/{scene_id}/placeables/{collection}/{placeable_id}",
        api_tactical_scene_placeable_delete,
    )