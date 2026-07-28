"""Application update routes."""

from __future__ import annotations

import os

from aiohttp import web

from src.version import __version__
from src.webui.routes._common import _get_api, _require_confirmed_request


async def api_update_status(request: web.Request) -> web.Response:
    return web.json_response(request.app["updater"].get_status())


async def api_update_download(request: web.Request) -> web.Response:
    confirmed = _require_confirmed_request(request)
    if confirmed is not None:
        return confirmed
    kind = (request.query.get("kind") or "source").strip().lower()
    if kind not in {"source", "portable"}:
        return web.json_response(
            {"ok": False, "error": "kind 必须为 source 或 portable"},
            status=400,
        )
    result = await request.app["updater"].download_update(
        _get_api(request), kind
    )
    return web.json_response(result)


async def api_update_apply(request: web.Request) -> web.Response:
    confirmed = _require_confirmed_request(request)
    if confirmed is not None:
        return confirmed
    result = await request.app["updater"].apply_update()
    return web.json_response(result, status=200 if result.get("ok") else 409)


async def api_update_health(request: web.Request) -> web.Response:
    """Public, non-sensitive endpoint used by the launcher during switchover."""
    return web.json_response(
        {"ok": True, "version": __version__, "pid": os.getpid()}
    )


def register_updater(app: web.Application) -> None:
    app.router.add_get("/api/system/update/status", api_update_status)
    app.router.add_post("/api/system/update/download", api_update_download)
    app.router.add_post("/api/system/update/apply", api_update_apply)
    app.router.add_get("/api/system/update/health", api_update_health)
