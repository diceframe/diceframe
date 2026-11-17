"""SSE 流式路由 handler：游戏流 / 行动流 / 分用户播放。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from aiohttp import web

from src.engine.economy import has_blocking_economy_decision, pending_economy_proposals
from src.engine.game_instance import GameState
from src.engine import module_hooks
from src.engine.modules import checks, progression_state, room_access, session_stats
from src.engine.visibility_rules import proposal_visible_to
from src.llm.parser import sanitize_narration
from src.webui.connection_pool import ConnectionPool
from src.webui.routes._common import MAX_ACTION_CHARS, _get_api
from src.webui.services.turns import economy_decision_pending_payload
from src.webui.access_control import ACCESS_CONTROL_KEY, BOT_GRANT_KEY
from src.webui.sse_ticket import SSE_GRANT_KEY, BotGrant, OwnerGrant, SseGrant
from src.localization.messages import t

logger = logging.getLogger("trpg")

_EVENT_CURSOR_RE = re.compile(
    r"^r(?P<round>\d{1,10})\.p(?P<private>\d{1,10})\.a(?P<action>0|[0-9a-f]{10})"
    r"(?:\.s(?P<public>0|[0-9a-f]{10}))?$"
)


async def api_sse_ticket(request: web.Request) -> web.Response:
    game_key = request.match_info["game_key"]
    user_id = request.get("user_id", "")
    api = _get_api(request)
    inst = api.get_game_instance(game_key)
    if not inst:
        return web.json_response({"error": "not found"}, status=404)
    if not user_id or user_id not in inst.players:
        return web.json_response({"error": "未加入本局，无法订阅"}, status=403)
    ticket, expires_in = request.app["sse_tickets"].issue(
        game_key, user_id, _ticket_grant(request, inst, user_id),
    )
    return web.json_response({"ticket": ticket, "expires_in": expires_in})


async def sse_stream(request: web.Request) -> web.StreamResponse:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"error": "not found"}, status=404)

    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    await response.prepare(request)

    seen_rounds = progression_state.round_value(inst)
    max_idle = 60
    try:
        for _ in range(max_idle * 2):
            current = api.get_game_instance(gk)
            if not current:
                await response.write(b"event: end\ndata: game_ended\n\n")
                break
            if progression_state.round_value(current) > seen_rounds:
                seen_rounds = progression_state.round_value(current)
                last_log = current.log[-1] if current.log else {}
                gm_text = sanitize_narration(last_log.get("gm_response", ""))
                if gm_text:
                    data = json.dumps({"narration": gm_text, "round": seen_rounds}, ensure_ascii=False)
                    await response.write(f"data: {data}\n\n".encode())
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    finally:
        return response


async def sse_stream_action(request: web.Request) -> web.StreamResponse:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    body = await request.json()
    text = body.get("text", "")
    if len(text) > MAX_ACTION_CHARS:
        return web.json_response({"error": t("webui.routes.sse.action_too_long", max_action_chars=MAX_ACTION_CHARS)}, status=400)
    selected_attribute = str(body.get("selected_attribute", "") or "")
    selected_skill = str(body.get("selected_skill", "") or "")
    target_text = str(body.get("target_text", "") or "")

    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"error": "not found"}, status=404)
    user_id = request.get("user_id", "")
    if not user_id or user_id not in inst.players:
        return web.json_response({"error": "未加入本局，无法提交行动"}, status=403)
    if inst.is_dead(user_id):
        return web.json_response({"error": "角色已死亡，无法提交行动"}, status=403)
    await api.drain_economy_outbox(gk)
    if has_blocking_economy_decision(inst):
        return web.json_response(
            economy_decision_pending_payload(inst, user_id),
            status=409,
        )
    if inst.state == GameState.ACTIVE_JUDGMENT:
        return web.json_response({"error": "本轮正在推进剧情，请等待下一轮开始"}, status=409)

    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    await response.prepare(request)

    try:
        await response.write(b"event: start\ndata: processing\n\n")

        # 与 POST /action 走同一条应用服务路径（turns.submit_action）：判定/叙事
        # 失败会回滚到行动阶段并返回结构化错误，不会把对局留在
        # ACTIVE_JUDGMENT（见 turns._process_round 与生产事故 2026-09-10）。
        result = await api.submit_action(
            gk,
            user_id,
            text,
            selected_attribute=selected_attribute,
            selected_skill=selected_skill,
            target_text=target_text,
        )
        payload = result.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if int(result.get("status", 200) or 200) >= 400 or payload.get("ok") is False:
            error_data = json.dumps(
                {
                    "error": payload.get("error") or t("webui.routes.sse.error_retry_later"),
                    "error_code": payload.get("error_code") or "",
                },
                ensure_ascii=False,
            )
            await response.write(f"event: error\ndata: {error_data}\n\n".encode())
            return response

        if not payload.get("advanced"):
            data = json.dumps(
                {
                    "narration": payload.get("narration") or "(已记录，等待推进)",
                    "waiting": True,
                },
                ensure_ascii=False,
            )
            await response.write(f"data: {data}\n\n".encode())
        else:
            narration = str(payload.get("narration") or "")
            parts = narration.split("\n\n") if narration else [""]
            for i, part in enumerate(parts):
                if not part.strip():
                    continue
                data = json.dumps({"narration": part.strip(), "index": i, "total": len(parts)}, ensure_ascii=False)
                await response.write(f"data: {data}\n\n".encode())
            check_results = payload.get("check_results") or []
            if check_results:
                await response.write(f"data: {json.dumps({'check_result': check_results[-1], 'check_results': check_results}, ensure_ascii=False)}\n\n".encode())
            recap = payload.get("recap")
            if recap:
                await response.write(f"data: {json.dumps({'recap': recap}, ensure_ascii=False)}\n\n".encode())

        await response.write(b"event: done\ndata: complete\n\n")
    except Exception:
        logger.exception("SSE 流处理异常")
        error_data = json.dumps({"error": t("webui.routes.sse.error_see_logs")}, ensure_ascii=False)
        await response.write(f"event: error\ndata: {error_data}\n\n".encode())
    finally:
        return response


async def sse_play(request: web.Request) -> web.StreamResponse:
    """分用户 SSE 推送：叙事 + 私聊 + 状态。"""
    game_key = request.match_info["game_key"]
    user_id = request.get("user_id", "")
    pool: ConnectionPool = request.app["connection_pool"]
    api = _get_api(request)
    inst = api.get_game_instance(game_key)
    if not inst:
        raise web.HTTPNotFound()
    if not user_id or user_id not in inst.players:
        return web.json_response({"error": "未加入本局，无法订阅"}, status=403)

    # Credentials are checked once, when the stream opens; re-check what it was
    # granted under every tick so rotation, removal, a room password change or
    # a closed player entrance also cuts off a stream that is already open
    # (and, for the owner, a revoked pairing or a changed access password; for
    # a bot, a rotated Bot API token or a revoked or disabled plugin).
    # A stream opened with headers instead of a ticket is granted the same way.
    if request.get("sse_ticket_authenticated"):
        grant = request.get(SSE_GRANT_KEY)
    else:
        grant = _ticket_grant(request, inst, user_id)
    resp = web.StreamResponse(
        headers={"Content-Type": "text/event-stream",
                 "Cache-Control": "no-cache",
                 "Connection": "keep-alive"})
    await resp.prepare(request)
    pool.add(game_key, user_id, resp)

    raw_cursor = (
        request.headers.get("Last-Event-ID", "")
        or request.query.get("cursor", "")
    )
    cursor = _parse_event_cursor(raw_cursor)
    action_signature = _play_action_signature(inst)
    public_signature = _play_public_signature(inst, user_id)
    fresh_connection = cursor is None
    if fresh_connection:
        # 页面已先通过 HTTP 获取完整快照。首次 SSE 连接只建立当前基线，
        # 不把既有回合、行动和私聊误报成新事件，再触发一整组重复 GET。
        last_round = progression_state.round_value(inst)
        last_private_count = len((module_hooks.read_value(inst, module_hooks.PRIVATE_LOG) or {}).get(user_id, []))
        last_action_digest = _signature_digest(action_signature)
        last_public_digest = _signature_digest(public_signature)
    else:
        assert cursor is not None
        last_round, last_private_count, last_action_digest, last_public_digest = cursor
    last_player_count = len(inst.players)
    try:
        if fresh_connection:
            lifecycle = module_hooks.read_value(inst, module_hooks.RUN_LIFECYCLE_VIEW) or {}
            workspace_changed = user_id == inst.gm_uid and bool(module_hooks.gm_workspace_revision(inst))
            await _write_play_event(
                resp,
                last_round,
                last_private_count,
                action_signature,
                public_signature,
                # Preparation can start after the initial HTTP snapshot but
                # before subscription. Refresh frozen/ended views once so a
                # missed broadcast cannot leave an active composer onscreen.
                {"type": "run_story_changed" if workspace_changed or lifecycle.get('status') in {'preparing', 'ended'} else "baseline"},
            )
        while True:
            current = api.get_game_instance(game_key)
            if not current:
                break
            inst = current
            revoked = _revocation(request, inst, user_id, grant)
            if revoked is not None:
                data = json.dumps(revoked, ensure_ascii=False)
                await resp.write(f"event: revoked\ndata: {data}\n\n".encode())
                break
            action_signature = _play_action_signature(inst)
            action_digest = _signature_digest(action_signature)
            public_signature = _play_public_signature(inst, user_id)
            public_digest = _signature_digest(public_signature)
            public_event_sent = False
            if progression_state.round_value(inst) > last_round:
                last_round = progression_state.round_value(inst)
                log_last = inst.log[-1] if inst.log else {}
                await _write_play_event(
                    resp,
                    last_round,
                    last_private_count,
                    action_signature,
                    public_signature,
                    {
                        "type": "narration",
                        "round": last_round,
                        "text": sanitize_narration(log_last.get("gm_response", "")),
                    },
                )
                # 状态
                cs = inst.get_character_sheet(user_id)
                await _write_play_event(resp, last_round, last_private_count, action_signature, public_signature, {'type':'state','hp':cs.get('hp'),'max_hp':cs.get('max_hp'),'gold':cs.get('gold'),'deceased':cs.get('deceased'),'status':cs.get('status'),'death_saves':cs.get('death_saves')})
                public_event_sent = True
            elif progression_state.round_value(inst) < last_round:
                # 回滚后回合号会倒退；同步游标，避免后续重新推进到旧回合号时漏报。
                last_round = progression_state.round_value(inst)
                await _write_play_event(resp, last_round, last_private_count, action_signature, public_signature, {'type':'rollback'})
                public_event_sent = True
            if action_digest != last_action_digest:
                last_action_digest = action_digest
                await _write_play_event(resp, last_round, last_private_count, action_signature, public_signature, {'type':'public_actions'})
                public_event_sent = True
            if len(inst.players) != last_player_count:
                last_player_count = len(inst.players)
                await _write_play_event(resp, last_round, last_private_count, action_signature, public_signature, {'type':'players'})
                public_event_sent = True
            if public_digest != last_public_digest:
                last_public_digest = public_digest
                if not public_event_sent:
                    # 同回合回滚、角色状态恢复等操作不会改变既有 SSE 游标，
                    # 仍需显式唤醒玩家端重新拉取完整公开状态。
                    await _write_play_event(resp, last_round, last_private_count, action_signature, public_signature, {'type':'refresh'})
            priv = (module_hooks.read_value(inst, module_hooks.PRIVATE_LOG) or {}).get(user_id, [])
            if len(priv) < last_private_count:
                # 清空私聊等回退操作也要更新游标；客户端通过完整刷新移除旧消息。
                last_private_count = len(priv)
                await _write_play_event(resp, last_round, last_private_count, action_signature, public_signature, {'type':'private_reset'})
            if len(priv) > last_private_count:
                for p in priv[last_private_count:]:
                    last_private_count += 1
                    await _write_play_event(resp, last_round, last_private_count, action_signature, public_signature, {'type':'private','text':p.get('text','')})
                last_private_count = len(priv)
            await asyncio.sleep(0.5)
    except ConnectionResetError:
        pass
    finally:
        pool.remove(game_key, user_id, resp)
    return resp


def _play_public_signature(inst, user_id: str) -> str:
    """返回会影响玩家游戏页的公开状态签名。"""
    payload = {
        "round_number": progression_state.round_value(inst),
        "state": inst.state.value,
        "run_lifecycle": module_hooks.read_value(inst, module_hooks.RUN_LIFECYCLE_VIEW) or {'status': 'playing'},
        "gm_workspace_revision": module_hooks.gm_workspace_revision(inst) if user_id == inst.gm_uid else None,
        "last_activity": session_stats.last_activity(inst),
        "log_count": len(inst.log),
        "scene": module_hooks.narrative_view(inst)["scene"],
        "scene_image": module_hooks.read_value(inst, module_hooks.SCENE_IMAGE) or {},
        "log_scene_image": (inst.log[-1].get("scene_image") if inst.log else None),
        "quick_actions": module_hooks.read(inst, module_hooks.QUICK_ACTIONS),
        "economy_proposals": [
            proposal for proposal in pending_economy_proposals(inst)
            if proposal_visible_to(
                proposal, viewer_uid=user_id,
                viewer_is_gm=bool(user_id) and user_id == inst.gm_uid,
            )
        ],
        "multiplayer": inst.multiplayer_status(),
        "round_checks_prepared": bool(checks.round_checks_prepared(inst)),
        "round_check_results": (
            checks.last_checks(inst)
            if inst.state == GameState.ACTIVE_JUDGMENT else []
        ),
        "character_sheet": inst.get_character_sheet(user_id),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _play_action_signature(inst) -> str:
    """返回当前公开行动队列的稳定签名原文。"""
    return json.dumps(
        [
            (action.get("user_id", ""), action.get("text", ""), action.get("timestamp", ""))
            for action in inst.action_queue
        ],
        ensure_ascii=False,
    )


def _signature_digest(signature: str) -> str:
    return hashlib.sha1(signature.encode("utf-8")).hexdigest()[:10] if signature else "0"


def _event_cursor(
    round_number: int,
    private_count: int,
    action_signature: str,
    public_signature: str = "",
) -> str:
    action_digest = _signature_digest(action_signature)
    public_digest = _signature_digest(public_signature)
    return f"r{round_number}.p{private_count}.a{action_digest}.s{public_digest}"


def _parse_event_cursor(value: str) -> tuple[int, int, str, str] | None:
    match = _EVENT_CURSOR_RE.fullmatch(str(value or ""))
    if not match:
        return None
    return (
        int(match.group("round")),
        int(match.group("private")),
        match.group("action"),
        match.group("public") or "0",
    )


async def _write_play_event(resp: web.StreamResponse, round_number: int, private_count: int,
                            action_signature: str, public_signature: str,
                            payload: dict) -> None:
    event_id = _event_cursor(round_number, private_count, action_signature, public_signature)
    data = json.dumps(payload, ensure_ascii=False)
    await resp.write(f"id: {event_id}\ndata: {data}\n\n".encode())


def _room_password_marker(inst) -> str:
    return str(room_access.room_password_hash(inst) or "")


def _owner_grant(request: web.Request) -> OwnerGrant | None:
    access = request.get(ACCESS_CONTROL_KEY)
    return access.owner_grant(request) if access is not None else None


def _ticket_grant(
    request: web.Request, inst, user_id: str,
) -> SseGrant | OwnerGrant | BotGrant | None:
    """Snapshot the credentials a stream rests on.

    A share-link stream rests on its seat credential and the room password.
    The owner, and a request outside a share link (a local session on a
    server without an access password), rest on the owner credential. A bot
    or plugin (``X-Bot-Token``) rests on that token alone.
    """
    if request.get("bot_authenticated"):
        return request.get(BOT_GRANT_KEY)
    if request.get("owner_authenticated"):
        return _owner_grant(request)
    share_request = bool(
        request.get("seat_token_uid")
        or request.query.get("user")
        or request.query.get("share", "") in {"1", "true", "yes"}
    )
    if not share_request:
        return _owner_grant(request)
    seat_credential = ""
    if request.get("seat_token_uid") == user_id:
        seat_credential = room_access.seat_credential_hash(inst, user_id)
    return SseGrant(
        seat_credential=seat_credential,
        room_password_hash=_room_password_marker(inst),
    )


def _revocation(
    request: web.Request, inst, user_id: str, grant: SseGrant | OwnerGrant | BotGrant | None,
) -> dict | None:
    """Why an open stream may no longer serve ``user_id`` (None while it may)."""
    if isinstance(grant, BotGrant):
        return _bot_revocation(request, inst, user_id, grant)
    if user_id not in inst.players:
        return {"error_code": "SEAT_TOKEN_INVALID", "error": t("webui.routes.sse.seat_removed")}
    if grant is None:
        return None
    if isinstance(grant, OwnerGrant):
        access = request.get(ACCESS_CONTROL_KEY)
        return access.owner_revocation(request.app, grant) if access is not None else None
    if not room_access.player_access_open(inst):
        return {"error_code": "PLAYER_ACCESS_CLOSED", "error": "本局玩家入口已关闭"}
    if grant.seat_credential and room_access.seat_credential_hash(inst, user_id) != grant.seat_credential:
        return {"error_code": "SEAT_TOKEN_INVALID", "error": t("webui.routes.sse.seat_credential_invalid")}
    if _room_password_marker(inst) != grant.room_password_hash:
        return {"error_code": "ROOM_TOKEN_INVALID", "error": "房间密码已更改，请重新输入", "needs_room_password": True}
    return None


def _bot_revocation(request: web.Request, inst, actor: str, grant: BotGrant) -> dict | None:
    """The admission rules of a bot request, re-applied to an open stream."""
    access = request.get(ACCESS_CONTROL_KEY)
    if access is not None:
        revoked = access.bot_revocation(request.app, grant)
        if revoked is not None:
            return revoked
    if actor not in inst.players:
        return {"error_code": "BOT_ACTOR_INVALID", "error": "Bot 代表玩家无效"}
    if not room_access.player_access_open(inst) and actor != inst.gm_uid:
        return {"error_code": "PLAYER_ACCESS_CLOSED", "error": "本局玩家入口已关闭"}
    return None


def register_sse(app: web.Application) -> None:
    app.router.add_post("/api/games/{game_key}/sse-ticket", api_sse_ticket)
    app.router.add_get("/api/games/{game_key}/stream", sse_stream)
    app.router.add_post("/api/games/{game_key}/stream-action", sse_stream_action)
    app.router.add_get("/api/games/{game_key}/sse", sse_play)
