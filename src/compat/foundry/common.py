"""Shared primitives for the Foundry scene adapter (scene-04 split).

Extracted from ``scene_adapter`` so that module stays under the size ratchet
(workplan §0.5 known-limitation 7). Nothing here interprets FVTT semantics:
these helpers only read, validate shapes and preserve what they do not map.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import mimetypes
from typing import Any

from .report import ConversionReport

NAMESPACE_V14 = "foundry_v14"
NAMESPACE_V13 = "foundry_v13"
NAMESPACE_UUID = "foundry_uuid"
CODE_VERSION_UNSUPPORTED = "foundry_version_unsupported"
CODE_SCENE_MALFORMED = "foundry_scene_malformed"
_ADAPTER_ID = "compat.foundry.scene_adapter@1"


class FoundrySceneError(ValueError):
    """A FVTT scene payload cannot be safely converted.

    ``code`` is one of the frozen error codes
    (``foundry_version_unsupported`` / ``foundry_scene_malformed``).
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# ---- primitive validation -------------------------------------------------


def _malformed(detail: str) -> Any:
    raise FoundrySceneError(CODE_SCENE_MALFORMED, detail)


def _unsupported(detail: str) -> Any:
    raise FoundrySceneError(CODE_VERSION_UNSUPPORTED, detail)


def _obj(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _malformed(f"{path} must be an object")
    return value


def _num(value: Any, path: str, default: float) -> float:
    if value is None:
        return float(default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _malformed(f"{path} must be a number")
    return float(value)


def _int(value: Any, path: str, default: int) -> int:
    if value is None:
        return int(default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _malformed(f"{path} must be an integer")
    return int(value)


def _bool(value: Any, path: str, default: bool) -> bool:
    if value is None:
        return bool(default)
    if not isinstance(value, bool):
        _malformed(f"{path} must be a boolean")
    return value


def _str(value: Any, path: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        _malformed(f"{path} must be a string")
    return value


def _flags(value: Any, path: str) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    mapping = _obj(value, path)
    result: dict[str, dict[str, Any]] = {}
    for key, child in mapping.items():
        if not isinstance(key, str) or not isinstance(child, Mapping):
            _malformed(f"{path} must map string namespaces to objects")
        result[key] = deepcopy(dict(child))
    return result


def _ownership(value: Any, path: str) -> dict[str, int]:
    if value is None:
        return {}
    mapping = _obj(value, path)
    result: dict[str, int] = {}
    for key, child in mapping.items():
        if not isinstance(key, str):
            _malformed(f"{path} keys must be strings")
        if isinstance(child, bool) or not isinstance(child, int) or child not in (0, 1, 2, 3):
            _malformed(f"{path}[{key!r}] must be one of 0, 1, 2, 3")
        result[key] = child
    return result


# ---- identity -------------------------------------------------------------


def _canonical_id(namespace: str, kind: str, key: str) -> str:
    digest = hashlib.sha256(f"{namespace}:{kind}:{key}".encode("utf-8")).hexdigest()[:20]
    return f"fvtt.{namespace}.{kind}.{digest}"


def _external_id(raw: Mapping[str, Any], path: str) -> str | None:
    value = raw.get("_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        _malformed(f"{path}._id must be a non-empty string")
    return value


def _asset_from_src(src: Any, *, path: str, report: ConversionReport) -> dict[str, str] | None:
    if src is None or (isinstance(src, str) and not src.strip()):
        report.missing("asset_ref", path)
        report.warn(f"{path}: empty media source; reference left empty (nothing created)")
        return None
    if not isinstance(src, str):
        _malformed(f"{path} must be a media path string")
    if src.startswith("data:"):
        report.missing("asset_ref", path, "<inline-data>")
        report.warn(f"{path}: inline data URI rejected; assets are never inlined")
        return None
    mime, _encoding = mimetypes.guess_type(src)
    report.warn(f"asset not fetched, source_path preserved: {src}")
    return {
        "asset_id": "foundry-asset-" + hashlib.sha256(src.encode("utf-8")).hexdigest()[:16],
        "digest": "",
        "source_path": src,
        "mime": mime or "",
    }


def _convert_texture(
    raw: Mapping[str, Any], *, path: str, report: ConversionReport,
) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    texture_raw = raw.get("texture")
    if texture_raw is None:
        return None, None
    texture = _obj(texture_raw, path)
    asset = _asset_from_src(texture.get("src"), path=f"{path}.src", report=report)
    leftover = {k: deepcopy(v) for k, v in texture.items() if k != "src"}
    return asset, leftover or None


def _merge_residual(target: dict[str, Any], residual: Any) -> None:
    if isinstance(residual, Mapping):
        for key, value in residual.items():
            target.setdefault(key, deepcopy(value))


def _export_media(
    value: Mapping[str, Any] | None, residual: Any,
) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    if value is not None:
        out["src"] = value["source_path"]
    _merge_residual(out, residual)
    return out or None

__all__ = [
    "CODE_SCENE_MALFORMED",
    "CODE_VERSION_UNSUPPORTED",
    "NAMESPACE_V13",
    "NAMESPACE_UUID",
    "NAMESPACE_V14",
    "FoundrySceneError",
]
