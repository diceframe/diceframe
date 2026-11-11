"""Character portrait upload storage and validation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from src.localization.messages import t

MAX_UPLOAD_BYTES = 3 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
AVATAR_SIZE = 256
ASSET_ID_RE = re.compile(r"^[a-f0-9]{64}$")


def save_avatar_upload(
    avatars_dir: Path | None, file_data: str, file_name: str = "",
) -> dict[str, Any]:
    if not file_data:
        return {"ok": False, "error": t("avatars.file_missing")}
    if len(file_data) > (MAX_UPLOAD_BYTES * 4 // 3) + 16:
        return {"ok": False, "error": t("avatars.file_too_large")}
    try:
        raw = base64.b64decode(file_data, validate=True)
    except (ValueError, binascii.Error):
        return {"ok": False, "error": t("avatars.invalid_data")}
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        return {"ok": False, "error": t("avatars.file_too_large")}

    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                return {"ok": False, "error": t("avatars.unsupported_format")}
            width, height = source.size
            if width < 32 or height < 32:
                return {"ok": False, "error": t("avatars.too_small")}
            if width > 4096 or height > 4096 or width * height > MAX_IMAGE_PIXELS:
                return {"ok": False, "error": t("avatars.too_large")}
            image = ImageOps.exif_transpose(source).convert("RGB")
            image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=88, method=6)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return {"ok": False, "error": t("avatars.unreadable_image")}

    payload = output.getvalue()
    asset_id = hashlib.sha256(payload).hexdigest()
    if avatars_dir is not None:
        path = avatars_dir / f"{asset_id}.webp"
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


def avatar_file(avatars_dir: Path, asset_id: str) -> Path | None:
    if not ASSET_ID_RE.fullmatch(asset_id):
        return None
    path = avatars_dir / f"{asset_id}.webp"
    return path if path.is_file() else None


def list_user_avatars(avatars_dir: Path) -> dict[str, Any]:
    """列出所有用户上传的头像（按 asset_id 排序）。"""
    if not avatars_dir or not avatars_dir.is_dir():
        return {"avatars": [], "total": 0}
    items: list[dict[str, Any]] = []
    for path in sorted(avatars_dir.glob("*.webp")):
        asset_id = path.stem
        if not ASSET_ID_RE.fullmatch(asset_id):
            continue
        items.append({"asset_id": asset_id, "size_kb": round(path.stat().st_size / 1024, 1)})
    return {"avatars": items, "total": len(items)}


def delete_avatar(avatars_dir: Path, asset_id: str) -> dict[str, Any]:
    """删除用户上传的头像文件。不检查引用，删后引用处显示占位。"""
    if not ASSET_ID_RE.fullmatch(asset_id):
        return {"ok": False, "error": t("avatars.invalid_id")}
    path = avatars_dir / f"{asset_id}.webp"
    if not path.is_file():
        return {"ok": False, "error": t("avatars.not_found")}
    try:
        path.unlink()
    except OSError:
        return {"ok": False, "error": t("avatars.delete_failed")}
    return {"ok": True}


class AvatarService:
    """Content-addressed avatar storage rooted at one explicit directory."""

    def __init__(self, avatars_dir: Path) -> None:
        self._avatars_dir = avatars_dir

    def save_upload(
        self, file_data: str, file_name: str = "",
    ) -> dict[str, Any]:
        return save_avatar_upload(self._avatars_dir, file_data, file_name)

    def preview_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        """Validate and predict the content identity without persisting an image."""
        return save_avatar_upload(None, file_data, file_name)

    def file(self, asset_id: str) -> Path | None:
        return avatar_file(self._avatars_dir, asset_id)

    def list_user_avatars(self) -> dict[str, Any]:
        return list_user_avatars(self._avatars_dir)

    def delete(self, asset_id: str) -> dict[str, Any]:
        return delete_avatar(self._avatars_dir, asset_id)
