"""认证路由 handler：当前用户信息。"""

from __future__ import annotations

from aiohttp import web


async def api_me(request: web.Request) -> web.Response:
    mgr = request.app.get("session_manager")
    token = request.get("session_token")
    name = mgr.get_name(token) if mgr and token else ""
    return web.json_response({"user_id": request.get("user_id", ""), "name": name})


def register_auth(app: web.Application) -> None:
    app.router.add_get("/api/me", api_me)
