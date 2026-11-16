"""Read-only projection for a party's shared rest proposal."""

from __future__ import annotations

from typing import Any

from src.engine.modules import ruleset_runtime
from src.engine.world.contracts import clock_from_total_minutes


def saved_rest_session(instance: Any) -> dict[str, Any]:
    state = ruleset_runtime.state(instance)
    if not isinstance(state, dict):
        return {}
    session = state.get("rest_session")
    return session if isinstance(session, dict) else {}


def _clock_of(tick: Any) -> dict[str, int] | None:
    """Logical ``{day, minute}`` for an absolute-minute stamp, or ``None``."""

    if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
        return None
    return clock_from_total_minutes(tick)


def _tick_of(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def public_rest_session(instance: Any) -> dict[str, Any]:
    """Return the shared rest proposal without exposing sheets or rolls.

    Every moment here is logical time: sessions saved by older builds carry
    wall-clock strings (``started_at`` / ``submitted_at`` / ``resolved_at``),
    which are ignored rather than parsed.  A rest that happened at "day 3,
    08:00" must read the same on every machine and at every later moment.
    """
    session = saved_rest_session(instance)
    status = str(session.get("status") or "idle")
    if status == "idle" and not session.get("participants"):
        return {
            "active": False,
            "status": "idle",
            "rest": None,
            "ready_count": 0,
            "active_count": 0,
            "participants": [],
        }
    required = [
        str(uid)
        for uid in session.get("required_uids", [])
        if str(uid) in instance.players
    ]
    submitted = session.get("participants")
    submitted = submitted if isinstance(submitted, dict) else {}
    rows: list[dict[str, Any]] = []
    for uid in required:
        player = instance.players.get(uid) or {}
        entry = submitted.get(uid)
        rows.append(
            {
                "user_id": uid,
                "character_name": str(player.get("character_name") or uid),
                "status": "submitted" if isinstance(entry, dict) else "waiting",
            }
        )
    ready_count = sum(row["status"] == "submitted" for row in rows)
    decision = session.get("decision")
    decision = decision if isinstance(decision, dict) else {}
    return {
        "active": status in {"collecting", "awaiting_approval", "resolving"},
        "status": status,
        "rest": session.get("rest"),
        "ready_count": ready_count,
        "active_count": len(rows),
        "participants": rows,
        "started_clock": _clock_of(_tick_of(session.get("started_tick"))),
        "awaiting_since_clock": (
            _clock_of(_tick_of(session.get("awaiting_since_tick")))
            if status == "awaiting_approval" else None
        ),
        "resolved_clock": _clock_of(_tick_of(session.get("resolved_tick"))),
        "duration_minutes": _tick_of(session.get("duration_minutes")),
        "decided": bool(decision.get("operation_id")),
        "error": session.get("error", ""),
    }
