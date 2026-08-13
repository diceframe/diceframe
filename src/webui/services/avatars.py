"""Character portrait upload storage and validation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps, UnidentifiedImageError

if TYPE_CHECKING:
    from src.webui.api import WebAPI


MAX_UPLOAD_BYTES = 3 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
AVATAR_SIZE = 256
ASSET_ID_RE = re.compile(r"^[a-f0-9]{64}$")


def save_avatar_upload(api: "WebAPI", file_data: str, file_name: str = "") -> dict[str, Any]:
    if not file_data:
        return {"ok": False, "error": "未提供头像文件"}
    if len(file_data) > (MAX_UPLOAD_BYTES * 4 // 3) + 16:
        return {"ok": False, "error": "头像文件不能超过 3 MB"}
    try:
        raw = base64.b64decode(file_data, validate=True)
    except (ValueError, binascii.Error):
        return {"ok": False, "error": "头像文件数据无效"}
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        return {"ok": False, "error": "头像文件不能超过 3 MB"}

    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                return {"ok": False, "error": "头像仅支持 PNG、JPEG 或 WebP"}
            width, height = source.size
            if width < 32 or height < 32:
                return {"ok": False, "error": "头像尺寸不能小于 32×32"}
            if width > 4096 or height > 4096 or width * height > MAX_IMAGE_PIXELS:
                return {"ok": False, "error": "头像尺寸过大"}
            image = ImageOps.exif_transpose(source).convert("RGB")
            image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=88, method=6)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return {"ok": False, "error": "无法读取该头像图片"}

    payload = output.getvalue()
    asset_id = hashlib.sha256(payload).hexdigest()
    path = api._avatars_dir / f"{asset_id}.webp"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp_path = path.with_suffix(".webp.tmp")
        tmp_path.write_bytes(payload)
        tmp_path.replace(path)
    return {
        "ok": True,
        "portrait": {"kind": "upload", "asset_id": asset_id},
        "file_name": Path(file_name).name,
    }


def avatar_file(api: "WebAPI", asset_id: str) -> Path | None:
    if not ASSET_ID_RE.fullmatch(asset_id):
        return None
    path = api._avatars_dir / f"{asset_id}.webp"
    return path if path.is_file() else None


def list_user_avatars(api: "WebAPI") -> dict[str, Any]:
    """列出所有用户上传的头像（按 asset_id 排序）。"""
    avatars_dir = api._avatars_dir
    if not avatars_dir or not avatars_dir.is_dir():
        return {"avatars": [], "total": 0}
    items: list[dict[str, Any]] = []
    for path in sorted(avatars_dir.glob("*.webp")):
        asset_id = path.stem
        if not ASSET_ID_RE.fullmatch(asset_id):
            continue
        items.append({"asset_id": asset_id, "size_kb": round(path.stat().st_size / 1024, 1)})
    return {"avatars": items, "total": len(items)}


def delete_avatar(api: "WebAPI", asset_id: str) -> dict[str, Any]:
    """删除用户上传的头像文件。不检查引用，删后引用处显示占位。"""
    if not ASSET_ID_RE.fullmatch(asset_id):
        return {"ok": False, "error": "无效的头像 ID"}
    path = api._avatars_dir / f"{asset_id}.webp"
    if not path.is_file():
        return {"ok": False, "error": "头像不存在"}
    try:
        path.unlink()
    except OSError:
        return {"ok": False, "error": "删除头像失败"}
    return {"ok": True}
