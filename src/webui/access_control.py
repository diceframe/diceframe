"""HTTP authentication and player-share access boundary."""

from __future__ import annotations

import hmac

from aiohttp import web

from src.webui.access_password import (
    is_valid_access_password,
    normalize_access_password,
    verify_access_password,
)
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY


_BOT_PUBLIC_ENDPOINTS = frozenset(
    {
        "/api/generate-character",
        "/api/generate-world",
        "/api/generate-text",
    }
)


class WebAccessControl:
    def __init__(self, state: dict) -> None:
        self.state = state

    @web.middleware
    async def middleware(self, request: web.Request, handler):
        bot_header = str(request.headers.get("X-Bot-Token") or "")
        if request.path.startswith("/api/bot/") or bot_header:
            return await self._handle_bot_request(request, handler, bot_header)

        if request.path.endswith("/sse") and request.query.get("ticket"):
            game_key = self.bot_request_game_key(request)
            store = request.app.get("sse_tickets")
            ticket = (
                store.consume(str(request.query.get("ticket") or ""), game_key)
                if store
                else None
            )
            if not ticket:
                return web.json_response(
                    {"ok": False, "error": "SSE 票据无效或已过期"},
                    status=401,
                )
            request["user_id"] = ticket.user_id
            request["sse_ticket_authenticated"] = True
            return await handler(request)

        token = normalize_access_password(self.state.get("access_token"))
        access_password_configured = is_valid_access_password(token)
        auth = request.headers.get("Authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        owner_authenticated = bool(
            access_password_configured and verify_access_password(bearer, token)
        )
        request["owner_authenticated"] = owner_authenticated
        request[ACCESS_PASSWORD_CONFIGURED_KEY] = access_password_configured
        share_uid = self.share_player_user_id(request)

        if self.requires_room_token(
            share_uid,
            owner_authenticated,
            request.path,
        ):
            instance = self.request_game_instance(request)
            if (
                instance
                and instance.room_password
                and not self.request_room_token_ok(instance, request)
            ):
                return web.json_response(
                    {
                        "ok": False,
                        "error": "需要房间密码",
                        "needs_room_password": True,
                    },
                    status=403,
                )

        if request.method == "POST" and request.path.endswith(
            "/verify-room-password"
        ):
            return await handler(request)

        if share_uid and request.query.get("user"):
            if not owner_authenticated and self.player_access_is_closed(request):
                return web.json_response(
                    {"ok": False, "error": "本局玩家入口已关闭"},
                    status=403,
                )
            viewer_uid = request.get("user_id", "")
            request["viewer_user_id"] = viewer_uid
            request["user_id"] = share_uid
            request["player_preview"] = bool(
                owner_authenticated and viewer_uid != share_uid
            )
            request["player_delegate"] = request.query.get("delegate", "") in {
                "1",
                "true",
                "yes",
            }
            return await handler(request)

        if request.method == "GET" and request.path == "/api/config":
            return await handler(request)
        if request.method == "GET" and request.path == "/api/announcements":
            return await handler(request)
        if request.method == "GET" and request.path.startswith("/api/legal/"):
            return await handler(request)
        if self.is_public_ruleset_builder_request(request):
            return await handler(request)
        if request.method == "GET" and request.path == "/api/system/update/health":
            return await handler(request)
        if request.method == "POST" and request.path == "/api/login":
            return await handler(request)
        if access_password_configured and request.path.startswith("/api/"):
            if not owner_authenticated:
                if share_uid:
                    if self.player_access_is_closed(request):
                        return web.json_response(
                            {"ok": False, "error": "本局玩家入口已关闭"},
                            status=403,
                        )
                    request["user_id"] = share_uid
                    return await handler(request)
                return web.json_response(
                    {"ok": False, "error": "未授权"},
                    status=401,
                )
        return await handler(request)

    async def _handle_bot_request(self, request, handler, bot_header: str):
        configured_bot_token = str(self.state.get("bot_token") or "")
        global_authenticated = bool(
            configured_bot_token
            and hmac.compare_digest(bot_header, configured_bot_token)
        )
        plugin_host = request.app.get("plugin_host")
        plugin_identity = (
            plugin_host.authenticate_api_token(bot_header) if plugin_host else None
        )
        if not global_authenticated and not plugin_identity:
            return web.json_response(
                {"ok": False, "error": "Bot 服务未授权"},
                status=401,
            )
        request["bot_authenticated"] = True
        if plugin_identity:
            request["plugin_authenticated"] = plugin_identity
        if request.path.startswith("/api/bot/"):
            return await handler(request)
        game_key = self.bot_request_game_key(request)
        api = request.app.get("api")
        if not game_key:
            if request.path in _BOT_PUBLIC_ENDPOINTS:
                return await handler(request)
            return web.json_response(
                {"ok": False, "error": "Bot 代表玩家无效"},
                status=403,
            )
        detail = api.game_detail(game_key) if api else None
        if not detail:
            return web.json_response(
                {
                    "ok": False,
                    "error": "游戏不存在",
                    "code": "GAME_NOT_FOUND",
                },
                status=404,
            )
        actor = str(request.headers.get("X-Bot-Actor") or "").strip()
        if not actor or not api or not api.bot_actor_allowed(game_key, actor):
            return web.json_response(
                {
                    "ok": False,
                    "error": "Bot 代表玩家无效",
                    "code": "BOT_ACTOR_INVALID",
                },
                status=403,
            )
        if detail.get("player_access_open") is False and actor != detail.get(
            "gm_uid"
        ):
            return web.json_response(
                {"ok": False, "error": "本局玩家入口已关闭"},
                status=403,
            )
        request["user_id"] = actor
        request["bot_actor"] = actor
        return await handler(request)

    @staticmethod
    def is_public_ruleset_builder_request(request: web.Request) -> bool:
        parts = [part for part in request.path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["api", "rules"]:
            return request.method == "GET" and parts[3] in {
                "experience",
                "progression",
            }
        return bool(
            len(parts) == 5
            and parts[:2] == ["api", "rules"]
            and request.method == "POST"
            and (
                (
                    parts[3] == "builder"
                    and parts[4] in {"choices", "validate", "derive", "finalize"}
                )
                or (
                    parts[3] == "advancement"
                    and parts[4] in {"preview", "apply"}
                )
                or (parts[3] == "rest" and parts[4] == "resolve")
            )
        )

    @staticmethod
    def bot_request_game_key(request: web.Request) -> str:
        parts = [part for part in request.path.split("/") if part]
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "games":
            return parts[2]
        return ""

    def request_game_instance(self, request: web.Request):
        game_key = self.bot_request_game_key(request)
        if not game_key:
            return None
        api = request.app.get("api")
        subsystems = request.app.get("subsystems")
        if not api or not subsystems:
            return None
        return subsystems.registry.get(api._parse_key(game_key))

    @staticmethod
    def requires_room_token(
        share_uid: str,
        owner_authenticated: bool,
        path: str,
    ) -> bool:
        if owner_authenticated or not share_uid:
            return False
        parts = [part for part in path.split("/") if part]
        if len(parts) < 4 or parts[3] == "verify-room-password":
            return False
        return True

    @staticmethod
    def request_room_token_ok(instance, request: web.Request) -> bool:
        token = str(request.query.get("room_token") or "")
        return bool(instance.room_token) and hmac.compare_digest(
            instance.room_token,
            token,
        )

    @staticmethod
    def share_player_user_id(request: web.Request) -> str:
        uid = str(request.query.get("user") or "").strip()
        share_mode = request.query.get("share", "") in {"1", "true", "yes"}
        if not uid and not share_mode:
            return ""
        parts = [part for part in request.path.split("/") if part]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "games":
            return ""
        if len(parts) == 3 and request.method == "GET":
            return uid or request.get("user_id", "")
        if len(parts) >= 4:
            tail = parts[3]
            if request.method == "GET" and tail in {
                "characters",
                "character-cards",
                "log",
                "private-log",
                "table-talk",
                "multiplayer",
                "sse",
                "map",
                "player-context",
                "available-actions",
                "avatars",
                "scene-image",
                "map-background-asset",
                "generated-images",
            }:
                return uid or request.get("user_id", "")
            if request.method == "POST" and tail in {
                "players",
                "action",
                "kp-question",
                "intents",
                "decisions",
                "sse-ticket",
                "avatars",
                "scene-image",
                "generated-images",
                "character",
            }:
                return uid or request.get("user_id", "")
            if (
                request.method == "POST"
                and tail == "payments"
                and len(parts) == 5
                and bool(parts[4])
            ):
                return uid or request.get("user_id", "")
            if (
                request.method == "POST"
                and tail == "checks"
                and len(parts) >= 6
                and parts[5] == "luck"
            ):
                return uid or request.get("user_id", "")
            if request.method in {"PUT", "PATCH"} and tail == "character":
                return uid or request.get("user_id", "")
        return ""

    def player_access_is_closed(self, request: web.Request) -> bool:
        parts = [part for part in request.path.split("/") if part]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "games":
            return False
        api = request.app.get("api")
        subsystems = request.app.get("subsystems")
        if not api or not subsystems:
            return False
        try:
            instance = subsystems.registry.get(api._parse_key(parts[2]))
        except Exception:
            return False
        return bool(instance and not getattr(instance, "player_access_open", True))
