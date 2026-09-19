"""Content-module catalogue HTTP routes (API-00)."""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api


async def api_modules(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_modules())


async def api_module_detail(request: web.Request) -> web.Response:
    result = _get_api(request).module_detail(request.match_info["module_id"])
    return web.json_response(result, status=200 if result.get("ok") else 404)


def register_modules(app: web.Application) -> None:
    app.router.add_get("/api/modules", api_modules)
    app.router.add_get("/api/modules/{module_id}", api_module_detail)
