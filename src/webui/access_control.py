"""HTTP authentication and player-share access boundary."""

from __future__ import annotations

import hmac

from aiohttp import web

from src.webui.access_password import (
    is_valid_access_password,
    normalize_access_password,
    verify_access_password,
)
from src.engine.modules import room_access
from src.webui.device_tokens import DEVICE_TOKENS_KEY
from src.webui.services._common import canonical_game_key
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY


SEAT_TOKEN_HEADER = "X-Seat-Token"
# Room tokens are only accepted as a header: a URL query would land them in
# access logs. (SSE needs no room token; it authenticates with a ticket.)
ROOM_TOKEN_HEADER = "X-Room-Token"

#: Id of the paired device whose token authenticated the request ('' for the
#: access password or no owner credential). Audit only, never authorization.
PAIRED_DEVICE_ID_KEY = web.RequestKey("paired_device_id", str)

# Share endpoints a visitor needs before holding a seat (join flow).  Every
# other share endpoint acts as a seat and requires that seat's token.
_LOBBY_GET_TAILS = frozenset({"characters", "character-cards"})
_LOBBY_POST_TAILS = frozenset({"players"})

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
        paired_device_id = ""
        if not owner_authenticated and bearer:
            # 设备令牌与访问密码平级：扫码配对过的设备不需要知道主密码，
            # 丢失时也能单独吊销而不牵连其它设备（见 device_tokens.py）。
            devices = request.app.get(DEVICE_TOKENS_KEY)
            device = devices.verify(bearer) if devices else None
            owner_authenticated = device is not None
            if device is not None:
                paired_device_id = str(device.get("id") or "")
        request["owner_authenticated"] = owner_authenticated
        # Which paired device made the request, for audit/provenance only. It is
        # never an authorization input: the device is already an owner here.
        request[PAIRED_DEVICE_ID_KEY] = paired_device_id
        request[ACCESS_PASSWORD_CONFIGURED_KEY] = access_password_configured
        share_uid, denied = self.resolve_share_identity(
            request, owner_authenticated, access_password_configured,
        )
        if denied is not None:
            return denied

        share_active = bool(share_uid or request.get("share_anonymous"))
        if self.requires_room_token(
            share_active,
            owner_authenticated,
            request.path,
        ):
            instance = self.request_game_instance(request)
            if (
                instance
                and room_access.has_room_password(instance)
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

        if share_uid and (request.query.get("user") or request.get("seat_token_uid")):
            if not owner_authenticated and self.player_access_is_closed(request):
                return web.json_response(
                    {"ok": False, "error": "本局玩家入口已关闭"},
                    status=403,
                )
            if not owner_authenticated and self.share_uid_is_gm_seat(request, share_uid):
                return web.json_response(
                    {"ok": False, "error_code": "GM_SEAT_REQUIRES_OWNER", "error": "GM 席位需要房主登录"},
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
        # 扫码兑换：手机此刻还没有任何凭据，必须匿名可达。安全性落在配对码
        # 本身（一次性 + 短 TTL）与 abuse_guard 的 login 级限流上。
        if request.method == "POST" and request.path == "/api/pairing/claim":
            return await handler(request)
        if access_password_configured and request.path.startswith("/api/"):
            if not owner_authenticated:
                if share_active:
                    if self.player_access_is_closed(request):
                        return web.json_response(
                            {"ok": False, "error": "本局玩家入口已关闭"},
                            status=403,
                        )
                    if self.share_uid_is_gm_seat(request, share_uid):
                        return web.json_response(
                            {"ok": False, "error_code": "GM_SEAT_REQUIRES_OWNER", "error": "GM 席位需要房主登录"},
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
        request[ACCESS_PASSWORD_CONFIGURED_KEY] = is_valid_access_password(
            normalize_access_password(self.state.get("access_token"))
        )
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

    def resolve_share_identity(
        self,
        request: web.Request,
        owner_authenticated: bool,
        access_password_configured: bool,
    ) -> tuple[str, web.Response | None]:
        """Resolve which seat a player-share request acts as.

        With an access password configured, a non-owner request is a seat
        only through that seat's token (``X-Seat-Token``); the public uid in
        ``?user=`` is never proof of identity.  Lobby endpoints of the join
        flow still work for a visitor without a seat, as the session's own
        uid.  The owner (P2P host delegation, preview) keeps ``?user=``.
        Without an access password there is no authentication boundary, so
        the legacy ``?user=`` identity remains as a fallback there.
        """

        # Keep the cookie session's own uid: routes that rebind or claim a
        # session must not confuse it with the seat a token acts as. A
        # binding revoked for this game (rotation, removal, reset) no longer
        # speaks for anyone here.
        session_uid = str(request.get("user_id", "") or "")
        request_game = self.bot_request_game_key(request)
        if (
            session_uid
            and request_game
            and canonical_game_key(request_game) in request.get("session_revoked_games", ())
        ):
            session_uid = ""
            request["user_id"] = ""
        request["session_user_id"] = session_uid
        kind = self.share_endpoint_kind(request)
        seat_token = str(request.headers.get(SEAT_TOKEN_HEADER) or "").strip()
        share_mode = bool(
            seat_token
            or request.query.get("user")
            or request.query.get("share", "") in {"1", "true", "yes"}
        )
        if not kind or not share_mode:
            return "", None
        token_uid = ""
        if seat_token:
            instance = self.request_game_instance(request)
            token_uid = (
                room_access.verify_seat_token(instance, seat_token) or ""
                if instance is not None else ""
            )
            if not token_uid and not owner_authenticated and access_password_configured:
                return "", web.json_response(
                    {"ok": False, "error_code": "SEAT_TOKEN_INVALID", "error": "席位凭证无效或已失效，请向 GM 重新获取链接"},
                    status=401,
                )
        query_uid = str(request.query.get("user") or "").strip()
        if token_uid:
            request["seat_token_uid"] = token_uid
        if owner_authenticated:
            return query_uid or token_uid or session_uid, None
        if not access_password_configured:
            return token_uid or query_uid or session_uid, None
        if token_uid:
            return token_uid, None
        if kind == "lobby":
            # A session bound to a seat speaks for it only until that seat has
            # a credential; after that only the token does (rotation must cut a
            # device off).  Such a session is then just an anonymous visitor.
            if not session_uid or self.session_seat_has_credential(request, session_uid):
                request["share_anonymous"] = True
                return "", None
            return session_uid, None
        return "", web.json_response(
            {"ok": False, "error_code": "SEAT_TOKEN_REQUIRED", "error": "需要席位凭证，请使用 GM 发出的链接重新加入"},
            status=401,
        )

    def session_seat_has_credential(self, request: web.Request, session_uid: str) -> bool:
        if not session_uid:
            return False
        instance = self.request_game_instance(request)
        if instance is None or session_uid not in (getattr(instance, "players", {}) or {}):
            return False
        try:
            return room_access.has_seat_token(instance, session_uid)
        except Exception:
            return True  # Unreadable credential state: fail closed.

    def share_uid_is_gm_seat(self, request: web.Request, share_uid: str) -> bool:
        """A non-owner share request may never act as the table's GM seat."""

        if not share_uid:
            return False
        instance = self.request_game_instance(request)
        gm_uid = str(getattr(instance, "gm_uid", "") or "") if instance is not None else ""
        return bool(gm_uid) and share_uid == gm_uid

    @staticmethod
    def requires_room_token(
        share_active: bool,
        owner_authenticated: bool,
        path: str,
    ) -> bool:
        if owner_authenticated or not share_active:
            return False
        parts = [part for part in path.split("/") if part]
        if len(parts) < 4 or parts[3] == "verify-room-password":
            return False
        return True

    @staticmethod
    def request_room_token_ok(instance, request: web.Request) -> bool:
        token = str(request.headers.get(ROOM_TOKEN_HEADER) or "").strip()
        return room_access.verify_room_token(instance, token)

    @staticmethod
    def share_endpoint_kind(request: web.Request) -> str:
        """Classify a game endpoint reachable from a player share link.

        ``"lobby"``: needed by a visitor before holding a seat (game detail,
        join-time character data, creating/rejoining a seat, claiming a seat
        token for an already bound session).  ``"seat"``: acts as a seat.
        ``""``: not reachable from a share link.
        """

        parts = [part for part in request.path.split("/") if part]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "games":
            return ""
        if len(parts) == 3 and request.method == "GET":
            return "lobby"
        if len(parts) < 4:
            return ""
        tail = parts[3]
        if request.method == "GET" and tail in _LOBBY_GET_TAILS and len(parts) == 4:
            return "lobby"
        if request.method == "POST" and tail in _LOBBY_POST_TAILS and len(parts) == 4:
            return "lobby"
        if (
            request.method == "POST"
            and tail == "seat-token"
            and len(parts) == 5
            and parts[4] == "claim"
        ):
            return "lobby"
        if request.method == "GET" and tail in {
            "adventure",
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
            "roll-requests",
        }:
            return "seat"
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
            "roll-requests",
        }:
            return "seat"
        if (
            request.method == "POST"
            and tail == "payments"
            and len(parts) == 5
            and bool(parts[4])
        ):
            return "seat"
        if (
            request.method == "POST"
            and tail == "checks"
            and len(parts) >= 6
            and parts[5] in {"luck", "reveal"}
        ):
            return "seat"
        if request.method in {"PUT", "PATCH"} and tail == "character":
            return "seat"
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
        return bool(instance and not room_access.player_access_open(instance))
