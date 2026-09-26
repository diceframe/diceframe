"""Request to participant identity for read projections."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from src.engine.participant_view import Viewer, resolve_viewer


def viewer_for(request: web.Request, instance: Any) -> Viewer:
    return resolve_viewer(
        instance,
        user_id=str(request.get("user_id", "") or ""),
        owner_authenticated=bool(request.get("owner_authenticated", False)),
        player_preview=bool(request.get("player_preview", False)),
    )
