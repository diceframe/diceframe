"""Game creation, reset, restart, and world lifecycle routes."""

from __future__ import annotations

import logging

from aiohttp import web

from src.webui.routes._common import (
    MAX_SEED_CHARS,
    _get_api,
    _require_confirmed_request,
)

logger = logging.getLogger("trpg")
from src.webui.routes.game_route_common import _gm_only_inst


async def api_create_game(request: web.Request) -> web.Response:
    body = await request.json()
    if len(str(body.get("description", ""))) > MAX_SEED_CHARS:
        return web.json_response(
            {"error": f"世界描述过长（上限 {MAX_SEED_CHARS} 字）"}, status=400
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
        narrative_perspective=str(body.get("narrative_perspective", "auto") or "auto"),
        gm_style_override=body.get("gm_style_override"),
        advancement_mode=str(body.get("advancement_mode", "milestone") or "milestone"),
        advancement_authority=str(
            body.get("advancement_authority", "ai_gm") or "ai_gm"
        ),
        # 逐卡控制方式与「未认领角色默认」；缺省时保持旧行为（每个席位 human）。
        unclaimed_control_default=str(body.get("unclaimed_control_default", "") or ""),
    )
    return web.json_response(result)


async def api_reset_game(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    gk = request.match_info["game_key"]
    _, denied = _gm_only_inst(request, gk)
    if denied is not None:
        return denied
    result = await _get_api(request).reset_game(gk)
    return web.json_response(result)


async def api_restart_game(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    gk = request.match_info["game_key"]
    _, denied = _gm_only_inst(request, gk)
    if denied is not None:
        return denied
    result = await _get_api(request).restart_game(gk)
    return web.json_response(result)


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
    return web.json_response(result)


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
    )
    return web.json_response(result)
