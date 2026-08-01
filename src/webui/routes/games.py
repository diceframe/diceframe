"""游戏路由 handler：游戏 CRUD / 行动推进 / 角色 / 玩家 / 切换 / 地图 / 滑动。"""

from __future__ import annotations

import logging
import json
import secrets
from urllib.parse import quote

from aiohttp import web

from src.webui.api import can_modify_character
from src.webui.routes._common import (
    MAX_ACTION_CHARS,
    MAX_SEED_CHARS,
    _get_api,
    _require_confirmed_request,
)

logger = logging.getLogger("trpg")


def _narration_callbacks(request: web.Request, game_key: str):
    """把 Web SSE 广播适配为回合 service 使用的可选回调。"""
    pool = request.app.get("connection_pool")
    if pool is None:
        return None, None

    async def on_delta(text: str) -> None:
        await pool.broadcast(game_key, {"type": "narration_delta", "text": text})

    async def on_reset() -> None:
        await pool.broadcast(game_key, {"type": "narration_reset"})

    return on_delta, on_reset


def _read_saved_gm_uid(registry, gk: tuple) -> str:
    save_path = registry._save_path(gk)
    for path in (save_path, save_path.with_name("state.backup.json")):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return str(data.get("gm_uid") or "")
        except Exception:
            logger.warning("读取存档 GM 身份失败: %s", path, exc_info=True)
    return ""


def _can_delete_save(request: web.Request, session_uid: str, gm_uid: str) -> bool:
    """Deleting a local save is allowed for the in-game GM or the WebUI owner.

    Old audit/dev saves can be bound to a browser session that no longer exists.
    The access-token-authenticated WebUI owner is still the local administrator,
    while player share links remain blocked because they do not set
    owner_authenticated.
    """
    return bool(gm_uid and (session_uid == gm_uid or request.get("owner_authenticated", False)))


async def api_games(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_games())


async def api_detail(request: web.Request) -> web.Response:
    d = _get_api(request).game_detail(request.match_info["game_key"])
    return web.json_response(d) if d else web.json_response({"error": "not found"}, status=404)


async def api_claim_gm_session(request: web.Request) -> web.Response:
    """Restore the saved GM identity after the owner signs in on another device."""
    api = _get_api(request)
    inst = request.app["subsystems"].registry.get(api._parse_key(request.match_info["game_key"]))
    if not inst or not inst.gm_uid:
        return web.json_response({"ok": False, "error": "存档没有可恢复的房主身份"}, status=404)
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
    return bool(request.get("user_id", "")) and request.get("user_id", "") == inst.gm_uid


def _system_log_allowed(request: web.Request, inst) -> bool:
    uid = request.get("user_id", "")
    if not uid:
        return False
    if uid == inst.gm_uid:
        return True
    return bool(getattr(inst, "solo_mode", False) and uid in getattr(inst, "players", {}))


async def api_game_health(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if not _system_log_allowed(request, inst):
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    include_resolved = request.query.get("include_resolved", "").lower() in {"1", "true", "yes"}
    result = api.game_health(gk, include_resolved)
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_mark_health_event(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
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
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if request.get("user_id", "") != inst.gm_uid:
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    body = await request.json()
    result = await api.set_solo_mode(gk, bool(body.get("solo")))
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_set_player_away(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    uid = request.match_info["user_id"]
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
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
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
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
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if request.get("user_id", "") != inst.gm_uid:
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    body = await request.json()
    password = str(body.get("password", "") or "")
    inst.set_room_password(password)
    await api._reg.save(inst)
    return web.json_response({"ok": True, "has_room_password": bool(password)})


async def api_gm_command(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if request.get("user_id", "") != inst.gm_uid:
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    body = await request.json()
    command = str(body.get("command", "") or "")
    if len(command) > MAX_ACTION_CHARS:
        return web.json_response({"ok": False, "error": f"GM 指令过长（上限 {MAX_ACTION_CHARS} 字）"}, status=400)
    result = await api.gm_command(gk, command, str(body.get("mode", "note") or "note"))
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_rollback(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst, err = _gm_only_inst(request, gk)
    if err:
        return err
    result = await api.rollback_round(gk)
    return web.json_response(result, status=200 if result.get("ok") else 400)


def _gm_only_inst(request: web.Request, gk: str):
    api = _get_api(request)
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return None, web.json_response({"ok": False, "error": "not found"}, status=404)
    if request.get("user_id", "") != inst.gm_uid:
        return None, web.json_response({"ok": False, "error": "GM only"}, status=403)
    return inst, None


def _should_rebind_player_session(
    session_uid: str,
    gm_uid: str,
    requested_uid: str,
    result: dict,
    join_as_new: bool,
) -> bool:
    if not result.get("ok"):
        return False
    if session_uid and session_uid == gm_uid:
        return False
    return bool(
        (requested_uid and result.get("user_id") == requested_uid) or join_as_new
    )


async def api_private_log(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    api = _get_api(request)
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    session_uid = request.get("user_id", "")
    if session_uid and session_uid == inst.gm_uid:
        result = _get_api(request).private_log(gk)
    elif session_uid in inst.players:
        result = _get_api(request).private_log_for_user(gk, session_uid)
    else:
        return web.json_response({"ok": False, "error": "未加入本局"}, status=403)
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_player_context(request: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "preview": bool(request.get("player_preview", False)),
        "delegate": bool(request.get("player_delegate", False)),
        "user_id": request.get("user_id", ""),
    })


async def api_gm_private_message(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    _, denied = _gm_only_inst(request, gk)
    if denied is not None:
        return denied
    body = await request.json()
    text = str(body.get("text", "") or "")
    if len(text) > MAX_ACTION_CHARS:
        return web.json_response({"ok": False, "error": f"悄悄话过长（上限 {MAX_ACTION_CHARS} 字）"}, status=400)
    result = await _get_api(request).gm_private_message(gk, str(body.get("user_id", "") or ""), text)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_chars(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_characters(request.match_info["game_key"]))


async def api_log(request: web.Request) -> web.Response:
    try:
        page = max(1, int(request.query.get("page", "1")))
        per_page = max(1, min(200, int(request.query.get("per_page", "50"))))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "分页参数必须是整数"}, status=400)
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    inst = request.app["subsystems"].registry.get(api._parse_key(game_key))
    include_internal = bool(inst and request.get("user_id", "") == inst.gm_uid)
    if include_internal:
        return web.json_response(api.get_log(game_key, page, per_page, True))
    return web.json_response(api.get_log(game_key, page, per_page))


async def api_create_game(request: web.Request) -> web.Response:
    body = await request.json()
    if len(str(body.get("description", ""))) > MAX_SEED_CHARS:
        return web.json_response({"error": f"世界描述过长（上限 {MAX_SEED_CHARS} 字）"}, status=400)
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
        room_password=str(body.get("room_password", "") or ""),
        language=str(body.get("language", "") or ""),
    )
    return web.json_response(result)


async def api_action(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    body = await request.json()
    text = body.get("text", "")
    if len(text) > MAX_ACTION_CHARS:
        return web.json_response({"error": f"行动文本过长（上限 {MAX_ACTION_CHARS} 字）"}, status=400)
    if request.get("player_preview", False) and not request.get("player_delegate", False):
        return web.json_response({"error": "当前是房主预览模式，请先开启允许代操作"}, status=403)
    on_delta, on_reset = _narration_callbacks(request, gk)
    try:
        result = await api.submit_action(
            gk,
            request.get("user_id", ""),
            text,
            confirm=bool(body.get("confirm", False)),
            d20=body.get("d20"),
            server_roll=bool(body.get("server_roll")),
            selected_attribute=str(body.get("selected_attribute", "") or ""),
            selected_skill=str(body.get("selected_skill", "") or ""),
            target_text=str(body.get("target_text", "") or ""),
            source=str(body.get("source", "") or ""),
            on_delta=on_delta,
            on_reset=on_reset,
        )
        return web.json_response(result["payload"], status=result["status"])
    except Exception:
        logger.exception("action 处理异常")
        return web.json_response({"narration": "处理请求时出错，请查看服务器日志", "advanced": False, "phase": "error"})


async def api_luck_decision(request: web.Request) -> web.Response:
    """叙事生成前处理一次幸运选择；Web 与 Bot 共用该接口。"""
    api = _get_api(request)
    gk = request.match_info["game_key"]
    actor_uid = request.get("user_id", "")
    if request.get("player_preview", False) and not request.get("player_delegate", False):
        return web.json_response({"ok": False, "error": "当前是房主预览模式，请先开启允许代操作"}, status=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    on_delta, on_reset = _narration_callbacks(request, gk)
    result = await api.resolve_luck_and_continue(
        gk,
        request.match_info["check_id"],
        actor_uid,
        bool(body.get("spend")),
        on_delta=on_delta,
        on_reset=on_reset,
    )
    return web.json_response(result["payload"], status=result["status"])


async def api_advance(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    on_delta, on_reset = _narration_callbacks(request, gk)
    result = await api.advance_turn(
        gk,
        request.get("user_id", ""),
        force=bool(body.get("force")),
        on_delta=on_delta,
        on_reset=on_reset,
    )
    return web.json_response(result["payload"], status=result["status"])


async def api_payment_resolve(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    payment_id = request.match_info["payment_id"]
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"error": "游戏不存在"}, status=404)
    session_uid = request.get("user_id", "")
    body = await request.json()
    result = await api.resolve_payment(gk, payment_id, bool(body.get("accepted")), session_uid)
    return web.json_response(result)


async def api_export_game(request: web.Request) -> web.Response:
    """导出单存档为 JSON 文件下载。"""
    api = _get_api(request)
    gk = api._parse_key(request.match_info["game_key"])
    registry = request.app["subsystems"].registry
    inst = registry.get(gk)
    if not inst:
        return web.json_response({"error": "not found"}, status=404)
    session_uid = request.get("user_id", "")
    if not session_uid or session_uid != inst.gm_uid:
        return web.json_response({"error": "仅 GM 可导出游戏"}, status=403)

    import re
    from datetime import datetime
    save_path = registry._save_path(gk)
    state_path = save_path if save_path.exists() else save_path.with_name("state.backup.json")
    if not state_path.exists():
        return web.json_response({"error": "存档文件不存在"}, status=404)
    try:
        body = state_path.read_bytes()
    except Exception as exc:
        logger.exception("读取存档失败: %s", state_path)
        return web.json_response({"error": "读取失败，请查看服务器日志"}, status=500)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^\w一-鿿-]+", "_", inst.world_name or "save").strip("_") or "save"
    filename = f"save_{safe_name}_{ts}.json"
    ascii_fallback = re.sub(r"[^\x21-\x7e]", "_", filename) or f"save_{ts}.json"
    return web.Response(
        body=body,
        content_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quote(filename)}',
            "Content-Length": str(len(body)),
        },
    )


async def api_char_update(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    uid = request.match_info["user_id"]
    body = await request.json()
    api = _get_api(request)
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"error": "游戏不存在"}, status=404)
    session_uid = request.get("user_id", "")
    if not can_modify_character(session_uid, uid, inst.gm_uid):
        return web.json_response({"error": "无权修改他人角色卡"}, status=403)
    return web.json_response(await api.update_character(gk, uid, body))


async def api_char_delete(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    gk = request.match_info["game_key"]
    uid = request.match_info["user_id"]
    api = _get_api(request)
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"error": "游戏不存在"}, status=404)
    session_uid = request.get("user_id", "")
    if not can_modify_character(session_uid, uid, inst.gm_uid):
        return web.json_response({"error": "无权删除他人角色"}, status=403)
    return web.json_response(await api.delete_character(gk, uid))


async def api_player_create(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    body = await request.json()
    api = _get_api(request)
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
    session_uid = request.get("user_id", "")
    requested_uid = str(body.get("user_id") or "").strip()
    join_as_new = bool(body.get("join_as_new")) and not requested_uid
    force_uid = "" if join_as_new else session_uid
    result = await api.create_player(gk, body, force_uid=force_uid, assign_new_id=join_as_new)
    # 换设备恢复：普通玩家可按链接恢复身份；GM 点击玩家操作链接时不能改绑成玩家。
    if _should_rebind_player_session(session_uid, inst.gm_uid, requested_uid, result, join_as_new):
        mgr = request.app.get("session_manager")
        token = request.get("session_token")
        if mgr and token:
            mgr.rebind(token, result.get("user_id", ""))
    return web.json_response(result)


async def api_verify_room_password(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    body = await request.json() if request.content_length else {}
    password = str(body.get("password", "") or "")
    api = _get_api(request)
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
    if not inst.room_password:
        return web.json_response({"ok": False, "error": "该游戏未设置房间密码"}, status=400)
    if not secrets.compare_digest(inst.room_password, password):
        return web.json_response({"ok": False, "error": "房间密码错误"}, status=403)
    if not inst.room_token:
        inst.set_room_token(secrets.token_urlsafe(24))
        await api._reg.save(inst)
    return web.json_response({"ok": True, "room_token": inst.room_token})


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


async def api_map_locations(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    result = _get_api(request).get_map_locations(gk)
    return web.json_response(result)


async def api_create_from_seed(request: web.Request) -> web.Response:
    body = await request.json()
    result = await _get_api(request).create_from_seed(
        seed_code=body.get("seed_code", ""),
        solo=body.get("solo", True),
        players=body.get("players", []),
        gm_uid=request.get("user_id", ""),
        language=str(body.get("language", "") or ""),
    )
    return web.json_response(result)


async def api_swipe(request: web.Request) -> web.Response:
    game_key = request.match_info["game_key"]
    round_num = int(request.match_info["round"])
    body = await request.json() if request.method == "POST" else {}
    api = _get_api(request)
    inst, denied = _gm_only_inst(request, game_key)
    if denied is not None:
        return denied
    if request.method == "PUT":
        nar = await api._handler.generate_swipe(inst, round_num)
        return web.json_response({"ok": True, "narration": nar})
    else:
        idx = body.get("swipe_index", 0)
        ok = await inst.switch_swipe(round_num, idx)
        return web.json_response({"ok": ok})


async def api_batch_delete_games(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    session_uid = request.get("user_id", "")
    if not session_uid:
        return web.json_response({"ok": False, "error": "未登录，无法删除存档"}, status=403)
    api = _get_api(request)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "请求体不是合法 JSON"}, status=400)
    keys = body.get("game_keys")
    if not isinstance(keys, list) or not keys:
        return web.json_response({"ok": False, "error": "game_keys 必须是非空列表"}, status=400)
    if len(keys) > 100:
        return web.json_response({"ok": False, "error": "单次最多 100 个"}, status=400)

    registry = request.app["subsystems"].registry
    deleted: list[str] = []
    failed: list[dict] = []
    for raw in keys:
        if not isinstance(raw, str) or not raw:
            failed.append({"key": str(raw), "error": "key 类型错误"})
            continue
        try:
            gk = api._parse_key(raw)
        except Exception as exc:
            failed.append({"key": raw, "error": f"key 解析失败: {exc}"})
            continue
        inst = registry.get(gk)
        save_dir = registry._save_path(gk).parent
        if not inst and not save_dir.exists():
            failed.append({"key": raw, "error": "游戏不存在或存档目录不存在"})
            continue
        gm_uid = inst.gm_uid if inst else _read_saved_gm_uid(registry, gk)
        if not gm_uid:
            failed.append({"key": raw, "error": "存档缺少 GM 身份，拒绝删除"})
            continue
        if not _can_delete_save(request, session_uid, gm_uid):
            failed.append({"key": raw, "error": "非 GM 不可删除"})
            continue
        result = api.delete_game(raw)
        if result.get("ok"):
            deleted.append(raw)
        else:
            failed.append({"key": raw, "error": str(result.get("error") or "删除失败")})
    return web.json_response({"ok": True, "deleted": deleted, "failed": failed})


async def api_delete_game(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    session_uid = request.get("user_id", "")
    if not session_uid:
        return web.json_response({"error": "未登录，无法删除存档"}, status=403)
    api = _get_api(request)
    gk = api._parse_key(request.match_info["game_key"])
    registry = request.app["subsystems"].registry
    inst = registry.get(gk)
    save_dir = registry._save_path(gk).parent
    if not inst and not save_dir.exists():
        return web.json_response({"error": "not found"}, status=404)
    gm_uid = inst.gm_uid if inst else _read_saved_gm_uid(registry, gk)
    if not gm_uid:
        return web.json_response({"error": "存档缺少 GM 身份，拒绝删除"}, status=403)
    if not _can_delete_save(request, session_uid, gm_uid):
        return web.json_response({"error": "仅 GM 可删除游戏"}, status=403)
    result = api.delete_game(request.match_info["game_key"])
    if not result.get("ok"):
        error = str(result.get("error") or "删除失败")
        status = 404 if error == "存档目录不存在" else 500
        return web.json_response({"error": error}, status=status)
    return web.json_response(result)


def register_games(app: web.Application) -> None:
    app.router.add_get("/api/games", api_games)
    app.router.add_get("/api/games/{game_key}", api_detail)
    app.router.add_post("/api/games/{game_key}/claim-gm", api_claim_gm_session)
    app.router.add_get("/api/games/{game_key}/multiplayer", api_multiplayer_status)
    app.router.add_get("/api/games/{game_key}/player-context", api_player_context)
    app.router.add_get("/api/games/{game_key}/health", api_game_health)
    app.router.add_post("/api/games/{game_key}/health/{event_id}/{action:resolve|ignore}", api_mark_health_event)
    app.router.add_post("/api/games/{game_key}/mode", api_set_solo_mode)
    app.router.add_post("/api/games/{game_key}/players/{user_id}/away", api_set_player_away)
    app.router.add_post("/api/games/{game_key}/player-access", api_set_player_access)
    app.router.add_post("/api/games/create", api_create_game)
    app.router.add_post("/api/games/create-from-seed", api_create_from_seed)
    app.router.add_post("/api/games/batch-delete", api_batch_delete_games)
    app.router.add_post("/api/games/{game_key}/action", api_action)
    app.router.add_post("/api/games/{game_key}/checks/{check_id}/luck", api_luck_decision)
    app.router.add_post("/api/games/{game_key}/advance", api_advance)
    app.router.add_post("/api/games/{game_key}/gm-command", api_gm_command)
    app.router.add_post("/api/games/{game_key}/rollback", api_rollback)
    app.router.add_get("/api/games/{game_key}/private-log", api_private_log)
    app.router.add_post("/api/games/{game_key}/private-message", api_gm_private_message)
    app.router.add_post("/api/games/{game_key}/payments/{payment_id}", api_payment_resolve)
    app.router.add_get("/api/games/{game_key}/characters", api_chars)
    app.router.add_get("/api/games/{game_key}/log", api_log)
    app.router.add_route("DELETE", "/api/games/{game_key}", api_delete_game)
    app.router.add_get("/api/games/{game_key}/export", api_export_game)
    app.router.add_route("PUT", "/api/games/{game_key}/character/{user_id}", api_char_update)
    app.router.add_route("DELETE", "/api/games/{game_key}/character/{user_id}", api_char_delete)
    app.router.add_post("/api/games/{game_key}/players", api_player_create)
    app.router.add_post("/api/games/{game_key}/verify-room-password", api_verify_room_password)
    app.router.add_post("/api/games/{game_key}/room-password", api_set_room_password)
    app.router.add_post("/api/games/{game_key}/reset", api_reset_game)
    app.router.add_post("/api/games/{game_key}/restart", api_restart_game)
    app.router.add_post("/api/games/{game_key}/switch-world", api_switch_world)
    app.router.add_get("/api/games/{game_key}/map", api_map_locations)
    app.router.add_post(r"/api/games/{game_key}/swipe/{round:\d+}", api_swipe)
    app.router.add_put(r"/api/games/{game_key}/swipe/{round:\d+}", api_swipe)
