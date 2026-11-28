"""Read-only Scene projections for the table and agents (B2).

The service owns permission classification and whitelist projection. It only
reads the ``tactical_scene`` slot: no write, no revision bump, no persistence.
An unknown or invalid persisted scene fails closed as ``tactical_scene_invalid_payload``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.engine.modules import tactical_scene
from src.engine.participant_view import Viewer
from src.tactical_scene.codec import decode_tactical_scene_document
from src.tactical_scene.contract import TacticalSceneContractError, is_canonical_id
from src.tactical_scene.projection import (
    PUBLIC_TOKEN_DISPOSITIONS,
    project_tactical_scene_gm,
    project_tactical_scene_player,
)
from src.webui.errors import error_payload


@dataclass(frozen=True)
class TacticalSceneReadDependencies:
    """Explicit owner seams; the service never receives the WebAPI facade."""

    get_instance: Callable[[str], Any | None]
    owned_actor_refs: Callable[[Any, str], set[str]] | None = None


def _not_found() -> dict[str, Any]:
    return error_payload("webui.services.tactical_scene_read.tactical_scene_not_found", code="tactical_scene_not_found")


def _forbidden() -> dict[str, Any]:
    return error_payload("webui.services.tactical_scene_read.tactical_scene_forbidden", code="tactical_scene_forbidden")


def _invalid() -> dict[str, Any]:
    return error_payload(
        "webui.services.tactical_scene_read.tactical_scene_invalid_payload", code="tactical_scene_invalid_payload",
    )


def _project(
    dependencies: TacticalSceneReadDependencies,
    instance: Any,
    viewer: Viewer,
    document: dict[str, Any],
) -> dict[str, Any]:
    if viewer.is_gm:
        return project_tactical_scene_gm(document)
    owned = (
        dependencies.owned_actor_refs(instance, viewer.uid)
        if dependencies.owned_actor_refs is not None
        else set()
    )
    return project_tactical_scene_player(
        document,
        uid=viewer.uid,
        owned_actor_refs=owned,
        public_dispositions=PUBLIC_TOKEN_DISPOSITIONS,
    )


def _resolve_scene_id(slot_instance: Any, scene_id: str | None) -> str | None:
    """Resolve explicitly or through the bound active reference; never by name."""

    ref = scene_id if scene_id is not None else tactical_scene.active_scene_ref(slot_instance)
    if not isinstance(ref, str) or not ref or not is_canonical_id(ref):
        return None
    return ref


def tactical_scene_read(
    dependencies: TacticalSceneReadDependencies,
    game_key: str,
    viewer: Viewer,
    scene_id: str | None = None,
) -> dict[str, Any]:
    """Read one scene through the viewer whitelist without writing state."""

    instance = dependencies.get_instance(game_key)
    if instance is None:
        return _not_found()
    if not viewer.is_member:
        return _forbidden()
    ref = _resolve_scene_id(instance, scene_id)
    if ref is None:
        return _not_found()
    scenes = tactical_scene.scenes(instance)
    raw = scenes.get(ref)
    if not isinstance(raw, dict):
        return _not_found()
    try:
        document = decode_tactical_scene_document(raw)
    except TacticalSceneContractError:
        return _invalid()
    return {
        "ok": True,
        "scene_id": document["canonical_id"],
        "scene": _project(dependencies, instance, viewer, document),
        "active_scene_ref": tactical_scene.active_scene_ref(instance),
        "revision": tactical_scene.revision(instance),
    }


def tactical_scene_list(
    dependencies: TacticalSceneReadDependencies,
    game_key: str,
    viewer: Viewer,
) -> dict[str, Any]:
    """List every scene through the same viewer whitelist, sorted deterministically."""

    instance = dependencies.get_instance(game_key)
    if instance is None:
        return _not_found()
    if not viewer.is_member:
        return _forbidden()
    scenes = tactical_scene.scenes(instance)
    projected: list[dict[str, Any]] = []
    for ref in sorted(scenes, key=str):
        if not isinstance(ref, str) or not is_canonical_id(ref):
            return _invalid()
        raw = scenes[ref]
        if not isinstance(raw, dict):
            return _invalid()
        try:
            document = decode_tactical_scene_document(raw)
        except TacticalSceneContractError:
            return _invalid()
        projected.append(_project(dependencies, instance, viewer, document))
    projected.sort(key=lambda scene: (scene.get("sort", 0), scene.get("canonical_id", "")))
    return {
        "ok": True,
        "scenes": projected,
        "active_scene_ref": tactical_scene.active_scene_ref(instance),
        "revision": tactical_scene.revision(instance),
    }


__all__ = ["TacticalSceneReadDependencies", "tactical_scene_list", "tactical_scene_read"]