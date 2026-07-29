"""认证路由 handler：登录、当前用户信息和登录记录。"""

from __future__ import annotations

import logging

from aiohttp import web

from src.webui.login_audit import LOGIN_AUDIT_KEY


logger = logging.getLogger("trpg")
ACCESS_PASSWORD_CONFIGURED_KEY = web.RequestKey("access_password_configured", bool)


async def api_login(request: web.Request) -> web.Response:
    success = (
        not request.get(ACCESS_PASSWORD_CONFIGURED_KEY, False)
        or bool(request.get("owner_authenticated", False))
    )
    audit = request.app.get(LOGIN_AUDIT_KEY)
    if audit:
        try:
            audit.record(request.remote or "unknown", success)
        except Exception:
            # 审计落盘失败不能把用户锁在登录页外，服务日志仍会保留异常。
            logger.exception("登录记录写入失败")
    if not success:
        return web.json_response(
            {"ok": False, "error": "密码错误"},
            status=401,
            headers={"Cache-Control": "no-store"},
        )
    return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})


async def api_login_history(request: web.Request) -> web.Response:
    audit = request.app.get(LOGIN_AUDIT_KEY)
    return web.json_response(
        {
            "entries": audit.recent(50) if audit else [],
            "max_entries": audit.max_entries if audit else 0,
        },
        headers={"Cache-Control": "no-store"},
    )


async def api_me(request: web.Request) -> web.Response:
    mgr = request.app.get("session_manager")
    token = request.get("session_token")
    name = mgr.get_name(token) if mgr and token else ""
    return web.json_response({"user_id": request.get("user_id", ""), "name": name})


def register_auth(app: web.Application) -> None:
    app.router.add_post("/api/login", api_login)
    app.router.add_get("/api/login-history", api_login_history)
    app.router.add_get("/api/me", api_me)
