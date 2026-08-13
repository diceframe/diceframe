"""Build declarative map contributions for exported content packs."""

from __future__ import annotations

import base64
import binascii
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps, UnidentifiedImageError

from src.plugin_host.content import safe_id_part

if TYPE_CHECKING:
    from src.webui.api import WebAPI


MAX_MAP_ICON_BYTES = 3 * 1024 * 1024
MAX_MAP_ICON_PIXELS = 16_000_000
MAX_MAP_ICON_EDGE = 512
MAX_MAP_ICON_UPLOADS = 128
MAX_TOTAL_MAP_ICON_BYTES = 24 * 1024 * 1024


@dataclass(frozen=True)
class ContentMapPackage:
    has_definitions: bool = False
    has_locations: bool = False
    has_icons: bool = False
    has_backgrounds: bool = False
    default_map: str = ""

    @property
    def has_map(self) -> bool:
        return any((
            self.has_definitions,
            self.has_locations,
            self.has_icons,
            self.has_backgrounds,
        ))


def package_content_map(
    api: "WebAPI",
    plugin_id: str,
    pack_name: str,
    world: dict[str, Any],
    entries: list[dict[str, Any]],
    files: dict[str, str | bytes],
    *,
    background_selection: dict[str, Any] | None = None,
    icon_uploads: list[dict[str, Any]] | None = None,
) -> ContentMapPackage:
    """Add a world's locations and optional artwork to ``files``.

    Locations are copied from lorebook entries instead of requiring authors to
    maintain a second location list. Icon file stems match a location ID or
    location name. Coordinates remain absent so the runtime's stable graph
    layout remains responsible for marker placement.
    """
    world_id = str(world.get("id") or world.get("world_id") or "").strip()
    world_name = str(world.get("name") or world.get("world_name") or world_id).strip()
    if not world_id:
        raise ValueError("所选世界缺少有效 ID，无法打包地图")

    location_entries = [
        entry for entry in entries
        if isinstance(entry, dict) and str(entry.get("type") or "").strip().lower() == "location"
    ]
    icons, icon_aliases = _package_icons(icon_uploads or [], files)

    locations: list[dict[str, Any]] = []
    used_paths: set[str] = set()
    for index, entry in enumerate(location_entries, start=1):
        location = _location_record(entry, world_id, index)
        icon_id = _matching_icon(location, icon_aliases)
        if icon_id:
            location["icon"] = icon_id
        locations.append(location)
        file_part = _unique_file_part(location["id"], used_paths)
        files[f"maps/locations/{file_part}.json"] = json.dumps(
            location,
            ensure_ascii=False,
            indent=2,
        )

    map_id = safe_id_part(f"{world_id}-map")
    background_id = _package_background(api, background_selection, map_id, files)
    default_map = ""
    if background_id:
        definition: dict[str, Any] = {
            "schema_version": 1,
            "id": map_id,
            "name": f"{world_name}地图" if world_name else pack_name,
            "worlds": [world_id],
            "mode": "graph",
            "default": True,
            "background": background_id,
            "nodes": [
                {
                    "location_ref": location["id"],
                    **({"icon": location["icon"]} if location.get("icon") else {}),
                }
                for location in locations
            ],
            "default_view": {"x": 0, "y": 0, "zoom": 1},
        }
        files[f"maps/definitions/{map_id}.json"] = json.dumps(
            definition,
            ensure_ascii=False,
            indent=2,
        )
        default_map = f"plugin:{plugin_id}:map:{map_id}"

    return ContentMapPackage(
        has_definitions=bool(background_id),
        has_locations=bool(locations),
        has_icons=bool(icons),
        has_backgrounds=bool(background_id),
        default_map=default_map,
    )


def _location_record(entry: dict[str, Any], world_id: str, index: int) -> dict[str, Any]:
    name = str(entry.get("name") or "").strip()
    location_id = str(entry.get("id") or "").strip() or safe_id_part(name or f"location-{index}")
    connected = entry.get("connected_to")
    keywords = entry.get("keywords")
    return {
        "id": location_id,
        "name": name or location_id,
        "world_id": world_id,
        "connected_to": [str(item).strip() for item in connected if str(item).strip()]
        if isinstance(connected, list) else [],
        "tier": str(entry.get("tier") or "background"),
        "content": str(entry.get("content") or ""),
        "keywords": [str(item).strip() for item in keywords if str(item).strip()]
        if isinstance(keywords, list) else [],
    }


def _package_background(
    api: "WebAPI",
    selection: dict[str, Any] | None,
    map_id: str,
    files: dict[str, str | bytes],
) -> str:
    if not selection:
        return ""
    source = api.resolve_map_background_file(selection)
    if source is None or not source.is_file():
        raise ValueError("无法读取为内容包选择的地图底图")
    payload = source.read_bytes()
    if not payload:
        raise ValueError("为内容包选择的地图底图为空")
    files[f"maps/backgrounds/{map_id}.webp"] = payload
    return map_id


def _package_icons(
    uploads: list[dict[str, Any]],
    files: dict[str, str | bytes],
) -> tuple[dict[str, bytes], dict[str, str]]:
    if len(uploads) > MAX_MAP_ICON_UPLOADS:
        raise ValueError(f"地图图标一次最多选择 {MAX_MAP_ICON_UPLOADS} 个")
    total_bytes = 0
    packaged: dict[str, bytes] = {}
    aliases: dict[str, str] = {}
    used_ids: set[str] = set()
    for index, upload in enumerate(uploads, start=1):
        if not isinstance(upload, dict):
            raise ValueError("地图图标数据格式无效")
        file_name = Path(str(upload.get("file_name") or "")).name
        source_id = str(upload.get("id") or Path(file_name).stem or f"icon-{index}").strip()
        raw = _decode_icon(str(upload.get("file_data") or ""))
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_MAP_ICON_BYTES:
            raise ValueError("地图图标总大小不能超过 24 MB")
        payload = _normalized_icon(raw)
        icon_id = _unique_icon_id(source_id, used_ids)
        packaged[icon_id] = payload
        files[f"maps/icons/{icon_id}.webp"] = payload
        for alias in {source_id, safe_id_part(source_id)}:
            if alias:
                aliases.setdefault(alias, icon_id)
    return packaged, aliases


def _decode_icon(file_data: str) -> bytes:
    if not file_data or len(file_data) > (MAX_MAP_ICON_BYTES * 4 // 3) + 32:
        raise ValueError("单个地图图标不能超过 3 MB")
    try:
        raw = base64.b64decode(file_data, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("地图图标文件数据无效") from exc
    if not raw or len(raw) > MAX_MAP_ICON_BYTES:
        raise ValueError("单个地图图标不能超过 3 MB")
    return raw


def _normalized_icon(raw: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("地图图标仅支持 PNG、JPEG 或 WebP")
            width, height = source.size
            if width < 16 or height < 16:
                raise ValueError("地图图标尺寸不能小于 16×16")
            if width > 4096 or height > 4096 or width * height > MAX_MAP_ICON_PIXELS:
                raise ValueError("地图图标尺寸过大")
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((MAX_MAP_ICON_EDGE, MAX_MAP_ICON_EDGE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="WEBP", lossless=True, method=6)
            return output.getvalue()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取地图图标") from exc


def _matching_icon(location: dict[str, Any], aliases: dict[str, str]) -> str:
    candidates = (
        str(location.get("id") or ""),
        str(location.get("name") or ""),
        safe_id_part(location.get("id")),
        safe_id_part(location.get("name")),
    )
    return next((aliases[item] for item in candidates if item in aliases), "")


def _unique_icon_id(source_id: str, used: set[str]) -> str:
    base = safe_id_part(source_id) or "icon"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base[:43]}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _unique_file_part(location_id: str, used: set[str]) -> str:
    base = safe_id_part(location_id) or "location"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base[:43]}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate
