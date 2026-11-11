"""Owner-facing editable FU content tables (monsters / items / skills)."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from src.rulesets.fabula_ultima import content_store
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY

OWNER_REQUIRED = "OWNER_REQUIRED"


def _owner_denied(request: web.Request) -> web.Response | None:
    if request.get("bot_authenticated", False):
        return web.json_response({"ok": False, "error_code": OWNER_REQUIRED, "error": "FU content tables are owner-only"}, status=403)
    if request.get(ACCESS_PASSWORD_CONFIGURED_KEY, True) and not request.get("owner_authenticated", False):
        return web.json_response({"ok": False, "error_code": OWNER_REQUIRED, "error": "FU content tables are owner-only"}, status=403)
    return None


def _kind(request: web.Request) -> str:
    kind = str(request.match_info.get("kind") or "")
    if kind not in content_store.KINDS:
        raise KeyError(kind)
    return kind


async def list_entries(request: web.Request) -> web.Response:
    denied = _owner_denied(request)
    if denied is not None:
        return denied
    try:
        kind = _kind(request)
    except KeyError:
        return web.json_response({"ok": False, "error_code": "KIND_INVALID"}, status=404)
    return web.json_response({"ok": True, "entries": content_store.load(kind)})


async def upsert_entry(request: web.Request) -> web.Response:
    denied = _owner_denied(request)
    if denied is not None:
        return denied
    try:
        kind = _kind(request)
    except KeyError:
        return web.json_response({"ok": False, "error_code": "KIND_INVALID"}, status=404)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = None
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error_code": "REQUEST_INVALID"}, status=400)
    body["id"] = str(request.match_info.get("entry_id") or body.get("id") or "")
    try:
        entry = content_store.upsert(kind, body)
    except (TypeError, ValueError) as exc:
        return web.json_response({"ok": False, "error_code": "ENTRY_INVALID", "error": str(exc)}, status=400)
    return web.json_response({"ok": True, "entry": entry})


async def delete_entry(request: web.Request) -> web.Response:
    denied = _owner_denied(request)
    if denied is not None:
        return denied
    try:
        kind = _kind(request)
    except KeyError:
        return web.json_response({"ok": False, "error_code": "KIND_INVALID"}, status=404)
    content_store.delete(kind, str(request.match_info.get("entry_id") or ""))
    return web.json_response({"ok": True})


def register(app: web.Application) -> None:
    app.router.add_get("/api/fu-content/{kind}", list_entries)
    app.router.add_put("/api/fu-content/{kind}/{entry_id}", upsert_entry)
    app.router.add_post("/api/fu-content/{kind}", upsert_entry)
    app.router.add_delete("/api/fu-content/{kind}/{entry_id}", delete_entry)
