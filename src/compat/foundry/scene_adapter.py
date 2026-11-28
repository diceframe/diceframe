"""FVTT Scene <-> DiceFrame TacticalSceneDocument adapter (B3, pure domain conversion).

Frozen source of truth: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §6.3
(Scene contract), §6.4 (Token contract), §6.7 (field map), §1.4 (compat
scope), §13.1/13.2 (tests and fixtures) and §15.1 (never execute foreign
code).

Boundaries:

- Read-only dependency on ``src.tactical_scene.contract``; this adapter never imports
  webui, rulesets, the engine aggregate or the scene runtime.
- v14 is the primary target, v13 is import-only. Versions are matched
  explicitly; unknown or future core versions fail closed.
- Nothing is fetched, executed or created: assets keep their relative source
  path, actors/levels stay external references, macros/scripts are preserved as
  inert data and reported.
- Fields outside the frozen field map go to ``extensions`` unchanged; they are
  never interpreted as runnable mechanics.

v14 moved scene media from top-level ``background``/``foreground`` into
``levels[*]`` and replaced the v13 fog ``exploration`` flag with a fog object.
This adapter reads both documented shapes, records the shape it actually saw,
and exports the v14 shape (with the documented v13 top-level media fields only
when a TacticalSceneDocument has no levels at all).
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import mimetypes
import re
from typing import Any

from src.tactical_scene.contract import (
    TACTICAL_SCENE_SCHEMA_VERSION,
    TacticalSceneContractError,
    TacticalSceneDocument,
    WALL_DIRECTION_MAX,
    WALL_DOOR_MAX,
    WALL_DOOR_STATE_MAX,
    WALL_RESTRICTION_MAX,
    normalize_tactical_scene_payload,
)

from .common import (
    CODE_SCENE_MALFORMED,
    CODE_VERSION_UNSUPPORTED,
    NAMESPACE_V13,
    NAMESPACE_UUID,
    NAMESPACE_V14,
    FoundrySceneError,
    _ADAPTER_ID,
    _asset_from_src,
    _bool,
    _canonical_id,
    _convert_texture,
    _export_media,
    _external_id,
    _flags,
    _int,
    _malformed,
    _merge_residual,
    _num,
    _obj,
    _ownership,
    _str,
    _unsupported,
)
from .placeables import _PLACEABLE_CONVERTERS, _PLACEABLE_EXPORTERS
from .report import ConversionLevel, ConversionReport



_PRIMARY_MAJOR = 14
_VERSION_NAMESPACES: dict[int, str] = {13: NAMESPACE_V13, 14: NAMESPACE_V14}

PLACEABLE_COLLECTIONS: tuple[str, ...] = (
    "tokens", "tiles", "walls", "lights", "sounds",
    "notes", "drawings", "templates", "regions",
)

_GRID_TYPE_FROM_NUMBER: dict[int, str] = {
    0: "gridless", 1: "square", 2: "hex", 3: "hex", 4: "hex", 5: "hex",
}
_GRID_TYPE_TO_NUMBER: dict[str, int] = {"gridless": 0, "square": 1, "hex": 2}
_GRID_CONSUMED_KEYS = frozenset({"type", "size", "distance", "units", "color", "alpha"})

# Executable-content containers recognized from the documented FVTT macro shape
# (``MacroData.command``) plus obvious script containers. Matching is
# conservative: the content is only ever preserved and reported.
_SCRIPT_KEYS = frozenset({"macro", "macros", "script", "scripts", "command"})

_COMMON_HANDLED_KEYS = frozenset({
    "_id", "name", "navName", "grid", "width", "height", "padding", "shiftX",
    "shiftY", "initial", "thumb", "environment", "fog", "weather", "ownership",
    "sort", "flags", "background", "foreground", *PLACEABLE_COLLECTIONS,
})
_V14_HANDLED_KEYS = _COMMON_HANDLED_KEYS | {"levels", "initialLevel"}
_V13_HANDLED_KEYS = _COMMON_HANDLED_KEYS

_RESIDUAL_CONTAINER_KEYS = frozenset({
    "background", "foreground", "grid", "environment", "fog", "initial",
})

_TOKEN_MAPPED_KEYS = frozenset({
    "_id", "actorId", "x", "y", "elevation", "width", "height", "rotation",
    "alpha", "hidden", "locked", "disposition", "texture", "ring", "sight",
    "detectionModes", "light", "flags", "delta", "bar1", "bar2", "displayName",
    "displayBars", "level",
})

_EXPORT_RESERVED_KEYS = frozenset({
    "_id", "name", "navName", "width", "height", "padding", "shiftX", "shiftY",
    "initial", "grid", "environment", "fog", "weather", "sort", "ownership",
    "flags", "thumb", "levels", "background", "foreground",
    *PLACEABLE_COLLECTIONS,
})


# ---- version detection ----------------------------------------------------


def _resolve_major(source_version: Any) -> int:
    if isinstance(source_version, bool):
        _unsupported(f"Foundry version {source_version!r} is not supported")
    major: int | None = None
    if isinstance(source_version, int):
        major = source_version
    elif isinstance(source_version, str):
        text = source_version.strip().lower()
        if text.startswith("v"):
            text = text[1:]
        head = text.split(".", 1)[0]
        if head.isdigit():
            major = int(head)
    if major is None or major not in _VERSION_NAMESPACES:
        _unsupported(
            f"Foundry version {source_version!r} is not supported "
            f"(v14 is the primary target, v13 import-only)"
        )
    return major  # type: ignore[return-value]


def _detected_core_major(raw: Mapping[str, Any]) -> tuple[int | None, str]:
    stats = raw.get("_stats")
    if not isinstance(stats, Mapping):
        return None, ""
    core_version = stats.get("coreVersion")
    if not isinstance(core_version, str) or not core_version.strip():
        return None, ""
    head = core_version.strip().split(".", 1)[0]
    if not head.isdigit():
        return None, core_version
    return int(head), core_version


def detect_foundry_version(raw_fvtt: Mapping[str, Any], *, source_version: Any) -> int:
    """Validate the declared version against the payload's ``_stats``.

    Returns the supported major version or raises ``FoundrySceneError`` with
    code ``foundry_version_unsupported``. No "latest" matching is performed.
    """
    if not isinstance(raw_fvtt, Mapping):
        _malformed("scene payload must be a JSON object")
    declared = _resolve_major(source_version)
    detected, core_version = _detected_core_major(raw_fvtt)
    if detected is not None:
        if detected not in _VERSION_NAMESPACES:
            _unsupported(
                f"_stats.coreVersion {core_version!r} is not a supported Foundry version"
            )
        if detected != declared:
            _unsupported(
                f"payload reports Foundry {core_version!r} but source_version declares v{declared}"
            )
    return declared


# ---- script scan (never executes anything) --------------------------------


def _scan_scripts(value: Any, path: str, report: ConversionReport, depth: int = 0) -> None:
    if depth > 32:
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(key, str) and key.lower() in _SCRIPT_KEYS:
                report.add(
                    ConversionLevel.CONTAINS_SCRIPT,
                    child_path,
                    "executable content preserved as inert data; never executed",
                )
            _scan_scripts(child, child_path, report, depth + 1)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_scripts(child, f"{path}[{index}]", report, depth + 1)


# ---- scene sub-structures -------------------------------------------------


def _grid_type(value: Any, report: ConversionReport) -> str:
    if value is None:
        return "gridless"
    if isinstance(value, bool):
        _malformed("scene.grid.type must be a number or string")
    if isinstance(value, int):
        if value in _GRID_TYPE_FROM_NUMBER:
            return _GRID_TYPE_FROM_NUMBER[value]
        report.add(
            ConversionLevel.DEGRADED_DISPLAY,
            "scene.grid.type",
            f"unknown numeric grid type {value} preserved as text; projection not derived",
        )
        return str(value)
    if isinstance(value, str):
        if value in _GRID_TYPE_TO_NUMBER:
            return value
        report.add(
            ConversionLevel.DEGRADED_DISPLAY,
            "scene.grid.type",
            f"unknown grid type {value!r} preserved; projection not derived",
        )
        return value
    _malformed("scene.grid.type must be a number or string")


def _scene_grid(
    raw: Mapping[str, Any], report: ConversionReport,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    residual: dict[str, Any] | None = None
    grid_raw = raw.get("grid")
    if grid_raw is None:
        report.warn("scene.grid missing; gridless defaults applied")
        return {
            "type": "gridless", "size": 0, "scale": 1.0, "units": "",
            "color": "", "alpha": 0.0, "distance": 0.0,
        }, None
    grid = _obj(grid_raw, "scene.grid")
    leftover = {k: deepcopy(v) for k, v in grid.items() if k not in _GRID_CONSUMED_KEYS}
    if leftover:
        residual = leftover
    report.warn("grid.scale has no FVTT GridData source; defaulted to 1.0")
    return {
        "type": _grid_type(grid.get("type"), report),
        "size": _int(grid.get("size"), "scene.grid.size", 0),
        "scale": 1.0,
        "units": _str(grid.get("units"), "scene.grid.units", ""),
        "color": _str(grid.get("color"), "scene.grid.color", ""),
        "alpha": _num(grid.get("alpha"), "scene.grid.alpha", 0.0),
        "distance": _num(grid.get("distance"), "scene.grid.distance", 0.0),
    }, residual


def _global_light(
    value: Any, *, path: str, major: int, report: ConversionReport,
) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        if major == _PRIMARY_MAJOR:
            report.add(
                ConversionLevel.DEGRADED_DISPLAY,
                path,
                "boolean globalLight is the v13 shape in a v14 payload",
            )
        return value
    if isinstance(value, Mapping):
        if major == 13:
            report.add(
                ConversionLevel.UNKNOWN_FUTURE_STRUCTURE,
                path,
                "object globalLight is the v14 shape in a v13 payload",
            )
        enabled = value.get("enabled")
        if enabled is None:
            return False
        if isinstance(enabled, bool):
            return enabled
        if isinstance(enabled, (int, float)):
            return enabled != 0
        _malformed(f"{path}.enabled must be a number or boolean")
    _malformed(f"{path} must be a boolean or object")


def _scene_dimensions(
    raw: Mapping[str, Any], report: ConversionReport,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    view_x = view_y = 0.0
    zoom = 1.0
    residual: dict[str, Any] | None = None
    initial_raw = raw.get("initial")
    if initial_raw is not None:
        initial = _obj(initial_raw, "scene.initial")
        view_x = _num(initial.get("x"), "scene.initial.x", 0.0)
        view_y = _num(initial.get("y"), "scene.initial.y", 0.0)
        zoom = _num(initial.get("scale"), "scene.initial.scale", 1.0)
        leftover = {
            k: deepcopy(v) for k, v in initial.items() if k not in {"x", "y", "scale"}
        }
        if leftover:
            residual = leftover
    return {
        "width": _int(raw.get("width"), "scene.width", 0),
        "height": _int(raw.get("height"), "scene.height", 0),
        "padding": _num(raw.get("padding"), "scene.padding", 0.0),
        "offset_x": _int(raw.get("shiftX"), "scene.shiftX", 0),
        "offset_y": _int(raw.get("shiftY"), "scene.shiftY", 0),
        "initial_view": {"x": view_x, "y": view_y, "zoom": zoom},
    }, residual


def _media_src(value: Any, path: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        src = value.get("src")
        if src is not None and not isinstance(src, str):
            _malformed(f"{path}.src must be a media path string")
        return src
    _malformed(f"{path} must be a media path string or object")


def _media_residual(value: Any, path: str) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    leftover = {k: deepcopy(v) for k, v in value.items() if k != "src"}
    if not leftover:
        return None
    if "src" not in value:
        _malformed(f"{path} must carry a src media path")
    return leftover


def _scrub_inline_level_media(
    levels: list[dict[str, Any]], report: ConversionReport,
) -> None:
    """Drop inline data URIs from preserved levels; assets are never inlined."""
    for index, level in enumerate(levels):
        for key in ("background", "foreground"):
            media = level.get(key)
            if not isinstance(media, dict):
                continue
            src = media.get("src")
            if isinstance(src, str) and src.startswith("data:"):
                media["src"] = ""
                report.missing(
                    "asset_ref", f"scene.levels[{index}].{key}.src", "<inline-data>",
                )
                report.warn(
                    f"scene.levels[{index}].{key}: inline data URI dropped; "
                    "assets are never inlined"
                )


def _pick_level(
    levels: list[dict[str, Any]], initial_level: Any, report: ConversionReport,
) -> dict[str, Any] | None:
    if not levels:
        return None
    if isinstance(initial_level, str):
        for level in levels:
            if level.get("_id") == initial_level:
                return level
        report.warn(f"initialLevel {initial_level!r} not found among scene levels")
    if len(levels) > 1:
        with_media = [
            level for level in levels
            if isinstance(level.get("background"), Mapping)
            and level["background"].get("src")
        ]
        if len(with_media) > 1:
            report.add(
                ConversionLevel.DEGRADED_DISPLAY,
                "scene.levels",
                f"{len(with_media)} levels carry background media; scene background "
                "taken from the first one, every level preserved in scene.levels",
            )
        if with_media:
            return with_media[0]
    return levels[0]


def _scene_environment(
    raw: Mapping[str, Any], *, major: int, report: ConversionReport,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    environment_residual: dict[str, Any] | None = None
    darkness = 0.0
    global_illumination = False
    env_raw = raw.get("environment")
    if env_raw is not None:
        env = _obj(env_raw, "scene.environment")
        darkness = _num(env.get("darknessLevel"), "scene.environment.darknessLevel", 0.0)
        global_illumination = _global_light(
            env.get("globalLight"),
            path="scene.environment.globalLight",
            major=major,
            report=report,
        )
        environment_residual = deepcopy(dict(env))
    elif major == _PRIMARY_MAJOR:
        report.warn("scene.environment missing; darkness and global light defaulted")

    exploration = False
    fog_residual: dict[str, Any] | None = None
    fog_raw = raw.get("fog")
    if fog_raw is not None:
        fog = _obj(fog_raw, "scene.fog")
        if "exploration" in fog:
            exploration = _bool(fog["exploration"], "scene.fog.exploration", False)
            if major == _PRIMARY_MAJOR:
                report.add(
                    ConversionLevel.DEGRADED_DISPLAY,
                    "scene.fog.exploration",
                    "v13 fog.exploration in a v14 payload; preserved but not re-exported",
                )
        else:
            report.warn(
                "scene.fog.exploration is absent from the v14 fog object; defaulted to false"
            )
        fog_residual = deepcopy(dict(fog))
        if major == 13:
            report.add(
                ConversionLevel.DEGRADED_DISPLAY,
                "scene.fog.exploration",
                "v13 fog.exploration has no v14 SceneData equivalent; preserved but not exported",
            )

    report.warn(
        "environment.fog.reset_policy is a DiceFrame policy field with no FVTT source; left empty"
    )
    return {
        "darkness": darkness,
        "global_illumination": global_illumination,
        "fog": {"exploration": exploration, "reset_policy": ""},
        "weather": _str(raw.get("weather"), "scene.weather", ""),
    }, environment_residual, fog_residual


# ---- tokens ---------------------------------------------------------------


def _convert_detection_modes(
    value: Any, *, path: str,
) -> list[Any]:
    modes = _obj(value, path)
    result: list[Any] = []
    for key, child in modes.items():
        if not isinstance(key, str) or not isinstance(child, Mapping):
            _malformed(f"{path} must map detection ids to objects")
        entry = deepcopy(dict(child))
        entry.setdefault("id", key)
        result.append(entry)
    return result


def _convert_token(
    raw: Mapping[str, Any],
    *,
    index: int,
    namespace: str,
    external_id: str | None,
    scene_key: str,
    report: ConversionReport,
) -> dict[str, Any]:
    path = f"scene.tokens[{index}]"
    key = external_id or f"{scene_key}:tokens:{index}"
    if external_id is None:
        report.warn(f"{path}: token has no _id; canonical id derived from its array index")
    canonical = _canonical_id(namespace, "token", key)
    extensions: dict[str, Any] = {}

    actor_id = raw.get("actorId")
    actor_ref: dict[str, str] | None = None
    if actor_id is not None:
        if not isinstance(actor_id, str) or not actor_id:
            _malformed(f"{path}.actorId must be a non-empty string")
        actor_ref = {
            "source_kind": "foundry",
            "source_id": namespace,
            "kind": "actor",
            "id": actor_id,
            "digest": "",
        }
        report.missing("actor_ref", f"{path}.actorId", actor_id)

    level_value = raw.get("level")
    position_level = 0
    if level_value is not None:
        if isinstance(level_value, bool):
            _malformed(f"{path}.level must be a level id or integer")
        if isinstance(level_value, int):
            position_level = level_value
        elif isinstance(level_value, str):
            extensions["level"] = level_value
            report.missing("level_ref", f"{path}.level", level_value)
            report.add(
                ConversionLevel.MISSING_EXTERNAL_REF,
                f"{path}.level",
                "FVTT token.level is a level id; TacticalSceneDocument position.level is numeric",
            )
        else:
            _malformed(f"{path}.level must be a level id or integer")

    texture, texture_residual = _convert_texture(raw, path=f"{path}.texture", report=report)
    if texture_residual:
        extensions["texture"] = texture_residual

    bars: dict[str, Any] = {}
    for bar_key in ("bar1", "bar2"):
        if bar_key in raw and raw[bar_key] is not None:
            bars[bar_key] = deepcopy(dict(_obj(raw[bar_key], f"{path}.{bar_key}")))

    display: dict[str, Any] = {}
    if "displayName" in raw and raw["displayName"] is not None:
        display["name"] = _int(raw["displayName"], f"{path}.displayName", 0)
    if "displayBars" in raw and raw["displayBars"] is not None:
        display["bars"] = _int(raw["displayBars"], f"{path}.displayBars", 0)

    detection_modes: list[Any] = []
    if raw.get("detectionModes") is not None:
        detection_modes = _convert_detection_modes(
            raw["detectionModes"], path=f"{path}.detectionModes",
        )

    for key_name, value in raw.items():
        if not isinstance(key_name, str):
            _malformed(f"{path} field names must be strings")
        if key_name in _TOKEN_MAPPED_KEYS:
            continue
        extensions[key_name] = deepcopy(value)
        report.unknown(f"{path}.{key_name}")

    return {
        "schema_version": TACTICAL_SCENE_SCHEMA_VERSION,
        "kind": "token",
        "canonical_id": canonical,
        "actor_ref": actor_ref,
        "position": {
            "x": _num(raw.get("x"), f"{path}.x", 0.0),
            "y": _num(raw.get("y"), f"{path}.y", 0.0),
            "elevation": _num(raw.get("elevation"), f"{path}.elevation", 0.0),
            "level": position_level,
        },
        "size": {
            "width": _num(raw.get("width"), f"{path}.width", 1.0),
            "height": _num(raw.get("height"), f"{path}.height", 1.0),
        },
        "rotation": _num(raw.get("rotation"), f"{path}.rotation", 0.0),
        "texture": texture,
        "ring": deepcopy(dict(_obj(raw.get("ring", {}), f"{path}.ring"))),
        "alpha": _num(raw.get("alpha"), f"{path}.alpha", 1.0),
        "display": display,
        "disposition": _int(raw.get("disposition"), f"{path}.disposition", 0),
        "hidden": _bool(raw.get("hidden"), f"{path}.hidden", False),
        "locked": _bool(raw.get("locked"), f"{path}.locked", False),
        "sight": deepcopy(dict(_obj(raw.get("sight", {}), f"{path}.sight"))),
        "detection_modes": detection_modes,
        "light": deepcopy(dict(_obj(raw.get("light", {}), f"{path}.light"))),
        "bars": bars,
        "delta": deepcopy(dict(_obj(raw.get("delta", {}), f"{path}.delta"))),
        "external_ids": {namespace: external_id} if external_id else {},
        "flags": _flags(raw.get("flags"), f"{path}.flags"),
        "extensions": extensions,
        "provenance": {
            "source": namespace,
            "external_id": external_id or "",
            "adapter": _ADAPTER_ID,
        },
    }


# ---- opaque placeables ----------------------------------------------------


def _opaque_placeable(
    raw: Mapping[str, Any],
    *,
    collection: str,
    index: int,
    namespace: str,
    external_id: str | None,
    scene_key: str,
    report: ConversionReport,
) -> dict[str, Any]:
    path = f"scene.{collection}[{index}]"
    kind = collection[:-1] if collection.endswith("s") else collection
    key = external_id or f"{scene_key}:{collection}:{index}"
    if external_id is None:
        report.warn(f"{path}: placeable has no _id; canonical id derived from its array index")
    document = deepcopy(dict(raw))
    document.pop("_id", None)
    document["canonical_id"] = _canonical_id(namespace, kind, key)
    document["external_ids"] = {namespace: external_id} if external_id else {}
    document["flags"] = _flags(document.get("flags"), f"{path}.flags")
    document["extensions"] = {}
    document["provenance"] = {
        "source": namespace,
        "collection": collection,
        "external_id": external_id or "",
        "adapter": _ADAPTER_ID,
    }
    return document


# ---- import ---------------------------------------------------------------


def _ensure_json_payload(raw: Any) -> None:
    try:
        json.dumps(raw, allow_nan=False)
    except (TypeError, ValueError) as err:
        _malformed(f"payload is not JSON-serializable: {err}")


def _scene_placeables(
    raw: Mapping[str, Any],
    *,
    namespace: str,
    scene_key: str,
    report: ConversionReport,
) -> dict[str, dict[str, Any]]:
    placeables: dict[str, dict[str, Any]] = {name: {} for name in PLACEABLE_COLLECTIONS}
    for collection in PLACEABLE_COLLECTIONS:
        entries = raw.get(collection)
        if entries is None:
            continue
        if not isinstance(entries, list):
            _malformed(f"scene.{collection} must be an array")
        if not entries:
            continue
        converter = (
            _convert_token if collection == "tokens"
            else _PLACEABLE_CONVERTERS.get(collection)
        )
        if converter is None:
            report.add(
                ConversionLevel.SAVED_NOT_RUNNABLE,
                f"scene.{collection}",
                f"{len(entries)} {collection} saved opaquely; not runnable in this phase",
            )
        seen: set[str] = set()
        for index, item in enumerate(entries):
            entry = _obj(item, f"scene.{collection}[{index}]")
            external_id = _external_id(entry, f"scene.{collection}[{index}]")
            if external_id is not None:
                if external_id in seen:
                    _malformed(
                        f"scene.{collection} contains duplicate _id {external_id!r}"
                    )
                seen.add(external_id)
            if converter is not None:
                document = converter(
                    entry,
                    index=index,
                    namespace=namespace,
                    external_id=external_id,
                    scene_key=scene_key,
                    report=report,
                )
            else:
                document = _opaque_placeable(
                    entry,
                    collection=collection,
                    index=index,
                    namespace=namespace,
                    external_id=external_id,
                    scene_key=scene_key,
                    report=report,
                )
            placeables[collection][document["canonical_id"]] = document
    # Walls and lights are display data until a ruleset capability uses them.
    for collection, rules in (("walls", "movement and line of sight"), ("lights", "lighting")):
        if placeables[collection]:
            report.warn(
                f"scene.{collection}: converted for display; "
                f"{rules} rules do not use them in this phase"
            )
    return placeables


def _v13_future_structure(
    raw: Mapping[str, Any], report: ConversionReport,
) -> None:
    for key in ("levels", "initialLevel"):
        if key in raw:
            report.add(
                ConversionLevel.UNKNOWN_FUTURE_STRUCTURE,
                f"scene.{key}",
                f"{key} is a v14 SceneData field inside a v13 payload; preserved opaquely",
            )
            report.unknown(f"scene.{key}")


def import_scene(
    raw_fvtt: Mapping[str, Any], *, source_version: Any,
) -> tuple[TacticalSceneDocument, ConversionReport]:
    """Convert one FVTT scene JSON object into a validated TacticalSceneDocument.

    ``source_version`` is required and must name a supported Foundry major
    (``"v14"``/``"v13"`` or ``13``/``14``); it is cross-checked against
    ``_stats.coreVersion``. Unknown or conflicting versions fail closed with
    ``foundry_version_unsupported``. Malformed payloads fail with
    ``foundry_scene_malformed``. Assets, actors and levels are never fetched,
    resolved or created; scripts are preserved but never executed.
    """
    if not isinstance(raw_fvtt, Mapping):
        _malformed("scene payload must be a JSON object")
    _ensure_json_payload(raw_fvtt)
    major = detect_foundry_version(raw_fvtt, source_version=source_version)
    namespace = _VERSION_NAMESPACES[major]
    report = ConversionReport(source_version=f"v{major}")

    _scan_scripts(raw_fvtt, "scene", report)

    scene_external_id = raw_fvtt.get("_id")
    if not isinstance(scene_external_id, str) or not scene_external_id:
        _malformed("scene._id is required: scene identity is never derived from its name")
    if "name" not in raw_fvtt:
        _malformed("scene.name is required")
    display_name = _str(raw_fvtt.get("name"), "scene.name", "")
    navigation_name = _str(raw_fvtt.get("navName"), "scene.navName", "")
    canonical_id = _canonical_id(namespace, "scene", scene_external_id)

    levels_raw = raw_fvtt.get("levels")
    levels: list[dict[str, Any]] = []
    if levels_raw is not None:
        if not isinstance(levels_raw, list):
            _malformed("scene.levels must be an array")
        for index, level in enumerate(levels_raw):
            levels.append(deepcopy(dict(_obj(level, f"scene.levels[{index}]"))))
    _scrub_inline_level_media(levels, report)

    extensions: dict[str, Any] = {}

    background: dict[str, str] | None = None
    foreground: dict[str, str] | None = None
    level = (
        _pick_level(levels, raw_fvtt.get("initialLevel"), report)
        if major == _PRIMARY_MAJOR
        else None
    )
    if level is not None:
        level_bg = level.get("background")
        if isinstance(level_bg, Mapping):
            background = _asset_from_src(
                level_bg.get("src"), path="scene.levels.background.src", report=report,
            )
        level_fg = level.get("foreground")
        if isinstance(level_fg, Mapping):
            foreground = _asset_from_src(
                level_fg.get("src"), path="scene.levels.foreground.src", report=report,
            )

    if "background" in raw_fvtt:
        background_raw = raw_fvtt["background"]
        if background is None:
            if major == _PRIMARY_MAJOR:
                report.add(
                    ConversionLevel.DEGRADED_DISPLAY,
                    "scene.background",
                    "v13-style top-level background in a v14 payload; v14 keeps it in levels",
                )
            background = _asset_from_src(
                _media_src(background_raw, "scene.background"),
                path="scene.background.src",
                report=report,
            )
        else:
            report.warn(
                "top-level background is superseded by level background; raw fields preserved"
            )
        residual = _media_residual(background_raw, "scene.background")
        if residual:
            extensions["background"] = residual
    if "foreground" in raw_fvtt:
        foreground_raw = raw_fvtt["foreground"]
        if foreground is None:
            if major == _PRIMARY_MAJOR:
                report.add(
                    ConversionLevel.DEGRADED_DISPLAY,
                    "scene.foreground",
                    "v13-style top-level foreground in a v14 payload; v14 keeps it in levels",
                )
            foreground = _asset_from_src(
                _media_src(foreground_raw, "scene.foreground"),
                path="scene.foreground.src",
                report=report,
            )
            if isinstance(foreground_raw, str) and major == 13:
                report.warn("v13 foreground is a media path string")
        else:
            report.warn(
                "top-level foreground is superseded by level foreground; raw fields preserved"
            )
        residual = _media_residual(foreground_raw, "scene.foreground")
        if residual:
            extensions["foreground"] = residual

    thumbnail = None
    if "thumb" in raw_fvtt and raw_fvtt["thumb"] is not None:
        thumbnail = _asset_from_src(
            _str(raw_fvtt["thumb"], "scene.thumb", ""),
            path="scene.thumb",
            report=report,
        )

    dimensions, initial_residual = _scene_dimensions(raw_fvtt, report)
    if initial_residual:
        extensions["initial"] = initial_residual
    grid, grid_residual = _scene_grid(raw_fvtt, report)
    if grid_residual:
        extensions["grid"] = grid_residual
    environment, environment_residual, fog_residual = _scene_environment(
        raw_fvtt, major=major, report=report,
    )
    if environment_residual:
        extensions["environment"] = environment_residual
    if fog_residual:
        extensions["fog"] = fog_residual

    placeables = _scene_placeables(
        raw_fvtt, namespace=namespace, scene_key=scene_external_id, report=report,
    )

    if major == 13:
        _v13_future_structure(raw_fvtt, report)
    elif "initialLevel" in raw_fvtt:
        extensions["initialLevel"] = deepcopy(raw_fvtt["initialLevel"])
        report.preserve("initialLevel")

    handled = _V14_HANDLED_KEYS if major == _PRIMARY_MAJOR else _V13_HANDLED_KEYS
    for key, value in raw_fvtt.items():
        if not isinstance(key, str):
            _malformed("scene field names must be strings")
        report.preserve(key)
        if key in handled:
            continue
        if key in _RESIDUAL_CONTAINER_KEYS:
            continue
        if key == "_stats":
            extensions[key] = deepcopy(value)
            continue
        extensions[key] = deepcopy(value)
        report.unknown(f"scene.{key}")

    payload: dict[str, Any] = {
        "schema_version": TACTICAL_SCENE_SCHEMA_VERSION,
        "kind": "scene",
        "canonical_id": canonical_id,
        "display_name": display_name,
        "navigation_name": navigation_name,
        "source_ref": None,
        "external_ids": {namespace: scene_external_id},
        "ownership": _ownership(raw_fvtt.get("ownership"), "scene.ownership"),
        "sort": _int(raw_fvtt.get("sort"), "scene.sort", 0),
        "background": background,
        "foreground": foreground,
        "thumbnail": thumbnail,
        "dimensions": dimensions,
        "grid": grid,
        "environment": environment,
        "placeables": placeables,
        "levels": levels,
        "flags": _flags(raw_fvtt.get("flags"), "scene.flags"),
        "extensions": extensions,
        "provenance": {
            "source": namespace,
            "source_version": f"v{major}",
            "core_version": _detected_core_major(raw_fvtt)[1],
            "adapter": _ADAPTER_ID,
        },
        "revision": 0,
    }

    try:
        document, contract_report = normalize_tactical_scene_payload(payload)
    except TacticalSceneContractError as err:
        raise FoundrySceneError(
            CODE_SCENE_MALFORMED,
            f"converted scene failed the scene contract: {err.reason}: {err.detail}",
        ) from err

    for path in contract_report.unknown_fields:
        report.unknown(path)
    for message in contract_report.degraded:
        report.warn(f"scene contract degradation: {message}")
    for ref in contract_report.missing_references:
        report.missing(ref.kind, ref.path)

    return document, report


# ---- export (v14) ---------------------------------------------------------


def _export_grid(grid: Mapping[str, Any], residual: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "size": grid["size"],
        "distance": grid["distance"],
        "units": grid["units"],
        "color": grid["color"],
        "alpha": grid["alpha"],
    }
    grid_type = grid["type"]
    if grid_type in _GRID_TYPE_TO_NUMBER:
        out["type"] = _GRID_TYPE_TO_NUMBER[grid_type]
    elif isinstance(grid_type, str) and grid_type.isdigit():
        out["type"] = int(grid_type)
    _merge_residual(out, residual)
    return out


def _export_environment(scene: Mapping[str, Any], residual: Any) -> dict[str, Any] | None:
    """Rebuild the FVTT environment, or ``None`` when the source had none.

    A scene that arrived without an ``environment`` and still holds the default
    darkness and global light is exported without one. Emitting defaults here
    would turn into residual data on the next import and break the round trip.
    """

    environment = scene["environment"]
    if (
        not isinstance(residual, Mapping)
        and environment["darkness"] == 0.0
        and not environment["global_illumination"]
    ):
        return None
    out: dict[str, Any] = deepcopy(dict(residual)) if isinstance(residual, Mapping) else {}
    out["darknessLevel"] = environment["darkness"]
    global_light = out.get("globalLight")
    if isinstance(global_light, Mapping):
        global_light = deepcopy(dict(global_light))
    else:
        global_light = {}
    global_light["enabled"] = 1 if environment["global_illumination"] else 0
    out["globalLight"] = global_light
    return out


def _export_fog(residual: Any) -> dict[str, Any] | None:
    if not isinstance(residual, Mapping):
        return None
    fog = deepcopy(dict(residual))
    fog.pop("exploration", None)
    return fog or None


def _select_export_level(
    levels: list[dict[str, Any]], initial_level: Any,
) -> dict[str, Any]:
    if isinstance(initial_level, str):
        for level in levels:
            if level.get("_id") == initial_level:
                return level
    for level in levels:
        background = level.get("background")
        if isinstance(background, Mapping) and background.get("src"):
            return level
    return levels[0]


def _export_levels(
    scene: Mapping[str, Any], extensions: Mapping[str, Any], out: dict[str, Any],
) -> None:
    levels = deepcopy(scene["levels"])
    background = scene["background"]
    foreground = scene["foreground"]
    if levels:
        target = _select_export_level(levels, extensions.get("initialLevel"))
        for key, asset in (("background", background), ("foreground", foreground)):
            if asset is None:
                continue
            current = target.get(key)
            if isinstance(current, Mapping) and current.get("src") == asset["source_path"]:
                continue
            merged = deepcopy(dict(current)) if isinstance(current, Mapping) else {}
            merged["src"] = asset["source_path"]
            target[key] = merged
        out["levels"] = levels
        return
    if background is not None:
        out["background"] = _export_media(background, extensions.get("background"))
    if foreground is not None:
        out["foreground"] = _export_media(foreground, extensions.get("foreground"))


def _export_detection_modes(entries: list[Any]) -> dict[str, Any]:
    modes: dict[str, Any] = {}
    for index, entry in enumerate(entries):
        if isinstance(entry, Mapping):
            item = deepcopy(dict(entry))
            raw_id = item.pop("id", None)
            key = str(raw_id) if raw_id not in (None, "") else str(index)
            modes[key] = item
        else:
            modes[str(index)] = deepcopy(entry)
    return modes


def _export_token(token: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    external = token["external_ids"]
    external_id = (
        external.get(NAMESPACE_V14) or external.get(NAMESPACE_V13) or external.get(NAMESPACE_UUID)
    )
    if external_id:
        out["_id"] = external_id
    position = token["position"]
    size = token["size"]
    out["x"] = position["x"]
    out["y"] = position["y"]
    out["elevation"] = position["elevation"]
    out["width"] = size["width"]
    out["height"] = size["height"]
    out["rotation"] = token["rotation"]
    out["alpha"] = token["alpha"]
    out["hidden"] = token["hidden"]
    out["locked"] = token["locked"]
    out["disposition"] = token["disposition"]

    extensions = token["extensions"]
    texture = _export_media(token["texture"], extensions.get("texture"))
    if texture is not None:
        out["texture"] = texture

    actor_ref = token["actor_ref"]
    if (
        isinstance(actor_ref, Mapping)
        and actor_ref.get("source_kind") == "foundry"
        and actor_ref.get("id")
    ):
        out["actorId"] = actor_ref["id"]

    bars = token["bars"]
    for key in ("bar1", "bar2"):
        if key in bars:
            out[key] = deepcopy(bars[key])
    display = token["display"]
    if "name" in display:
        out["displayName"] = display["name"]
    if "bars" in display:
        out["displayBars"] = display["bars"]
    for key in ("ring", "sight", "light", "delta"):
        if token[key]:
            out[key] = deepcopy(token[key])
    if token["flags"]:
        out["flags"] = deepcopy(token["flags"])
    if token["detection_modes"]:
        out["detectionModes"] = _export_detection_modes(token["detection_modes"])

    for key, value in extensions.items():
        if key in out:
            continue
        out[key] = deepcopy(value)
    return out


def _export_opaque_placeable(document: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    external = document.get("external_ids") or {}
    external_id = (
        external.get(NAMESPACE_V14) or external.get(NAMESPACE_V13) or external.get(NAMESPACE_UUID)
    )
    if external_id:
        out["_id"] = external_id
    for key, value in document.items():
        if key in {"canonical_id", "external_ids", "extensions", "provenance"}:
            continue
        out[key] = deepcopy(value)
    for key, value in (document.get("extensions") or {}).items():
        out.setdefault(key, deepcopy(value))
    return out


def export_scene(scene_document: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one TacticalSceneDocument into a FVTT v14 scene JSON object.

    Supported fields round-trip: ``_id`` is restored from ``external_ids`` and
    v14-only nesting (levels, environment, fog) is rebuilt. Unknown FVTT fields
    that were preserved in ``extensions`` are re-emitted. Media lives in
    ``levels`` when the TacticalSceneDocument has levels; a TacticalSceneDocument without levels
    falls back to the documented v13 top-level ``background``/``foreground``
    fields so the media is not silently dropped. The invalid document fails
    closed with ``foundry_scene_malformed``.
    """
    try:
        scene = normalize_tactical_scene_payload(scene_document)[0]
    except TacticalSceneContractError as err:
        raise FoundrySceneError(
            CODE_SCENE_MALFORMED,
            f"scene document is invalid: {err.reason}: {err.detail}",
        ) from err

    extensions = scene["extensions"]
    out: dict[str, Any] = {}
    external = scene["external_ids"]
    external_id = (
        external.get(NAMESPACE_V14) or external.get(NAMESPACE_V13) or external.get(NAMESPACE_UUID)
    )
    if external_id:
        out["_id"] = external_id
    out["name"] = scene["display_name"]
    if scene["navigation_name"]:
        out["navName"] = scene["navigation_name"]

    dimensions = scene["dimensions"]
    view = dimensions["initial_view"]
    out["width"] = dimensions["width"]
    out["height"] = dimensions["height"]
    out["padding"] = dimensions["padding"]
    out["shiftX"] = dimensions["offset_x"]
    out["shiftY"] = dimensions["offset_y"]
    initial: dict[str, Any] = {"x": view["x"], "y": view["y"], "scale": view["zoom"]}
    _merge_residual(initial, extensions.get("initial"))
    out["initial"] = initial

    out["grid"] = _export_grid(scene["grid"], extensions.get("grid"))
    environment = _export_environment(scene, extensions.get("environment"))
    if environment is not None:
        out["environment"] = environment
    fog = _export_fog(extensions.get("fog"))
    if fog is not None:
        out["fog"] = fog
    if scene["environment"]["weather"]:
        out["weather"] = scene["environment"]["weather"]
    out["sort"] = scene["sort"]
    if scene["thumbnail"] is not None:
        out["thumb"] = scene["thumbnail"]["source_path"]
    if scene["ownership"]:
        out["ownership"] = deepcopy(scene["ownership"])
    if scene["flags"]:
        out["flags"] = deepcopy(scene["flags"])

    _export_levels(scene, extensions, out)

    placeables = scene["placeables"]
    tokens = [_export_token(token) for token in placeables["tokens"].values()]
    if tokens:
        out["tokens"] = tokens
    for collection in PLACEABLE_COLLECTIONS[1:]:
        documents = list(placeables[collection].values())
        if documents:
            exporter = _PLACEABLE_EXPORTERS.get(collection, _export_opaque_placeable)
            out[collection] = [exporter(doc) for doc in documents]

    for key, value in extensions.items():
        if key in _EXPORT_RESERVED_KEYS or key in out:
            continue
        out[key] = deepcopy(value)
    return out


__all__ = [
    "CODE_SCENE_MALFORMED",
    "CODE_VERSION_UNSUPPORTED",
    "FoundrySceneError",
    "NAMESPACE_UUID",
    "NAMESPACE_V13",
    "NAMESPACE_V14",
    "PLACEABLE_COLLECTIONS",
    "detect_foundry_version",
    "export_scene",
    "import_scene",
]