"""Adventure scene-image assets, defaults, and portable references."""

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


MAX_SCENE_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_SCENE_IMAGE_PIXELS = 24_000_000
SCENE_IMAGE_SIZE = (1600, 900)
ASSET_ID_RE = re.compile(r"^[a-f0-9]{64}$")
BUILTIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# IDs are stable content-contract values. Paths are implementation details and
# may change without invalidating saves or content packs.
BUILTIN_SCENE_IMAGE_PATHS: dict[str, str] = {
    "dnd5e": "ui/campaign-mountain-city.jpg",
    "freeform_fantasy": "ui/rules/rule-freeform-fantasy.webp",
    "freeform_coc": "ui/rules/rule-freeform-coc.webp",
    "freeform_cyberpunk": "ui/rules/rule-freeform-cyberpunk.webp",
    "freeform_wuxia": "ui/rules/rule-freeform-wuxia.webp",
    "tavern_free": "ui/rules/rule-tavern-free.webp",
}


def builtin_scene_image_ref(rule_id: str = "") -> dict[str, str]:
    image_id = str(rule_id or "").strip()
    if image_id not in BUILTIN_SCENE_IMAGE_PATHS:
        image_id = "freeform_fantasy"
    return {"kind": "builtin", "id": image_id}


def save_scene_image_upload(api: "WebAPI", file_data: str, file_name: str = "") -> dict[str, Any]:
    if not file_data:
        return {"ok": False, "error": "未提供冒险头图文件"}
    if len(file_data) > (MAX_SCENE_IMAGE_UPLOAD_BYTES * 4 // 3) + 32:
        return {"ok": False, "error": "冒险头图不能超过 8 MB"}
    try:
        raw = base64.b64decode(file_data, validate=True)
    except (ValueError, binascii.Error):
        return {"ok": False, "error": "冒险头图文件数据无效"}
    if not raw or len(raw) > MAX_SCENE_IMAGE_UPLOAD_BYTES:
        return {"ok": False, "error": "冒险头图不能超过 8 MB"}
    try:
        payload = _normalized_scene_image(raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    reference = _store_payload(api, payload)
    return {"ok": True, "scene_image": reference, "file_name": Path(file_name).name}


def scene_image_file(api: "WebAPI", asset_id: str) -> Path | None:
    if not ASSET_ID_RE.fullmatch(str(asset_id or "")):
        return None
    path = api._scene_images_dir / f"{asset_id}.webp"
    return path if path.is_file() else None


def builtin_scene_image_file(image_id: str) -> Path | None:
    relative = BUILTIN_SCENE_IMAGE_PATHS.get(str(image_id or ""))
    if not relative:
        return None
    root = Path(__file__).resolve().parents[3]
    for candidate in (root / "static-v2" / relative, root / "frontend-v2" / "public" / relative):
        if candidate.is_file():
            return candidate
    return None


def validate_scene_image_ref(api: "WebAPI", reference: Any) -> dict[str, str]:
    if not isinstance(reference, dict):
        raise ValueError("冒险头图引用必须是对象")
    kind = str(reference.get("kind") or "").strip()
    if kind == "builtin":
        image_id = str(reference.get("id") or "").strip()
        if not BUILTIN_ID_RE.fullmatch(image_id) or image_id not in BUILTIN_SCENE_IMAGE_PATHS:
            raise ValueError("未知的内置冒险头图")
        return {"kind": "builtin", "id": image_id}
    if kind == "upload":
        asset_id = str(reference.get("asset_id") or "").strip()
        if not scene_image_file(api, asset_id):
            raise ValueError("上传的冒险头图不存在")
        return {"kind": "upload", "asset_id": asset_id}
    if kind == "generated":
        asset_id = str(reference.get("asset_id") or "").strip()
        if not api.generated_image_file(asset_id):
            raise ValueError("生成的冒险头图不存在")
        return {"kind": "generated", "asset_id": asset_id}
    if kind == "plugin":
        plugin_id = str(reference.get("plugin_id") or "").strip()
        relative_path = str(reference.get("path") or "").replace("\\", "/").strip("/")
        if not plugin_id or not relative_path:
            raise ValueError("内容包冒险头图引用不完整")
        try:
            path = api.plugin_asset_path(plugin_id, relative_path)
        except (KeyError, ValueError) as exc:
            raise ValueError("内容包冒险头图不存在或未声明") from exc
        if path.stat().st_size > MAX_SCENE_IMAGE_UPLOAD_BYTES:
            raise ValueError("内容包冒险头图不能超过 8 MB")
        return {"kind": "plugin", "plugin_id": plugin_id, "path": relative_path}
    raise ValueError("不支持的冒险头图引用类型")


def resolve_default_scene_image(api: "WebAPI", world_id: str = "", rule_id: str = "") -> dict[str, str]:
    world = api._load_world_template(str(world_id or "")) if world_id else None
    if isinstance(world, dict):
        reference = world.get("scene_image")
        if isinstance(reference, dict):
            try:
                return validate_scene_image_ref(api, reference)
            except ValueError:
                pass
        rule_id = str(rule_id or world.get("default_rule") or "")

    rule_result = api.get_rule_template(str(rule_id or "")) if rule_id else {}
    rule = rule_result.get("rule") if isinstance(rule_result, dict) else None
    if isinstance(rule, dict):
        reference = rule.get("scene_image")
        if isinstance(reference, dict):
            try:
                return validate_scene_image_ref(api, reference)
            except ValueError:
                pass
        source_rule_id = str(rule.get("source_rule_id") or "")
        if source_rule_id in BUILTIN_SCENE_IMAGE_PATHS:
            return builtin_scene_image_ref(source_rule_id)
    return builtin_scene_image_ref(rule_id)


def materialize_scene_image(api: "WebAPI", reference: Any) -> dict[str, str]:
    normalized = validate_scene_image_ref(api, reference)
    if normalized["kind"] != "plugin":
        return normalized
    source = api.plugin_asset_path(normalized["plugin_id"], normalized["path"])
    payload = _normalized_scene_image(source.read_bytes())
    return _store_payload(api, payload)


def resolve_scene_image_file(api: "WebAPI", reference: Any) -> Path | None:
    try:
        normalized = validate_scene_image_ref(api, reference)
    except ValueError:
        return None
    if normalized["kind"] == "builtin":
        return builtin_scene_image_file(normalized["id"])
    if normalized["kind"] == "upload":
        return scene_image_file(api, normalized["asset_id"])
    if normalized["kind"] == "generated":
        return api.generated_image_file(normalized["asset_id"])
    return api.plugin_asset_path(normalized["plugin_id"], normalized["path"])


def package_scene_image(
    api: "WebAPI",
    reference: Any,
    files: dict[str, str | bytes],
) -> dict[str, str] | None:
    try:
        normalized = validate_scene_image_ref(api, reference)
    except ValueError:
        return None
    if normalized["kind"] == "builtin":
        return normalized
    source = resolve_scene_image_file(api, normalized)
    if source is None:
        return None
    try:
        payload = _normalized_scene_image(source.read_bytes())
    except ValueError:
        return None
    digest = hashlib.sha256(payload).hexdigest()
    relative_path = f"assets/scenes/{digest}.webp"
    files.setdefault(relative_path, payload)
    return {"kind": "asset", "path": relative_path}


def _normalized_scene_image(raw: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("冒险头图仅支持 PNG、JPEG 或 WebP")
            width, height = source.size
            if width < 320 or height < 180:
                raise ValueError("冒险头图尺寸不能小于 320×180")
            if width > 8192 or height > 8192 or width * height > MAX_SCENE_IMAGE_PIXELS:
                raise ValueError("冒险头图尺寸过大")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image = ImageOps.fit(image, SCENE_IMAGE_SIZE, Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=88, method=6)
            return output.getvalue()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取该冒险头图") from exc


def _store_payload(api: "WebAPI", payload: bytes) -> dict[str, str]:
    asset_id = hashlib.sha256(payload).hexdigest()
    path = api._scene_images_dir / f"{asset_id}.webp"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp_path = path.with_suffix(".webp.tmp")
        tmp_path.write_bytes(payload)
        tmp_path.replace(path)
    return {"kind": "upload", "asset_id": asset_id}
