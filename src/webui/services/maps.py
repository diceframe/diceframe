"""Map service facade: assemble location views and persist background choices."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.webui.map_domain.backgrounds import apply_background_selection, background_options
from src.webui.map_domain.locations import (
    find_map_anchor,
    lore_locations,
    match_current_location,
    merge_contributed_locations,
)
from src.webui.map_domain.presentation import apply_map_presentation, public_map_definition
from src.webui.map_domain.selection import select_map_definition, select_plugin_map
from src.webui.map_presets import builtin_map_preset

if TYPE_CHECKING:
    from src.webui.api import WebAPI


def get_map_locations(api: "WebAPI", game_key: str) -> dict[str, Any]:
    """Return the compatible location list plus read-only map presentation data."""
    instance = api._reg.get(api._parse_key(game_key))
    if not instance or not instance.world_id:
        return {"locations": [], "current_scene": "", "current_location_id": ""}

    entries = api._lore.list_entries(instance.world_id, "location")
    locations = lore_locations(entries)
    assets = _content_map_assets(api, instance.world_id)
    merge_contributed_locations(locations, assets.get("locations", []))

    selection = _saved_background_selection(api, instance)
    definitions = assets.get("maps", [])
    if selection["kind"] == "plugin":
        active_definition = select_plugin_map(definitions, selection.get("map_id", ""))
    else:
        world = _world_template(api, str(instance.world_id or ""))
        active_definition = select_map_definition(
            str(instance.world_id or ""),
            definitions,
            str(world.get("default_map") or ""),
        )
    apply_map_presentation(locations, active_definition, assets)

    current_scene = str(instance.scene or "")
    current_location_id = _append_current_scene(locations, current_scene)
    automatic_map = public_map_definition(active_definition, assets) or builtin_map_preset(
        str(instance.world_id or ""),
        _map_rule_id(api, instance),
    )
    public_map = apply_background_selection(
        game_key,
        automatic_map,
        selection,
        lambda asset_id: api.map_background_file(asset_id) is not None,
    )
    return {
        "schema_version": 1,
        "map_mode": "graph",
        "locations": locations,
        "current_scene": current_scene,
        "current_location_id": current_location_id,
        "active_map": public_map,
        "background_selection": selection,
        "background_options": background_options(assets, selection),
        "assets": {
            "icons": assets.get("icons", []),
            "scenes": assets.get("scenes", []),
        },
        "capabilities": {
            "can_expand": True,
            "can_edit": False,
            "has_background": bool(public_map and public_map.get("background")),
            "has_plugin_assets": any(
                assets.get(key) for key in ("maps", "locations", "icons", "scenes")
            ),
        },
    }


async def update_map_background(
    api: "WebAPI",
    game_key: str,
    selection: Any,
) -> dict[str, Any]:
    instance = api._reg.get(api._parse_key(game_key))
    if not instance:
        return {"ok": False, "error": "游戏不存在"}
    try:
        normalized = api.validate_map_background_selection(selection)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if normalized["kind"] == "plugin":
        assets = _content_map_assets(api, str(instance.world_id or ""))
        definition = select_plugin_map(assets.get("maps", []), normalized.get("map_id", ""))
        if not definition or not public_map_definition(definition, assets).get("background"):
            return {"ok": False, "error": "内容包地图背景不存在或不适用于当前世界"}
    instance.set_map_background(normalized)
    await api._reg.save(instance)
    return {
        "ok": True,
        "map_background": normalized,
        "map": get_map_locations(api, game_key),
    }


def map_background_asset(api: "WebAPI", game_key: str, asset_id: str) -> Path | None:
    """Resolve only the upload currently selected by this game."""
    instance = api._reg.get(api._parse_key(game_key))
    if not instance:
        return None
    try:
        selection = api.validate_map_background_selection(
            getattr(instance, "map_background", None),
        )
    except ValueError:
        return None
    if selection.get("kind") != "upload" or selection.get("asset_id") != asset_id:
        return None
    return api.map_background_file(asset_id)


def _append_current_scene(locations: list[dict[str, Any]], current_scene: str) -> str:
    if not current_scene or not locations:
        return ""
    matched = match_current_location(current_scene, locations)
    if matched:
        return str(matched.get("id") or matched.get("name") or "")
    anchor = find_map_anchor(current_scene, locations)
    locations.append({
        "id": "__current_scene__",
        "name": current_scene,
        "connected_to": [anchor["id"]] if anchor else [],
        "tier": "current",
        "content": "当前剧情场景，尚未写入世界书地点条目。",
        "keywords": [],
    })
    return "__current_scene__"


def _map_rule_id(api: "WebAPI", instance: Any) -> str:
    rule_id = str(getattr(instance, "rule_id", "") or "").strip()
    if rule_id:
        return rule_id
    return str(_world_template(api, str(instance.world_id or "")).get("default_rule") or "").strip()


def _world_template(api: "WebAPI", world_id: str) -> dict[str, Any]:
    try:
        return api._load_world_template(world_id) or {}
    except (AttributeError, OSError, ValueError):
        return {}


def _saved_background_selection(api: "WebAPI", instance: Any) -> dict[str, str]:
    try:
        return api.validate_map_background_selection(getattr(instance, "map_background", None))
    except (AttributeError, ValueError):
        return {"kind": "auto"}


def _content_map_assets(api: "WebAPI", world_id: str) -> dict[str, list[dict[str, Any]]]:
    plugin_host = getattr(api, "_plugins", None)
    if not plugin_host:
        return {"maps": [], "locations": [], "icons": [], "scenes": []}
    return plugin_host.list_map_assets(world_id)
