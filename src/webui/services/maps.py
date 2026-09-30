"""Map service facade: assemble location views and persist background choices."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from src.content_modules.projection import ContentProjectionService

@dataclass(frozen=True)
class MapDependencies:
    get_instance: Callable[[tuple[str, ...]], Any | None]
    parse_game_key: Callable[[str], tuple[str, ...]]
    list_lore_entries: Callable[[str, str], list[dict[str, Any]]]
    list_map_assets: Callable[[str], dict[str, list[dict[str, Any]]]]
    validate_background_selection: Callable[[Any], dict[str, str]]
    save_instance: Callable[[Any], Awaitable[None]]
    load_world_template: Callable[[str], dict[str, Any] | None]
    map_background_file: Callable[[str], Path | None]
    generated_image_file: Callable[[str], Path | None]
    content_projection: ContentProjectionService | None = None


def get_map_locations(
    dependencies: MapDependencies,
    game_key: str,
) -> dict[str, Any]:
    """Return the compatible location list plus read-only map presentation data."""
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance or not instance.world_id:
        return {"locations": [], "current_scene": "", "current_location_id": ""}

    if dependencies.content_projection is not None:
        entries = dependencies.content_projection.for_game(
            instance, viewer_kind="gm", entry_type="location",
        )
    else:
        entries = dependencies.list_lore_entries(instance.world_id, "location")
    locations = lore_locations(entries)
    assets = _content_map_assets(dependencies, instance.world_id)
    merge_contributed_locations(locations, assets.get("locations", []))

    selection = _saved_background_selection(dependencies, instance)
    definitions = assets.get("maps", [])
    if selection["kind"] == "plugin":
        active_definition = select_plugin_map(definitions, selection.get("map_id", ""))
    else:
        world = _world_template(dependencies, str(instance.world_id or ""))
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
        _map_rule_id(dependencies, instance),
    )
    public_map = apply_background_selection(
        game_key,
        automatic_map,
        selection,
        lambda asset_id: (
            dependencies.map_background_file(asset_id) is not None
            or dependencies.generated_image_file(asset_id) is not None
        ),
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
    dependencies: MapDependencies,
    game_key: str,
    selection: Any,
) -> dict[str, Any]:
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance:
        return {"ok": False, "error": "游戏不存在"}
    try:
        normalized = dependencies.validate_background_selection(selection)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if normalized["kind"] == "plugin":
        assets = _content_map_assets(
            dependencies,
            str(instance.world_id or ""),
        )
        definition = select_plugin_map(assets.get("maps", []), normalized.get("map_id", ""))
        if not definition or not public_map_definition(definition, assets).get("background"):
            return {"ok": False, "error": "内容包地图背景不存在或不适用于当前世界"}
    instance.set_map_background(normalized)
    await dependencies.save_instance(instance)
    return {
        "ok": True,
        "map_background": normalized,
        "map": get_map_locations(dependencies, game_key),
    }


def map_background_asset(
    dependencies: MapDependencies,
    game_key: str,
    asset_id: str,
) -> Path | None:
    """Resolve only the upload currently selected by this game."""
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance:
        return None
    try:
        selection = dependencies.validate_background_selection(
            getattr(instance, "map_background", None),
        )
    except ValueError:
        return None
    if selection.get("kind") not in {"upload", "generated"} or selection.get("asset_id") != asset_id:
        return None
    if selection.get("kind") == "generated":
        return dependencies.generated_image_file(asset_id)
    return dependencies.map_background_file(asset_id)


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


def _map_rule_id(
    dependencies: MapDependencies,
    instance: Any,
) -> str:
    rule_id = str(getattr(instance, "rule_id", "") or "").strip()
    if rule_id:
        return rule_id
    return str(
        _world_template(dependencies, str(instance.world_id or "")).get(
            "default_rule"
        ) or ""
    ).strip()


def _world_template(
    dependencies: MapDependencies,
    world_id: str,
) -> dict[str, Any]:
    try:
        return dependencies.load_world_template(world_id) or {}
    except (OSError, ValueError):
        return {}


def _saved_background_selection(
    dependencies: MapDependencies,
    instance: Any,
) -> dict[str, str]:
    try:
        return dependencies.validate_background_selection(
            getattr(instance, "map_background", None)
        )
    except ValueError:
        return {"kind": "auto"}


def _content_map_assets(
    dependencies: MapDependencies,
    world_id: str,
) -> dict[str, list[dict[str, Any]]]:
    return dependencies.list_map_assets(world_id)
