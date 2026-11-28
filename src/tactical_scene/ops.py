"""Pure Scene write operations over a tactical_scene slot draft (scene-03).

Frozen source of truth: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §6.8.
One operation is validated and applied to a copy of the slot draft; the caller
receives the new slot and never has its input mutated. This module performs no
IO, takes no locks and never touches a live GameInstance: the owning module and
the write service own those boundaries.

Unknown or unstable payloads fail closed with a stable ``TacticalSceneOpError.code``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Final

from src.tactical_scene.contract import (
    PLACEABLE_COLLECTIONS,
    TacticalSceneContractError,
    is_canonical_id,
    normalize_tactical_scene_payload,
    validate_token_payload,
)

TACTICAL_SCENE_CREATE: Final = "tactical_scene_create"
TACTICAL_SCENE_UPDATE: Final = "tactical_scene_update"
TACTICAL_SCENE_DELETE: Final = "tactical_scene_delete"
TACTICAL_SCENE_SET_ACTIVE: Final = "tactical_scene_set_active"
PLACEABLE_CREATE: Final = "tactical_scene_placeable_create"
PLACEABLE_UPDATE: Final = "tactical_scene_placeable_update"
PLACEABLE_DELETE: Final = "tactical_scene_placeable_delete"

CODE_NOT_FOUND: Final = "tactical_scene_not_found"
CODE_STALE: Final = "tactical_scene_stale_revision"
CODE_INVALID: Final = "tactical_scene_invalid_payload"
CODE_PLACEABLE_NOT_FOUND: Final = "tactical_scene_placeable_not_found"

_PLACEABLE_PROBE: Final = "placeable_probe"
_SLOT_SCHEMA_VERSION: Final = 1


class TacticalSceneOpError(ValueError):
    """A write operation cannot be applied; ``code`` is stable contract data."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _fail(code: str, detail: str) -> None:
    raise TacticalSceneOpError(code, detail)


def _tactical_scene_document(raw: Any) -> dict[str, Any]:
    try:
        return normalize_tactical_scene_payload(raw)[0]
    except TacticalSceneContractError as exc:
        raise TacticalSceneOpError(exc.code, exc.detail) from exc


def _tactical_scene_ref(value: Any) -> str:
    if not is_canonical_id(value):
        # An active/explicit binding is never guessed from a name or repair.
        _fail(CODE_NOT_FOUND, f"scene reference is not resolvable: {value!r}")
    return str(value)


def _existing_tactical_scene(draft: dict[str, Any], scene_id: str) -> dict[str, Any]:
    raw = draft["scenes"].get(scene_id)
    if not isinstance(raw, dict):
        _fail(CODE_NOT_FOUND, f"scene {scene_id!r} does not exist")
    return raw


def _placeable_collection(op: dict[str, Any]) -> str:
    collection = op.get("collection")
    if collection not in PLACEABLE_COLLECTIONS:
        _fail(CODE_INVALID, f"unknown placeable collection: {collection!r}")
    return str(collection)


def _placeable_document(collection: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        _fail(CODE_INVALID, f"{collection} document must be an object")
    if collection == "tokens":
        try:
            return validate_token_payload(raw)
        except TacticalSceneContractError as exc:
            raise TacticalSceneOpError(exc.code, exc.detail) from exc
    # Non-token placeables only have the shared envelope contract in B1; the
    # type-specific fields stay opaque and are never interpreted here.
    probe = {
        "schema_version": 1,
        "kind": "scene",
        "canonical_id": _PLACEABLE_PROBE,
        "placeables": {collection: {_PLACEABLE_PROBE: raw}},
    }
    document = _tactical_scene_document(probe)
    return document["placeables"][collection][_PLACEABLE_PROBE]


def _bump_tactical_scene_revision(draft: dict[str, Any], scene: dict[str, Any]) -> None:
    scene["revision"] += 1


def _create_tactical_scene(draft: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    document = _tactical_scene_document(op.get("scene"))
    scene_id = document["canonical_id"]
    if scene_id in draft["scenes"]:
        _fail(CODE_INVALID, f"scene {scene_id!r} already exists")
    document["revision"] = draft["revision"] + 1
    draft["scenes"][scene_id] = document
    return {"scene_id": scene_id}


def _update_tactical_scene(draft: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    scene_id = _tactical_scene_ref(op.get("scene_id"))
    current = _tactical_scene_document(_existing_tactical_scene(draft, scene_id))
    document = _tactical_scene_document(op.get("scene"))
    if document["canonical_id"] != scene_id:
        # Canonical identity is immutable; a rename is a different scene.
        _fail(CODE_INVALID, "scene canonical_id cannot change")
    document["revision"] = current["revision"] + 1
    draft["scenes"][scene_id] = document
    return {"scene_id": scene_id}


def _delete_tactical_scene(draft: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    scene_id = _tactical_scene_ref(op.get("scene_id"))
    _existing_tactical_scene(draft, scene_id)
    del draft["scenes"][scene_id]
    if draft.get("active_scene_ref") == scene_id:
        draft["active_scene_ref"] = None
    return {"scene_id": scene_id}


def _set_active(draft: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    scene_id = _tactical_scene_ref(op.get("scene_id"))
    _existing_tactical_scene(draft, scene_id)
    draft["active_scene_ref"] = scene_id
    return {"scene_id": scene_id}


def _create_placeable(draft: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    collection = _placeable_collection(op)
    scene_id = _tactical_scene_ref(op.get("scene_id"))
    scene = _tactical_scene_document(_existing_tactical_scene(draft, scene_id))
    document = _placeable_document(collection, op.get("placeable"))
    placeable_id = document["canonical_id"]
    entries = scene["placeables"][collection]
    if placeable_id in entries:
        _fail(CODE_INVALID, f"{collection} {placeable_id!r} already exists")
    entries[placeable_id] = document
    _bump_tactical_scene_revision(draft, scene)
    draft["scenes"][scene_id] = scene
    return {"scene_id": scene_id, "collection": collection, "placeable_id": placeable_id}


def _placeable_id(op: dict[str, Any]) -> str:
    value = op.get("placeable_id")
    if not is_canonical_id(value):
        _fail(CODE_INVALID, f"invalid placeable id: {value!r}")
    return str(value)


def _update_placeable(draft: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    collection = _placeable_collection(op)
    scene_id = _tactical_scene_ref(op.get("scene_id"))
    placeable_id = _placeable_id(op)
    scene = _tactical_scene_document(_existing_tactical_scene(draft, scene_id))
    if placeable_id not in scene["placeables"][collection]:
        _fail(CODE_PLACEABLE_NOT_FOUND, f"{collection} {placeable_id!r} does not exist")
    document = _placeable_document(collection, op.get("placeable"))
    if document["canonical_id"] != placeable_id:
        _fail(CODE_INVALID, "placeable canonical_id cannot change")
    scene["placeables"][collection][placeable_id] = document
    _bump_tactical_scene_revision(draft, scene)
    draft["scenes"][scene_id] = scene
    return {"scene_id": scene_id, "collection": collection, "placeable_id": placeable_id}


def _delete_placeable(draft: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    collection = _placeable_collection(op)
    scene_id = _tactical_scene_ref(op.get("scene_id"))
    placeable_id = _placeable_id(op)
    scene = _tactical_scene_document(_existing_tactical_scene(draft, scene_id))
    if placeable_id not in scene["placeables"][collection]:
        _fail(CODE_PLACEABLE_NOT_FOUND, f"{collection} {placeable_id!r} does not exist")
    del scene["placeables"][collection][placeable_id]
    _bump_tactical_scene_revision(draft, scene)
    draft["scenes"][scene_id] = scene
    return {"scene_id": scene_id, "collection": collection, "placeable_id": placeable_id}


_HANDLERS: Final[dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]]] = {
    TACTICAL_SCENE_CREATE: _create_tactical_scene,
    TACTICAL_SCENE_UPDATE: _update_tactical_scene,
    TACTICAL_SCENE_DELETE: _delete_tactical_scene,
    TACTICAL_SCENE_SET_ACTIVE: _set_active,
    PLACEABLE_CREATE: _create_placeable,
    PLACEABLE_UPDATE: _update_placeable,
    PLACEABLE_DELETE: _delete_placeable,
}


def _slot_copy(slot_draft: Any) -> dict[str, Any]:
    if not isinstance(slot_draft, dict) or slot_draft.get("schema_version") != _SLOT_SCHEMA_VERSION:
        _fail(CODE_INVALID, "scene slot cannot be written safely")
    scenes = slot_draft.get("scenes")
    revision = slot_draft.get("revision")
    if (
        not isinstance(scenes, dict)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
    ):
        _fail(CODE_INVALID, "scene slot cannot be written safely")
    return deepcopy(slot_draft)


def apply_tactical_scene_op(
    slot_draft: Any,
    op: Any,
    *,
    expected_revision: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one write operation to a copied slot draft.

    Returns ``(new_slot, summary)``; the summary always carries
    ``operation``, ``scene_id``, ``revision`` (the committed slot revision) and
    ``active_scene_ref``. The input draft is never mutated.
    """

    draft = _slot_copy(slot_draft)
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        _fail(CODE_INVALID, "expected_revision must be an integer")
    if draft["revision"] != expected_revision:
        _fail(
            CODE_STALE,
            f"scene revision {draft['revision']} does not match expected {expected_revision}",
        )
    if not isinstance(op, dict):
        _fail(CODE_INVALID, "scene operation must be an object")
    kind = op.get("kind")
    handler = _HANDLERS.get(kind) if isinstance(kind, str) else None
    if handler is None:
        _fail(CODE_INVALID, f"unknown scene operation: {kind!r}")

    summary = handler(draft, op)
    draft["revision"] += 1
    summary["operation"] = kind
    summary["revision"] = draft["revision"]
    summary["active_scene_ref"] = draft.get("active_scene_ref")
    return draft, summary


__all__ = [
    "CODE_INVALID",
    "CODE_NOT_FOUND",
    "CODE_PLACEABLE_NOT_FOUND",
    "CODE_STALE",
    "PLACEABLE_CREATE",
    "PLACEABLE_DELETE",
    "PLACEABLE_UPDATE",
    "TACTICAL_SCENE_CREATE",
    "TACTICAL_SCENE_DELETE",
    "TACTICAL_SCENE_SET_ACTIVE",
    "TACTICAL_SCENE_UPDATE",
    "TacticalSceneOpError",
    "apply_tactical_scene_op",
]