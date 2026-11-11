"""Validated, content-addressed custom background images for location maps."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps, UnidentifiedImageError

from src.webui.map_presets import builtin_map_preset_by_asset
from src.localization.messages import t

MAX_MAP_BACKGROUND_BYTES = 8 * 1024 * 1024
MAX_MAP_BACKGROUND_PIXELS = 24_000_000
MAX_MAP_BACKGROUND_EDGE = 2048
ASSET_ID_RE = re.compile(r"^[a-f0-9]{64}$")


def validate_map_background_selection(
    backgrounds_dir: Path,
    generated_image_file: Callable[[str], Path | None],
    selection: Any,
) -> dict[str, str]:
    """Normalize a persisted selection without accepting arbitrary URLs."""
    if not selection:
        return {"kind": "auto"}
    if not isinstance(selection, dict):
        raise ValueError(t("map_backgrounds.selection_not_object"))
    kind = str(selection.get("kind") or "").strip()
    if kind in {"auto", "none"}:
        return {"kind": kind}
    if kind == "builtin":
        asset_id = str(selection.get("id") or "").strip()
        if builtin_map_preset_by_asset(asset_id) is None:
            raise ValueError(t("map_backgrounds.unknown_builtin"))
        return {"kind": "builtin", "id": asset_id}
    if kind == "upload":
        asset_id = str(selection.get("asset_id") or "").strip()
        if map_background_file(backgrounds_dir, asset_id) is None:
            raise ValueError(t("map_backgrounds.uploaded_missing"))
        return {"kind": "upload", "asset_id": asset_id}
    if kind == "generated":
        asset_id = str(selection.get("asset_id") or "").strip()
        if generated_image_file(asset_id) is None:
            raise ValueError(t("map_backgrounds.generated_missing"))
        return {"kind": "generated", "asset_id": asset_id}
    if kind == "plugin":
        map_id = str(selection.get("map_id") or "").strip()
        if not map_id or len(map_id) > 256 or not map_id.startswith("plugin:"):
            raise ValueError(t("map_backgrounds.content_pack_reference_invalid"))
        return {"kind": "plugin", "map_id": map_id}
    raise ValueError(t("map_backgrounds.unsupported_selection"))


def save_map_background_upload(
    backgrounds_dir: Path, file_data: str, file_name: str = "",
) -> dict[str, Any]:
    if not file_data:
        return {"ok": False, "error": t("map_backgrounds.file_missing")}
    if len(file_data) > (MAX_MAP_BACKGROUND_BYTES * 4 // 3) + 32:
        return {"ok": False, "error": t("map_backgrounds.file_too_large")}
    try:
        raw = base64.b64decode(file_data, validate=True)
    except (ValueError, binascii.Error):
        return {"ok": False, "error": t("map_backgrounds.file_data_invalid")}
    if not raw or len(raw) > MAX_MAP_BACKGROUND_BYTES:
        return {"ok": False, "error": t("map_backgrounds.file_too_large")}
    try:
        payload = _normalized_map_background(raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    asset_id = hashlib.sha256(payload).hexdigest()
    path = backgrounds_dir / f"{asset_id}.webp"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".webp.tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
    return {
        "ok": True,
        "map_background": {"kind": "upload", "asset_id": asset_id},
        "file_name": Path(file_name).name,
    }


def map_background_file(backgrounds_dir: Path, asset_id: str) -> Path | None:
    if not ASSET_ID_RE.fullmatch(str(asset_id or "")):
        return None
    path = backgrounds_dir / f"{asset_id}.webp"
    return path if path.is_file() else None


def resolve_map_background_file(
    backgrounds_dir: Path,
    generated_image_file: Callable[[str], Path | None],
    selection: Any,
) -> Path | None:
    try:
        normalized = validate_map_background_selection(
            backgrounds_dir, generated_image_file, selection,
        )
    except ValueError:
        return None
    if normalized["kind"] == "upload":
        return map_background_file(backgrounds_dir, normalized["asset_id"])
    if normalized["kind"] == "generated":
        return generated_image_file(normalized["asset_id"])
    return None


def _normalized_map_background(raw: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError(t("map_backgrounds.unsupported_format"))
            width, height = source.size
            if width < 320 or height < 180:
                raise ValueError(t("map_backgrounds.too_small"))
            if width > 8192 or height > 8192 or width * height > MAX_MAP_BACKGROUND_PIXELS:
                raise ValueError(t("map_backgrounds.dimensions_too_large"))
            image = ImageOps.exif_transpose(source).convert("RGB")
            if max(image.size) > MAX_MAP_BACKGROUND_EDGE:
                image.thumbnail(
                    (MAX_MAP_BACKGROUND_EDGE, MAX_MAP_BACKGROUND_EDGE),
                    Image.Resampling.LANCZOS,
                )
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=84, method=6)
            return output.getvalue()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(t("map_backgrounds.unreadable")) from exc


class MapBackgroundService:
    """Validated background storage with one explicit generated-asset boundary."""

    def __init__(
        self,
        backgrounds_dir: Path,
        generated_image_file: Callable[[str], Path | None],
    ) -> None:
        self._backgrounds_dir = backgrounds_dir
        self._generated_image_file = generated_image_file

    def validate(self, selection: Any) -> dict[str, str]:
        return validate_map_background_selection(
            self._backgrounds_dir, self._generated_image_file, selection,
        )

    def save_upload(
        self, file_data: str, file_name: str = "",
    ) -> dict[str, Any]:
        return save_map_background_upload(
            self._backgrounds_dir, file_data, file_name,
        )

    def file(self, asset_id: str) -> Path | None:
        return map_background_file(self._backgrounds_dir, asset_id)

    def resolve_file(self, selection: Any) -> Path | None:
        return resolve_map_background_file(
            self._backgrounds_dir, self._generated_image_file, selection,
        )
