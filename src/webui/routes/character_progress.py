"""Transport for atomic declarative character growth."""
from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api
from src.webui.services.character_progress import update_character_progress


async def _update(request: web.Request, operation: str, field: str) -> web.Response:
    api = _get_api(request)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    game_key = request.match_info["game_key"]
    result, status = await update_character_progress(
        game_key=game_key, uid=request.match_info["uid"],
        requester_id=str(request.get("user_id", "") or ""),
        expected_run_id=str(body.get("run_id") or ""),
        operation=operation, selection=str(body.get(field) or ""),
        get_instance=api.get_game_instance, load_rule=api.rule_for_game,
        save_instance=api.save_game_instance,
    )
    if result.get("ok"):
        pool = request.app.get("connection_pool")
        if pool is not None:
            await pool.broadcast(game_key, {"type": "character_progress_changed"})
    return web.json_response(result, status=status)


async def class_level(request: web.Request) -> web.Response:
    return await _update(request, "class-level", "class")


async def skill_point(request: web.Request) -> web.Response:
    return await _update(request, "skill-point", "skill")


async def attribute_step(request: web.Request) -> web.Response:
    return await _update(request, "attribute-step", "attribute")


def register(app: web.Application) -> None:
    app.router.add_post("/api/games/{game_key}/characters/{uid}/class-level", class_level)
    app.router.add_post("/api/games/{game_key}/characters/{uid}/skill-point", skill_point)
    app.router.add_post("/api/games/{game_key}/characters/{uid}/attribute-step", attribute_step)
