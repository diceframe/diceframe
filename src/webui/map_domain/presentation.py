"""Convert map definitions and assets into the public map presentation."""

from __future__ import annotations

from typing import Any

from .selection import map_definition_ref


def apply_map_presentation(
    locations: list[dict[str, Any]],
    active_map: dict[str, Any] | None,
    assets: dict[str, list[dict[str, Any]]],
) -> None:
    definition_plugin = str((active_map or {}).get("plugin_id") or "")
    nodes = active_map.get("nodes", []) if isinstance(active_map, dict) else []
    nodes_by_reference = {
        str(node.get("location_ref") or ""): node
        for node in nodes
        if isinstance(node, dict) and str(node.get("location_ref") or "")
    }
    for location in locations:
        location_id = str(location.get("id") or "")
        location_name = str(location.get("name") or "")
        node = nodes_by_reference.get(location_id) or nodes_by_reference.get(location_name) or {}
        x = bounded_number(node.get("x"), -50, 50)
        y = bounded_number(node.get("y"), -50, 50)
        if x is not None and y is not None:
            location["x"] = x
            location["y"] = y
        source_plugin = definition_plugin if node else str(location.get("plugin_id") or "")
        icon = find_asset(
            assets.get("icons", []),
            source_plugin,
            node.get("icon") or location.get("icon"),
        )
        scene = find_asset(
            assets.get("scenes", []),
            source_plugin,
            node.get("image") or location.get("image"),
        )
        if icon:
            location["icon_asset"] = icon
            location["icon_url"] = icon.get("url", "")
        if scene:
            location["image_asset"] = scene
            location["image_url"] = scene.get("url", "")


def public_map_definition(
    definition: dict[str, Any] | None,
    assets: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    if not definition:
        return None
    plugin_id = str(definition.get("plugin_id") or "")
    background = find_asset(assets.get("scenes", []), plugin_id, definition.get("background"))
    default_view = (
        definition.get("default_view")
        if isinstance(definition.get("default_view"), dict)
        else {}
    )
    return {
        "id": map_definition_ref(definition),
        "source_id": str(definition.get("id") or ""),
        "name": str(definition.get("name") or definition.get("id") or ""),
        "description": str(definition.get("description") or ""),
        "mode": "graph",
        "plugin_id": plugin_id,
        "plugin_name": str(definition.get("plugin_name") or ""),
        "background": background,
        "default_view": {
            "x": bounded_number(default_view.get("x"), -50, 50) or 0,
            "y": bounded_number(default_view.get("y"), -50, 50) or 0,
            "zoom": bounded_number(default_view.get("zoom"), 0.25, 8) or 1,
        },
    }


def find_asset(
    assets: list[dict[str, Any]],
    plugin_id: str,
    reference: Any,
) -> dict[str, Any] | None:
    asset_id = str(reference or "").strip()
    if not asset_id:
        return None
    for asset in assets:
        if plugin_id and str(asset.get("plugin_id") or "") != plugin_id:
            continue
        if asset_id in {str(asset.get("id") or ""), str(asset.get("ref") or "")}:
            return asset
    return None


def bounded_number(value: Any, minimum: float, maximum: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if minimum <= number <= maximum else None
