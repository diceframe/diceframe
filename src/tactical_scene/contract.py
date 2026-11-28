"""Typed Scene / Token / placeable contract and validation (B1).

Frozen source of truth: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §6.

This module validates persisted plain-dict payloads. It never resolves
references, never executes scripts and never turns a display name into
identity. Envelope documents (scene, token) preserve unknown top-level fields
under ``extensions``. Structures this contract cannot classify fail closed with
a stable ``TacticalSceneContractError.reason``.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import math
import re
from typing import Any, Final, NoReturn, TypedDict

TACTICAL_SCENE_SCHEMA_VERSION: Final = 1
TACTICAL_SCENE_KIND: Final = "scene"
TOKEN_KIND: Final = "token"
WALL_KIND: Final = "wall"
LIGHT_KIND: Final = "light"
NOTE_KIND: Final = "note"

GRID_TYPES: Final = ("square", "gridless", "hex")
OWNERSHIP_LEVELS: Final = (0, 1, 2, 3)
PLACEABLE_COLLECTIONS: Final = (
    "tokens", "tiles", "walls", "lights", "sounds",
    "notes", "drawings", "templates", "regions",
)
#: Collections whose documents have a typed contract. The others keep only the
#: shared envelope and stay opaque to projections and renderers.
TYPED_PLACEABLE_COLLECTIONS: Final = ("tokens", "walls", "lights", "notes")

# FVTT wall vocabularies are stored as their original integers. Their meaning
# (which value is a secret door, which restriction is "limited") is display
# data in this phase: nothing interprets them until a ruleset capability does,
# and the enumerations must be confirmed against real samples before that.
WALL_DIRECTION_MAX: Final = 2
WALL_DOOR_MAX: Final = 2
WALL_DOOR_STATE_MAX: Final = 2
WALL_RESTRICTION_MAX: Final = 3

# Same canonical identity grammar as WorldState v1: no whitespace, Unicode
# display names or path separators.
_CANONICAL_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
_SOURCE_KIND: Final = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_COLOR: Final = re.compile(r"#[0-9a-fA-F]{6}")

# Stable rejection reasons. Callers may branch on ``reason``; the user-facing
# message is not part of the contract.
REASON_NOT_OBJECT: Final = "not_an_object"
REASON_MISSING_FIELD: Final = "missing_field"
REASON_INVALID_TYPE: Final = "invalid_type"
REASON_INVALID_KIND: Final = "invalid_kind"
REASON_INVALID_CANONICAL_ID: Final = "invalid_canonical_id"
REASON_UNSUPPORTED_SCHEMA: Final = "unsupported_schema_version"
REASON_UNKNOWN_FIELD: Final = "unknown_field"
REASON_EXTENSION_CONFLICT: Final = "extension_conflict"
REASON_INVALID_ASSET_REF: Final = "invalid_asset_ref"
REASON_INVALID_CONTENT_REF: Final = "invalid_content_ref"
REASON_INVALID_OWNERSHIP: Final = "invalid_ownership"
REASON_INVALID_VALUE: Final = "invalid_value"

_INVALID_PAYLOAD_CODE: Final = "tactical_scene_invalid_payload"
_UNSUPPORTED_SCHEMA_CODE: Final = "tactical_scene_unsupported_schema"


class TacticalSceneContractError(ValueError):
    """A scene/token payload cannot be safely interpreted: fail closed."""

    def __init__(self, reason: str, detail: str, *, code: str = _INVALID_PAYLOAD_CODE) -> None:
        self.reason = reason
        self.code = code
        self.detail = detail
        super().__init__(detail)


# ---- typed shapes ---------------------------------------------------------


class AssetRef(TypedDict):
    asset_id: str
    digest: str
    source_path: str
    mime: str


class ContentRef(TypedDict):
    source_kind: str
    source_id: str
    kind: str
    id: str
    digest: str


class TokenPosition(TypedDict):
    x: float
    y: float
    elevation: float
    level: int


class TokenSize(TypedDict):
    width: float
    height: float


class TokenDocument(TypedDict):
    schema_version: int
    kind: str
    canonical_id: str
    actor_ref: ContentRef | None
    position: TokenPosition
    size: TokenSize
    rotation: float
    texture: AssetRef | None
    ring: dict[str, Any]
    alpha: float
    display: dict[str, Any]
    disposition: int
    hidden: bool
    locked: bool
    sight: dict[str, Any]
    detection_modes: list[Any]
    light: dict[str, Any]
    bars: dict[str, Any]
    delta: dict[str, Any]
    external_ids: dict[str, str]
    flags: dict[str, dict[str, Any]]
    extensions: dict[str, Any]
    provenance: dict[str, Any]


class Point(TypedDict):
    x: float
    y: float


class WallEndpoints(TypedDict):
    x0: float
    y0: float
    x1: float
    y1: float


class WallRestrictions(TypedDict):
    move: int
    sight: int
    sound: int
    light: int


class WallDocument(TypedDict):
    schema_version: int
    kind: str
    canonical_id: str
    endpoints: WallEndpoints
    direction: int
    door: int
    door_state: int
    restrictions: WallRestrictions
    external_ids: dict[str, str]
    flags: dict[str, dict[str, Any]]
    extensions: dict[str, Any]
    provenance: dict[str, Any]


class LightRadius(TypedDict):
    dim: float
    bright: float


class LightDocument(TypedDict):
    schema_version: int
    kind: str
    canonical_id: str
    position: Point
    rotation: float
    elevation: float
    radius: LightRadius
    color: str
    intensity: float
    angle: float
    hidden: bool
    provides_vision: bool
    blocked_by_walls: bool
    external_ids: dict[str, str]
    flags: dict[str, dict[str, Any]]
    extensions: dict[str, Any]
    provenance: dict[str, Any]


class NoteJournalRef(TypedDict):
    entry_id: str | None
    page_id: str | None


class NoteDocument(TypedDict):
    schema_version: int
    kind: str
    canonical_id: str
    position: Point
    journal: NoteJournalRef
    text: str
    text_color: str
    font_size: float
    icon_size: float
    texture: AssetRef | None
    elevation: float
    visible_globally: bool
    external_ids: dict[str, str]
    flags: dict[str, dict[str, Any]]
    extensions: dict[str, Any]
    provenance: dict[str, Any]


class TacticalSceneInitialView(TypedDict):
    x: float
    y: float
    zoom: float


class TacticalSceneDimensions(TypedDict):
    width: int
    height: int
    padding: float
    offset_x: int
    offset_y: int
    initial_view: TacticalSceneInitialView


class TacticalSceneGrid(TypedDict):
    type: str
    size: int
    scale: float
    units: str
    color: str
    alpha: float
    distance: float


class TacticalSceneFog(TypedDict):
    exploration: bool
    reset_policy: str


class TacticalSceneEnvironment(TypedDict):
    darkness: float
    global_illumination: bool
    fog: TacticalSceneFog
    weather: str


class TacticalScenePlaceables(TypedDict):
    tokens: dict[str, TokenDocument]
    tiles: dict[str, dict[str, Any]]
    walls: dict[str, WallDocument]
    lights: dict[str, LightDocument]
    sounds: dict[str, dict[str, Any]]
    notes: dict[str, NoteDocument]
    drawings: dict[str, dict[str, Any]]
    templates: dict[str, dict[str, Any]]
    regions: dict[str, dict[str, Any]]


class TacticalSceneDocument(TypedDict):
    schema_version: int
    kind: str
    canonical_id: str
    display_name: str
    navigation_name: str
    source_ref: ContentRef | None
    external_ids: dict[str, str]
    ownership: dict[str, int]
    sort: int
    background: AssetRef | None
    foreground: AssetRef | None
    thumbnail: AssetRef | None
    dimensions: TacticalSceneDimensions
    grid: TacticalSceneGrid
    environment: TacticalSceneEnvironment
    placeables: TacticalScenePlaceables
    levels: list[dict[str, Any]]
    flags: dict[str, dict[str, Any]]
    extensions: dict[str, Any]
    provenance: dict[str, Any]
    revision: int


# ---- validation report ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class MissingReference:
    """One reference slot kept empty instead of guessed or auto-created."""

    kind: str  # "actor_ref" | "asset_ref"
    path: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "path": self.path}


@dataclass(frozen=True, slots=True)
class TacticalSceneValidationReport:
    unknown_fields: tuple[str, ...] = ()
    degraded: tuple[str, ...] = ()
    missing_references: tuple[MissingReference, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "unknown_fields": list(self.unknown_fields),
            "degraded": list(self.degraded),
            "missing_references": [ref.to_dict() for ref in self.missing_references],
        }


@dataclass
class _Report:
    unknown_fields: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    missing_references: list[MissingReference] = field(default_factory=list)

    def freeze(self) -> TacticalSceneValidationReport:
        return TacticalSceneValidationReport(
            unknown_fields=tuple(self.unknown_fields),
            degraded=tuple(self.degraded),
            missing_references=tuple(self.missing_references),
        )


# ---- small validators -----------------------------------------------------


def _fail(
    reason: str, detail: str, *, code: str = _INVALID_PAYLOAD_CODE,
) -> NoReturn:
    raise TacticalSceneContractError(reason, detail, code=code)


def _opt(raw: Mapping[str, Any], key: str, default: Any) -> Any:
    return raw[key] if key in raw else default


def _field(raw: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in raw:
        _fail(REASON_MISSING_FIELD, f"{path} is required")
    return raw[key]


def _as_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(REASON_INVALID_TYPE, f"{path} must be an object")
    return value


def _as_str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        _fail(REASON_INVALID_TYPE, f"{path} must be a string")
    return value


def _as_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(REASON_INVALID_TYPE, f"{path} must be a boolean")
    return value


def _as_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(REASON_INVALID_TYPE, f"{path} must be an integer")
    if minimum is not None and value < minimum:
        _fail(REASON_INVALID_VALUE, f"{path} must be >= {minimum}")
    return value


def _as_float(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(REASON_INVALID_TYPE, f"{path} must be a number")
    return float(value)


def _as_finite_float(value: Any, path: str) -> float:
    number = _as_float(value, path)
    if not math.isfinite(number):
        _fail(REASON_INVALID_VALUE, f"{path} must be a finite number")
    return number


def _as_non_negative_float(value: Any, path: str) -> float:
    number = _as_finite_float(value, path)
    if number < 0:
        _fail(REASON_INVALID_VALUE, f"{path} must be >= 0")
    return number


def _as_unit_float(value: Any, path: str) -> float:
    number = _as_finite_float(value, path)
    if not 0.0 <= number <= 1.0:
        _fail(REASON_INVALID_VALUE, f"{path} must be between 0 and 1")
    return number


def _as_bounded_float(value: Any, path: str, *, maximum: float) -> float:
    number = _as_non_negative_float(value, path)
    if number > maximum:
        _fail(REASON_INVALID_VALUE, f"{path} must be <= {maximum}")
    return number


def _as_bounded_int(value: Any, path: str, *, maximum: int) -> int:
    number = _as_int(value, path, minimum=0)
    if number > maximum:
        _fail(REASON_INVALID_VALUE, f"{path} must be <= {maximum}")
    return number


def _as_color(value: Any, path: str) -> str:
    text = _as_str(value, path)
    if text and _COLOR.fullmatch(text) is None:
        _fail(REASON_INVALID_VALUE, f"{path} must be '#rrggbb' or an empty string")
    return text


def _as_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(REASON_INVALID_TYPE, f"{path} must be a list")
    return deepcopy(value)


def _as_opaque_dict(value: Any, path: str) -> dict[str, Any]:
    """Copy an opaque object; generic code never interprets its contents."""
    mapping = _as_mapping(value, path)
    result: dict[str, Any] = {}
    for key, child in mapping.items():
        if not isinstance(key, str):
            _fail(REASON_INVALID_TYPE, f"{path} keys must be strings")
        result[key] = deepcopy(child)
    return result


def _as_str_map(value: Any, path: str) -> dict[str, str]:
    mapping = _as_mapping(value, path)
    result: dict[str, str] = {}
    for key, child in mapping.items():
        if not isinstance(key, str) or not isinstance(child, str):
            _fail(REASON_INVALID_TYPE, f"{path} must map strings to strings")
        result[key] = child
    return result


def _as_flags(value: Any, path: str) -> dict[str, dict[str, Any]]:
    mapping = _as_mapping(value, path)
    result: dict[str, dict[str, Any]] = {}
    for key, child in mapping.items():
        if not isinstance(key, str) or not isinstance(child, Mapping):
            _fail(REASON_INVALID_TYPE, f"{path} must map namespaces to objects")
        result[key] = deepcopy(dict(child))
    return result


def _as_ownership(value: Any, path: str) -> dict[str, int]:
    mapping = _as_mapping(value, path)
    result: dict[str, int] = {}
    for key, child in mapping.items():
        if not isinstance(key, str):
            _fail(REASON_INVALID_TYPE, f"{path} keys must be strings")
        if isinstance(child, bool) or not isinstance(child, int) or child not in OWNERSHIP_LEVELS:
            _fail(REASON_INVALID_OWNERSHIP, f"{path}[{key!r}] must be 0, 1, 2 or 3")
        result[key] = child
    return result


def _reject_unknown(raw: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
    extra = sorted(str(key) for key in raw if key not in allowed)
    if extra:
        _fail(REASON_UNKNOWN_FIELD, f"{path} has unknown field {extra[0]!r}")


def _schema_version(raw: Mapping[str, Any], path: str) -> int:
    value = _field(raw, "schema_version", f"{path}.schema_version")
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(REASON_INVALID_TYPE, f"{path}.schema_version must be an integer")
    if value != TACTICAL_SCENE_SCHEMA_VERSION:
        _fail(
            REASON_UNSUPPORTED_SCHEMA,
            f"{path}.schema_version {value!r} is not supported",
            code=_UNSUPPORTED_SCHEMA_CODE,
        )
    return value


def _expect_kind(raw: Mapping[str, Any], expected: str, path: str) -> None:
    kind = _field(raw, "kind", f"{path}.kind")
    if not isinstance(kind, str):
        _fail(REASON_INVALID_KIND, f"{path}.kind must be a string")
    if kind != expected:
        _fail(REASON_INVALID_KIND, f"{path}.kind must be {expected!r}")


def is_canonical_id(value: Any) -> bool:
    """Return whether ``value`` is a valid persisted scene identity."""
    return isinstance(value, str) and _CANONICAL_ID.fullmatch(value) is not None


def _canonical_id(value: Any, path: str) -> str:
    if not is_canonical_id(value):
        _fail(REASON_INVALID_CANONICAL_ID, f"{path} is not a canonical id: {value!r}")
    return str(value)


# ---- asset / content references ------------------------------------------

_ASSET_REF_FIELDS: Final = frozenset({"asset_id", "digest", "source_path", "mime"})
_CONTENT_REF_FIELDS: Final = frozenset({"source_kind", "source_id", "kind", "id", "digest"})


def _asset_ref(raw: Any, path: str) -> AssetRef:
    mapping = _as_mapping(raw, path)
    _reject_unknown(mapping, _ASSET_REF_FIELDS, path)
    result: dict[str, str] = {}
    for key in ("asset_id", "digest", "source_path", "mime"):
        value = _field(mapping, key, f"{path}.{key}")
        if not isinstance(value, str):
            _fail(REASON_INVALID_ASSET_REF, f"{path}.{key} must be a string")
        result[key] = value
    if not result["asset_id"].strip():
        _fail(REASON_INVALID_ASSET_REF, f"{path}.asset_id must not be empty")
    if result["source_path"].startswith("data:"):
        _fail(REASON_INVALID_ASSET_REF, f"{path}.source_path must reference an asset, not inline data")
    return AssetRef(
        asset_id=result["asset_id"],
        digest=result["digest"],
        source_path=result["source_path"],
        mime=result["mime"],
    )


def _optional_asset_ref(raw: Any, path: str) -> AssetRef | None:
    return None if raw is None else _asset_ref(raw, path)


def _content_ref(raw: Any, path: str) -> ContentRef:
    mapping = _as_mapping(raw, path)
    _reject_unknown(mapping, _CONTENT_REF_FIELDS, path)
    parts: dict[str, str] = {}
    for key in ("source_kind", "source_id", "kind", "id"):
        value = _field(mapping, key, f"{path}.{key}")
        if not isinstance(value, str) or not value.strip():
            _fail(REASON_INVALID_CONTENT_REF, f"{path}.{key} must be a non-empty string")
        parts[key] = value
    digest = _field(mapping, "digest", f"{path}.digest") if "digest" in mapping else ""
    if not isinstance(digest, str):
        _fail(REASON_INVALID_CONTENT_REF, f"{path}.digest must be a string")
    if _SOURCE_KIND.fullmatch(parts["source_kind"]) is None:
        _fail(REASON_INVALID_CONTENT_REF, f"{path}.source_kind is invalid: {parts['source_kind']!r}")
    return ContentRef(
        source_kind=parts["source_kind"],
        source_id=parts["source_id"],
        kind=parts["kind"],
        id=parts["id"],
        digest=digest,
    )


def _optional_content_ref(raw: Any, path: str) -> ContentRef | None:
    return None if raw is None else _content_ref(raw, path)


def validate_asset_ref(raw: Any, *, path: str = "asset_ref") -> AssetRef:
    """Validate one AssetRef without interpreting the referenced asset."""
    return _asset_ref(raw, path)


def validate_content_ref(raw: Any, *, path: str = "content_ref") -> ContentRef:
    """Validate the portable ContentRef shape; never resolve it."""
    return _content_ref(raw, path)


# ---- envelope extension policy ------------------------------------------


def _extensions_with_unknown(
    raw: Mapping[str, Any],
    known: frozenset[str],
    path: str,
    report: _Report,
) -> dict[str, Any]:
    """Copy ``extensions`` and preserve unclassifiable top-level fields there."""

    extensions = _as_opaque_dict(_opt(raw, "extensions", {}), f"{path}.extensions")
    for key, value in raw.items():
        if key in known:
            continue
        if not isinstance(key, str):
            _fail(REASON_INVALID_TYPE, f"{path} field names must be strings")
        if key in extensions:
            if extensions[key] != value:
                _fail(
                    REASON_EXTENSION_CONFLICT,
                    f"{path}.extensions already defines {key!r}",
                )
            continue
        extensions[key] = deepcopy(value)
        report.unknown_fields.append(f"{path}.{key}")
    return extensions


# ---- scene sub-structures ------------------------------------------------

_DIMENSION_FIELDS: Final = frozenset({
    "width", "height", "padding", "offset_x", "offset_y", "initial_view",
})
_VIEW_FIELDS: Final = frozenset({"x", "y", "zoom"})
_GRID_FIELDS: Final = frozenset({"type", "size", "scale", "units", "color", "alpha", "distance"})
_ENVIRONMENT_FIELDS: Final = frozenset({"darkness", "global_illumination", "fog", "weather"})
_FOG_FIELDS: Final = frozenset({"exploration", "reset_policy"})
_POSITION_FIELDS: Final = frozenset({"x", "y", "elevation", "level"})
_SIZE_FIELDS: Final = frozenset({"width", "height"})


def _normalize_dimensions(raw: Any, path: str) -> TacticalSceneDimensions:
    mapping = _as_mapping(raw, path)
    _reject_unknown(mapping, _DIMENSION_FIELDS, path)
    view = _as_mapping(_opt(mapping, "initial_view", {}), f"{path}.initial_view")
    _reject_unknown(view, _VIEW_FIELDS, f"{path}.initial_view")
    return TacticalSceneDimensions(
        width=_as_int(_opt(mapping, "width", 0), f"{path}.width", minimum=0),
        height=_as_int(_opt(mapping, "height", 0), f"{path}.height", minimum=0),
        padding=_as_float(_opt(mapping, "padding", 0.0), f"{path}.padding"),
        offset_x=_as_int(_opt(mapping, "offset_x", 0), f"{path}.offset_x"),
        offset_y=_as_int(_opt(mapping, "offset_y", 0), f"{path}.offset_y"),
        initial_view=TacticalSceneInitialView(
            x=_as_float(_opt(view, "x", 0.0), f"{path}.initial_view.x"),
            y=_as_float(_opt(view, "y", 0.0), f"{path}.initial_view.y"),
            zoom=_as_float(_opt(view, "zoom", 1.0), f"{path}.initial_view.zoom"),
        ),
    )


def _normalize_grid(raw: Any, path: str, report: _Report) -> TacticalSceneGrid:
    mapping = _as_mapping(raw, path)
    _reject_unknown(mapping, _GRID_FIELDS, path)
    grid_type = _as_str(_opt(mapping, "type", "gridless"), f"{path}.type")
    if grid_type not in GRID_TYPES:
        # Unknown projection is saved and reported, never guessed into mechanics.
        report.degraded.append(f"{path}.type:{grid_type}")
    return TacticalSceneGrid(
        type=grid_type,
        size=_as_int(_opt(mapping, "size", 0), f"{path}.size", minimum=0),
        scale=_as_float(_opt(mapping, "scale", 1.0), f"{path}.scale"),
        units=_as_str(_opt(mapping, "units", ""), f"{path}.units"),
        color=_as_str(_opt(mapping, "color", ""), f"{path}.color"),
        alpha=_as_float(_opt(mapping, "alpha", 0.0), f"{path}.alpha"),
        distance=_as_float(_opt(mapping, "distance", 0.0), f"{path}.distance"),
    )


def _normalize_environment(raw: Any, path: str) -> TacticalSceneEnvironment:
    mapping = _as_mapping(raw, path)
    _reject_unknown(mapping, _ENVIRONMENT_FIELDS, path)
    fog = _as_mapping(_opt(mapping, "fog", {}), f"{path}.fog")
    _reject_unknown(fog, _FOG_FIELDS, f"{path}.fog")
    return TacticalSceneEnvironment(
        darkness=_as_float(_opt(mapping, "darkness", 0.0), f"{path}.darkness"),
        global_illumination=_as_bool(
            _opt(mapping, "global_illumination", False), f"{path}.global_illumination",
        ),
        fog=TacticalSceneFog(
            exploration=_as_bool(_opt(fog, "exploration", False), f"{path}.fog.exploration"),
            reset_policy=_as_str(_opt(fog, "reset_policy", ""), f"{path}.fog.reset_policy"),
        ),
        weather=_as_str(_opt(mapping, "weather", ""), f"{path}.weather"),
    )


# ---- token ----------------------------------------------------------------


def _normalize_position(raw: Any, path: str) -> TokenPosition:
    mapping = _as_mapping(raw, path)
    _reject_unknown(mapping, _POSITION_FIELDS, path)
    return TokenPosition(
        x=_as_float(_opt(mapping, "x", 0.0), f"{path}.x"),
        y=_as_float(_opt(mapping, "y", 0.0), f"{path}.y"),
        elevation=_as_float(_opt(mapping, "elevation", 0.0), f"{path}.elevation"),
        level=_as_int(_opt(mapping, "level", 0), f"{path}.level"),
    )


def _normalize_size(raw: Any, path: str) -> TokenSize:
    mapping = _as_mapping(raw, path)
    _reject_unknown(mapping, _SIZE_FIELDS, path)
    return TokenSize(
        width=_as_float(_opt(mapping, "width", 1.0), f"{path}.width"),
        height=_as_float(_opt(mapping, "height", 1.0), f"{path}.height"),
    )


_TOKEN_FIELDS: Final = frozenset({
    "schema_version", "kind", "canonical_id", "actor_ref", "position", "size",
    "rotation", "texture", "ring", "alpha", "display", "disposition", "hidden",
    "locked", "sight", "detection_modes", "light", "bars", "delta",
    "external_ids", "flags", "extensions", "provenance",
})


def _normalize_token(raw: Any, path: str, report: _Report) -> TokenDocument:
    mapping = _as_mapping(raw, path)
    _schema_version(mapping, path)
    _expect_kind(mapping, TOKEN_KIND, path)
    canonical = _canonical_id(_field(mapping, "canonical_id", f"{path}.canonical_id"), f"{path}.canonical_id")
    extensions = _extensions_with_unknown(mapping, _TOKEN_FIELDS, path, report)

    actor_ref = _optional_content_ref(_opt(mapping, "actor_ref", None), f"{path}.actor_ref")
    if actor_ref is None:
        # Missing links are reported and preserved; names are never matched and
        # no actor is created on behalf of a token.
        report.missing_references.append(MissingReference("actor_ref", f"{path}.actor_ref"))
    texture = _optional_asset_ref(_opt(mapping, "texture", None), f"{path}.texture")
    if texture is None:
        report.missing_references.append(MissingReference("asset_ref", f"{path}.texture"))

    return TokenDocument(
        schema_version=TACTICAL_SCENE_SCHEMA_VERSION,
        kind=TOKEN_KIND,
        canonical_id=canonical,
        actor_ref=actor_ref,
        position=_normalize_position(_opt(mapping, "position", {}), f"{path}.position"),
        size=_normalize_size(_opt(mapping, "size", {}), f"{path}.size"),
        rotation=_as_float(_opt(mapping, "rotation", 0.0), f"{path}.rotation"),
        texture=texture,
        ring=_as_opaque_dict(_opt(mapping, "ring", {}), f"{path}.ring"),
        alpha=_as_float(_opt(mapping, "alpha", 1.0), f"{path}.alpha"),
        display=_as_opaque_dict(_opt(mapping, "display", {}), f"{path}.display"),
        disposition=_as_int(_opt(mapping, "disposition", 0), f"{path}.disposition"),
        hidden=_as_bool(_opt(mapping, "hidden", False), f"{path}.hidden"),
        locked=_as_bool(_opt(mapping, "locked", False), f"{path}.locked"),
        sight=_as_opaque_dict(_opt(mapping, "sight", {}), f"{path}.sight"),
        detection_modes=_as_list(_opt(mapping, "detection_modes", []), f"{path}.detection_modes"),
        light=_as_opaque_dict(_opt(mapping, "light", {}), f"{path}.light"),
        bars=_as_opaque_dict(_opt(mapping, "bars", {}), f"{path}.bars"),
        delta=_as_opaque_dict(_opt(mapping, "delta", {}), f"{path}.delta"),
        external_ids=_as_str_map(_opt(mapping, "external_ids", {}), f"{path}.external_ids"),
        flags=_as_flags(_opt(mapping, "flags", {}), f"{path}.flags"),
        extensions=extensions,
        provenance=_as_opaque_dict(_opt(mapping, "provenance", {}), f"{path}.provenance"),
    )


# ---- typed placeables: wall, light, note -----------------------------------
#
# Workplan §6.12. Coordinates are canvas pixels. FVTT vocabularies keep their
# integer values; this contract validates the known range only and never
# interprets what a value means.

_POINT_FIELDS: Final = frozenset({"x", "y"})
_WALL_FIELDS: Final = frozenset({
    "schema_version", "kind", "canonical_id", "endpoints", "direction", "door",
    "door_state", "restrictions", "external_ids", "flags", "extensions",
    "provenance",
})
_WALL_ENDPOINT_FIELDS: Final = frozenset({"x0", "y0", "x1", "y1"})
_WALL_RESTRICTION_FIELDS: Final = frozenset({"move", "sight", "sound", "light"})
_LIGHT_FIELDS: Final = frozenset({
    "schema_version", "kind", "canonical_id", "position", "rotation", "elevation",
    "radius", "color", "intensity", "angle", "hidden", "provides_vision",
    "blocked_by_walls", "external_ids", "flags", "extensions", "provenance",
})
_LIGHT_RADIUS_FIELDS: Final = frozenset({"dim", "bright"})
_NOTE_FIELDS: Final = frozenset({
    "schema_version", "kind", "canonical_id", "position", "journal", "text",
    "text_color", "font_size", "icon_size", "texture", "elevation",
    "visible_globally", "external_ids", "flags", "extensions", "provenance",
})
_NOTE_JOURNAL_FIELDS: Final = frozenset({"entry_id", "page_id"})


def _normalize_point(raw: Any, path: str) -> Point:
    mapping = _as_mapping(raw, path)
    _reject_unknown(mapping, _POINT_FIELDS, path)
    return Point(
        x=_as_finite_float(_field(mapping, "x", f"{path}.x"), f"{path}.x"),
        y=_as_finite_float(_field(mapping, "y", f"{path}.y"), f"{path}.y"),
    )


def _normalize_wall(raw: Any, path: str, report: _Report) -> WallDocument:
    mapping = _as_mapping(raw, path)
    _schema_version(mapping, path)
    _expect_kind(mapping, WALL_KIND, path)
    canonical = _canonical_id(
        _field(mapping, "canonical_id", f"{path}.canonical_id"), f"{path}.canonical_id",
    )
    extensions = _extensions_with_unknown(mapping, _WALL_FIELDS, path, report)
    endpoints = _as_mapping(
        _field(mapping, "endpoints", f"{path}.endpoints"), f"{path}.endpoints",
    )
    _reject_unknown(endpoints, _WALL_ENDPOINT_FIELDS, f"{path}.endpoints")
    restrictions = _as_mapping(_opt(mapping, "restrictions", {}), f"{path}.restrictions")
    _reject_unknown(restrictions, _WALL_RESTRICTION_FIELDS, f"{path}.restrictions")
    return WallDocument(
        schema_version=TACTICAL_SCENE_SCHEMA_VERSION,
        kind=WALL_KIND,
        canonical_id=canonical,
        endpoints=WallEndpoints(
            x0=_as_finite_float(_field(endpoints, "x0", f"{path}.endpoints.x0"), f"{path}.endpoints.x0"),
            y0=_as_finite_float(_field(endpoints, "y0", f"{path}.endpoints.y0"), f"{path}.endpoints.y0"),
            x1=_as_finite_float(_field(endpoints, "x1", f"{path}.endpoints.x1"), f"{path}.endpoints.x1"),
            y1=_as_finite_float(_field(endpoints, "y1", f"{path}.endpoints.y1"), f"{path}.endpoints.y1"),
        ),
        direction=_as_bounded_int(
            _opt(mapping, "direction", 0), f"{path}.direction", maximum=WALL_DIRECTION_MAX,
        ),
        door=_as_bounded_int(_opt(mapping, "door", 0), f"{path}.door", maximum=WALL_DOOR_MAX),
        door_state=_as_bounded_int(
            _opt(mapping, "door_state", 0), f"{path}.door_state", maximum=WALL_DOOR_STATE_MAX,
        ),
        restrictions=WallRestrictions(
            move=_as_bounded_int(
                _opt(restrictions, "move", 0), f"{path}.restrictions.move",
                maximum=WALL_RESTRICTION_MAX,
            ),
            sight=_as_bounded_int(
                _opt(restrictions, "sight", 0), f"{path}.restrictions.sight",
                maximum=WALL_RESTRICTION_MAX,
            ),
            sound=_as_bounded_int(
                _opt(restrictions, "sound", 0), f"{path}.restrictions.sound",
                maximum=WALL_RESTRICTION_MAX,
            ),
            light=_as_bounded_int(
                _opt(restrictions, "light", 0), f"{path}.restrictions.light",
                maximum=WALL_RESTRICTION_MAX,
            ),
        ),
        external_ids=_as_str_map(_opt(mapping, "external_ids", {}), f"{path}.external_ids"),
        flags=_as_flags(_opt(mapping, "flags", {}), f"{path}.flags"),
        extensions=extensions,
        provenance=_as_opaque_dict(_opt(mapping, "provenance", {}), f"{path}.provenance"),
    )


def _normalize_light(raw: Any, path: str, report: _Report) -> LightDocument:
    mapping = _as_mapping(raw, path)
    _schema_version(mapping, path)
    _expect_kind(mapping, LIGHT_KIND, path)
    canonical = _canonical_id(
        _field(mapping, "canonical_id", f"{path}.canonical_id"), f"{path}.canonical_id",
    )
    extensions = _extensions_with_unknown(mapping, _LIGHT_FIELDS, path, report)
    radius = _as_mapping(_opt(mapping, "radius", {}), f"{path}.radius")
    _reject_unknown(radius, _LIGHT_RADIUS_FIELDS, f"{path}.radius")
    return LightDocument(
        schema_version=TACTICAL_SCENE_SCHEMA_VERSION,
        kind=LIGHT_KIND,
        canonical_id=canonical,
        position=_normalize_point(
            _field(mapping, "position", f"{path}.position"), f"{path}.position",
        ),
        rotation=_as_bounded_float(_opt(mapping, "rotation", 0.0), f"{path}.rotation", maximum=360.0),
        elevation=_as_finite_float(_opt(mapping, "elevation", 0.0), f"{path}.elevation"),
        radius=LightRadius(
            dim=_as_non_negative_float(_opt(radius, "dim", 0.0), f"{path}.radius.dim"),
            bright=_as_non_negative_float(_opt(radius, "bright", 0.0), f"{path}.radius.bright"),
        ),
        color=_as_color(_opt(mapping, "color", ""), f"{path}.color"),
        intensity=_as_unit_float(_opt(mapping, "intensity", 1.0), f"{path}.intensity"),
        angle=_as_bounded_float(_opt(mapping, "angle", 360.0), f"{path}.angle", maximum=360.0),
        hidden=_as_bool(_opt(mapping, "hidden", False), f"{path}.hidden"),
        provides_vision=_as_bool(_opt(mapping, "provides_vision", False), f"{path}.provides_vision"),
        blocked_by_walls=_as_bool(
            _opt(mapping, "blocked_by_walls", False), f"{path}.blocked_by_walls",
        ),
        external_ids=_as_str_map(_opt(mapping, "external_ids", {}), f"{path}.external_ids"),
        flags=_as_flags(_opt(mapping, "flags", {}), f"{path}.flags"),
        extensions=extensions,
        provenance=_as_opaque_dict(_opt(mapping, "provenance", {}), f"{path}.provenance"),
    )


def _normalize_note(raw: Any, path: str, report: _Report) -> NoteDocument:
    mapping = _as_mapping(raw, path)
    _schema_version(mapping, path)
    _expect_kind(mapping, NOTE_KIND, path)
    canonical = _canonical_id(
        _field(mapping, "canonical_id", f"{path}.canonical_id"), f"{path}.canonical_id",
    )
    extensions = _extensions_with_unknown(mapping, _NOTE_FIELDS, path, report)
    journal = _as_mapping(_opt(mapping, "journal", {}), f"{path}.journal")
    _reject_unknown(journal, _NOTE_JOURNAL_FIELDS, f"{path}.journal")

    def _journal_id(key: str) -> str | None:
        value = _opt(journal, key, None)
        if value is None:
            return None
        text = _as_str(value, f"{path}.journal.{key}")
        if not text:
            _fail(REASON_INVALID_VALUE, f"{path}.journal.{key} must not be empty")
        return text

    entry_id = _journal_id("entry_id")
    return NoteDocument(
        schema_version=TACTICAL_SCENE_SCHEMA_VERSION,
        kind=NOTE_KIND,
        canonical_id=canonical,
        position=_normalize_point(
            _field(mapping, "position", f"{path}.position"), f"{path}.position",
        ),
        journal=NoteJournalRef(entry_id=entry_id, page_id=_journal_id("page_id")),
        text=_as_str(_opt(mapping, "text", ""), f"{path}.text"),
        text_color=_as_str(_opt(mapping, "text_color", ""), f"{path}.text_color"),
        font_size=_as_non_negative_float(_opt(mapping, "font_size", 0.0), f"{path}.font_size"),
        icon_size=_as_non_negative_float(_opt(mapping, "icon_size", 0.0), f"{path}.icon_size"),
        texture=_optional_asset_ref(_opt(mapping, "texture", None), f"{path}.texture"),
        elevation=_as_finite_float(_opt(mapping, "elevation", 0.0), f"{path}.elevation"),
        visible_globally=_as_bool(
            _opt(mapping, "visible_globally", False), f"{path}.visible_globally",
        ),
        external_ids=_as_str_map(_opt(mapping, "external_ids", {}), f"{path}.external_ids"),
        flags=_as_flags(_opt(mapping, "flags", {}), f"{path}.flags"),
        extensions=extensions,
        provenance=_as_opaque_dict(_opt(mapping, "provenance", {}), f"{path}.provenance"),
    )


# ---- embedded placeables --------------------------------------------------


def _normalize_placeable(raw: Any, path: str) -> dict[str, Any]:
    """Validate the shared placeable envelope and keep type-specific fields.

    Tokens, walls, lights and notes have typed contracts. Tiles, sounds,
    drawings, templates and regions keep their type-specific fields opaque;
    they are never interpreted by generic code.
    """
    mapping = _as_mapping(raw, path)
    document = deepcopy(dict(mapping))
    canonical = _field(document, "canonical_id", f"{path}.canonical_id")
    document["canonical_id"] = _canonical_id(canonical, f"{path}.canonical_id")
    if "external_ids" in document:
        document["external_ids"] = _as_str_map(document["external_ids"], f"{path}.external_ids")
    else:
        document["external_ids"] = {}
    if "flags" in document:
        document["flags"] = _as_flags(document["flags"], f"{path}.flags")
    else:
        document["flags"] = {}
    if "extensions" in document:
        document["extensions"] = _as_opaque_dict(document["extensions"], f"{path}.extensions")
    else:
        document["extensions"] = {}
    if "provenance" in document:
        document["provenance"] = _as_opaque_dict(document["provenance"], f"{path}.provenance")
    else:
        document["provenance"] = {}
    return document


_PLACEABLE_NORMALIZERS: Final[dict[str, Any]] = {
    "tokens": _normalize_token,
    "walls": _normalize_wall,
    "lights": _normalize_light,
    "notes": _normalize_note,
}


def _normalize_placeables(raw: Any, path: str, report: _Report) -> TacticalScenePlaceables:
    mapping = _as_mapping(raw, path)
    _reject_unknown(mapping, frozenset(PLACEABLE_COLLECTIONS), path)
    result: dict[str, dict[str, Any]] = {}
    for collection in PLACEABLE_COLLECTIONS:
        entries = _as_mapping(_opt(mapping, collection, {}), f"{path}.{collection}")
        normalized: dict[str, Any] = {}
        for key, child in entries.items():
            if not isinstance(key, str):
                _fail(REASON_INVALID_TYPE, f"{path}.{collection} keys must be strings")
            entry_path = f"{path}.{collection}.{key}"
            normalizer = _PLACEABLE_NORMALIZERS.get(collection)
            if normalizer is not None:
                normalized[key] = normalizer(child, entry_path, report)
            else:
                normalized[key] = _normalize_placeable(child, entry_path)
        result[collection] = normalized
    return TacticalScenePlaceables(**result)  # type: ignore[typeddict-item]


# ---- scene ----------------------------------------------------------------

_TACTICAL_SCENE_FIELDS: Final = frozenset({
    "schema_version", "kind", "canonical_id", "display_name", "navigation_name",
    "source_ref", "external_ids", "ownership", "sort", "background",
    "foreground", "thumbnail", "dimensions", "grid", "environment",
    "placeables", "levels", "flags", "extensions", "provenance", "revision",
})


def _normalize_tactical_scene(raw: Any, report: _Report) -> TacticalSceneDocument:
    if not isinstance(raw, Mapping):
        _fail(REASON_NOT_OBJECT, "scene payload must be an object")
    _schema_version(raw, "scene")
    _expect_kind(raw, TACTICAL_SCENE_KIND, "scene")
    canonical = _canonical_id(_field(raw, "canonical_id", "scene.canonical_id"), "scene.canonical_id")
    extensions = _extensions_with_unknown(raw, _TACTICAL_SCENE_FIELDS, "scene", report)

    levels_raw = _as_list(_opt(raw, "levels", []), "scene.levels")
    levels: list[dict[str, Any]] = []
    for index, item in enumerate(levels_raw):
        if not isinstance(item, Mapping):
            _fail(REASON_INVALID_TYPE, f"scene.levels[{index}] must be an object")
        levels.append(deepcopy(dict(item)))

    return TacticalSceneDocument(
        schema_version=TACTICAL_SCENE_SCHEMA_VERSION,
        kind=TACTICAL_SCENE_KIND,
        canonical_id=canonical,
        display_name=_as_str(_opt(raw, "display_name", ""), "scene.display_name"),
        navigation_name=_as_str(_opt(raw, "navigation_name", ""), "scene.navigation_name"),
        source_ref=_optional_content_ref(_opt(raw, "source_ref", None), "scene.source_ref"),
        external_ids=_as_str_map(_opt(raw, "external_ids", {}), "scene.external_ids"),
        ownership=_as_ownership(_opt(raw, "ownership", {}), "scene.ownership"),
        sort=_as_int(_opt(raw, "sort", 0), "scene.sort"),
        background=_optional_asset_ref(_opt(raw, "background", None), "scene.background"),
        foreground=_optional_asset_ref(_opt(raw, "foreground", None), "scene.foreground"),
        thumbnail=_optional_asset_ref(_opt(raw, "thumbnail", None), "scene.thumbnail"),
        dimensions=_normalize_dimensions(_opt(raw, "dimensions", {}), "scene.dimensions"),
        grid=_normalize_grid(_opt(raw, "grid", {}), "scene.grid", report),
        environment=_normalize_environment(_opt(raw, "environment", {}), "scene.environment"),
        placeables=_normalize_placeables(_opt(raw, "placeables", {}), "scene.placeables", report),
        levels=levels,
        flags=_as_flags(_opt(raw, "flags", {}), "scene.flags"),
        extensions=extensions,
        provenance=_as_opaque_dict(_opt(raw, "provenance", {}), "scene.provenance"),
        revision=_as_int(_opt(raw, "revision", 0), "scene.revision", minimum=0),
    )


# ---- public entry points --------------------------------------------------


def normalize_tactical_scene_payload(raw: Any) -> tuple[TacticalSceneDocument, TacticalSceneValidationReport]:
    """Validate a scene payload and return the normalized document plus report."""
    report = _Report()
    document = _normalize_tactical_scene(raw, report)
    return document, report.freeze()


def validate_tactical_scene_payload(raw: Any) -> TacticalSceneDocument:
    """Validate one persisted TacticalSceneDocument; unknown shapes fail closed."""
    return normalize_tactical_scene_payload(raw)[0]


def normalize_token_payload(raw: Any) -> tuple[TokenDocument, TacticalSceneValidationReport]:
    """Validate a standalone token payload and return it plus report."""
    report = _Report()
    document = _normalize_token(raw, "token", report)
    return document, report.freeze()


def validate_token_payload(raw: Any) -> TokenDocument:
    """Validate one persisted TokenDocument; unknown shapes fail closed."""
    return normalize_token_payload(raw)[0]


__all__ = [
    "AssetRef",
    "ContentRef",
    "GRID_TYPES",
    "LIGHT_KIND",
    "LightDocument",
    "LightRadius",
    "MissingReference",
    "NOTE_KIND",
    "NoteDocument",
    "NoteJournalRef",
    "OWNERSHIP_LEVELS",
    "PLACEABLE_COLLECTIONS",
    "Point",
    "TACTICAL_SCENE_KIND",
    "TACTICAL_SCENE_SCHEMA_VERSION",
    "TYPED_PLACEABLE_COLLECTIONS",
    "TacticalSceneContractError",
    "TacticalSceneDimensions",
    "TacticalSceneDocument",
    "TacticalSceneEnvironment",
    "TacticalSceneGrid",
    "TacticalScenePlaceables",
    "TacticalSceneValidationReport",
    "TOKEN_KIND",
    "TokenDocument",
    "TokenPosition",
    "TokenSize",
    "WALL_DIRECTION_MAX",
    "WALL_DOOR_MAX",
    "WALL_DOOR_STATE_MAX",
    "WALL_KIND",
    "WALL_RESTRICTION_MAX",
    "WallDocument",
    "WallEndpoints",
    "WallRestrictions",
    "is_canonical_id",
    "normalize_tactical_scene_payload",
    "normalize_token_payload",
    "validate_asset_ref",
    "validate_content_ref",
    "validate_tactical_scene_payload",
    "validate_token_payload",
]