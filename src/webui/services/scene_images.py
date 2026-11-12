"""Adventure scene-image assets, defaults, and portable references."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps, UnidentifiedImageError
from src.localization.messages import t

MAX_SCENE_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_SCENE_IMAGE_PIXELS = 24_000_000
SCENE_IMAGE_SIZE = (1600, 900)
ASSET_ID_RE = re.compile(r"^[a-f0-9]{64}$")
BUILTIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# IDs are stable content-contract values. Paths are implementation details and
# may change without invalidating saves or content packs.
BUILTIN_SCENE_IMAGE_PATHS: dict[str, str] = {
    "dnd5e": "ui/campaign-mountain-city.jpg",
    "mountain_city": "ui/campaign-mountain-city.jpg",
    "ashen_throne": "ui/dnd-ashen-throne.png",
    "moonlit_ruins": "ui/campaign-moonlit-ruins.jpg",
    "dark_fantasy": "ui/dark-fantasy-atmosphere.jpg",
    "freeform_fantasy": "ui/rules/rule-freeform-fantasy.webp",
    "freeform_coc": "ui/rules/rule-freeform-coc.webp",
    "freeform_coc_harbor": "ui/rules/rule-freeform-coc-harbor.webp",
    "freeform_coc_town": "ui/rules/rule-freeform-coc-town.webp",
    "freeform_cyberpunk": "ui/rules/rule-freeform-cyberpunk.webp",
    "freeform_cyberpunk_alley": "ui/rules/rule-freeform-cyberpunk-alley.webp",
    "freeform_cyberpunk_rooftops": "ui/rules/rule-freeform-cyberpunk-rooftops.webp",
    "freeform_wuxia": "ui/rules/rule-freeform-wuxia.webp",
    "freeform_wuxia_mountains": "ui/rules/rule-freeform-wuxia-mountains.webp",
    "freeform_wuxia_riverside": "ui/rules/rule-freeform-wuxia-riverside.webp",
    "tavern_free": "ui/rules/rule-tavern-free.webp",
}

# Stable IDs let us add/remove art without changing save references. A keyed
# selection is derived from the save key, so each new save gets a varied cover
# while a legacy save remains stable when no key is supplied.
BUILTIN_SCENE_IMAGE_VARIANTS: dict[str, tuple[str, ...]] = {
    "dnd5e": ("dnd5e", "ashen_throne", "moonlit_ruins"),
    "dnd2024_srd": ("dnd5e", "ashen_throne", "moonlit_ruins"),
    "freeform_fantasy": ("freeform_fantasy", "mountain_city", "dark_fantasy"),
    "freeform_coc": ("freeform_coc", "freeform_coc_harbor", "freeform_coc_town"),
    "freeform_cyberpunk": (
        "freeform_cyberpunk",
        "freeform_cyberpunk_alley",
        "freeform_cyberpunk_rooftops",
    ),
    "freeform_wuxia": ("freeform_wuxia", "freeform_wuxia_mountains", "freeform_wuxia_riverside"),
    "tavern_free": ("tavern_free", "moonlit_ruins", "mountain_city"),
}

# A world or ruleset may provide ``scene_images`` as a pool of references.
# Keep this separate from ``scene_image``: the latter is the existing explicit
# single-image contract, while the pool is opt-in and remains backwards
# compatible with older templates and saves.


@dataclass(frozen=True)
class SceneImageDependencies:
    images_dir: Path
    load_world_template: Callable[[str], dict[str, Any] | None]
    get_rule_template: Callable[[str], dict[str, Any]]
    generated_image_file: Callable[[str], Path | None]
    plugin_asset_path: Callable[[str, str], Path]


def builtin_scene_image_ref(rule_id: str = "") -> dict[str, str]:
    image_id = str(rule_id or "").strip()
    if image_id not in BUILTIN_SCENE_IMAGE_PATHS:
        image_id = "freeform_fantasy"
    return {"kind": "builtin", "id": image_id}


def save_scene_image_upload(dependencies: SceneImageDependencies | None, file_data: str, file_name: str = "") -> dict[str, Any]:
    if not file_data:
        return {"ok": False, "error": t("webui.services.scene_images.file_missing")}
    if len(file_data) > (MAX_SCENE_IMAGE_UPLOAD_BYTES * 4 // 3) + 32:
        return {"ok": False, "error": t("webui.services.scene_images.file_too_large")}
    try:
        raw = base64.b64decode(file_data, validate=True)
    except (ValueError, binascii.Error):
        return {"ok": False, "error": t("webui.services.scene_images.file_data_invalid")}
    if not raw or len(raw) > MAX_SCENE_IMAGE_UPLOAD_BYTES:
        return {"ok": False, "error": t("webui.services.scene_images.file_too_large")}
    try:
        payload = _normalized_scene_image(raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    reference = _store_payload(dependencies, payload) if dependencies is not None else {
        "kind": "upload", "asset_id": hashlib.sha256(payload).hexdigest(),
    }
    return {"ok": True, "scene_image": reference, "file_name": Path(file_name).name}


def scene_image_file(dependencies: SceneImageDependencies, asset_id: str) -> Path | None:
    if not ASSET_ID_RE.fullmatch(str(asset_id or "")):
        return None
    path = dependencies.images_dir / f"{asset_id}.webp"
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


def validate_scene_image_ref(dependencies: SceneImageDependencies, reference: Any) -> dict[str, str]:
    if not isinstance(reference, dict):
        raise ValueError(t("webui.services.scene_images.reference_not_object"))
    kind = str(reference.get("kind") or "").strip()
    if kind == "builtin":
        image_id = str(reference.get("id") or "").strip()
        if not BUILTIN_ID_RE.fullmatch(image_id) or image_id not in BUILTIN_SCENE_IMAGE_PATHS:
            raise ValueError(t("webui.services.scene_images.unknown_builtin"))
        return {"kind": "builtin", "id": image_id}
    if kind == "upload":
        asset_id = str(reference.get("asset_id") or "").strip()
        if not scene_image_file(dependencies, asset_id):
            raise ValueError(t("webui.services.scene_images.uploaded_missing"))
        return {"kind": "upload", "asset_id": asset_id}
    if kind == "generated":
        asset_id = str(reference.get("asset_id") or "").strip()
        if not dependencies.generated_image_file(asset_id):
            raise ValueError(t("webui.services.scene_images.generated_missing"))
        return {"kind": "generated", "asset_id": asset_id}
    if kind == "plugin":
        plugin_id = str(reference.get("plugin_id") or "").strip()
        relative_path = str(reference.get("path") or "").replace("\\", "/").strip("/")
        if not plugin_id or not relative_path:
            raise ValueError(t("webui.services.scene_images.content_pack_reference_incomplete"))
        try:
            path = dependencies.plugin_asset_path(plugin_id, relative_path)
        except (KeyError, ValueError) as exc:
            raise ValueError(t("webui.services.scene_images.content_pack_cover_missing")) from exc
        if path.stat().st_size > MAX_SCENE_IMAGE_UPLOAD_BYTES:
            raise ValueError(t("webui.services.scene_images.content_pack_cover_too_large"))
        return {"kind": "plugin", "plugin_id": plugin_id, "path": relative_path}
    raise ValueError(t("webui.services.scene_images.unsupported_reference_type"))


def _template_scene_image_candidates(
    dependencies: SceneImageDependencies,
    template: dict[str, Any] | None,
) -> list[dict[str, str]]:
    if not isinstance(template, dict):
        return []
    # ``scene_image`` stays the legacy primary and may be omitted from a
    # hand-authored ``scene_images`` list.  Always prepend it, then de-dupe,
    # so old and new content contracts describe the same ordered pool.
    raw_values: list[Any] = []
    if isinstance(template.get("scene_image"), dict):
        raw_values.append(template["scene_image"])
    raw_options = template.get("scene_images")
    if isinstance(raw_options, list):
        raw_values.extend(raw_options)
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_values:
        if not isinstance(raw, dict):
            continue
        try:
            normalized = validate_scene_image_ref(dependencies, raw)
        except ValueError:
            continue
        identity = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(normalized)
    return candidates


def _select_scene_image(candidates: list[dict[str, str]], selection_key: str = "") -> dict[str, str] | None:
    if not candidates:
        return None
    if not selection_key:
        # Keep reads of legacy saves stable. Creation passes a unique key below,
        # so newly-created saves still receive a different pool member.
        return dict(candidates[0])
    digest = hashlib.sha256(selection_key.encode("utf-8", errors="replace")).digest()
    index = int.from_bytes(digest[:8], "big") % len(candidates)
    return dict(candidates[index])


def builtin_scene_image_candidates(rule_id: str = "") -> list[dict[str, str]]:
    ids = BUILTIN_SCENE_IMAGE_VARIANTS.get(str(rule_id or "").strip())
    if not ids:
        normalized = builtin_scene_image_ref(rule_id)
        return [normalized]
    return [builtin_scene_image_ref(image_id) for image_id in ids]


def default_scene_image_candidates(
    dependencies: SceneImageDependencies,
    world_id: str = "",
    rule_id: str = "",
) -> list[dict[str, str]]:
    """Return the validated pool used for a new save's default cover."""

    world = dependencies.load_world_template(str(world_id or "")) if world_id else None
    if isinstance(world, dict):
        candidates = _template_scene_image_candidates(dependencies, world)
        if candidates:
            return candidates
        rule_id = str(rule_id or world.get("default_rule") or "")

    rule_result = dependencies.get_rule_template(str(rule_id or "")) if rule_id else {}
    rule = rule_result.get("rule") if isinstance(rule_result, dict) else None
    if isinstance(rule, dict):
        candidates = _template_scene_image_candidates(dependencies, rule)
        if candidates:
            # Built-in rules historically declared one ``scene_image``. Treat
            # that legacy declaration as the primary member of the built-in
            # pool; custom/plugin references remain explicit single choices.
            if (
                not isinstance(rule.get("scene_images"), list)
                and candidates[0].get("kind") == "builtin"
                and candidates[0].get("id") in BUILTIN_SCENE_IMAGE_VARIANTS.get(
                    str(rule_id or ""), ()
                )
                and (str(rule_id or "") in BUILTIN_SCENE_IMAGE_VARIANTS
                     or str(rule.get("source_rule_id") or "") in BUILTIN_SCENE_IMAGE_VARIANTS)
            ):
                return builtin_scene_image_candidates(
                    str(rule_id or "") or str(rule.get("source_rule_id") or "")
                )
            return candidates
        source_rule_id = str(rule.get("source_rule_id") or "")
        if source_rule_id in BUILTIN_SCENE_IMAGE_VARIANTS:
            return builtin_scene_image_candidates(source_rule_id)
    return builtin_scene_image_candidates(rule_id)


def resolve_default_scene_image(
    dependencies: SceneImageDependencies,
    world_id: str = "",
    rule_id: str = "",
    selection_key: str = "",
) -> dict[str, str]:
    return _select_scene_image(
        default_scene_image_candidates(dependencies, world_id, rule_id),
        selection_key,
    ) or builtin_scene_image_ref(rule_id)


def materialize_scene_image(dependencies: SceneImageDependencies, reference: Any) -> dict[str, str]:
    normalized = validate_scene_image_ref(dependencies, reference)
    if normalized["kind"] != "plugin":
        return normalized
    source = dependencies.plugin_asset_path(normalized["plugin_id"], normalized["path"])
    payload = _normalized_scene_image(source.read_bytes())
    return _store_payload(dependencies, payload)


def resolve_scene_image_file(dependencies: SceneImageDependencies, reference: Any) -> Path | None:
    try:
        normalized = validate_scene_image_ref(dependencies, reference)
    except ValueError:
        return None
    if normalized["kind"] == "builtin":
        return builtin_scene_image_file(normalized["id"])
    if normalized["kind"] == "upload":
        return scene_image_file(dependencies, normalized["asset_id"])
    if normalized["kind"] == "generated":
        return dependencies.generated_image_file(normalized["asset_id"])
    return dependencies.plugin_asset_path(normalized["plugin_id"], normalized["path"])


def package_scene_image(
    dependencies: SceneImageDependencies,
    reference: Any,
    files: dict[str, str | bytes],
) -> dict[str, str] | None:
    try:
        normalized = validate_scene_image_ref(dependencies, reference)
    except ValueError:
        return None
    if normalized["kind"] == "builtin":
        return normalized
    source = resolve_scene_image_file(dependencies, normalized)
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
                raise ValueError(t("webui.services.scene_images.unsupported_format"))
            width, height = source.size
            if width < 320 or height < 180:
                raise ValueError(t("webui.services.scene_images.too_small"))
            if width > 8192 or height > 8192 or width * height > MAX_SCENE_IMAGE_PIXELS:
                raise ValueError(t("webui.services.scene_images.dimensions_too_large"))
            image = ImageOps.exif_transpose(source).convert("RGB")
            image = ImageOps.fit(image, SCENE_IMAGE_SIZE, Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=88, method=6)
            return output.getvalue()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(t("webui.services.scene_images.unreadable")) from exc


def _store_payload(dependencies: SceneImageDependencies, payload: bytes) -> dict[str, str]:
    asset_id = hashlib.sha256(payload).hexdigest()
    path = dependencies.images_dir / f"{asset_id}.webp"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp_path = path.with_suffix(".webp.tmp")
        tmp_path.write_bytes(payload)
        tmp_path.replace(path)
    return {"kind": "upload", "asset_id": asset_id}


class SceneImageService:
    """Scene-image storage and reference resolution with explicit boundaries."""

    def __init__(self, dependencies: SceneImageDependencies) -> None:
        self._dependencies = dependencies

    def save_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        return save_scene_image_upload(self._dependencies, file_data, file_name)

    def preview_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        """Validate and predict a native upload without writing storage."""
        return save_scene_image_upload(None, file_data, file_name)

    def file(self, asset_id: str) -> Path | None:
        return scene_image_file(self._dependencies, asset_id)

    def validate(self, reference: Any) -> dict[str, str]:
        return validate_scene_image_ref(self._dependencies, reference)

    def resolve_default(
        self, world_id: str = "", rule_id: str = "", selection_key: str = "",
    ) -> dict[str, str]:
        return resolve_default_scene_image(self._dependencies, world_id, rule_id, selection_key)

    def default_candidates(
        self, world_id: str = "", rule_id: str = "",
    ) -> list[dict[str, str]]:
        return default_scene_image_candidates(self._dependencies, world_id, rule_id)

    def choose_default(
        self, world_id: str = "", rule_id: str = "", selection_key: str = "",
    ) -> dict[str, str]:
        return self.resolve_default(world_id, rule_id, selection_key)

    def materialize(self, reference: Any) -> dict[str, str]:
        return materialize_scene_image(self._dependencies, reference)

    def resolve_file(self, reference: Any) -> Path | None:
        return resolve_scene_image_file(self._dependencies, reference)

    def package(
        self, reference: Any, files: dict[str, str | bytes],
    ) -> dict[str, str] | None:
        return package_scene_image(self._dependencies, reference, files)
