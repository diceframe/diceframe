"""Validation for declarative map contributions carried by content packs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError


class MapContribution(Protocol):
    kind: str
    key: str
    path: Path


MAP_KINDS = frozenset({"map_definition", "map_location", "map_icon", "map_scene"})
MAP_IMAGE_KINDS = frozenset({"map_icon", "map_scene"})


def validate_map_image(kind: str, path: Path, relative_path: Path) -> None:
    expected_format = {
        ".png": "PNG",
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".webp": "WEBP",
    }[path.suffix.lower()]
    label = {
        "map_icon": "地图图标",
        "map_scene": "地图底图",
    }[kind]
    try:
        with Image.open(path) as image:
            width, height = image.size
            if image.format != expected_format:
                raise ValueError(f"{label}文件内容与扩展名不匹配：{relative_path}")
            limit = 16_000_000 if kind == "map_icon" else 40_000_000
            max_side = 4096 if kind == "map_icon" else 8192
            if width < 1 or height < 1 or width > max_side or height > max_side or width * height > limit:
                raise ValueError(f"{label}尺寸超出允许范围：{relative_path}")
            image.verify()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"无法读取{label}：{relative_path}") from exc


def validate_map_references(items: Sequence[MapContribution], plugin_dir: Path) -> None:
    keys_by_kind = {
        kind: {item.key for item in items if item.kind == kind}
        for kind in MAP_IMAGE_KINDS
    }
    for item in items:
        if item.kind not in {"map_definition", "map_location"}:
            continue
        data = json.loads(item.path.read_text(encoding="utf-8"))
        references: list[tuple[str, str, str]] = []
        if item.kind == "map_definition":
            references.append(("background", str(data.get("background") or ""), "map_scene"))
            nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                references.extend([
                    ("nodes.icon", str(node.get("icon") or ""), "map_icon"),
                    ("nodes.image", str(node.get("image") or ""), "map_scene"),
                ])
        else:
            references.extend([
                ("icon", str(data.get("icon") or ""), "map_icon"),
                ("image", str(data.get("image") or ""), "map_scene"),
            ])
        for field, reference, target_kind in references:
            if reference and reference not in keys_by_kind[target_kind]:
                relative_path = item.path.relative_to(plugin_dir)
                raise ValueError(f"地图资源引用不存在：{relative_path} {field}={reference}")


def validate_map_definition(data: dict, relative_path: Path) -> None:
    if data.get("schema_version") != 1:
        raise ValueError(f"地图定义 schema_version 必须为 1：{relative_path}")
    if str(data.get("mode") or "graph") != "graph":
        raise ValueError(f"当前地图定义仅支持 graph 模式：{relative_path}")
    value = data.get("worlds")
    if value is not None and (
        not isinstance(value, list)
        or len(value) > 128
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"地图定义 worlds 必须是不超过 128 项的字符串数组：{relative_path}")
    background = data.get("background")
    if background is not None and not isinstance(background, str):
        raise ValueError(f"地图定义 background 必须引用当前内容包的地图底图 ID：{relative_path}")
    nodes = data.get("nodes", [])
    if not isinstance(nodes, list) or len(nodes) > 500:
        raise ValueError(f"地图定义 nodes 必须是不超过 500 项的数组：{relative_path}")
    refs: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError(f"地图定义节点必须是对象：{relative_path}")
        location_ref = str(node.get("location_ref") or "").strip()
        if not location_ref or location_ref in refs:
            raise ValueError(f"地图定义节点 location_ref 不能为空或重复：{relative_path}")
        refs.add(location_ref)
        has_x = "x" in node
        has_y = "y" in node
        if has_x != has_y:
            raise ValueError(f"地图定义节点必须同时声明 x/y：{relative_path}")
        if has_x:
            try:
                x = float(node["x"])
                y = float(node["y"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"地图定义节点 x/y 必须是数字：{relative_path}") from exc
            if not (-50 <= x <= 50 and -50 <= y <= 50):
                raise ValueError(f"地图定义节点 x/y 必须位于 -50 到 50：{relative_path}")
        if node.get("icon") is not None and not isinstance(node.get("icon"), str):
            raise ValueError(f"地图定义节点 icon 必须是素材 ID：{relative_path}")
        if node.get("image") is not None and not isinstance(node.get("image"), str):
            raise ValueError(f"地图定义节点 image 必须是素材 ID：{relative_path}")
    default_view = data.get("default_view")
    if default_view is not None:
        if not isinstance(default_view, dict):
            raise ValueError(f"地图定义 default_view 必须是对象：{relative_path}")
        try:
            zoom = float(default_view.get("zoom", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"地图定义 default_view.zoom 必须是数字：{relative_path}") from exc
        if not 0.25 <= zoom <= 8:
            raise ValueError(f"地图定义 default_view.zoom 必须位于 0.25 到 8：{relative_path}")
