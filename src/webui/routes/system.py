"""System metadata routes."""

from __future__ import annotations

from datetime import datetime, timezone

from aiohttp import web

from src.web_transport.local_addresses import reachable_host_candidates
from src.webui.routes._common import _get_api, _require_confirmed_request
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY
from src.localization.messages import t


async def api_update_check(request: web.Request) -> web.Response:
    raw = request.query.get("prerelease")
    override = raw.strip().lower() in {"1", "true", "yes"} if raw is not None else None
    return web.json_response(await _get_api(request).check_updates(override))


async def api_runtime_log_status(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).runtime_log_status())


async def api_clear_runtime_logs(request: web.Request) -> web.Response:
    if denied := _require_confirmed_request(request):
        return denied
    return web.json_response(_get_api(request).clear_runtime_logs())


async def api_export_runtime_logs(request: web.Request) -> web.Response:
    try:
        payload, _file_count = _get_api(request).export_runtime_logs()
    except FileNotFoundError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"DiceFrame-runtime-logs-{timestamp}.zip"
    return web.Response(
        body=payload,
        content_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )



async def api_network_addresses(request: web.Request) -> web.Response:
    """本机对外可达的候选服务器地址。

    浏览器只知道自己访问用的那个 origin——GM 在本机打开的通常是 localhost，
    对手机毫无意义。二维码里该编哪个地址，只有服务端答得上来。

    仅对 owner 会话开放：它暴露的是内网拓扑，不属于匿名可探测的信息。
    """
    owner_allowed = not request.get(ACCESS_PASSWORD_CONFIGURED_KEY, False) or bool(
        request.get("owner_authenticated", False)
    )
    if not owner_allowed:
        return web.json_response({"ok": False, "error": t("routes_system.admin_session_required")}, status=403)
    transport = request.app.get("web_transport")
    endpoint = getattr(transport, "endpoint", None)
    if endpoint is None:
        return web.json_response(
            {"ok": False, "error": t("routes_system.transport_not_ready")},
            status=503,
        )
    addresses = [
        {"host": host, "url": endpoint.url(host)}
        for host in reachable_host_candidates()
    ]
    return web.json_response(
        {
            "ok": True,
            "scheme": endpoint.scheme,
            "port": endpoint.port,
            "addresses": addresses,
        },
        headers={"Cache-Control": "no-store"},
    )


def register_system(app: web.Application) -> None:
    app.router.add_get("/api/system/network", api_network_addresses)
    app.router.add_get("/api/system/update-check", api_update_check)
    app.router.add_get("/api/system/runtime-logs", api_runtime_log_status)
    app.router.add_get("/api/system/runtime-logs/export", api_export_runtime_logs)
    app.router.add_post("/api/system/runtime-logs/clear", api_clear_runtime_logs)
