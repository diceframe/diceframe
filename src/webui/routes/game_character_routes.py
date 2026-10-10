"""Character and live-advancement routes."""

from __future__ import annotations

import logging

from aiohttp import web

from src.engine.modules import ruleset_runtime, table_settings
from src.webui.api import can_modify_character
from src.webui.routes.character_cards import sees_full_card_library
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY
from src.webui.services._common import canonical_game_key, is_game_gm
from src.webui.character_sheet_authority import (
    ADOPT_REQUIRES_GM,
    DELETE_REQUIRES_GM,
    FIELD_REQUIRES_GM,
)
from src.webui.routes._common import (
    _get_api,
    _require_confirmed_request,
)

logger = logging.getLogger("trpg")
from src.webui.routes.game_route_common import (
    _broadcast_ruleset_change,
    _gm_only_inst,
    _should_rebind_player_session,
)


async def api_char_update(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    uid = request.match_info["user_id"]
    body = await request.json()
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"error": "游戏不存在"}, status=404)
    session_uid = request.get("user_id", "")
    if not can_modify_character(
        session_uid,
        uid,
        inst.gm_uid,
        owner=bool(request.get("owner_authenticated", False)),
    ):
        return web.json_response({"error": "无权修改他人角色卡"}, status=403)
    result = await api.update_character(
        gk, uid, body if isinstance(body, dict) else {},
        gm_authority=_has_sheet_authority(request, inst),
    )
    code = result.get("error_code")
    status = 409 if code == "REWRITE_IN_PROGRESS" else 403 if code == FIELD_REQUIRES_GM else 200
    return web.json_response(result, status=status)


def _has_sheet_authority(request: web.Request, inst) -> bool:
    """May this request change sheet mechanics (HP, gold, attributes...)?

    Only the table's GM, or the owner acting as itself.  A seated player
    (seat token / share link), a bot acting for a player seat and a P2P guest
    the host relays with ``delegate=1`` (owner-authenticated, but speaking
    for the guest's seat) are player-side callers.
    """
    if request.get("player_delegate", False):
        return False
    return is_game_gm(
        inst,
        str(request.get("user_id", "") or ""),
        bool(request.get("owner_authenticated", False)),
    )


async def api_ruleset_character_profile_update(request: web.Request) -> web.Response:
    """Patch non-mechanical profile data for a ruleset-authoritative character."""
    gk = request.match_info["game_key"]
    uid = request.match_info["user_id"]
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
    session_uid = request.get("user_id", "")
    if not can_modify_character(
        session_uid,
        uid,
        inst.gm_uid,
        owner=bool(request.get("owner_authenticated", False)),
    ):
        return web.json_response(
            {"ok": False, "error": "无权修改他人角色卡"}, status=403
        )
    body = await request.json()
    result = await api.update_ruleset_character_profile(gk, uid, body)
    if result.get("ok"):
        return web.json_response(result)
    code = str(result.get("error_code") or "")
    status = 409 if code == "REWRITE_IN_PROGRESS" else 404 if code == "CHARACTER_NOT_FOUND" else 422
    return web.json_response(result, status=status)


async def api_ruleset_character_adopt_card(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    uid = request.match_info["user_id"]
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
    session_uid = request.get("user_id", "")
    if not can_modify_character(
        session_uid,
        uid,
        inst.gm_uid,
        owner=bool(request.get("owner_authenticated", False)),
    ):
        return web.json_response(
            {"ok": False, "error": "无权修改他人角色卡"}, status=403
        )
    body = await request.json()
    card_id = str(body.get("card_id") or "")
    if not sees_full_card_library(request):
        shareable = {
            str(card.get("card_id") or card.get("id") or "")
            for card in api.list_shareable_character_cards()["cards"]
        }
        if card_id not in shareable:
            # Same visibility as the list: a non-owner cannot adopt (and so
            # read) a card it is not allowed to see.
            return web.json_response(
                {"ok": False, "error_code": "CARD_NOT_AVAILABLE", "error": "这张角色卡不可用"},
                status=404,
            )
    # Visibility above covers both the classic and the rules-aware dispatch.
    # Once the seat has acted a player-side caller (seat, bot for a player
    # seat, P2P delegate) may not adopt onto it; the service checks this
    # inside the authoritative write.
    result = await api.adopt_ruleset_character_card(
        gk, uid, card_id, gm_authority=_has_sheet_authority(request, inst),
    )
    if result.get("ok"):
        return web.json_response(result)
    code = str(result.get("error_code") or "")
    status = (
        409 if code == "REWRITE_IN_PROGRESS"
        else 404 if code == "CHARACTER_NOT_FOUND"
        else 403 if code == ADOPT_REQUIRES_GM
        else 422
    )
    return web.json_response(result, status=status)


async def _api_live_character_advancement(
    request: web.Request,
    action: str,
) -> web.Response:
    gk = request.match_info["game_key"]
    uid = request.match_info["user_id"]
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
    session_uid = request.get("user_id", "")
    runtime_id = str((ruleset_runtime.binding(inst) or {}).get("id") or "")
    can_advance = (
        session_uid == uid
        if runtime_id == "core:dnd2024"
        else can_modify_character(
            session_uid,
            uid,
            inst.gm_uid,
            owner=bool(request.get("owner_authenticated", False)),
        )
    )
    if not can_advance:
        return web.json_response(
            {"ok": False, "error": "无权修改他人角色卡"}, status=403
        )
    body = await request.json()
    try:
        result = (
            await api.apply_live_character_advancement(gk, uid, body)
            if action == "apply"
            else api.preview_live_character_advancement(gk, uid, body)
        )
    except ValueError as exc:
        result = {"ok": False, "code": "INVALID_ADVANCEMENT", "error": str(exc)}
    code = str(result.get("code") or "")
    status = (
        200
        if result.get("ok")
        else 404
        if code in {"GAME_NOT_FOUND", "CHARACTER_NOT_FOUND"}
        else 409
        if code in {"STALE_CHARACTER_REVISION", "REWRITE_IN_PROGRESS", "STALE_RUN"}
        else 422
    )
    return web.json_response(result, status=status)


async def api_live_character_advancement_preview(request: web.Request) -> web.Response:
    return await _api_live_character_advancement(request, "preview")


async def api_live_character_advancement_apply(request: web.Request) -> web.Response:
    return await _api_live_character_advancement(request, "apply")


async def api_live_advancement_control(request: web.Request) -> web.Response:
    game_key = request.match_info["game_key"]
    _, denied = _gm_only_inst(request, game_key)
    if denied is not None:
        return denied
    result = await _get_api(request).control_live_advancement(
        game_key, await request.json(),
    )
    await _broadcast_ruleset_change(request, game_key, result)
    code = str(result.get("code") or "")
    status = (
        200
        if result.get("ok")
        else 404
        if code
        in {
            "GAME_NOT_FOUND",
            "CHARACTER_NOT_FOUND",
        }
        else 409
        if code in {"REWRITE_IN_PROGRESS", "STALE_RUN"}
        else 422
    )
    return web.json_response(result, status=status)


async def api_live_character_rest(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    uid = request.match_info["user_id"]
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
    if not can_modify_character(
        request.get("user_id", ""),
        uid,
        inst.gm_uid,
        owner=bool(request.get("owner_authenticated", False)),
    ):
        return web.json_response(
            {"ok": False, "error": "无权修改他人角色卡"}, status=403
        )
    payload = await request.json()
    result = await (
        api.ruleset_rest_resolve_live_party(gk, uid, payload)
        if not table_settings.solo_mode(inst)
        else api.ruleset_rest_resolve_live(gk, uid, payload)
    )
    await _broadcast_ruleset_change(request, gk, result)
    code = str(result.get("code") or "")
    status = (
        200
        if result.get("ok")
        else 404
        if code in {"GAME_NOT_FOUND", "CHARACTER_NOT_FOUND"}
        else 409
        if code == "STALE_CHARACTER_REVISION"
        else 422
    )
    return web.json_response(result, status=status)


async def api_char_delete(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    gk = request.match_info["game_key"]
    uid = request.match_info["user_id"]
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"error": "游戏不存在"}, status=404)
    session_uid = request.get("user_id", "")
    if not can_modify_character(
        session_uid,
        uid,
        inst.gm_uid,
        owner=bool(request.get("owner_authenticated", False)),
    ):
        return web.json_response({"error": "无权删除他人角色"}, status=403)
    # A player-side caller may delete its seat only before the seat acted.
    result = await api.delete_character(
        gk, uid, gm_authority=_has_sheet_authority(request, inst),
    )
    if result.get("ok") and uid != str(inst.gm_uid or ""):
        # A removed seat's devices must not keep speaking for it here. The GM
        # identity outlives its character, so the GM's own session is kept.
        mgr = request.app.get("session_manager")
        if mgr is not None:
            mgr.revoke_game_binding(uid, canonical_game_key(gk))
    code = result.get("error_code")
    status = 409 if code == "REWRITE_IN_PROGRESS" else 403 if code == DELETE_REQUIRES_GM else 200
    return web.json_response(result, status=status)


async def api_npc_portrait_update(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    npc_id = request.match_info["npc_id"]
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"error": "游戏不存在"}, status=404)
    if request.get("user_id", "") != inst.gm_uid:
        return web.json_response({"error": "仅 GM 可修改 NPC 头像"}, status=403)
    body = await request.json()
    result = await api.update_npc_portrait(gk, npc_id, body.get("portrait"))
    status = 409 if result.get("error_code") == "REWRITE_IN_PROGRESS" else 200
    return web.json_response(result, status=status)


async def api_player_create(request: web.Request) -> web.Response:
    gk = request.match_info["game_key"]
    body = await request.json()
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)
    owner = bool(request.get("owner_authenticated", False))
    acting_uid = request.get("user_id", "")
    session_uid = str(request.get("session_user_id", acting_uid) or "")
    seat_token_uid = str(request.get("seat_token_uid", "") or "")
    requested_uid = str(body.get("user_id") or "").strip()
    join_as_new = bool(body.get("join_as_new")) and not requested_uid
    force_uid = "" if join_as_new else acting_uid
    # Rejoining an existing seat needs that seat's token (owner and bot
    # excepted); checked inside the seat-creation lock, not here.
    result = await api.create_player(
        gk, body, force_uid=force_uid, assign_new_id=join_as_new,
        seat_token_uid=seat_token_uid,
        require_seat_proof=bool(
            request.get(ACCESS_PASSWORD_CONFIGURED_KEY, False)
            and not owner
            and not request.get("bot_authenticated", False)
        ),
    )
    if result.get("error_code") == "SEAT_TOKEN_REQUIRED":
        return web.json_response(result, status=403)
    # 换设备恢复：持有席位凭证的会话改绑到该席位；GM 会话不能改绑成玩家。
    mgr = request.app.get("session_manager")
    token = request.get("session_token")
    if _should_rebind_player_session(
        session_uid, inst.gm_uid, requested_uid or seat_token_uid, result, join_as_new
    ):
        if mgr and token:
            mgr.rebind(token, result.get("user_id", ""), canonical_game_key(gk))
    elif mgr and token and result.get("ok") and result.get("user_id") == session_uid:
        mgr.note_game(token, canonical_game_key(gk))
    status = 409 if result.get("error_code") == "REWRITE_IN_PROGRESS" else 200
    return web.json_response(result, status=status)


async def api_seat_token_issue(request: web.Request) -> web.Response:
    """GM/owner: issue a seat's takeover token; rotating one needs ``rotate``."""
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    body = await request.json() if request.can_read_body else {}
    body = body if isinstance(body, dict) else {}
    gk = canonical_game_key(request.match_info["game_key"])
    uid = request.match_info["uid"]
    mgr = request.app.get("session_manager")
    result = await _get_api(request).issue_seat_token(
        gk,
        uid,
        requester_uid=str(request.get("user_id", "") or ""),
        owner=bool(request.get("owner_authenticated", False)),
        rotate=bool(body.get("rotate")),
        bound_sessions=mgr.count_bound(uid, gk) if mgr is not None else 0,
        check_token=str(body.get("check") or ""),
    )
    status = int(result.pop("status", 200))
    if result.get("ok") and not result.get("reused") and mgr is not None:
        # The old credential is gone; in this game so is every session that
        # was still bound to the seat.
        mgr.revoke_game_binding(uid, gk)
    return web.json_response(result, status=status)


async def api_seat_token_claim(request: web.Request) -> web.Response:
    """A session already bound to a seat gets that seat's first token."""
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    result = await _get_api(request).claim_seat_token(
        request.match_info["game_key"],
        session_uid=str(request.get("session_user_id", request.get("user_id", "")) or ""),
    )
    status = int(result.pop("status", 200))
    return web.json_response(result, status=status)
