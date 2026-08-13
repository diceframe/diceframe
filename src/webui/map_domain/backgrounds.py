"""Apply saved background choices and expose picker options."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.webui.map_presets import builtin_map_options, builtin_map_preset_by_asset

from .presentation import public_map_definition


def apply_background_selection(
    game_key: str,
    automatic_map: dict[str, Any] | None,
    selection: dict[str, str],
    upload_exists: Callable[[str], bool],
) -> dict[str, Any] | None:
    kind = selection["kind"]
    if kind in {"auto", "plugin"}:
        return automatic_map
    if kind == "none":
        return _map_with_background(automatic_map, None, fallback_name="场景地图")
    if kind == "builtin":
        preset = builtin_map_preset_by_asset(selection.get("id", ""))
        if not preset:
            return automatic_map
        return _map_with_background(
            automatic_map,
            preset["background"],
            fallback=preset,
            fallback_name=str(preset["name"]),
        )
    if kind == "upload":
        asset_id = selection.get("asset_id", "")
        if not upload_exists(asset_id):
            return automatic_map
        background = {
            "id": asset_id,
            "ref": f"upload:map-background:{asset_id}",
            "name": "自定义地图背景",
            "url": f"/api/games/{game_key}/map-background-asset/{asset_id}",
        }
        return _map_with_background(
            automatic_map,
            background,
            fallback_name="自定义地图背景",
        )
    return automatic_map


def background_options(
    assets: dict[str, list[dict[str, Any]]],
    selection: dict[str, str],
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = [
        {"id": "auto", "kind": "auto", "name": "自动推荐"},
        {"id": "none", "kind": "none", "name": "关闭背景"},
    ]
    options.extend({
        "id": f"builtin:{item['id']}",
        "kind": "builtin",
        "name": item["name"],
        "description": item["description"],
        "url": item["url"],
        "selection": {"kind": "builtin", "id": item["id"]},
    } for item in builtin_map_options())
    for definition in assets.get("maps", []):
        public = public_map_definition(definition, assets)
        if not public or not public.get("background"):
            continue
        options.append({
            "id": public["id"],
            "kind": "plugin",
            "name": public["name"],
            "description": public.get("description", ""),
            "plugin_name": public.get("plugin_name", ""),
            "url": public["background"].get("url", ""),
            "selection": {"kind": "plugin", "map_id": public["id"]},
        })
    if selection["kind"] == "upload":
        options.append({
            "id": f"upload:{selection.get('asset_id', '')}",
            "kind": "upload",
            "name": "当前上传背景",
            "selection": dict(selection),
        })
    return options


def _map_with_background(
    base: dict[str, Any] | None,
    background: dict[str, Any] | None,
    *,
    fallback: dict[str, Any] | None = None,
    fallback_name: str,
) -> dict[str, Any]:
    if base and base.get("plugin_id"):
        merged = dict(base)
        merged["background"] = background
        return merged
    if fallback:
        merged = dict(fallback)
        merged["background"] = background
        return merged
    return {
        "id": "builtin:map:custom" if background else "builtin:map:none",
        "source_id": "custom" if background else "none",
        "name": fallback_name,
        "description": "",
        "mode": "graph",
        "plugin_id": "",
        "plugin_name": "DiceFrame",
        "background": background,
        "default_view": {"x": 0, "y": 0, "zoom": 1},
    }
