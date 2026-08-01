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


def _get_api(request: web.Request) -> WebAPI:
    return request.app["api"]


def _require_confirmed_request(request: web.Request) -> web.Response | None:
    if str(request.headers.get("X-TRPG-Confirm", "")).lower() in {"true", "yes", "1"}:
        return None
    return web.json_response({"ok": False, "error": "缺少确认头"}, status=403)
