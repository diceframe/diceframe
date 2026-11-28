"""Typed FVTT wall / light / note conversion and export (scene-04).

Frozen contract: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §6.12. These three
collections get DiceFrame field names, like the token; every other FVTT field
is preserved under ``extensions`` and re-emitted on export. FVTT enumerations
are copied as their integers: a value outside the known range is never
interpreted, it stays in ``extensions`` and the typed field keeps the neutral 0.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
import re
from typing import Any

from src.tactical_scene.contract import (
    TACTICAL_SCENE_SCHEMA_VERSION,
    WALL_DIRECTION_MAX,
    WALL_DOOR_MAX,
    WALL_DOOR_STATE_MAX,
    WALL_RESTRICTION_MAX,
)

from .common import (
    NAMESPACE_V13,
    NAMESPACE_UUID,
    NAMESPACE_V14,
    _ADAPTER_ID,
    _bool,
    _canonical_id,
    _convert_texture,
    _export_media,
    _flags,
    _malformed,
    _merge_residual,
    _num,
    _obj,
    _str,
)
from .report import ConversionLevel, ConversionReport

# ---- import ----------------------------------------------------------------
#
# Workplan §6.12. These three collections get DiceFrame field names, like the
# token. Every other FVTT field is preserved under ``extensions`` and re-emitted
# on export. FVTT enumerations are copied as their integers. A value outside
# the known range is never interpreted: it stays in ``extensions`` and the
# typed field keeps the neutral 0.


_WALL_MAPPED_KEYS = frozenset({
    "_id", "c", "dir", "door", "ds", "move", "sight", "sound", "light", "flags",
})
_LIGHT_MAPPED_KEYS = frozenset({
    "_id", "x", "y", "rotation", "elevation", "hidden", "vision", "walls",
    "config", "flags",
})
_LIGHT_CONFIG_MAPPED_KEYS = frozenset({"dim", "bright", "alpha", "angle", "color"})
_NOTE_MAPPED_KEYS = frozenset({
    "_id", "x", "y", "entryId", "pageId", "text", "textColor", "fontSize",
    "iconSize", "texture", "elevation", "global", "flags",
})
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def _placeable_path_and_id(
    *,
    kind: str,
    collection: str,
    index: int,
    namespace: str,
    external_id: str | None,
    scene_key: str,
    report: ConversionReport,
) -> tuple[str, str]:
    """Return the payload path and canonical id of one typed placeable."""

    path = f"scene.{collection}[{index}]"
    if external_id is None:
        report.warn(f"{path}: placeable has no _id; canonical id derived from its array index")
    key = external_id or f"{scene_key}:{collection}:{index}"
    return path, _canonical_id(namespace, kind, key)


def _envelope_and_residual(
    raw: Mapping[str, Any],
    *,
    path: str,
    namespace: str,
    external_id: str | None,
    mapped_keys: frozenset[str],
    extensions: dict[str, Any],
    report: ConversionReport,
) -> dict[str, Any]:
    """Preserve every unmapped FVTT field and build the shared envelope."""

    for key_name, value in raw.items():
        if not isinstance(key_name, str):
            _malformed(f"{path} field names must be strings")
        if key_name in mapped_keys:
            continue
        extensions[key_name] = deepcopy(value)
        report.unknown(f"{path}.{key_name}")
    return {
        "external_ids": {namespace: external_id} if external_id else {},
        "flags": _flags(raw.get("flags"), f"{path}.flags"),
        "extensions": extensions,
        "provenance": {
            "source": namespace,
            "external_id": external_id or "",
            "adapter": _ADAPTER_ID,
        },
    }


def _wall_vocabulary(
    raw: Mapping[str, Any],
    key: str,
    path: str,
    *,
    maximum: int,
    extensions: dict[str, Any],
    report: ConversionReport,
) -> int:
    """Copy an FVTT wall integer, or keep an unknown value in ``extensions``."""

    value = raw.get(key)
    if value is None:
        return 0
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
        return value
    extensions[key] = deepcopy(value)
    report.add(
        ConversionLevel.DEGRADED_DISPLAY,
        f"{path}.{key}",
        f"FVTT wall {key} value {value!r} is outside the known range; preserved in extensions",
    )
    return 0


def _clamped_number(
    value: Any,
    path: str,
    default: float,
    *,
    maximum: float | None,
    report: ConversionReport,
) -> float:
    """Copy an FVTT number; clamp an out-of-range value and report it."""

    number = _num(value, path, default)
    clamped = min(max(number, 0.0), maximum if maximum is not None else math.inf)
    if clamped != number:
        report.add(
            ConversionLevel.DEGRADED_DISPLAY,
            path,
            f"FVTT value {number!r} is outside its range; clamped to {clamped!r}",
        )
    return clamped


def _convert_wall(
    raw: Mapping[str, Any],
    *,
    index: int,
    namespace: str,
    external_id: str | None,
    scene_key: str,
    report: ConversionReport,
) -> dict[str, Any]:
    path, canonical = _placeable_path_and_id(
        kind="wall", collection="walls", index=index, namespace=namespace,
        external_id=external_id, scene_key=scene_key, report=report,
    )
    extensions: dict[str, Any] = {}
    coordinates = raw.get("c")
    if coordinates is not None and (
        not isinstance(coordinates, (list, tuple)) or len(coordinates) != 4
        or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in coordinates
        )
    ):
        _malformed(f"{path}.c must be an array of four finite numbers")
    endpoints = (
        [float(value) for value in coordinates] if coordinates is not None
        else [0.0, 0.0, 0.0, 0.0]
    )
    vocabulary = {
        key: _wall_vocabulary(
            raw, key, path, maximum=maximum, extensions=extensions, report=report,
        )
        for key, maximum in (
            ("dir", WALL_DIRECTION_MAX), ("door", WALL_DOOR_MAX),
            ("ds", WALL_DOOR_STATE_MAX),
        )
    }
    restrictions = {
        key: _wall_vocabulary(
            raw, key, path, maximum=WALL_RESTRICTION_MAX, extensions=extensions,
            report=report,
        )
        for key in ("move", "sight", "sound", "light")
    }
    return {
        "schema_version": TACTICAL_SCENE_SCHEMA_VERSION,
        "kind": "wall",
        "canonical_id": canonical,
        "endpoints": {
            "x0": endpoints[0], "y0": endpoints[1], "x1": endpoints[2], "y1": endpoints[3],
        },
        "direction": vocabulary["dir"],
        "door": vocabulary["door"],
        "door_state": vocabulary["ds"],
        "restrictions": restrictions,
        **_envelope_and_residual(
            raw, path=path, namespace=namespace, external_id=external_id,
            mapped_keys=_WALL_MAPPED_KEYS, extensions=extensions, report=report,
        ),
    }


def _convert_light(
    raw: Mapping[str, Any],
    *,
    index: int,
    namespace: str,
    external_id: str | None,
    scene_key: str,
    report: ConversionReport,
) -> dict[str, Any]:
    path, canonical = _placeable_path_and_id(
        kind="light", collection="lights", index=index, namespace=namespace,
        external_id=external_id, scene_key=scene_key, report=report,
    )
    extensions: dict[str, Any] = {}
    config_raw = raw.get("config")
    config = _obj(config_raw, f"{path}.config") if config_raw is not None else {}
    color = ""
    raw_color = config.get("color")
    if raw_color is not None:
        if isinstance(raw_color, str) and _HEX_COLOR.fullmatch(raw_color):
            color = raw_color
        else:
            report.add(
                ConversionLevel.DEGRADED_DISPLAY,
                f"{path}.config.color",
                f"FVTT light color {raw_color!r} is not '#rrggbb'; preserved in extensions",
            )
    config_residual = {
        key: deepcopy(value) for key, value in config.items()
        if key not in _LIGHT_CONFIG_MAPPED_KEYS or (key == "color" and not color)
    }
    light_values = {
        "dim": _clamped_number(
            config.get("dim"), f"{path}.config.dim", 0.0, maximum=None, report=report,
        ),
        "bright": _clamped_number(
            config.get("bright"), f"{path}.config.bright", 0.0, maximum=None,
            report=report,
        ),
        "alpha": _clamped_number(
            config.get("alpha"), f"{path}.config.alpha", 1.0, maximum=1.0, report=report,
        ),
        "angle": _clamped_number(
            config.get("angle"), f"{path}.config.angle", 360.0, maximum=360.0,
            report=report,
        ),
    }
    # A clamped number is preserved verbatim so the export reproduces the source.
    for key, typed in light_values.items():
        if key in config and config[key] != typed:
            config_residual[key] = deepcopy(config[key])
    if config_residual:
        extensions["config"] = config_residual
    return {
        "schema_version": TACTICAL_SCENE_SCHEMA_VERSION,
        "kind": "light",
        "canonical_id": canonical,
        "position": {
            "x": _num(raw.get("x"), f"{path}.x", 0.0),
            "y": _num(raw.get("y"), f"{path}.y", 0.0),
        },
        "rotation": _clamped_number(
            raw.get("rotation"), f"{path}.rotation", 0.0, maximum=360.0, report=report,
        ),
        "elevation": _num(raw.get("elevation"), f"{path}.elevation", 0.0),
        "radius": {"dim": light_values["dim"], "bright": light_values["bright"]},
        "color": color,
        "intensity": light_values["alpha"],
        "angle": light_values["angle"],
        "hidden": _bool(raw.get("hidden"), f"{path}.hidden", False),
        "provides_vision": _bool(raw.get("vision"), f"{path}.vision", False),
        "blocked_by_walls": _bool(raw.get("walls"), f"{path}.walls", False),
        **_envelope_and_residual(
            raw, path=path, namespace=namespace, external_id=external_id,
            mapped_keys=_LIGHT_MAPPED_KEYS, extensions=extensions, report=report,
        ),
    }


def _external_document_id(value: Any, path: str) -> str | None:
    """Read an external document id: a non-empty string or null, never a guess."""

    if value is None:
        return None
    if not isinstance(value, str) or not value:
        _malformed(f"{path} must be a non-empty string or null")
    return value


def _convert_note(
    raw: Mapping[str, Any],
    *,
    index: int,
    namespace: str,
    external_id: str | None,
    scene_key: str,
    report: ConversionReport,
) -> dict[str, Any]:
    path, canonical = _placeable_path_and_id(
        kind="note", collection="notes", index=index, namespace=namespace,
        external_id=external_id, scene_key=scene_key, report=report,
    )
    extensions: dict[str, Any] = {}
    entry_id = _external_document_id(raw.get("entryId"), f"{path}.entryId")
    page_id = _external_document_id(raw.get("pageId"), f"{path}.pageId")
    if entry_id is not None:
        # The journal entry is not part of a scene import: reported, not resolved.
        report.missing("journal_ref", f"{path}.entryId", entry_id)
    texture, texture_residual = _convert_texture(raw, path=f"{path}.texture", report=report)
    if texture_residual:
        extensions["texture"] = texture_residual
    return {
        "schema_version": TACTICAL_SCENE_SCHEMA_VERSION,
        "kind": "note",
        "canonical_id": canonical,
        "position": {
            "x": _num(raw.get("x"), f"{path}.x", 0.0),
            "y": _num(raw.get("y"), f"{path}.y", 0.0),
        },
        "journal": {"entry_id": entry_id, "page_id": page_id},
        "text": _str(raw.get("text"), f"{path}.text", ""),
        "text_color": _str(raw.get("textColor"), f"{path}.textColor", ""),
        "font_size": _clamped_number(
            raw.get("fontSize"), f"{path}.fontSize", 0.0, maximum=None, report=report,
        ),
        "icon_size": _clamped_number(
            raw.get("iconSize"), f"{path}.iconSize", 0.0, maximum=None, report=report,
        ),
        "texture": texture,
        "elevation": _num(raw.get("elevation"), f"{path}.elevation", 0.0),
        "visible_globally": _bool(raw.get("global"), f"{path}.global", False),
        **_envelope_and_residual(
            raw, path=path, namespace=namespace, external_id=external_id,
            mapped_keys=_NOTE_MAPPED_KEYS, extensions=extensions, report=report,
        ),
    }


# The token converter stays in scene_adapter; this module owns the three
# collections it added type contracts for (scene-04).
_PLACEABLE_CONVERTERS = {
    "walls": _convert_wall,
    "lights": _convert_light,
    "notes": _convert_note,
}


def _external_id_of(document: Mapping[str, Any]) -> str | None:
    """The FVTT ``_id`` recorded in a placeable envelope, if any."""

    external = document.get("external_ids") or {}
    return (
        external.get(NAMESPACE_V14) or external.get(NAMESPACE_V13) or external.get(NAMESPACE_UUID)
    ) or None


def _export_wall(document: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    external_id = _external_id_of(document)
    if external_id:
        out["_id"] = external_id
    endpoints = document["endpoints"]
    restrictions = document["restrictions"]
    residual = document.get("extensions") or {}
    out["c"] = [endpoints["x0"], endpoints["y0"], endpoints["x1"], endpoints["y1"]]
    # A vocabulary value outside the known range round-trips verbatim.
    for key, typed in (
        ("dir", document["direction"]), ("door", document["door"]),
        ("ds", document["door_state"]), ("move", restrictions["move"]),
        ("sight", restrictions["sight"]), ("sound", restrictions["sound"]),
        ("light", restrictions["light"]),
    ):
        out[key] = deepcopy(residual[key]) if key in residual else typed
    if document["flags"]:
        out["flags"] = deepcopy(document["flags"])
    _merge_residual(out, residual)
    return out


def _export_light(document: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    external_id = _external_id_of(document)
    if external_id:
        out["_id"] = external_id
    position = document["position"]
    radius = document["radius"]
    residual = document.get("extensions") or {}
    out["x"] = position["x"]
    out["y"] = position["y"]
    out["rotation"] = document["rotation"]
    out["elevation"] = document["elevation"]
    out["hidden"] = document["hidden"]
    out["vision"] = document["provides_vision"]
    out["walls"] = document["blocked_by_walls"]
    config = deepcopy(dict(residual["config"])) if isinstance(residual.get("config"), Mapping) else {}
    config.setdefault("dim", radius["dim"])
    config.setdefault("bright", radius["bright"])
    config.setdefault("alpha", document["intensity"])
    config.setdefault("angle", document["angle"])
    if document["color"]:
        config.setdefault("color", document["color"])
    out["config"] = config
    if document["flags"]:
        out["flags"] = deepcopy(document["flags"])
    _merge_residual(out, residual)
    return out


def _export_note(document: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    external_id = _external_id_of(document)
    if external_id:
        out["_id"] = external_id
    position = document["position"]
    journal = document["journal"]
    residual = document.get("extensions") or {}
    out["x"] = position["x"]
    out["y"] = position["y"]
    out["elevation"] = document["elevation"]
    out["global"] = document["visible_globally"]
    if journal["entry_id"] is not None:
        out["entryId"] = journal["entry_id"]
    if journal["page_id"] is not None:
        out["pageId"] = journal["page_id"]
    out["text"] = document["text"]
    out["textColor"] = document["text_color"]
    out["fontSize"] = document["font_size"]
    out["iconSize"] = document["icon_size"]
    texture = _export_media(document["texture"], residual.get("texture"))
    if texture is not None:
        out["texture"] = texture
    if document["flags"]:
        out["flags"] = deepcopy(document["flags"])
    _merge_residual(out, residual)
    return out


_PLACEABLE_EXPORTERS = {
    "walls": _export_wall,
    "lights": _export_light,
    "notes": _export_note,
}

__all__ = [
    "_PLACEABLE_CONVERTERS",
    "_PLACEABLE_EXPORTERS",
]
