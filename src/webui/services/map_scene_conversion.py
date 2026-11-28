"""Explicit GM conversion of a location-map background into a Tactical Scene.

Frozen contract: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §12.2, §12.3, §9 B5.

The conversion only reads the existing ``media.map_background`` selection. It
validates the stored background asset, builds one ``tactical_scene_create``
operation (background ``AssetRef`` plus a default square grid) and applies it
through the scene write service, which owns authority, locking, save rollback
and the SSE projection. Nothing here guesses tokens, walls, routes or
coordinates, and the original selection is never modified or deleted.

The scene identity is derived from the stored asset, so converting the same
background twice returns the existing scene instead of creating a duplicate.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.engine.module_state import ModuleStateError
from src.engine.modules import media, tactical_scene
from src.engine.participant_view import Viewer
from src.tactical_scene.codec import decode_tactical_scene_document
from src.tactical_scene.contract import TacticalSceneContractError
from src.tactical_scene.projection import project_tactical_scene_gm
from src.webui.services.map_backgrounds import ASSET_ID_RE
from src.webui.services.tactical_scene_ops import (
    Broadcast,
    TacticalSceneOpDependencies,
    apply_tactical_scene_op,
)

CODE_FORBIDDEN = "map_background_forbidden"
CODE_NOT_FOUND = "map_background_not_found"
CODE_UNCONFIRMED = "map_background_unconfirmed"
CODE_UNRESOLVABLE = "map_background_unresolvable"
CODE_CONFLICT = "map_background_conversion_conflict"
CODE_INVALID = "tactical_scene_invalid_payload"

SCENE_SCHEMA_VERSION = 1
SCENE_DISPLAY_NAME = "Map background scene"
DEFAULT_GRID: dict[str, Any] = {
    "type": "square",
    "size": 100,
    "scale": 1.0,
    "units": "ft",
    "color": "#000000",
    "alpha": 0.25,
    "distance": 5.0,
}
DEFAULT_DIMENSIONS: dict[str, int] = {"width": 1600, "height": 1200}
_SOURCE_DIRS = {"upload": "map-backgrounds", "generated": "generated-images"}

ResolveBackgroundFile = Callable[[Any], Path | None]


def _error(code: str, message: str) -> dict[str, Any]:
    # ASCII messages: no locale catalog owns this service yet, so ``t`` call
    # sites here would fail the message-key audit.
    return {"ok": False, "error_code": code, "error": message}


def map_background_scene_id(kind: str, asset_id: str) -> str:
    """Deterministic canonical scene id for one stored background asset."""
    digest = hashlib.sha256(f"map-background:{kind}:{asset_id}".encode("utf-8")).hexdigest()[:20]
    return f"mapbg.{kind}.{digest}"


def _background_asset(kind: str, asset_id: str) -> dict[str, str]:
    return {
        "asset_id": asset_id,
        "digest": asset_id,
        "source_path": f"{_SOURCE_DIRS[kind]}/{asset_id}.webp",
        "mime": "image/webp",
    }


def _is_confirmed(intent: Mapping[str, Any]) -> bool:
    # An explicit grid payload is the second accepted confirmation carrier.
    return intent.get("confirm") is True or isinstance(intent.get("grid"), Mapping)


def _requested_grid(intent: Mapping[str, Any]) -> dict[str, Any]:
    grid = intent.get("grid")
    if isinstance(grid, Mapping):
        return dict(grid)
    return dict(DEFAULT_GRID)


def _image_dimensions(path: Path) -> dict[str, int]:
    """Read stored image pixel size when Pillow can decode it; otherwise default."""

    try:
        from PIL import Image  # type: ignore[import-not-found]
        with Image.open(path) as image:
            width, height = image.size
            if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
                return {"width": width, "height": height}
    except Exception:
        pass
    return dict(DEFAULT_DIMENSIONS)


def _scene_payload(
    scene_id: str,
    asset: dict[str, str],
    grid: dict[str, Any],
    dimensions: dict[str, int],
) -> dict[str, Any]:
    return {
        "schema_version": SCENE_SCHEMA_VERSION,
        "kind": "scene",
        "canonical_id": scene_id,
        "display_name": SCENE_DISPLAY_NAME,
        "background": asset,
        "dimensions": dimensions,
        "grid": grid,
        "placeables": {},
    }


def _existing_scene(
    instance: Any, scene_id: str, asset_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(result_or_None, error_or_None)`` for a scene already at the id."""

    existing = tactical_scene.scenes(instance).get(scene_id)
    if not isinstance(existing, dict):
        return None, None
    try:
        document = decode_tactical_scene_document(existing)
    except TacticalSceneContractError:
        return None, _error(CODE_INVALID, "The existing scene cannot be read safely.")
    background = document.get("background") or {}
    if background.get("asset_id") != asset_id:
        return None, _error(
            CODE_CONFLICT,
            "A scene already uses this background identity with a different background.",
        )
    return {
        "ok": True,
        "created": False,
        "scene_id": scene_id,
        "revision": tactical_scene.revision(instance),
        "active_scene_ref": tactical_scene.active_scene_ref(instance),
        "scene": project_tactical_scene_gm(document),
    }, None


async def convert_map_background_to_scene(
    scene_dependencies: TacticalSceneOpDependencies,
    resolve_background_file: ResolveBackgroundFile,
    game_key: str,
    viewer: Viewer,
    *,
    intent: Any,
    broadcast: Broadcast | None = None,
) -> dict[str, Any]:
    """Create (or reuse) one Scene from the game's stored map background."""

    if not viewer.is_gm:
        return _error(CODE_FORBIDDEN, "Only the GM can convert the map background into a tactical scene.")
    instance = scene_dependencies.get_instance(game_key)
    if instance is None:
        return _error(CODE_NOT_FOUND, "The game does not exist.")
    if not isinstance(intent, Mapping) or not _is_confirmed(intent):
        return _error(CODE_UNCONFIRMED, "Confirm the conversion and its grid before creating a tactical scene.")
    try:
        selection = media.map_background(instance)
        slot_revision = tactical_scene.revision(instance)
    except ModuleStateError:
        return _error(CODE_INVALID, "The scene state cannot be read safely.")

    kind = selection.get("kind") if isinstance(selection, dict) else None
    asset_id = str(selection.get("asset_id") or "") if isinstance(selection, dict) else ""
    path = resolve_background_file(selection)
    if path is None or kind not in _SOURCE_DIRS or ASSET_ID_RE.fullmatch(asset_id) is None:
        # auto/none/builtin/plugin selections have no per-save image asset; the
        # conversion is refused instead of guessing a reference.
        return _error(CODE_UNRESOLVABLE, "The current map background does not resolve to a stored image asset.")

    asset = _background_asset(str(kind), asset_id)
    scene_id = map_background_scene_id(str(kind), asset_id)
    try:
        existing, error = _existing_scene(instance, scene_id, asset_id)
    except ModuleStateError:
        return _error(CODE_INVALID, "The scene state cannot be read safely.")
    if error is not None:
        return error
    if existing is not None:
        return existing

    expected_revision = intent.get("expected_revision")
    if expected_revision is None:
        expected_revision = slot_revision
    op = {
        "kind": "tactical_scene_create",
        "scene": _scene_payload(scene_id, asset, _requested_grid(intent), _image_dimensions(path)),
    }
    result = await apply_tactical_scene_op(
        scene_dependencies, game_key, viewer, op,
        expected_revision=expected_revision, broadcast=broadcast,
    )
    if result.get("ok"):
        result["created"] = True
    return result


__all__ = [
    "DEFAULT_GRID",
    "convert_map_background_to_scene",
    "map_background_scene_id",
]