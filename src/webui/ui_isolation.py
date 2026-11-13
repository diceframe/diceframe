"""Internal U02 experiment for a prebuilt UI isolation boundary.

This module validates the host side of a future iframe/WebView bridge.  It
does not create a browser frame, execute package JavaScript, or claim OS level
sandboxing.  The caller still owns the browser policy and the action service.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit


MAX_MESSAGE_BYTES = 64 * 1024
MAX_RESOURCE_PATH = 240
MAX_REQUEST_ID = 96
MAX_NONCE = 128
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
_NONCE = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class UiIsolationError(ValueError):
    """Raised when an untrusted UI frame input crosses the host boundary."""


@dataclass
class UiReconnectPolicy:
    """Bounded reconnect state for an embedding WebView/iframe owner.

    The host never replays an ambiguous write.  A caller must either use a
    confirmed receipt or ask the user to retry; only explicitly read-only
    requests may be replayed automatically after a successful handshake.
    """

    max_attempts: int = 3
    base_delay_ms: int = 250
    attempts: int = 0
    state: str = "connected"

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 8:
            raise UiIsolationError("max reconnect attempts are invalid")
        if type(self.base_delay_ms) is not int or not 50 <= self.base_delay_ms <= 10_000:
            raise UiIsolationError("reconnect delay is invalid")

    def transport_lost(self) -> dict[str, Any]:
        if self.state == "readonly":
            return {"state": self.state, "attempt": self.attempts, "delay_ms": 0}
        if self.attempts >= self.max_attempts:
            self.state = "readonly"
            return {"state": self.state, "attempt": self.attempts, "delay_ms": 0}
        self.attempts += 1
        self.state = "reconnecting"
        return {
            "state": self.state,
            "attempt": self.attempts,
            "delay_ms": min(self.base_delay_ms * (2 ** (self.attempts - 1)), 30_000),
        }

    def handshake_succeeded(self) -> dict[str, Any]:
        self.attempts = 0
        self.state = "connected"
        return {"state": self.state, "attempt": self.attempts, "delay_ms": 0}

    def replay_decision(
        self, request_id: str, *, read_only: bool = False, confirmed: bool = False,
    ) -> str:
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise UiIsolationError("invalid UI request id")
        if self.state != "connected":
            return "wait_for_reconnect"
        if confirmed:
            return "use_confirmed_receipt"
        if read_only:
            return "replay_read_only"
        return "manual_retry_required"


@dataclass(frozen=True)
class UiFrameDescriptor:
    plugin_id: str
    entrypoint: str
    origin: str
    assets: tuple[str, ...]
    csp: str
    handshake_type: str = "diceframe.ui.hello"
    message_type: str = "diceframe.ui.action"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "entrypoint": self.entrypoint,
            "origin": self.origin,
            "assets": list(self.assets),
            "csp": self.csp,
            "handshake_type": self.handshake_type,
            "message_type": self.message_type,
        }


def _relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise UiIsolationError(f"{field} must be a relative path")
    raw = value.replace("\\", "/").strip()
    if raw.startswith("/"):
        raise UiIsolationError(f"{field} is outside the plugin UI root")
    normalized = raw.strip("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or len(normalized) > MAX_RESOURCE_PATH
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(char) < 0x20 for char in normalized)
    ):
        raise UiIsolationError(f"{field} is outside the plugin UI root")
    return "/".join(path.parts)


def _http_origin(value: Any, field: str = "origin") -> str:
    if not isinstance(value, str):
        raise UiIsolationError(f"{field} must be an explicit HTTP(S) origin")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise UiIsolationError(f"{field} must be an explicit HTTP(S) origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UiIsolationError(f"{field} has an invalid port") from exc
    host = parsed.hostname.casefold()
    netloc = f"{host}:{port}" if port is not None else host
    return f"{parsed.scheme.casefold()}://{netloc}"


def build_frame_descriptor(
    plugin_id: str,
    entrypoint: str,
    *,
    origin: str,
    assets: list[str] | tuple[str, ...] = (),
) -> UiFrameDescriptor:
    plugin = str(plugin_id or "").strip()
    if not _ID.fullmatch(plugin):
        raise UiIsolationError("plugin_id is invalid")
    normalized_origin = _http_origin(origin, "UI origin")
    normalized_entrypoint = _relative_path(entrypoint, "entrypoint")
    if not normalized_entrypoint.endswith((".html", ".htm")):
        raise UiIsolationError("UI entrypoint must be a prebuilt HTML file")
    if not isinstance(assets, (list, tuple)) or len(assets) > 256:
        raise UiIsolationError("UI assets must be a bounded list")
    normalized_assets = tuple(dict.fromkeys(_relative_path(item, "asset") for item in assets))
    csp = (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'none'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return UiFrameDescriptor(plugin, normalized_entrypoint, normalized_origin, normalized_assets, csp)


def validate_resource_request(path: Any, descriptor: UiFrameDescriptor) -> str:
    """Allow only the predeclared package-local HTML/assets for a frame."""

    normalized = _relative_path(path, "resource")
    if normalized != descriptor.entrypoint and normalized not in descriptor.assets:
        raise UiIsolationError("UI resource is not declared")
    return normalized


def validate_navigation_target(url: Any, *, expected_origin: str) -> str:
    """Reject cross-origin navigation and credential-bearing URLs."""

    normalized = _http_origin(expected_origin, "expected origin")
    if not isinstance(url, str):
        raise UiIsolationError("UI navigation target must be an HTTP(S) URL")
    parsed = urlsplit(url.strip())
    if parsed.username is not None or parsed.password is not None:
        raise UiIsolationError("UI navigation may not include credentials")
    target_origin = _http_origin(
        f"{parsed.scheme}://{parsed.netloc}", "navigation origin",
    )
    if target_origin != normalized:
        raise UiIsolationError("UI navigation origin mismatch")
    return url.strip()


def frame_response_headers(descriptor: UiFrameDescriptor) -> dict[str, str]:
    """Return the host headers required when serving a prebuilt frame.

    The helper is deliberately pure so an embedding WebView/HTTP owner can
    apply the same policy without making this module own a server.  It never
    enables cookies, cross-origin requests, or framing by another site.
    """

    if not isinstance(descriptor, UiFrameDescriptor):
        raise UiIsolationError("frame descriptor is invalid")
    return {
        "Content-Security-Policy": descriptor.csp,
        "Cache-Control": "no-store",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }


def _bounded_json(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UiIsolationError(f"{field} must be an object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise UiIsolationError(f"{field} must be JSON serializable") from exc
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise UiIsolationError(f"{field} is too large")
    return value


def validate_handshake(
    message: Any,
    *,
    plugin_id: str,
    nonce: str,
    origin: str,
    expected_origin: str | None = None,
) -> dict[str, Any]:
    payload = _bounded_json(message, "handshake")
    if set(payload) != {"type", "plugin_id", "nonce", "sdk"}:
        raise UiIsolationError("handshake contains unsupported fields")
    if payload.get("type") != "diceframe.ui.hello" or payload.get("sdk") != 1:
        raise UiIsolationError("unsupported UI handshake")
    if payload.get("plugin_id") != plugin_id or payload.get("nonce") != nonce:
        raise UiIsolationError("UI handshake identity mismatch")
    if not _NONCE.fullmatch(str(nonce or "")):
        raise UiIsolationError("invalid handshake nonce")
    received = _http_origin(origin, "handshake origin")
    expected = _http_origin(expected_origin or origin, "expected origin")
    if received != expected:
        raise UiIsolationError("UI handshake origin mismatch")
    return {"ok": True, "type": "diceframe.ui.ready", "plugin_id": plugin_id, "sdk": 1}


def validate_action_message(
    message: Any,
    *,
    plugin_id: str,
    allowed_actions: set[str] | frozenset[str],
) -> dict[str, Any]:
    payload = _bounded_json(message, "message")
    if set(payload) != {"type", "plugin_id", "request_id", "action", "arguments"}:
        raise UiIsolationError("UI message contains unsupported fields")
    if payload.get("type") != "diceframe.ui.action" or payload.get("plugin_id") != plugin_id:
        raise UiIsolationError("UI message identity mismatch")
    request_id = payload.get("request_id")
    action = payload.get("action")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise UiIsolationError("invalid UI request id")
    if not isinstance(action, str) or not _ACTION.fullmatch(action) or action not in allowed_actions:
        raise UiIsolationError("UI action is not allowed")
    arguments = _bounded_json(payload.get("arguments"), "arguments")
    return {"plugin_id": plugin_id, "request_id": request_id, "action": action, "arguments": arguments}
