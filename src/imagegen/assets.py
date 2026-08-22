"""Generated image assets and generation history."""

from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from .contracts import IMAGE_PURPOSES, ImageGenerationResult


ASSET_ID_RE = re.compile(r"^[a-f0-9]{64}$")
GENERATION_ID_RE = re.compile(r"^[a-f0-9]{32}$")
MAX_IMAGE_PIXELS = 32_000_000


class ImageAssetError(RuntimeError):
    pass


class ImageAssetStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.images_dir = self.root / "images"
        self.records_dir = self.root / "records"

    def store(
        self,
        raw: bytes,
        *,
        purpose: str,
        prompt: str,
        revised_prompt: str,
        provider: str,
        model: str,
        owner_type: str,
        owner_id: str,
        context: dict[str, Any] | None = None,
    ) -> ImageGenerationResult:
        purpose = str(purpose or "").strip().lower()
        if purpose not in IMAGE_PURPOSES:
            raise ImageAssetError("不支持的图片用途")
        payload = _normalize_generated_image(raw, purpose)
        asset_id = hashlib.sha256(payload).hexdigest()
        generation_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)
        image_path = self.images_dir / f"{asset_id}.webp"
        if not image_path.exists():
            temporary = image_path.with_suffix(".webp.tmp")
            temporary.write_bytes(payload)
            temporary.replace(image_path)
        record = {
            "generation_id": generation_id,
            "asset_id": asset_id,
            "purpose": purpose,
            "prompt": str(prompt or "")[:8000],
            "revised_prompt": str(revised_prompt or "")[:8000],
            "provider": str(provider or "")[:80],
            "model": str(model or "")[:160],
            "owner_type": str(owner_type or "system")[:40],
            "owner_id": str(owner_id or "")[:300],
            "context": dict(context or {}),
            "created_at": created_at,
        }
        _atomic_write_json(self.records_dir / f"{generation_id}.json", record)
        return ImageGenerationResult(
            generation_id=generation_id,
            asset_id=asset_id,
            purpose=purpose,
            prompt=record["prompt"],
            revised_prompt=record["revised_prompt"],
            provider=record["provider"],
            model=record["model"],
            created_at=created_at,
        )

    def file(self, asset_id: str) -> Path | None:
        asset_id = str(asset_id or "").strip()
        if not ASSET_ID_RE.fullmatch(asset_id):
            return None
        path = self.images_dir / f"{asset_id}.webp"
        return path if path.is_file() else None

    def list_records(
        self,
        *,
        owner_type: str = "",
        owner_id: str = "",
        purpose: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not self.records_dir.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for path in self.records_dir.glob("*.json"):
            if not GENERATION_ID_RE.fullmatch(path.stem):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict) or self.file(str(record.get("asset_id") or "")) is None:
                continue
            if owner_type and str(record.get("owner_type") or "") != owner_type:
                continue
            if owner_id and str(record.get("owner_id") or "") != owner_id:
                continue
            if purpose and str(record.get("purpose") or "") != purpose:
                continue
            result.append(record)
        result.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return result[:max(1, min(int(limit), 500))]


def _normalize_generated_image(raw: bytes, purpose: str) -> bytes:
    if not raw:
        raise ImageAssetError("生成图片为空")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ImageAssetError("生成图片仅支持 PNG、JPEG 或 WebP")
            width, height = source.size
            if width < 128 or height < 128:
                raise ImageAssetError("生成图片尺寸不能小于 128×128")
            if width > 12288 or height > 12288 or width * height > MAX_IMAGE_PIXELS:
                raise ImageAssetError("生成图片尺寸过大")
            image = ImageOps.exif_transpose(source).convert("RGB")
            if purpose == "avatar":
                image = ImageOps.fit(image, (512, 512), Image.Resampling.LANCZOS)
            elif purpose == "item":
                image = ImageOps.fit(image, (768, 768), Image.Resampling.LANCZOS)
            elif purpose == "scene":
                image = ImageOps.fit(image, (1600, 900), Image.Resampling.LANCZOS)
            elif purpose == "map":
                image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            elif max(image.size) > 2048:
                image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=88, method=6)
            return output.getvalue()
    except ImageAssetError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageAssetError("无法读取生成图片") from exc


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
