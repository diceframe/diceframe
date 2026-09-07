"""Shared authorization and request adapters for game routes."""

from __future__ import annotations

import logging

from aiohttp import web

from src.webui.routes._common import (
    _get_api,
)
from src.webui.services._common import is_game_gm

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


async def _broadcast_ruleset_change(
    request: web.Request,
    game_key: str,
    result: dict,
) -> None:
    """Wake connected clients after a persisted authoritative ruleset change."""

    if not result.get("ok"):
        return
    pool = request.app.get("connection_pool")
    if pool is None:
        return
    gameplay = result.get("gameplay")
    gameplay = gameplay if isinstance(gameplay, dict) else {}
    await pool.broadcast(
        game_key,
        {
            "type": "ruleset_state_changed",
            "state_version": int(gameplay.get("state_version", 0) or 0),
        },
    )


async def _broadcast_combat_change(
    request: web.Request,
    game_key: str,
    result: dict,
) -> None:
    """Wake every table client after a persisted combat-extension mutation."""

    if not result.get("ok"):
        return
    pool = request.app.get("connection_pool")
    if pool is not None:
        await pool.broadcast(game_key, {"type": "combat_state_changed"})


def _gm_only_inst(request: web.Request, gk: str):
    api = _get_api(request)
    inst = api.get_game_instance(gk)
    if not inst:
        return None, web.json_response({"ok": False, "error": "not found"}, status=404)
    if not is_game_gm(
        inst,
        request.get("user_id", ""),
        bool(request.get("owner_authenticated", False)),
    ):
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


def _can_delete_save(request: web.Request, session_uid: str, gm_uid: str) -> bool:
    """Deleting a local save is allowed for the in-game GM or the WebUI owner.

    Old audit/dev saves can be bound to a browser session that no longer exists.
    The access-token-authenticated WebUI owner is still the local administrator,
    while player share links remain blocked because they do not set
    owner_authenticated.
    """
    return bool(
        gm_uid and (session_uid == gm_uid or request.get("owner_authenticated", False))
    )
