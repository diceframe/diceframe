"""路由 handler 共享 helper。

_get_api / _require_confirmed_request 从 web_server 拆出，供各域 handler 复用。
"""

from __future__ import annotations

from aiohttp import web

from src.webui.api import WebAPI

MAX_LOREBOOK_CHARS = 5000  # 世界书单条 content 上限
MAX_ACTION_CHARS = 1000  # 玩家单次行动文本上限（防 token 暴涨/DoS）
MAX_ACTIONS_PER_TURN = 3  # 单玩家单回合行动条数上限（防灌数量拖垮上下文）
MAX_SEED_CHARS = 2000  # 世界/游戏描述上限


def _get_api(request: web.Request) -> WebAPI:
    return request.app["api"]


def _require_confirmed_request(request: web.Request) -> web.Response | None:
    if str(request.headers.get("X-TRPG-Confirm", "")).lower() in {"true", "yes", "1"}:
        return None
    return web.json_response({"ok": False, "error": "缺少确认头"}, status=403)
