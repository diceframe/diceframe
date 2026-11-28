# -*- coding: utf-8 -*-
"""HTTP routes for the DiceFrame Honors / Commemorative Badges feature."""

from __future__ import annotations

from aiohttp import web

from src.webui.services.honors import HonorsService, HonorsTokenError

HONORS_SERVICE_KEY = "honors_service"


def _get_service(request: web.Request) -> HonorsService:
    return request.app[HONORS_SERVICE_KEY]


async def api_honors_get(request: web.Request) -> web.Response:
    return web.json_response(_get_service(request).get_state())


async def api_honors_activate(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "code": "invalid_json", "error": "Request body must be valid JSON."},
            status=400,
        )
    if not isinstance(body, dict):
        return web.json_response(
            {"ok": False, "code": "invalid_json", "error": "Request body must be a JSON object."},
            status=400,
        )
    try:
        state = _get_service(request).activate_token(str(body.get("token") or ""))
    except HonorsTokenError as exc:
        return web.json_response(
            {"ok": False, "code": exc.code, "error": exc.message},
            status=400,
        )
    return web.json_response(state)


async def api_honors_featured(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "code": "invalid_json", "error": "Request body must be valid JSON."},
            status=400,
        )
    if not isinstance(body, dict):
        return web.json_response(
            {"ok": False, "code": "invalid_json", "error": "Request body must be a JSON object."},
            status=400,
        )
    try:
        state = _get_service(request).set_featured_badge(str(body.get("badge_id") or ""))
    except HonorsTokenError as exc:
        return web.json_response(
            {"ok": False, "code": exc.code, "error": exc.message},
            status=400,
        )
    return web.json_response(state)


async def api_honors_clear(request: web.Request) -> web.Response:
    return web.json_response(_get_service(request).clear_credential())


async def api_honors_wall(request: web.Request) -> web.Response:
    def _parse_int(name: str, default: int) -> int:
        raw = request.query.get(name, "")
        try:
            return int(raw)
        except ValueError:
            return default

    page = _parse_int("page", 1)
    limit = _parse_int("limit", 60)
    badge = request.query.get("badge", "")
    query = request.query.get("q", "")
    refresh = request.query.get("refresh", "").lower() in {"1", "true", "yes"}
    payload = await _get_service(request).fetch_wall(
        page=page,
        limit=limit,
        badge=badge,
        query=query,
        refresh=refresh,
    )
    return web.json_response(payload)


def register_honors(app: web.Application) -> None:
    app.router.add_get("/api/honors", api_honors_get)
    app.router.add_post("/api/honors/activate", api_honors_activate)
    app.router.add_post("/api/honors/featured", api_honors_featured)
    app.router.add_delete("/api/honors", api_honors_clear)
    app.router.add_get("/api/honors/wall", api_honors_wall)
