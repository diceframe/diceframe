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
from src.engine.participant_view import Viewer
from src.engine.modules import media, narrative_notes
from src.knowledge.visibility import entry_visible_to_viewer

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
    *,
    viewer: Viewer,
) -> dict[str, Any]:
    """Return the location list plus read-only map presentation data.

    Lorebook locations are projected for ``viewer``: the GM sees every
    location of the game's World/Game Books, a seat only the locations its
    character may see, anyone else only public locations.
    """
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance or not instance.world_id:
        return {"locations": [], "current_scene": "", "current_location_id": ""}

    gm_viewer = Viewer("gm", str(getattr(instance, "gm_uid", "") or ""))
    gm_entries = _location_entries(dependencies, instance, gm_viewer)
    entries = gm_entries if viewer.is_gm else _location_entries(dependencies, instance, viewer)
    locations = lore_locations(entries)
    assets = _content_map_assets(dependencies, instance.world_id)
    contributed = list(assets.get("locations", []) or [])
    if viewer.is_gm:
        merge_contributed_locations(locations, contributed)
        visibility_hint = _gm_visibility_hint(
            dependencies, instance, gm_entries, contributed,
        )
    else:
        # A plugin location must not stand in for a lore location hidden
        # from this viewer: its id/name would reveal the hidden place.
        hidden = _location_tokens(gm_entries) - _location_tokens(entries)
        merge_contributed_locations(locations, [
            location for location in contributed
            if not (_location_tokens([location]) & hidden)
        ])
        _prune_hidden_connections(locations)
        visibility_hint = "" if locations else "no_visible_locations"

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

    current_scene = str(narrative_notes.scene(instance) or "")
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
        "visibility_hint": visibility_hint,
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
        # Only the GM changes the background, so the echoed map is the GM view.
        "map": get_map_locations(
            dependencies, game_key, viewer=Viewer("gm", str(instance.gm_uid or "")),
        ),
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
            media.map_background(instance),
        )
    except ValueError:
        return None
    if selection.get("kind") not in {"upload", "generated"} or selection.get("asset_id") != asset_id:
        return None
    if selection.get("kind") == "generated":
        return dependencies.generated_image_file(asset_id)
    return dependencies.map_background_file(asset_id)


def _location_entries(
    dependencies: MapDependencies,
    instance: Any,
    viewer: Viewer,
) -> list[dict[str, Any]]:
    projection = dependencies.content_projection
    if projection is not None:
        # action_actor_uids=[]: the GM map is not shaped by who acts this round.
        return projection.for_viewer(
            instance, viewer, entry_type="location", action_actor_uids=[],
        )
    entries = dependencies.list_lore_entries(instance.world_id, "location")
    if viewer.is_gm:
        return entries
    # Compatibility store without Book bindings: the shared predicate still
    # decides; the seat's character name is only a secondary match token.
    kind = "character" if viewer.is_seat and viewer.uid else "party"
    seat = (getattr(instance, "players", {}) or {}).get(viewer.uid) or {}
    name = str(seat.get("character_name") or "") if isinstance(seat, dict) else ""
    return [
        entry for entry in entries
        if entry_visible_to_viewer(entry, kind, viewer.uid, name)
    ]


def _location_tokens(entries: list[dict[str, Any]]) -> set[str]:
    return {
        str(token)
        for entry in entries
        for token in (entry.get("id"), entry.get("name"))
        if isinstance(entry, dict) and token
    }


def _gm_visibility_hint(
    dependencies: MapDependencies,
    instance: Any,
    gm_entries: list[dict[str, Any]],
    contributed: list[dict[str, Any]],
) -> str:
    """Tell the GM when lore locations exist but no seat can see any of them."""

    if not gm_entries:
        return ""
    gm_tokens = _location_tokens(gm_entries)
    if any(not (_location_tokens([location]) & gm_tokens) for location in contributed):
        return ""  # an independent plugin location reaches every seat
    players = getattr(instance, "players", {}) or {}
    audiences = [Viewer("outsider", "")] + [Viewer("seat", str(uid)) for uid in players]
    for audience in audiences:
        if _location_entries(dependencies, instance, audience):
            return ""
    return "players_see_no_locations"


def _prune_hidden_connections(locations: list[dict[str, Any]]) -> None:
    """Drop edges to locations this viewer cannot see (they would name them)."""

    known = {
        str(token)
        for location in locations
        for token in (location.get("id"), location.get("name"))
        if token
    }
    for location in locations:
        edges = location.get("connected_to")
        if isinstance(edges, list):
            location["connected_to"] = [edge for edge in edges if str(edge) in known]
        else:
            location["connected_to"] = []


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
            media.map_background(instance)
        )
    except ValueError:
        return {"kind": "auto"}


def _content_map_assets(
    dependencies: MapDependencies,
    world_id: str,
) -> dict[str, list[dict[str, Any]]]:
    return dependencies.list_map_assets(world_id)
