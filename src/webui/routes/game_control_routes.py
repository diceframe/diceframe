"""Multiplayer session and room-control routes."""

from __future__ import annotations

import logging
import secrets

from aiohttp import web

from src.webui.routes._common import (
    _get_api,
)
from src.webui.services._common import is_game_gm

logger = logging.getLogger("trpg")


async def api_claim_gm_session(request: web.Request) -> web.Response:
    """Restore the saved GM identity after the owner signs in on another device."""
    api = _get_api(request)
    inst = api.get_game_instance(request.match_info["game_key"])
    if not inst or not inst.gm_uid:
        return web.json_response(
            {"ok": False, "error": "存档没有可恢复的房主身份"}, status=404
        )
    token = request.get("session_token", "")
    manager = request.app.get("session_manager")
    if not token or not manager:
        return web.json_response({"ok": False, "error": "浏览器会话不可用"}, status=400)
    manager.rebind(token, inst.gm_uid)
    return web.json_response({"ok": True, "user_id": inst.gm_uid})


async def api_multiplayer_status(request: web.Request) -> web.Response:
    result = _get_api(request).multiplayer_status(request.match_info["game_key"])
    return web.json_response(result, status=200 if result.get("ok") else 404)


def _health_allowed(request: web.Request, inst) -> bool:
    return is_game_gm(
        inst,
        request.get("user_id", ""),
        bool(request.get("owner_authenticated", False)),
    )


def _system_log_allowed(request: web.Request, inst) -> bool:
    uid = request.get("user_id", "")
    if not uid:
        return False
    if is_game_gm(inst, uid, bool(request.get("owner_authenticated", False))):
        return True
    return bool(
        getattr(inst, "solo_mode", False) and uid in getattr(inst, "players", {})
    )


async def api_game_health(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if not _system_log_allowed(request, inst):
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    include_resolved = request.query.get("include_resolved", "").lower() in {
        "1",
        "true",
        "yes",
    }
    result = api.game_health(gk, include_resolved)
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_mark_health_event(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if not _health_allowed(request, inst):
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    action = request.match_info["action"]
    result = await api.mark_game_health_event(
        gk,
        request.match_info["event_id"],
        resolved=action == "resolve",
        ignored=action == "ignore",
    )
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_set_solo_mode(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if not is_game_gm(
        inst,
        request.get("user_id", ""),
        bool(request.get("owner_authenticated", False)),
    ):
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    body = await request.json()
    result = await api.set_solo_mode(gk, bool(body.get("solo")))
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_set_narrative_perspective(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if not is_game_gm(
        inst,
        request.get("user_id", ""),
        bool(request.get("owner_authenticated", False)),
    ):
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    body = await request.json()
    result = await api.set_narrative_perspective(
        gk,
        str(body.get("perspective", "") or ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_set_luck_timeout(request: web.Request) -> web.Response:
    """GM 按局设置幸运超时秒数（0=禁用，异步局建议 0，实时局默认 60）。"""
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if request.get("user_id", "") != inst.gm_uid:
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    body = await request.json()
    try:
        seconds = int(body.get("seconds"))
    except (TypeError, ValueError):
        return web.json_response(
            {"ok": False, "error": "seconds 必须是整数（秒，0=禁用）"}, status=400
        )
    try:
        inst.configure_session(luck_timeout_seconds=seconds)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    await api.save_game_instance(inst)
    return web.json_response(
        {"ok": True, "luck_timeout_seconds": inst.luck_timeout_seconds}
    )


async def api_set_reward_policy(request: web.Request) -> web.Response:
    """GM 按局设置奖励自动结算策略。

    body: {"mode": "auto_small_cash"|"gm_confirm", "auto_reward_cap": int?}。
    mode=auto_small_cash 时纯货币小额奖励自动到账；gm_confirm 时所有剧情
    奖励等待 GM 确认。auto_reward_cap 省略时沿用规则模板/全局默认。
    """
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if request.get("user_id", "") != inst.gm_uid:
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    body = await request.json()
    policy = {
        "mode": str(body.get("mode") or ""),
        "auto_reward_cap": body.get("auto_reward_cap"),
    }
    result = await api.set_economy_reward_policy(gk, policy)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_set_player_away(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    uid = request.match_info["user_id"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    actor = request.get("user_id", "")
    if actor != inst.gm_uid and actor != uid:
        return web.json_response({"ok": False, "error": "GM or self only"}, status=403)
    body = await request.json()
    result = await api.set_player_away(gk, uid, bool(body.get("away")))
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_set_player_access(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if request.get("user_id", "") != inst.gm_uid:
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    body = await request.json()
    result = await api.set_player_access(gk, bool(body.get("open")))
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_set_room_password(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if request.get("user_id", "") != inst.gm_uid:
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    body = await request.json()
    password = str(body.get("password", "") or "")
    try:
        inst.set_room_password(password)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    await api.save_game_instance(inst)
    return web.json_response({"ok": True, "has_room_password": bool(password)})


async def api_private_log(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    session_uid = request.get("user_id", "")
    if session_uid and is_game_gm(
        inst, session_uid, bool(request.get("owner_authenticated", False))
    ):
        result = _get_api(request).private_log(gk)
    elif session_uid in inst.players:
        result = _get_api(request).private_log_for_user(gk, session_uid)
    else:
        return web.json_response({"ok": False, "error": "未加入本局"}, status=403)
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_table_talk(request: web.Request) -> web.Response:
    """Read the public table-talk channel for a player or the game GM."""
    gk = request.match_info["game_key"]
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    session_uid = str(request.get("user_id", "") or "")
    owner = bool(request.get("owner_authenticated", False))
    if not is_game_gm(inst, session_uid, owner) and session_uid not in inst.players:
        return web.json_response({"ok": False, "error": "未加入本局"}, status=403)
    return web.json_response(api.table_talk(gk))


async def api_verify_room_password(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    body = await request.json() if request.content_length else {}
    password = str(body.get("password", "") or "")
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
    if not inst.room_password:
        return web.json_response(
            {"ok": False, "error": "该游戏未设置房间密码"}, status=400
        )
    if not secrets.compare_digest(inst.room_password, password):
        return web.json_response({"ok": False, "error": "房间密码错误"}, status=403)
    if not inst.room_token:
        inst.set_room_token(secrets.token_urlsafe(24))
        await api.save_game_instance(inst)
    return web.json_response({"ok": True, "room_token": inst.room_token})
