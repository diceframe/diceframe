"""Validated, content-addressed custom background images for location maps."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps, UnidentifiedImageError

from src.webui.map_presets import builtin_map_preset_by_asset

if TYPE_CHECKING:
    from src.webui.api import WebAPI


MAX_MAP_BACKGROUND_BYTES = 8 * 1024 * 1024
MAX_MAP_BACKGROUND_PIXELS = 24_000_000
MAX_MAP_BACKGROUND_EDGE = 2048
ASSET_ID_RE = re.compile(r"^[a-f0-9]{64}$")


def validate_map_background_selection(api: "WebAPI", selection: Any) -> dict[str, str]:
    """Normalize a persisted selection without accepting arbitrary URLs."""
    if not selection:
        return {"kind": "auto"}
    if not isinstance(selection, dict):
        raise ValueError("地图背景选择必须是对象")
    kind = str(selection.get("kind") or "").strip()
    if kind in {"auto", "none"}:
        return {"kind": kind}
    if kind == "builtin":
        asset_id = str(selection.get("id") or "").strip()
        if builtin_map_preset_by_asset(asset_id) is None:
            raise ValueError("未知的内置地图背景")
        return {"kind": "builtin", "id": asset_id}
    if kind == "upload":
        asset_id = str(selection.get("asset_id") or "").strip()
        if map_background_file(api, asset_id) is None:
            raise ValueError("上传的地图背景不存在")
        return {"kind": "upload", "asset_id": asset_id}
    if kind == "generated":
        asset_id = str(selection.get("asset_id") or "").strip()
        if api.generated_image_file(asset_id) is None:
            raise ValueError("生成的地图背景不存在")
        return {"kind": "generated", "asset_id": asset_id}
    if kind == "plugin":
        map_id = str(selection.get("map_id") or "").strip()
        if not map_id or len(map_id) > 256 or not map_id.startswith("plugin:"):
            raise ValueError("内容包地图引用无效")
        return {"kind": "plugin", "map_id": map_id}
    raise ValueError("不支持的地图背景选择")


def save_map_background_upload(api: "WebAPI", file_data: str, file_name: str = "") -> dict[str, Any]:
    if not file_data:
        return {"ok": False, "error": "未提供地图背景文件"}
    if len(file_data) > (MAX_MAP_BACKGROUND_BYTES * 4 // 3) + 32:
        return {"ok": False, "error": "地图背景不能超过 8 MB"}
    try:
        raw = base64.b64decode(file_data, validate=True)
    except (ValueError, binascii.Error):
        return {"ok": False, "error": "地图背景文件数据无效"}
    if not raw or len(raw) > MAX_MAP_BACKGROUND_BYTES:
        return {"ok": False, "error": "地图背景不能超过 8 MB"}
    try:
        payload = _normalized_map_background(raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    asset_id = hashlib.sha256(payload).hexdigest()
    path = api._map_backgrounds_dir / f"{asset_id}.webp"
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


def map_background_file(api: "WebAPI", asset_id: str) -> Path | None:
    if not ASSET_ID_RE.fullmatch(str(asset_id or "")):
        return None
    path = api._map_backgrounds_dir / f"{asset_id}.webp"
    return path if path.is_file() else None


def resolve_map_background_file(api: "WebAPI", selection: Any) -> Path | None:
    try:
        normalized = validate_map_background_selection(api, selection)
    except ValueError:
        return None
    if normalized["kind"] == "upload":
        return map_background_file(api, normalized["asset_id"])
    if normalized["kind"] == "generated":
        return api.generated_image_file(normalized["asset_id"])
    return None


def _normalized_map_background(raw: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("地图背景仅支持 PNG、JPEG 或 WebP")
            width, height = source.size
            if width < 320 or height < 180:
                raise ValueError("地图背景尺寸不能小于 320×180")
            if width > 8192 or height > 8192 or width * height > MAX_MAP_BACKGROUND_PIXELS:
                raise ValueError("地图背景尺寸过大")
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
        raise ValueError("无法读取地图背景") from exc
