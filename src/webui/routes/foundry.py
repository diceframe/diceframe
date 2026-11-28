"""FVTT Scene import preview/commit routes (foundry-02)."""

from __future__ import annotations

import json

from aiohttp import web

from src.webui.errors import declared_error_code
from src.webui.routes._common import _get_api
from src.webui.viewer import viewer_for

_STATUS_BY_CODE = {
    "tactical_scene_not_found": 404,
    "tactical_scene_forbidden": 403,
    "tactical_scene_invalid_payload": 422,
    "foundry_version_unsupported": 422,
    "foundry_import_plan_invalid": 422,
    "foundry_import_stale": 409,
    "tactical_scene_stale_revision": 409,
    "rewrite_in_progress": 409,
    "stale_run": 409,
    "save_failed": 500,
}


def _status(result: dict) -> int:
    if result.get("ok"):
        return 200
    return _STATUS_BY_CODE.get(declared_error_code(result), 400)


async def _json_body(request: web.Request) -> dict:
    raw = await request.text()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _viewer(request: web.Request):
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    return api, game_key, viewer_for(request, api.get_game_instance(game_key))


async def api_foundry_scene_preview(request: web.Request) -> web.Response:
    api, game_key, viewer = _viewer(request)
    body = await _json_body(request)
    result = api.foundry_import_preview(
        game_key, viewer, body.get("scene"), source_version=body.get("source_version"),
    )
    return web.json_response(result, status=_status(result))


async def api_foundry_scene_commit(request: web.Request) -> web.Response:
    api, game_key, viewer = _viewer(request)
    body = await _json_body(request)
    pool = request.app.get("connection_pool")
    result = await api.foundry_import_commit(
        game_key, viewer, body.get("plan"),
        plan_digest=body.get("plan_digest"),
        policies=body.get("policies"),
        operation_id=body.get("operation_id"),
        broadcast=pool.broadcast if pool is not None else None,
    )
    return web.json_response(result, status=_status(result))


def register_foundry(app: web.Application) -> None:
    app.router.add_post(
        "/api/games/{game_key}/foundry/scenes/preview", api_foundry_scene_preview,
    )
    app.router.add_post(
        "/api/games/{game_key}/foundry/scenes/commit", api_foundry_scene_commit,
    )