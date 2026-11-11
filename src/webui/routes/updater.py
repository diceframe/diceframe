"""Application update routes."""

from __future__ import annotations

import asyncio
import os
import signal

from aiohttp import web

from src.version import __version__
from src.docker_launcher.data_versions import activation_pending
from src.webui.routes._common import _require_confirmed_request
from src.localization.messages import t


async def _restart_after_response() -> None:
    """Give aiohttp time to flush the response, then enter its graceful shutdown path."""
    await asyncio.sleep(0.5)
    signal.raise_signal(signal.SIGINT)


async def api_update_status(request: web.Request) -> web.Response:
    return web.json_response(request.app["updater"].get_status())


async def api_update_download(request: web.Request) -> web.Response:
    confirmed = _require_confirmed_request(request)
    if confirmed is not None:
        return confirmed
    kind = (request.query.get("kind") or "source").strip().lower()
    if kind not in {"source", "portable", "docker"}:
        return web.json_response(
            {"ok": False, "error": t("routes_updater.kind_invalid")},
            status=400,
        )
    result = await request.app["updater"].download_update(kind)
    return web.json_response(result)


async def api_update_apply(request: web.Request) -> web.Response:
    confirmed = _require_confirmed_request(request)
    if confirmed is not None:
        return confirmed
    result = await request.app["updater"].apply_update()
    return web.json_response(result, status=200 if result.get("ok") else 409)


async def api_update_rollback(request: web.Request) -> web.Response:
    confirmed = _require_confirmed_request(request)
    if confirmed is not None:
        return confirmed
    result = await request.app["updater"].rollback_update()
    return web.json_response(result, status=200 if result.get("ok") else 409)


async def api_update_health(request: web.Request) -> web.Response:
    """Public, non-sensitive endpoint used by the launcher during switchover."""
    return web.json_response(
        {
            "ok": True,
            "version": __version__,
            "pid": os.getpid(),
            "boot_id": request.app["runtime_control"]["boot_id"],
            "update_pending": activation_pending(os.environ),
        }
    )


async def api_application_restart(request: web.Request) -> web.Response:
    confirmed = _require_confirmed_request(request)
    if confirmed is not None:
        return confirmed
    control = request.app["runtime_control"]
    if control["restart_requested"]:
        return web.json_response(
            {"ok": False, "error": t("routes_updater.already_restarting")},
            status=409,
        )
    control["restart_requested"] = True
    control["restart_task"] = asyncio.create_task(_restart_after_response())
    return web.json_response(
        {
            "ok": True,
            "restarting": True,
            "boot_id": control["boot_id"],
        }
    )


def register_updater(app: web.Application) -> None:
    app.router.add_get("/api/system/update/status", api_update_status)
    app.router.add_post("/api/system/update/download", api_update_download)
    app.router.add_post("/api/system/update/apply", api_update_apply)
    app.router.add_post("/api/system/update/rollback", api_update_rollback)
    app.router.add_get("/api/system/update/health", api_update_health)
    app.router.add_post("/api/system/restart", api_application_restart)
