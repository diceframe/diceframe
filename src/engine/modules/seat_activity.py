"""Which seats have acted in the current run (persisted module slot).

A seat "has acted" once it declared an action for a round (queued or
deferred) while played for its player (see ``plays_for_its_player``), had a
player-side rules-aware intent applied for it, or spent luck on its own check.
Saves from before this slot existed are seeded by the v40 -> v41 instance
migration.  The flag gates
player-side sheet resets: after a seat acted, only the GM may apply a library
card to it or delete it, so a player cannot refill / re-roll a character that
is already in play (directly, or by deleting it and joining again).

Why a dedicated slot instead of reading the log: the log is persisted only as
its last 100 rounds, a seat may idle for many rounds, and rules-aware combat
resolves intents outside the narrative log.  The flag belongs to the run, like
the log: a new run (restart / reset) starts with no seat having acted, and a
removed seat drops its flag.  It is monotonic within a run (a historical
rewrite does not clear it), which errs on the side of the GM.

The core flow reaches this module only through ``module_hooks`` (``seat.*``
and the instance reset points).  Without the module no seat is locked, which
is the behaviour before the seat lock existed (#480).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.engine import module_hooks
from src.engine.module_state import (
    ModuleStateError, ModuleStateSpec, get_module_state, register_module_state,
)

MODULE_NAME = "seat_activity"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "acted_seats": []}


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    seats = raw.get("acted_seats")
    if not isinstance(seats, list):
        seats = []
    raw["acted_seats"] = list(dict.fromkeys(
        seat for seat in seats if isinstance(seat, str) and seat
    ))
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
        raise ModuleStateError(
            f"unsupported seat_activity module schema: {slot.get('schema_version')!r}"
        )


def plays_for_its_player(control: Mapping[str, Any]) -> bool:
    """Does an action under this (normalized) control record count as the
    seat's own play?

    A human-controlled seat, and the server AI hosting it while its player is
    away (``temporary`` with ``resume_mode == "human"``), both play the
    player's character.  A seat the GM or the room handed to the AI for good,
    or one still unclaimed, does not: a player who later claims it may still
    switch it to their own card.
    """
    mode = control.get("mode")
    if mode == "human":
        return True
    return (
        mode == "ai"
        and bool(control.get("temporary"))
        and control.get("resume_mode") == "human"
    )


def has_acted(instance: Any, user_id: str) -> bool:
    """Fail closed: an unreadable slot counts as "acted" (GM-only resets)."""
    try:
        seats = get_module_state(instance, MODULE_NAME)["acted_seats"]
    except ModuleStateError:
        return True
    return str(user_id or "") in seats


def payload_acted_seats(payload: Mapping[str, Any]) -> set[str] | None:
    """Acted seats recorded in a raw save payload; ``None`` when unreadable.

    A missing slot reads as "no seat acted" (its fresh default). A slot of an
    unknown schema or a damaged list is not evidence of anything, so callers
    that delete data on its absence must treat ``None`` as "cannot tell".
    """
    modules = payload.get("modules")
    slot = modules.get(MODULE_NAME) if isinstance(modules, dict) else None
    if slot is None:
        return set()
    if not isinstance(slot, dict) or slot.get("schema_version") != SCHEMA_VERSION:
        return None
    seats = slot.get("acted_seats")
    if not isinstance(seats, list):
        return None
    return {seat for seat in seats if isinstance(seat, str) and seat}


def mark_acted(instance: Any, user_id: str) -> None:
    """Record that a seat of this run acted (callers check it is a seat)."""
    seats = get_module_state(instance, MODULE_NAME)["acted_seats"]
    uid = str(user_id or "")
    if uid and uid not in seats:
        seats.append(uid)


def forget_seat(instance: Any, user_id: str) -> None:
    seats = get_module_state(instance, MODULE_NAME)["acted_seats"]
    uid = str(user_id or "")
    if uid in seats:
        seats.remove(uid)


def reset(instance: Any) -> None:
    get_module_state(instance, MODULE_NAME)["acted_seats"] = []


def seat_acted(instance: Any, user_id: str, source: str) -> None:
    """``seat.acted``: a declared action counts only when played for its player."""
    if user_id not in getattr(instance, "players", {}):
        return
    if source == module_hooks.SEAT_SOURCE_ACTION:
        from src.engine.player_control import get_control

        if not plays_for_its_player(get_control(instance, user_id)):
            return
    mark_acted(instance, user_id)


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
module_hooks.register(module_hooks.SEAT_PREFLIGHT, MODULE_NAME, require_writable)
module_hooks.register(module_hooks.SEAT_ACTED, MODULE_NAME, seat_acted)
module_hooks.register(module_hooks.SEAT_FORGOTTEN, MODULE_NAME, forget_seat)
module_hooks.register(module_hooks.SEAT_HAS_ACTED, MODULE_NAME, has_acted)
module_hooks.register(module_hooks.INSTANCE_RESET_PREFLIGHT, MODULE_NAME, require_writable)
module_hooks.register(module_hooks.INSTANCE_RESET, MODULE_NAME, reset)
