"""GM-only Scene write service (scene-03).

Frozen contract: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §6.8.

The service owns the write gate, identity fence, stale revision guard, save
rollback and post-commit SSE projection broadcast. It receives explicit
dependencies and never the WebAPI facade; the pure operation logic lives in
``src.tactical_scene.ops`` and the slot belongs to ``src.engine.modules.tactical_scene``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.engine.module_state import ModuleStateError
from src.engine.modules import tactical_scene
from src.engine.participant_view import Viewer
from src.tactical_scene.codec import decode_tactical_scene_document
from src.tactical_scene.contract import TacticalSceneContractError
from src.tactical_scene.ops import TacticalSceneOpError
from src.tactical_scene.projection import project_tactical_scene_gm, project_tactical_scene_player
from src.webui.errors import error_payload

#: ``ConnectionPool.broadcast(game_key, data, exclude_uid=..., private_uid=...)``.
Broadcast = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class TacticalSceneOpDependencies:
    """Explicit owner seams; no WebAPI facade and no transport objects."""

    get_instance: Callable[[str], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]


def _error(code: str) -> dict[str, Any]:
    return error_payload(f"webui.services.tactical_scene_ops.{code}", code=code)


def _changed_document(instance: Any, scene_id: Any) -> dict[str, Any] | None:
    scenes = tactical_scene.scenes(instance)
    raw = scenes.get(scene_id) if isinstance(scene_id, str) else None
    if not isinstance(raw, dict):
        return None
    return decode_tactical_scene_document(raw)


async def _broadcast_projection(
    broadcast: Broadcast | None,
    game_key: str,
    instance: Any,
    summary: dict[str, Any],
    document: dict[str, Any] | None,
) -> None:
    """Broadcast the event projection per viewer whitelist, never the save."""

    if broadcast is None:
        return
    base = {
        "event_type": "tactical_scene_updated",
        "run_id": instance.run_id,
        "scene_id": summary["scene_id"],
        "revision": summary["revision"],
        "source_operation": summary["operation"],
    }
    gm_uid = str(getattr(instance, "gm_uid", "") or "")
    if document is None:
        # A deleted scene has no projection; the event asks clients to re-read.
        seat_event = {**base, "visibility": "seat", "projection": None}
        gm_event = {**base, "visibility": "gm", "projection": None}
    else:
        seat_event = {
            **base,
            "visibility": "seat",
            # Ownership is resolved per viewer; the seat broadcast is fail-closed
            # (no owned actor refs), so an unreferenced actor stays null.
            "projection": project_tactical_scene_player(document, uid="", owned_actor_refs=set()),
        }
        gm_event = {**base, "visibility": "gm", "projection": project_tactical_scene_gm(document)}
    await broadcast(game_key, seat_event, exclude_uid=gm_uid)
    if gm_uid:
        await broadcast(game_key, gm_event, private_uid=gm_uid)


async def apply_tactical_scene_op(
    dependencies: TacticalSceneOpDependencies,
    game_key: str,
    viewer: Viewer,
    op: Any,
    *,
    expected_revision: Any,
    broadcast: Broadcast | None = None,
) -> dict[str, Any]:
    """Apply one GM write, commit it durably and broadcast its projection."""

    if not viewer.is_gm:
        return _error("tactical_scene_forbidden")
    instance = dependencies.get_instance(game_key)
    if instance is None:
        return _error("tactical_scene_not_found")

    async with instance.authoritative_write() as entered:
        if not entered:
            return _error("rewrite_in_progress")
        if dependencies.get_instance(game_key) is not instance:
            return _error("stale_run")
        async with instance._lock:
            try:
                before = tactical_scene.snapshot(instance)
            except ModuleStateError:
                return _error("tactical_scene_invalid_payload")
            try:
                summary = tactical_scene.apply_op(
                    instance, op, expected_revision=expected_revision,
                )
            except TacticalSceneOpError as exc:
                return _error(exc.code)
            try:
                await dependencies.save_instance(instance)
            except asyncio.CancelledError:
                tactical_scene.restore(instance, before)
                raise
            except Exception:
                tactical_scene.restore(instance, before)
                return _error("save_failed")
            try:
                document = _changed_document(instance, summary.get("scene_id"))
            except (ModuleStateError, TacticalSceneContractError):
                document = None

    await _broadcast_projection(broadcast, game_key, instance, summary, document)
    return {
        "ok": True,
        **summary,
        "scene": project_tactical_scene_gm(document) if document is not None else None,
    }


__all__ = ["TacticalSceneOpDependencies", "apply_tactical_scene_op"]