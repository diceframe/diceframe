"""GM, action, turn, and ruleset gameplay routes."""

from __future__ import annotations

import json
import logging

from aiohttp import web

from src.webui.routes._common import (
    MAX_ACTION_CHARS,
    _get_api,
)
from src.webui.services._common import is_game_gm

logger = logging.getLogger("trpg")
from src.webui.routes.game_route_common import (
    _broadcast_ruleset_change,
    _gm_only_inst,
    _narration_callbacks,
)


async def api_gm_command(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    if request.get("user_id", "") != inst.gm_uid:
        return web.json_response({"ok": False, "error": "GM only"}, status=403)
    body = await request.json()
    command = str(body.get("command", "") or "")
    if len(command) > MAX_ACTION_CHARS:
        return web.json_response(
            {"ok": False, "error": f"GM 指令过长（上限 {MAX_ACTION_CHARS} 字）"},
            status=400,
        )
    result = await api.gm_command(gk, command, str(body.get("mode", "note") or "note"))
    status = 200 if result.get("ok") else 409 if result.get("code") == "REWRITE_IN_PROGRESS" else 400
    return web.json_response(result, status=status)


async def api_rollback(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst, err = _gm_only_inst(request, gk)
    if err:
        return err
    result = await api.rollback_round(gk)
    status = 200 if result.get("ok") else 409 if result.get("code") == "REWRITE_IN_PROGRESS" else 400
    return web.json_response(result, status=status)


async def api_story_recap(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    _, err = _gm_only_inst(request, gk)
    if err:
        return err
    result = await api.generate_story_recap(gk)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_gm_private_message(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    _, denied = _gm_only_inst(request, gk)
    if denied is not None:
        return denied
    body = await request.json()
    text = str(body.get("text", "") or "")
    if len(text) > MAX_ACTION_CHARS:
        return web.json_response(
            {"ok": False, "error": f"悄悄话过长（上限 {MAX_ACTION_CHARS} 字）"},
            status=400,
        )
    result = await _get_api(request).gm_private_message(
        gk, str(body.get("user_id", "") or ""), text
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_action(request: web.Request) -> web.Response:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    body = await request.json()
    text = body.get("text", "")
    if len(text) > MAX_ACTION_CHARS:
        return web.json_response(
            {"error": f"行动文本过长（上限 {MAX_ACTION_CHARS} 字）"}, status=400
        )
    if request.get("player_preview", False) and not request.get(
        "player_delegate", False
    ):
        return web.json_response(
            {"error": "当前是房主预览模式，请先开启允许代操作"}, status=403
        )
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
        return web.json_response(
            {
                "narration": "处理请求时出错，请查看服务器日志",
                "advanced": False,
                "phase": "error",
            }
        )


async def api_kp_question(request: web.Request) -> web.Response:
    """Answer player table talk without submitting an action or broadcasting a turn."""
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    question = str(body.get("question", "") or "").strip()
    visibility = str(body.get("visibility", "private") or "private")
    if len(question) > MAX_ACTION_CHARS:
        return web.json_response(
            {
                "ok": False,
                "code": "QUESTION_TOO_LONG",
                "error": f"问题过长（上限 {MAX_ACTION_CHARS} 字）",
            },
            status=400,
        )
    if request.get("player_preview", False) and not request.get(
        "player_delegate", False
    ):
        return web.json_response(
            {
                "ok": False,
                "code": "PREVIEW_MODE_FORBIDDEN",
                "error": "当前是房主预览模式，请先开启允许代操作",
            },
            status=403,
        )
    result = await api.ask_kp_question(
        game_key,
        str(request.get("user_id", "") or ""),
        question,
        visibility,
    )
    if result["status"] == 200 and result["payload"].get("visibility") == "party":
        pool = request.app.get("connection_pool")
        if pool is not None:
            await pool.broadcast(game_key, {"type": "table_talk_changed"})
    return web.json_response(result["payload"], status=result["status"])


def _ruleset_gameplay_status(result: dict) -> int:
    if result.get("ok"):
        return 200
    code = str(result.get("code") or "")
    if code in {"GAME_NOT_FOUND", "RULE_NOT_FOUND"}:
        return 404
    if code in {"AUTH_REQUIRED", "PLAYER_NOT_IN_GAME"}:
        return 403
    if code in {"RULESET_INTENTS_UNAVAILABLE", "RULESET_RUNTIME_UNAVAILABLE"}:
        return 409
    if code in {"SESSION_ZERO_REQUIRED", "COMBAT_ACTION_REQUIRED"}:
        return 409
    if code == "LLM_NOT_CONFIGURED":
        return 503
    if code == "LLM_REQUEST_FAILED":
        return 502
    return 422


def _ruleset_requester_is_gm(request: web.Request, inst) -> bool:
    if request.get("player_preview", False):
        return False
    return is_game_gm(
        inst,
        str(request.get("user_id", "") or ""),
        bool(request.get("owner_authenticated", False)),
    )


async def api_ruleset_available_actions(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    inst = api.get_game_instance(game_key)
    requester_id = str(request.get("user_id", "") or "")
    requester_is_gm = _ruleset_requester_is_gm(request, inst)
    result = await api.ruleset_available_actions(
        game_key,
        requester_id,
        requester_is_gm,
    )
    return web.json_response(result, status=_ruleset_gameplay_status(result))


async def api_ruleset_submit_intent(request: web.Request) -> web.Response:
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    inst = api.get_game_instance(game_key)
    requester_id = str(request.get("user_id", "") or "")
    if request.get("player_preview", False) and not request.get(
        "player_delegate", False
    ):
        return web.json_response(
            {
                "ok": False,
                "code": "PREVIEW_MODE_FORBIDDEN",
                "error": "当前是房主预览模式，请先开启允许代操作",
            },
            status=403,
        )
    requester_is_gm = _ruleset_requester_is_gm(request, inst)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response(
            {"ok": False, "code": "INVALID_JSON", "error": "请求体必须是 JSON 对象"},
            status=400,
        )
    result = await api.ruleset_submit_intent(
        game_key,
        requester_id,
        requester_is_gm,
        body,
    )
    await _broadcast_ruleset_change(request, game_key, result)
    return web.json_response(result, status=_ruleset_gameplay_status(result))


async def api_ruleset_resolve_decision(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    intent = {
        **body,
        "type": "decision.resolve",
        "decision_id": request.match_info["decision_id"],
    }
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    inst = api.get_game_instance(game_key)
    requester_id = str(request.get("user_id", "") or "")
    if request.get("player_preview", False) and not request.get(
        "player_delegate", False
    ):
        return web.json_response(
            {
                "ok": False,
                "code": "PREVIEW_MODE_FORBIDDEN",
                "error": "当前是房主预览模式，请先开启允许代操作",
            },
            status=403,
        )
    requester_is_gm = _ruleset_requester_is_gm(request, inst)
    result = await api.ruleset_submit_intent(
        game_key,
        requester_id,
        requester_is_gm,
        intent,
    )
    await _broadcast_ruleset_change(request, game_key, result)
    return web.json_response(result, status=_ruleset_gameplay_status(result))


async def api_luck_decision(request: web.Request) -> web.Response:
    """叙事生成前处理一次幸运选择；Web 与 Bot 共用该接口。"""
    api = _get_api(request)
    gk = request.match_info["game_key"]
    actor_uid = request.get("user_id", "")
    if request.get("player_preview", False) and not request.get(
        "player_delegate", False
    ):
        return web.json_response(
            {"ok": False, "error": "当前是房主预览模式，请先开启允许代操作"}, status=403
        )
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
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"error": "游戏不存在"}, status=404)
    session_uid = request.get("user_id", "")
    body = await request.json()
    result = await api.resolve_payment(
        gk, payment_id, bool(body.get("accepted")), session_uid
    )
    code = str(result.get("code") or "")
    status = (
        200 if result.get("ok")
        else 404 if code in {"NOT_FOUND", "GAME_NOT_FOUND", "PROPOSAL_NOT_FOUND"}
        else 403 if code in {"FORBIDDEN", "PAYMENT_FORBIDDEN"}
        else 409 if code in {
            "ALREADY_RESOLVED",
            "STALE_RUN",
            "EFFECT_COMMIT_FAILED",
            "INSUFFICIENT_FUNDS",
            "REWRITE_IN_PROGRESS",
        }
        else 400
    )
    return web.json_response(result, status=status)


async def api_payment_create(request: web.Request) -> web.Response:
    """Create a payment proposal from the GM console."""

    api = _get_api(request)
    gk = request.match_info["game_key"]
    _inst, denied = _gm_only_inst(request, gk)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    result = await api.create_payment_proposal(
        gk,
        payer_uid=str(body.get("payer_uid") or ""),
        amount=body.get("amount", 0),
        reason=str(body.get("reason") or ""),
        recipient_uid=str(body.get("recipient_uid") or ""),
        items=body.get("items") if isinstance(body.get("items"), list) else [],
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_swipe(request: web.Request) -> web.Response:
    game_key = request.match_info["game_key"]
    round_num = int(request.match_info["round"])
    body = await request.json() if request.method == "POST" else {}
    api = _get_api(request)
    inst, denied = _gm_only_inst(request, game_key)
    if denied is not None:
        return denied
    if request.method == "PUT":
        nar = await api.generate_game_swipe(inst, round_num)
        external_effects_committed = await api.drain_economy_outbox(game_key)
        return web.json_response({
            "ok": True,
            "narration": nar,
            "external_effects_committed": external_effects_committed,
        })
    else:
        idx = body.get("swipe_index", 0)
        ok = await inst.switch_swipe(round_num, idx)
        if ok:
            await api.save_game_instance(inst)
        return web.json_response({"ok": ok})
