"""Standing portrait upload storage and validation (GAL track P8, ADR-0010 §7).

Full-body transparent portraits for characters. Unlike avatars (which are
force-fitted to an opaque 256×256 square), standing portraits keep the
original aspect ratio and **preserve the alpha channel** — transparency is
the core property of the asset. Storage is content-addressed, mirroring
avatars/scene images.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_WIDTH = 2048
MAX_HEIGHT = 4096
MIN_WIDTH = 64
MIN_HEIGHT = 128
ASSET_ID_RE = re.compile(r"^[a-f0-9]{64}$")


def _normalized_standing_portrait(raw: bytes) -> bytes:
    """Validate and re-encode one upload, preserving the alpha channel."""

    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "WEBP"}:
                raise ValueError("立绘仅支持 PNG 或 WebP（需保留透明背景）")
            width, height = source.size
            if width < MIN_WIDTH or height < MIN_HEIGHT:
                raise ValueError(f"立绘尺寸不能小于 {MIN_WIDTH}×{MIN_HEIGHT}")
            if width > MAX_WIDTH or height > MAX_HEIGHT or width * height > MAX_IMAGE_PIXELS:
                raise ValueError("立绘尺寸过大")
            image = ImageOps.exif_transpose(source)
            # 透明是立绘的核心属性：保留 alpha（RGBA），不做方形裁切或缩放。
            image = image.convert("RGBA")
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=90, method=6)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取该立绘图片") from exc
    payload = output.getvalue()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("立绘编码后超过 8 MB，请压缩后重试")
    return payload


def save_standing_portrait_upload(
    portraits_dir: Path | None, file_data: str, file_name: str = "",
) -> dict[str, Any]:
    if not file_data:
        return {"ok": False, "error": "未提供立绘文件"}
    if len(file_data) > (MAX_UPLOAD_BYTES * 4 // 3) + 32:
        return {"ok": False, "error": "立绘文件不能超过 8 MB"}
    try:
        raw = base64.b64decode(file_data, validate=True)
    except (ValueError, binascii.Error):
        return {"ok": False, "error": "立绘文件数据无效"}
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        return {"ok": False, "error": "立绘文件不能超过 8 MB"}
    try:
        payload = _normalized_standing_portrait(raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    asset_id = hashlib.sha256(payload).hexdigest()
    if portraits_dir is not None:
        path = portraits_dir / f"{asset_id}.webp"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            tmp_path = path.with_suffix(".webp.tmp")
            tmp_path.write_bytes(payload)
            tmp_path.replace(path)
    return {
        "ok": True,
        "standing_portrait": {"kind": "upload", "asset_id": asset_id},
        "file_name": Path(file_name).name,
    }


def standing_portrait_file(portraits_dir: Path, asset_id: str) -> Path | None:
    if not ASSET_ID_RE.fullmatch(str(asset_id or "")):
        return None
    path = portraits_dir / f"{asset_id}.webp"
    return path if path.is_file() else None


def validate_standing_portrait_ref(
    reference: Any, file_lookup: Callable[[str], Path | None],
) -> dict[str, str] | None:
    """Validate a ``{"kind": "upload", "asset_id"}`` reference, fail closed.

    ``file_lookup(asset_id)`` must return the stored file (avatars use the
    same boundary check); a missing asset rejects the reference.
    """

    if reference is None or reference == {}:
        return None
    if not isinstance(reference, dict):
        raise ValueError("立绘数据无效")
    kind = str(reference.get("kind") or "")
    if kind != "upload":
        raise ValueError("立绘类型无效")
    asset_id = str(reference.get("asset_id") or "")
    if not asset_id or file_lookup(asset_id) is None:
        raise ValueError("上传立绘不存在")
    return {"kind": "upload", "asset_id": asset_id}


class StandingPortraitService:
    """Content-addressed standing-portrait storage rooted at one directory."""

    def __init__(self, portraits_dir: Path) -> None:
        self._portraits_dir = portraits_dir

    def save_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        return save_standing_portrait_upload(self._portraits_dir, file_data, file_name)

    def preview_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        """Validate and predict the content identity without persisting."""
        return save_standing_portrait_upload(None, file_data, file_name)

    def file(self, asset_id: str) -> Path | None:
        return standing_portrait_file(self._portraits_dir, asset_id)

    def delete(self, asset_id: str) -> dict[str, Any]:
        if not ASSET_ID_RE.fullmatch(str(asset_id or "")):
            return {"ok": False, "error": "无效的立绘 ID"}
        path = self._portraits_dir / f"{asset_id}.webp"
        if not path.is_file():
            return {"ok": False, "error": "立绘不存在"}
        try:
            path.unlink()
        except OSError:
            return {"ok": False, "error": "删除立绘失败"}
        return {"ok": True}
