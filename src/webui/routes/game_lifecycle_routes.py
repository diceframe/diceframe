"""Game creation, reset, restart, and world lifecycle routes."""

from __future__ import annotations

import logging

from aiohttp import web
from src.game_preferences import UNSET

from src.webui.routes._common import (
    MAX_SEED_CHARS,
    _get_api,
    _require_confirmed_request,
)

logger = logging.getLogger("trpg")
from src.webui.routes.game_route_common import _gm_only_inst
from src.webui.services._common import canonical_game_key
from src.localization.messages import t


async def api_create_game(request: web.Request) -> web.Response:
    body = await request.json()
    if len(str(body.get("description", ""))) > MAX_SEED_CHARS:
        return web.json_response(
            {"error": t("routes_game_lifecycle_routes.world_description_too_long", max_seed_chars=MAX_SEED_CHARS)}, status=400
        )
    # 前端 difficulty 下拉给的是中文值（轻松/标准/硬核），与规则模板的
    # difficulty_instructions 键一致；旧代码硬编码 "standard" 会命中不到任何
    # 难度指令，导致难度系统形同虚设。
    difficulty = body.get("difficulty") or "标准"
    result = await _get_api(request).create_game(
        body.get("world_id", "default_fantasy"),
        body.get("game_name", ""),
        body.get("group_name", "Web端"),
        body.get("rule_id", ""),
        solo=body.get("solo", True),
        lorebook_world_id=body.get("lorebook_world_id", ""),
        difficulty=difficulty,
        description=body.get("description", ""),
        create_lorebook=body.get("create_lorebook", False),
        blank_lorebook=body.get("blank_lorebook", False),
        source_world_id=body.get("source_world_id", ""),
        players=body.get("players", []),
        custom_world=body.get("custom_world", False),
        gm_uid=request.get("user_id", ""),
        # 区分三态：字段缺失/JSON null → None（后端按 solo/多人决定是否生成随机密码）；
        # 显式空串 "" → 开放；非空 → 加密。
        room_password=body.get("room_password"),
        language=str(body.get("language", "") or ""),
        scene_image=body.get("scene_image"),
        map_background=body.get("map_background"),
        adventure_id=str(body.get("adventure_id", "") or ""),
        adventure_source_kind=str(body.get("adventure_source_kind", "") or ""),
        adventure_source_id=str(body.get("adventure_source_id", "") or ""),
        play_mode=str(body.get("play_mode", "") or ""),
        narrative_perspective=body.get("narrative_perspective", UNSET),
        story_mode=body.get("story_mode", UNSET),
        gm_style_override=body.get("gm_style_override", UNSET),
        advancement_mode=body.get("advancement_mode", UNSET),
        advancement_authority=body.get("advancement_authority", UNSET),
        # 逐卡控制方式与「未认领角色默认」；缺省时保持旧行为（每个席位 human）。
        unclaimed_control_default=str(body.get("unclaimed_control_default", "") or ""),
        world_ref=body.get("world_ref"),
        book_bindings=body.get("book_bindings"),
    )
    return web.json_response(result)


_LIFECYCLE_ERROR_STATUS = {
    "GAME_NOT_FOUND": 404,
    "WORLD_NOT_FOUND": 404,
    "INVALID_WORLD_ID": 400,
    "INVALID_WORLD_REF": 422,
    "INVALID_STORY_MODE": 400,
    "UNSUPPORTED_STORYTELLING_SCHEMA": 422,
    "ADVENTURE_WORLD_LOCKED": 409,
    "ADVENTURE_RUNTIME_INIT_FAILED": 422,
    "RULESET_RUNTIME_INIT_FAILED": 422,
    "REWRITE_IN_PROGRESS": 409,
    "STALE_RUN": 409,
    "ROUND_PROCESSING": 409,
    "WORLD_LOAD_FAILED": 500,
}


def _lifecycle_status(result: dict) -> int:
    """HTTP status for a coded lifecycle failure; uncoded legacy failures stay 200."""

    if result.get("ok", True):
        return 200
    return _LIFECYCLE_ERROR_STATUS.get(str(result.get("error_code") or ""), 200)


async def api_reset_game(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    gk = request.match_info["game_key"]
    inst, denied = _gm_only_inst(request, gk)
    if denied is not None:
        return denied
    before = set(getattr(inst, "players", {}) or {})
    # The GM identity survives a reset; only the departed seats' devices go.
    before.discard(str(getattr(inst, "gm_uid", "") or ""))
    api = _get_api(request)
    result = await api.reset_game(gk)
    if result.get("ok"):
        # Reset starts a run without the old seats: their devices are just
        # visitors in this game from now on.
        current = api.get_game_instance(gk)
        remaining = set(getattr(current, "players", {}) or {}) if current is not None else set()
        mgr = request.app.get("session_manager")
        if mgr is not None:
            for uid in sorted(before - remaining):
                mgr.revoke_game_binding(uid, canonical_game_key(gk))
    return web.json_response(result, status=_lifecycle_status(result))


async def api_restart_game(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    gk = request.match_info["game_key"]
    _, denied = _gm_only_inst(request, gk)
    if denied is not None:
        return denied
    result = await _get_api(request).restart_game(gk)
    return web.json_response(result, status=_lifecycle_status(result))


async def api_switch_world(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    gk = request.match_info["game_key"]
    _, denied = _gm_only_inst(request, gk)
    if denied is not None:
        return denied
    body = await request.json()
    result = await _get_api(request).switch_world(gk, body.get("world_id", ""))
    return web.json_response(result, status=_lifecycle_status(result))


async def api_create_from_seed(request: web.Request) -> web.Response:
    body = await request.json()
    result = await _get_api(request).create_from_seed(
        seed_code=body.get("seed_code", ""),
        solo=body.get("solo", True),
        players=body.get("players", []),
        gm_uid=request.get("user_id", ""),
        language=str(body.get("language", "") or ""),
        scene_image=body.get("scene_image"),
        narrative_perspective=str(body.get("narrative_perspective", "") or ""),
        story_mode=body.get("story_mode", ""),
    )
    return web.json_response(result)
