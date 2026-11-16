"""Read-only public activity for recovery and shared D&D rests.

The authoritative recovery and rest ledgers remain owned by their modules.  This
module only validates through those owners and projects a deliberately small
allowlist for the shared game detail response.
"""

from __future__ import annotations

import hashlib
from typing import Any

from src.engine.modules import resource_recovery
from src.engine.module_state import ModuleStateError
from src.webui.ruleset_rest_projection import public_rest_session, saved_rest_session


_RECOVERY_KINDS = {"overnight", "therapy"}
_DND_RESTS = {"short", "long"}
_MAX_ACTIVITY = 40


def _actor(instance: Any, uid: Any) -> dict[str, str] | None:
    """Resolve a public actor from the current canonical roster only."""

    if not isinstance(uid, str) or not uid or uid not in getattr(instance, "players", {}):
        return None
    player = instance.players.get(uid)
    if not isinstance(player, dict):
        return None
    return {"uid": uid, "name": str(player.get("character_name") or uid)}


def _actors(instance: Any, uids: list[Any]) -> list[dict[str, str]] | None:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for uid in uids:
        actor = _actor(instance, uid)
        if actor is None:
            return None
        if actor["uid"] not in seen:
            result.append(actor)
            seen.add(actor["uid"])
    return result


def _activity_item(activity_id: str, kind: str, status: str,
                   actors: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "id": activity_id,
        "kind": kind,
        "status": status,
        "actors": actors,
    }


def _recovery_activity(instance: Any) -> list[dict[str, Any]]:
    try:
        state = resource_recovery.view(instance)
    except (ModuleStateError, ValueError, TypeError):
        # The owner validator rejects unknown/future schemas and malformed
        # ledgers.  A public projection must fail closed in that case.
        return []

    sources = state.get("sources")
    receipts = state.get("receipts")
    if not isinstance(sources, dict) or not isinstance(receipts, dict):
        return []

    projected: list[dict[str, Any]] = []
    current_run = str(getattr(instance, "run_id", "") or "")
    for source_id, source in sources.items():
        if not isinstance(source_id, str) or not isinstance(source, dict):
            continue
        if source.get("run_id") != current_run or source.get("kind") not in _RECOVERY_KINDS:
            continue
        status = source.get("status")
        actor_uids = [source.get("actor_uid")]
        if source.get("kind") == "therapy":
            actor_uids.append(source.get("healer_uid"))
        actors = _actors(instance, actor_uids)
        if not actors:
            # A source whose actor has left the current roster is historical
            # from the public view's perspective.
            continue

        requested_id = f"{source_id}:requested"
        projected.append(_activity_item(requested_id, source["kind"], "requested", actors))
        if status == "rejected":
            projected.append(_activity_item(f"{source_id}:rejected", source["kind"], "rejected", actors))
        elif status == "settled":
            # resource_recovery.view validates the receipt shape and its
            # linkage; retaining this explicit check makes the projection
            # fail closed if a future owner loosens that validation.
            receipt = receipts.get(source_id)
            if (
                isinstance(receipt, dict)
                and receipt.get("id") == source_id
                and receipt.get("source_id") == source_id
                and receipt.get("run_id") == current_run
            ):
                projected.append(_activity_item(f"{source_id}:settled", source["kind"], "settled", actors))

    return projected


def _session_identity(instance: Any, started_tick: int, rest: str) -> str:
    game_key = "|".join(str(part) for part in (getattr(instance, "game_key", ()) or ()))
    raw = "\x1f".join(
        (game_key, str(getattr(instance, "run_id", "") or ""), str(started_tick), rest)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _dnd_activity(instance: Any) -> list[dict[str, Any]]:
    # saved_rest_session() is intentionally not called for a missing or
    # incomplete slot: its owner accessor materializes missing state, which a
    # read projection must never do.
    modules = getattr(instance, "modules", None)
    slot = modules.get("ruleset_runtime") if isinstance(modules, dict) else None
    if (
        not isinstance(slot, dict)
        or slot.get("schema_version") != 1
        or not isinstance(slot.get("state"), dict)
    ):
        return []
    try:
        session = saved_rest_session(instance)
        public = public_rest_session(instance)
    except (ModuleStateError, ValueError, TypeError):
        return []
    if not isinstance(session, dict) or not isinstance(public, dict):
        return []

    status = public.get("status")
    rest = public.get("rest")
    started_tick = session.get("started_tick")
    if status not in {
        "collecting", "awaiting_approval", "resolving", "completed", "error", "rejected",
    }:
        return []
    if (
        rest not in _DND_RESTS
        or isinstance(started_tick, bool)
        or not isinstance(started_tick, int)
        or started_tick < 0
    ):
        return []

    participants = public.get("participants")
    if not isinstance(participants, list):
        return []
    actor_uids: list[str] = []
    for row in participants:
        if not isinstance(row, dict):
            continue
        if row.get("status") != "submitted":
            continue
        uid = row.get("user_id")
        if isinstance(uid, str) and uid not in actor_uids:
            actor_uids.append(uid)
    actors = _actors(instance, actor_uids)
    if not actors:
        return []

    base = "rest:" + _session_identity(instance, started_tick, rest)
    projected = [_activity_item(base + ":requested", rest, "requested", actors)]
    if status == "completed":
        projected.append(_activity_item(base + ":settled", rest, "settled", actors))
    elif status == "rejected":
        projected.append(_activity_item(base + ":rejected", rest, "rejected", actors))
    elif status == "error":
        projected.append(_activity_item(base + ":error", rest, "error", actors))
    return projected


def rest_activity(instance: Any) -> list[dict[str, Any]]:
    """Return the bounded, public activity stream for the current run."""

    # Keep each owner independently fail-closed: a corrupt optional slot must
    # not make a valid activity source from the other domain visible or vice
    # versa.
    recovery = _recovery_activity(instance)
    dnd = _dnd_activity(instance)
    combined = recovery + dnd
    if len(combined) <= _MAX_ACTIVITY:
        return combined

    # Sources and sessions are emitted in requested/terminal pairs. Retaining
    # complete pairs keeps requested before settled/rejected/error while
    # preferring the newest records. A requested-only source is a singleton.
    groups: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(combined):
        current = combined[index]
        if (
            index + 1 < len(combined)
            and current.get("status") == "requested"
            and combined[index + 1].get("status") in {"settled", "rejected", "error"}
        ):
            groups.append(combined[index:index + 2])
            index += 2
        else:
            groups.append([current])
            index += 1
    selected: list[list[dict[str, Any]]] = []
    count = 0
    for group in reversed(groups):
        if count + len(group) > _MAX_ACTIVITY:
            continue
        selected.append(group)
        count += len(group)
    result: list[dict[str, Any]] = []
    for group in reversed(selected):
        result.extend(group)
    return result
