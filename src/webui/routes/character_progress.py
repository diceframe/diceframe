"""规则声明式成长的写接口：分配职业等级 / 提升属性骰 / 消耗技能点。"""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api
from src.webui.services.character_progress import (
    apply_attribute_step,
    assign_class_level,
    spend_skill_point,
)


def _authorized(request: web.Request, instance, target_uid: str) -> bool:
    actor = str(request.get("user_id", "") or "")
    return bool(actor) and (actor == target_uid or actor == instance.gm_uid)


def _rule_for(api, instance):
    """按存档自身规则加载 RuleSystem；失败返回 None（服务层退化为不校验职业名）。"""
    try:
        return api.rule_for_game(instance)
    except Exception:
        return None


async def _body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return {}
    return body if isinstance(body, dict) else {}


async def class_level(request: web.Request) -> web.Response:
    """POST /api/games/{game_key}/characters/{uid}/class-level

    body: ``{"class": "神射手", "run_id": "..."}``；本人或 GM 可用。
    """
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    uid = request.match_info["uid"]
    instance = api.get_game_instance(game_key)
    if instance is None:
        return web.json_response({"ok": False, "error_code": "game_missing"}, status=404)
    if not _authorized(request, instance, uid):
        return web.json_response({"ok": False, "error_code": "forbidden"}, status=403)
    if uid not in instance.players:
        return web.json_response({"ok": False, "error_code": "player_missing"}, status=404)

    body = await _body(request)
    run_id = str(body.get("run_id") or "")
    if run_id and run_id != instance.run_id:
        return web.json_response({"ok": False, "error_code": "run_changed"}, status=409)

    rule = _rule_for(api, instance)
    try:
        result = assign_class_level(instance, uid, str(body.get("class") or ""), rule)
    except ValueError as exc:
        return web.json_response({"ok": False, "error_code": "invalid", "error": str(exc)}, status=400)

    await api.save_game_instance(instance)
    pool = request.app.get("connection_pool")
    if pool is not None:
        await pool.broadcast(game_key, {"type": "character_progress_changed"})
    return web.json_response({"ok": True, **result})


async def skill_point(request: web.Request) -> web.Response:
    """POST /api/games/{game_key}/characters/{uid}/skill-point

    body: ``{"skill": "远程武器精通", "run_id": "..."}``；本人或 GM 可用。
    """
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    uid = request.match_info["uid"]
    instance = api.get_game_instance(game_key)
    if instance is None:
        return web.json_response({"ok": False, "error_code": "game_missing"}, status=404)
    if not _authorized(request, instance, uid):
        return web.json_response({"ok": False, "error_code": "forbidden"}, status=403)
    if uid not in instance.players:
        return web.json_response({"ok": False, "error_code": "player_missing"}, status=404)

    body = await _body(request)
    run_id = str(body.get("run_id") or "")
    if run_id and run_id != instance.run_id:
        return web.json_response({"ok": False, "error_code": "run_changed"}, status=409)

    rule = _rule_for(api, instance)
    try:
        result = spend_skill_point(instance, uid, str(body.get("skill") or ""), rule)
    except ValueError as exc:
        return web.json_response({"ok": False, "error_code": "invalid", "error": str(exc)}, status=400)

    await api.save_game_instance(instance)
    pool = request.app.get("connection_pool")
    if pool is not None:
        await pool.broadcast(game_key, {"type": "character_progress_changed"})
    return web.json_response({"ok": True, **result})


async def attribute_step(request: web.Request) -> web.Response:
    """POST /api/games/{game_key}/characters/{uid}/attribute-step

    body: ``{"attribute": "dex", "run_id": "..."}``；20/40 级里程碑提升一档骰。
    """
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    uid = request.match_info["uid"]
    instance = api.get_game_instance(game_key)
    if instance is None:
        return web.json_response({"ok": False, "error_code": "game_missing"}, status=404)
    if not _authorized(request, instance, uid):
        return web.json_response({"ok": False, "error_code": "forbidden"}, status=403)
    if uid not in instance.players:
        return web.json_response({"ok": False, "error_code": "player_missing"}, status=404)

    body = await _body(request)
    run_id = str(body.get("run_id") or "")
    if run_id and run_id != instance.run_id:
        return web.json_response({"ok": False, "error_code": "run_changed"}, status=409)

    rule = _rule_for(api, instance)
    try:
        result = apply_attribute_step(instance, uid, str(body.get("attribute") or ""), rule)
    except ValueError as exc:
        return web.json_response({"ok": False, "error_code": "invalid", "error": str(exc)}, status=400)

    await api.save_game_instance(instance)
    pool = request.app.get("connection_pool")
    if pool is not None:
        await pool.broadcast(game_key, {"type": "character_progress_changed"})
    return web.json_response({"ok": True, **result})


def register(app: web.Application) -> None:
    app.router.add_post(
        "/api/games/{game_key}/characters/{uid}/class-level", class_level
    )
    app.router.add_post(
        "/api/games/{game_key}/characters/{uid}/skill-point", skill_point
    )
    app.router.add_post(
        "/api/games/{game_key}/characters/{uid}/attribute-step", attribute_step
    )
