"""扫码配对路由：签发配对码、兑换配对码。

配对码是「Web 端出示二维码 → 移动端扫码登录」这条链路上唯一的一次性凭据。
兑换端点必须保持匿名可达（手机此刻还没有任何凭据），因此它的安全性完全落在
一次性 + 短 TTL + 与 /api/login 同级的限流和审计上。

兑换换到的是设备令牌而不是访问密码，配套的设备清单 / 吊销也在本模块：
签发与吊销是同一条凭据生命周期，分到两个域反而更难看出谁能撤销什么。
"""

from __future__ import annotations

from aiohttp import web

from src.webui.device_tokens import DEVICE_TOKENS_KEY, normalize_install_id, sanitize_label
from src.webui.pairing import PairingService
from src.webui.routes._common import _require_confirmed_request
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY
from src.localization.messages import t


PAIRING_SERVICE_KEY = web.AppKey("pairing_service", PairingService)


def _owner_allowed(request: web.Request) -> bool:
    """与 /api/login 同一判定：免密服务器视为已授权，否则必须是 owner 会话。"""
    return not request.get(ACCESS_PASSWORD_CONFIGURED_KEY, False) or bool(
        request.get("owner_authenticated", False)
    )


async def api_pairing_issue(request: web.Request) -> web.Response:
    if denied := _require_confirmed_request(request):
        return denied
    if not _owner_allowed(request):
        return web.json_response(
            {"ok": False, "error": t("webui.routes.pairing.admin_session_required")},
            status=403,
            headers={"Cache-Control": "no-store"},
        )
    service = request.app[PAIRING_SERVICE_KEY]
    return web.json_response(service.issue(), headers={"Cache-Control": "no-store"})


async def api_pairing_claim(request: web.Request) -> web.Response:
    if denied := _require_confirmed_request(request):
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    install_id = body.get("install_id")
    if install_id not in (None, "") and not normalize_install_id(install_id):
        # Checked before the code is consumed: a malformed id must not burn it.
        return web.json_response(
            {"ok": False, "error": t("webui.routes.pairing.install_id_invalid"), "error_code": "INSTALL_ID_INVALID"},
            status=400,
            headers={"Cache-Control": "no-store"},
        )
    service = request.app[PAIRING_SERVICE_KEY]
    payload, status = service.claim(
        str(body.get("code") or ""),
        (request.remote or "unknown")[:128],
        sanitize_label(body.get("label")),
        normalize_install_id(install_id),
    )
    return web.json_response(
        payload,
        status=status,
        headers={"Cache-Control": "no-store"},
    )


async def api_devices_list(request: web.Request) -> web.Response:
    if not _owner_allowed(request):
        return web.json_response({"ok": False, "error": t("webui.routes.pairing.admin_session_required")}, status=403)
    devices = request.app[DEVICE_TOKENS_KEY]
    return web.json_response(
        {"ok": True, "devices": devices.entries()},
        headers={"Cache-Control": "no-store"},
    )


async def api_device_revoke(request: web.Request) -> web.Response:
    if denied := _require_confirmed_request(request):
        return denied
    if not _owner_allowed(request):
        return web.json_response({"ok": False, "error": t("webui.routes.pairing.admin_session_required")}, status=403)
    devices = request.app[DEVICE_TOKENS_KEY]
    device_id = request.match_info.get("device_id", "")
    if not devices.revoke(device_id):
        return web.json_response(
            {"ok": False, "error": t("webui.routes.pairing.device_missing_or_revoked")},
            status=404,
        )
    return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})


async def api_device_clear_install_id(request: web.Request) -> web.Response:
    """Owner action: unbind a device from its content-sync install id."""
    if denied := _require_confirmed_request(request):
        return denied
    if not _owner_allowed(request):
        return web.json_response({"ok": False, "error": t("webui.routes.pairing.admin_session_required")}, status=403)
    devices = request.app[DEVICE_TOKENS_KEY]
    if not devices.clear_install_id(request.match_info.get("device_id", "")):
        return web.json_response({"ok": False, "error": t("webui.routes.pairing.device_missing_or_revoked")}, status=404)
    return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})


async def api_devices_revoke_all(request: web.Request) -> web.Response:
    if denied := _require_confirmed_request(request):
        return denied
    if not _owner_allowed(request):
        return web.json_response({"ok": False, "error": t("webui.routes.pairing.admin_session_required")}, status=403)
    revoked = request.app[DEVICE_TOKENS_KEY].revoke_all()
    return web.json_response(
        {"ok": True, "revoked": revoked},
        headers={"Cache-Control": "no-store"},
    )


def register_pairing(app: web.Application) -> None:
    app.router.add_post("/api/pairing", api_pairing_issue)
    app.router.add_post("/api/pairing/claim", api_pairing_claim)
    app.router.add_get("/api/devices", api_devices_list)
    app.router.add_post("/api/devices/revoke-all", api_devices_revoke_all)
    app.router.add_delete("/api/devices/{device_id}", api_device_revoke)
    app.router.add_delete("/api/devices/{device_id}/install-id", api_device_clear_install_id)
