"""Room access settings and credentials, retained across game resets.

No credential is stored in plaintext:

* the room password is kept only as a salted PBKDF2 hash
  (``room_password_hash``, same format as the owner access password, see
  ``src.password_hashing``);
* every room token handed out after a correct room password is kept only as a
  SHA-256 digest with an expiry (``room_tokens``);
* per-seat share credentials keep only a SHA-256 digest (``seat_credentials``).

Plaintext tokens are returned once by ``issue_room_token`` /
``issue_seat_token`` and never persisted, exported or projected.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from src.engine.module_state import (
    ModuleStateError, ModuleStateSpec, get_module_state, register_module_state,
)
from src.password_hashing import hash_password, verify_password_hash

MODULE_NAME = "room_access"
SCHEMA_VERSION = 3
SEAT_TOKEN_BYTES = 24
ROOM_TOKEN_BYTES = 24
# Applies to passwords set from now on; hashes of shorter passwords set under
# the old 4-character rule keep working.
NEW_ROOM_PASSWORD_MIN_LENGTH = 6
ROOM_PASSWORD_TOO_SHORT = f"房间密码至少 {NEW_ROOM_PASSWORD_MIN_LENGTH} 位"
ROOM_PASSWORD_BLANK = "房间密码不能只包含空白字符"
DEFAULT_ROOM_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
# Every successful password entry mints its own token; keep only the newest.
MAX_ROOM_TOKENS = 64


def fresh() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "max_players": 6,
        "player_access_open": True,
        "bot_bind_token": "",
        "room_password_hash": "",
        "room_tokens": [],
        "seat_credentials": {},
    }


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    # The old codec used data.get(key, default): only missing keys default.
    # Preserve present values, including None and unconventional types.
    for key, default in fresh().items():
        raw.setdefault(key, default)
    if not isinstance(raw.get("seat_credentials"), dict):
        raw["seat_credentials"] = {}
    if not isinstance(raw.get("room_tokens"), list):
        raw["room_tokens"] = []
    return raw


def require_writable(instance: Any) -> None:
    """Read-only transaction preflight; never repair or materialize a slot."""
    modules = getattr(instance, "modules", None)
    if not isinstance(modules, dict):
        raise ModuleStateError("instance has no module state container")
    slot = modules.get(MODULE_NAME)
    if not isinstance(slot, dict):
        return  # Missing/corrupt slots have the documented fresh default.
    if slot.get("schema_version") != SCHEMA_VERSION:
        raise ModuleStateError(f"unsupported room_access module schema: {slot.get('schema_version')!r}")


def max_players(instance: Any) -> int:
    return get_module_state(instance, MODULE_NAME)["max_players"]


def replace_max_players(instance: Any, value: int) -> None:
    get_module_state(instance, MODULE_NAME)["max_players"] = value


def player_access_open(instance: Any) -> bool:
    return get_module_state(instance, MODULE_NAME)["player_access_open"]


def replace_player_access_open(instance: Any, value: bool) -> None:
    get_module_state(instance, MODULE_NAME)["player_access_open"] = value


def bot_bind_token(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["bot_bind_token"]


def replace_bot_bind_token(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["bot_bind_token"] = value


# ---- room password ---------------------------------------------------------


def has_room_password(instance: Any) -> bool:
    """Whether joining needs the room password.

    Any non-empty stored value counts, so an unreadable hash keeps the room
    locked (nothing verifies against it) instead of opening it.
    """
    return bool(get_module_state(instance, MODULE_NAME)["room_password_hash"])


def validate_new_room_password(password: str) -> None:
    """Rules for a password being set now (``""`` means no password).

    Passwords are never trimmed, on set or on verify: what the GM typed is
    exactly what players must type. A whitespace-only password is refused
    rather than silently trimmed to nothing.
    """
    if not password:
        return
    if not password.strip():
        raise ValueError(ROOM_PASSWORD_BLANK)
    if len(password) < NEW_ROOM_PASSWORD_MIN_LENGTH:
        raise ValueError(ROOM_PASSWORD_TOO_SHORT)


def set_room_password(instance: Any, password: str) -> None:
    """Replace the room password (``""`` removes it) and revoke every room token."""
    password = str(password or "")
    validate_new_room_password(password)
    require_writable(instance)
    state = get_module_state(instance, MODULE_NAME)
    state["room_password_hash"] = hash_password(password) if password else ""
    state["room_tokens"] = []


def room_password_hash(instance: Any) -> Any:
    """The stored hash, for a check that must later confirm it is unchanged."""
    return get_module_state(instance, MODULE_NAME)["room_password_hash"]


def verify_room_password_hash(candidate: str, stored: Any) -> bool:
    """Constant-time check of ``candidate`` against a stored hash value.

    Pure (touches no instance), so it can run off the event loop. The
    candidate is compared exactly as typed; a stored value that is not a
    well-formed hash never matches.
    """
    if not stored or not isinstance(candidate, str) or not candidate:
        return False
    return verify_password_hash(candidate, stored)


def verify_room_password(instance: Any, candidate: str) -> bool:
    """Constant-time check of ``candidate`` against the instance's password."""
    return verify_room_password_hash(candidate, room_password_hash(instance))


# ---- room tokens -------------------------------------------------------------


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _expires_at(record: Any) -> datetime | None:
    raw = record.get("expires_at") if isinstance(record, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _live_room_tokens(records: Any, now: datetime) -> list[dict[str, Any]]:
    """Well-formed, unexpired token records; anything unreadable is dropped."""
    live: list[dict[str, Any]] = []
    for record in records if isinstance(records, list) else []:
        expires = _expires_at(record)
        if expires is not None and expires > now and isinstance(record.get("hash"), str):
            live.append(record)
    return live


def issue_room_token(
    instance: Any,
    *,
    ttl_seconds: int = DEFAULT_ROOM_TOKEN_TTL_SECONDS,
    now: datetime | None = None,
    token: str | None = None,
) -> tuple[str, str]:
    """Mint a room token after a correct room password; return ``(token, expires_at)``.

    Only the digest is stored.  Expired records are pruned and at most
    ``MAX_ROOM_TOKENS`` of the newest are kept.  ``token`` lets a caller supply
    the plaintext (tests); by default a fresh random one is generated.
    """
    if not has_room_password(instance):
        raise ValueError("room has no password")
    require_writable(instance)
    current = _now(now)
    plaintext = token or secrets.token_urlsafe(ROOM_TOKEN_BYTES)
    expires_at = (current + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()
    state = get_module_state(instance, MODULE_NAME)
    records = _live_room_tokens(state["room_tokens"], current)
    records.append({
        "hash": _token_digest(plaintext),
        "issued_at": current.isoformat(),
        "expires_at": expires_at,
    })
    state["room_tokens"] = records[-MAX_ROOM_TOKENS:]
    return plaintext, expires_at


def verify_room_token(instance: Any, token: str, *, now: datetime | None = None) -> bool:
    """Whether ``token`` is an unexpired room token of a password-protected room.

    Every stored digest is compared in constant time without stopping early.
    """
    if not isinstance(token, str) or not token or not has_room_password(instance):
        return False
    digest = _token_digest(token)
    matched = False
    for record in _live_room_tokens(get_module_state(instance, MODULE_NAME)["room_tokens"], _now(now)):
        if hmac.compare_digest(record["hash"], digest):
            matched = True
    return matched


def copy_room_password(target: Any, source: Any) -> None:
    """Carry the room password hash and live room tokens into a new run."""
    require_writable(target)
    source_state = get_module_state(source, MODULE_NAME)
    target_state = get_module_state(target, MODULE_NAME)
    target_state["room_password_hash"] = source_state["room_password_hash"]
    target_state["room_tokens"] = copy.deepcopy(_live_room_tokens(source_state["room_tokens"], _now(None)))


# ---- staged commits ------------------------------------------------------------

_ABSENT = object()


def capture_slot(instance: Any) -> Any:
    """The live slot object (or a marker for "absent"), for ``restore_slot``."""
    modules = getattr(instance, "modules", None)
    return modules.get(MODULE_NAME, _ABSENT) if isinstance(modules, dict) else _ABSENT


def restore_slot(instance: Any, captured: Any) -> None:
    """Put back a slot captured before a staged aggregate replaced ``modules``.

    Room access (password hash, room tokens, seat credentials) is committed on
    its own, never through a staged copy, so a copy taken before a password
    change or a new seat link must not roll either back.
    """
    modules = getattr(instance, "modules", None)
    if not isinstance(modules, dict):
        return
    if captured is _ABSENT:
        modules.pop(MODULE_NAME, None)
    else:
        modules[MODULE_NAME] = captured


# ---- seat credentials --------------------------------------------------------


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_seat_token(instance: Any, uid: str) -> str:
    """Issue a new share credential for ``uid`` and return its plaintext once.

    Any previous credential for the seat is replaced, so older links stop
    working.  Only the digest is stored.
    """
    uid = str(uid or "")
    if not uid:
        raise ValueError("seat uid is required")
    require_writable(instance)
    credentials = get_module_state(instance, MODULE_NAME)["seat_credentials"]
    previous = credentials.get(uid)
    epoch = int(previous.get("epoch", 0)) + 1 if isinstance(previous, dict) else 1
    token = secrets.token_urlsafe(SEAT_TOKEN_BYTES)
    credentials[uid] = {
        "hash": _token_digest(token),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "epoch": epoch,
    }
    return token


def verify_seat_token(instance: Any, token: str) -> str | None:
    """Return the seat uid a token belongs to, or ``None``.

    Compares against every stored digest in constant time and never stops
    early, so timing does not reveal which seat (if any) matched.
    """
    if not isinstance(token, str) or not token:
        return None
    digest = _token_digest(token)
    matched: str | None = None
    seats = getattr(instance, "players", None) or {}
    credentials = get_module_state(instance, MODULE_NAME)["seat_credentials"]
    for uid, record in credentials.items():
        stored = record.get("hash") if isinstance(record, dict) else None
        if isinstance(stored, str) and hmac.compare_digest(stored, digest):
            matched = str(uid)
    # A credential never outlives its seat.
    return matched if matched is not None and matched in seats else None


def revoke_seat_token(instance: Any, uid: str) -> bool:
    """Drop a seat's credential. Returns whether one existed."""
    require_writable(instance)
    credentials = get_module_state(instance, MODULE_NAME)["seat_credentials"]
    return credentials.pop(str(uid or ""), None) is not None


def prune_seat_credentials(instance: Any) -> list[str]:
    """Drop credentials of seats that no longer exist; return their uids."""
    require_writable(instance)
    seats = set(getattr(instance, "players", None) or {})
    credentials = get_module_state(instance, MODULE_NAME)["seat_credentials"]
    departed = [uid for uid in credentials if uid not in seats]
    for uid in departed:
        credentials.pop(uid, None)
    return departed


def seat_credential_hash(instance: Any, uid: str) -> str:
    """The stored digest of a seat's current credential ('' if it has none).

    Rotation and removal both change it, so a holder can later confirm the
    credential it was granted under is still the live one.
    """
    record = get_module_state(instance, MODULE_NAME)["seat_credentials"].get(str(uid or ""))
    stored = record.get("hash") if isinstance(record, dict) else None
    return stored if isinstance(stored, str) else ""


def has_seat_token(instance: Any, uid: str) -> bool:
    return str(uid or "") in get_module_state(instance, MODULE_NAME)["seat_credentials"]


def copy_seat_credentials(target: Any, source: Any) -> None:
    """Carry seat credentials into a new run, only for the seats it kept."""
    require_writable(target)
    kept = set(getattr(target, "players", None) or {})
    get_module_state(target, MODULE_NAME)["seat_credentials"] = {
        uid: copy.deepcopy(record)
        for uid, record in get_module_state(source, MODULE_NAME)["seat_credentials"].items()
        if uid in kept
    }


# ---- export / import ---------------------------------------------------------


def payload_seat_credential_uids(payload: Any) -> set[str]:
    """Seats that hold a share credential in a raw save payload.

    Read-only and schema-tolerant: seat credentials exist from slot v2 on and
    keep the same shape; any slot without a readable map yields no seats.
    """
    modules = payload.get("modules") if isinstance(payload, dict) else None
    slot = modules.get(MODULE_NAME) if isinstance(modules, dict) else None
    credentials = slot.get("seat_credentials") if isinstance(slot, dict) else None
    return {uid for uid in credentials if isinstance(uid, str) and uid} if isinstance(credentials, dict) else set()


def payload_has_plaintext_credentials(payload: Any) -> bool:
    """Whether a raw save still holds a plaintext room password or room token.

    True for saves written before room_access slot v3 (top-level fields of
    pre-slot saves, or the v1/v2 slot fields), which the loader then rewrites.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("room_password") or payload.get("room_token"):
        return True
    modules = payload.get("modules")
    slot = modules.get(MODULE_NAME) if isinstance(modules, dict) else None
    return isinstance(slot, dict) and bool(slot.get("room_password") or slot.get("room_token"))

_SCRUBBED_SLOT_VALUES: dict[str, Any] = {
    "bot_bind_token": "",
    "room_password_hash": "",
    "room_tokens": [],
    "seat_credentials": {},
    # Credential fields of earlier slot schemas.
    "room_password": "",
    "room_token": "",
}
# Saves older than the room_access slot kept these at the top level.
_SCRUBBED_LEGACY_VALUES: dict[str, Any] = {"bot_bind_token": "", "room_password": "", "room_token": ""}


def scrub_access_credentials(payload: Any, *, close_entrance: bool = False) -> bool:
    """Strip every stored access credential from a raw save payload, in place.

    Used on export and again on import: the room password (hash or legacy
    plaintext), room tokens, the bot bind token and seat credentials never
    travel with a save.  A password-protected save gets its player entrance
    closed, so it never arrives as an open room; the GM sets a new password
    and reopens it.  Only known credential keys are touched, so this is safe
    on any schema, including unknown future slots.  Returns whether the
    payload was password protected.
    """
    if not isinstance(payload, dict):
        return False
    modules = payload.get("modules")
    slot = modules.get(MODULE_NAME) if isinstance(modules, dict) else None
    slot_password = False
    if isinstance(slot, dict):
        slot_password = bool(slot.get("room_password_hash") or slot.get("room_password"))
        for key, empty in _SCRUBBED_SLOT_VALUES.items():
            if key in slot:
                slot[key] = copy.deepcopy(empty)
        if slot_password or close_entrance:
            slot["player_access_open"] = False
    legacy_password = bool(payload.get("room_password"))
    for key, empty in _SCRUBBED_LEGACY_VALUES.items():
        if key in payload:
            payload[key] = empty
    if legacy_password or close_entrance:
        payload["player_access_open"] = False
    return slot_password or legacy_password


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
