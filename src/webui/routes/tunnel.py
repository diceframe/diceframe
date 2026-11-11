"""隧道接入路由：插件上报公网 URL、设置页查询状态。"""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api
from src.localization.messages import t


async def api_tunnel_publish(request: web.Request) -> web.Response:
    """插件进程上报公网 URL（X-Bot-Token 鉴权，走 auth_middleware 插件分支）。"""
    token = str(request.headers.get("X-Bot-Token") or "").strip()
    identity = request.get("plugin_authenticated")
    if not identity:
        identity = _get_api(request).authenticate_plugin_token(token)
    if not identity:
        return web.json_response({"ok": False, "error": t("routes_tunnel.plugin_token_invalid")}, status=401)
    plugin_id = str(identity.get("plugin_id") or "")
    if "tunnel.publish" not in (identity.get("permissions") or []):
        return web.json_response({"ok": False, "error": t("routes_tunnel.tunnel_publish_permission_missing")}, status=403)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": t("routes_tunnel.body_must_be_json")}, status=400)
    try:
        result = _get_api(request).publish_tunnel_url(plugin_id, str(body.get("url") or ""))
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result, status=200)


async def api_tunnel_release(request: web.Request) -> web.Response:
    """插件进程停止隧道时释放 public_base_url（X-Bot-Token 鉴权）。"""
    token = str(request.headers.get("X-Bot-Token") or "").strip()
    identity = request.get("plugin_authenticated")
    if not identity:
        identity = _get_api(request).authenticate_plugin_token(token)
    if not identity:
        return web.json_response({"ok": False, "error": t("routes_tunnel.plugin_token_invalid")}, status=401)
    plugin_id = str(identity.get("plugin_id") or "")
    result = _get_api(request).release_tunnel_url(plugin_id)
    return web.json_response(result, status=200)


async def api_tunnel_status(request: web.Request) -> web.Response:
    """设置页查询隧道状态（owner 会话鉴权）。"""
    if not request.get("owner_authenticated", False):
        return web.json_response({"ok": False, "error": t("routes_tunnel.admin_session_required")}, status=403)
    return web.json_response(_get_api(request).tunnel_status(), status=200)


def register_tunnel(app: web.Application) -> None:
    # publish/release 走 /api/bot/ 前缀，auth_middleware 用 X-Bot-Token 做插件鉴权
    app.router.add_post("/api/bot/tunnel/publish", api_tunnel_publish)
    app.router.add_post("/api/bot/tunnel/release", api_tunnel_release)
    app.router.add_get("/api/system/tunnel", api_tunnel_status)
