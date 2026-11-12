"""路由 handler 共享 helper。

_get_api / _require_confirmed_request 从 web_server 拆出，供各域 handler 复用。
"""

from __future__ import annotations

from aiohttp import web

from src.webui.api import WebAPI
from src.webui.services._common import (
    MAX_ACTION_CHARS,
    MAX_ACTIONS_PER_TURN,
    MAX_LOREBOOK_CHARS,
    MAX_SEED_CHARS,
)
from src.localization.messages import t


def _get_api(request: web.Request) -> WebAPI:
    return request.app["api"]


def require_content_owner(request: web.Request) -> web.Response | None:
    """Owner-only private content, with the existing open local-server mode."""
    from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY
    if request.get("owner_authenticated"):
        return None
    if request.get(ACCESS_PASSWORD_CONFIGURED_KEY) or request.query.get("user") or request.headers.get("X-Bot-Token"):
        return web.json_response({"ok": False, "error_code": "OWNER_REQUIRED"}, status=403)
    return None


def _require_confirmed_request(request: web.Request) -> web.Response | None:
    if str(request.headers.get("X-TRPG-Confirm", "")).lower() in {"true", "yes", "1"}:
        return None
    return web.json_response({"ok": False, "error": t("webui.routes._common.confirm_header_missing")}, status=403)
