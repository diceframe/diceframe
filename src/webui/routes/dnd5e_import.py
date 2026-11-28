"""dnd5e Actor import: character creation preview/commit (m2-02)."""

from __future__ import annotations

import json

from aiohttp import web

from src.webui.errors import declared_error_code
from src.webui.routes._common import _get_api
from src.webui.services import dnd5e_import as dnd5e_import_service
from src.webui.viewer import viewer_for

_STATUS_BY_CODE = {
    "dnd5e_import_forbidden": 403,
    "game_not_found": 404,
    "dnd5e_ruleset_required": 422,
    "dnd5e_actor_invalid": 422,
    "dnd5e_selection_invalid": 422,
    "dnd5e_creation_invalid": 422,
    "dnd5e_draft_unavailable": 422,
    "dnd5e_creation_incomplete": 422,
    "dnd5e_level_requires_progression": 422,
    "dnd5e_creation_failed": 422,
    "game_room_full": 409,
    "REWRITE_IN_PROGRESS": 409,
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


def _context(request: web.Request):
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    viewer = viewer_for(request, api.get_game_instance(game_key))
    dependencies = dnd5e_import_service.Dnd5eImportDependencies(
        get_instance=api.get_game_instance,
        create_player=api.create_player,
        builder_choices=api.ruleset_builder_choices,
        builder_validate=api.ruleset_builder_validate,
        builder_finalize=api.ruleset_builder_finalize,
    )
    return dependencies, game_key, viewer


async def api_dnd5e_character_preview(request: web.Request) -> web.Response:
    dependencies, game_key, viewer = _context(request)
    body = await _json_body(request)
    result = dnd5e_import_service.preview(
        dependencies, game_key, viewer, body.get("actor"),
        selections=body.get("selections"), locale=body.get("locale", ""),
    )
    return web.json_response(result, status=_status(result))


async def api_dnd5e_character_commit(request: web.Request) -> web.Response:
    dependencies, game_key, viewer = _context(request)
    body = await _json_body(request)
    result = await dnd5e_import_service.commit(
        dependencies, game_key, viewer, body.get("actor"),
        selections=body.get("selections"), locale=body.get("locale", ""),
    )
    return web.json_response(result, status=_status(result))