"""Location-list assembly and current-scene matching for maps."""

from __future__ import annotations

from typing import Any


def lore_locations(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "id": entry.get("id", ""),
        "name": entry.get("name", ""),
        "connected_to": entry.get("connected_to", []),
        "tier": entry.get("tier", "background"),
        "content": entry.get("content", "")[:120],
        "keywords": entry.get("keywords", []),
        "source": "lorebook",
    } for entry in entries]


def merge_contributed_locations(
    locations: list[dict[str, Any]],
    contributed: list[dict[str, Any]],
) -> None:
    existing_ids = {
        str(item.get("id") or item.get("name") or "")
        for item in locations
    }
    for location in contributed:
        location_id = str(location.get("id") or location.get("name") or "")
        if not location_id or location_id in existing_ids:
            continue
        locations.append({
            "id": location_id,
            "name": str(location.get("name") or location_id),
            "connected_to": location.get("connected_to", []),
            "tier": location.get("tier", "background"),
            "content": str(location.get("content") or location.get("description") or "")[:120],
            "keywords": location.get("keywords", []),
            "icon": location.get("icon", ""),
            "image": location.get("image", ""),
            "plugin_id": location.get("plugin_id", ""),
            "plugin_name": location.get("plugin_name", ""),
            "source": "plugin",
        })
        existing_ids.add(location_id)


def match_current_location(
    current_scene: str,
    locations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    exact = next(
        (
            location for location in locations
            if current_scene in {
                str(location.get("id") or ""),
                str(location.get("name") or ""),
            }
        ),
        None,
    )
    if exact:
        return exact
    candidates = [
        location for location in locations
        if str(location.get("name") or "")
        and (
            str(location.get("name")) in current_scene
            or current_scene in str(location.get("name"))
        )
    ]
    return max(candidates, key=lambda item: len(str(item.get("name") or "")), default=None)


def find_map_anchor(
    current_scene: str,
    locations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    best_location = None
    best_score = 0
    for location in locations:
        score = 0
        name = str(location.get("name") or "")
        if name and (name in current_scene or current_scene in name):
            score += 20
        for keyword in location.get("keywords", []):
            if keyword and keyword in current_scene:
                score += 8 + min(len(keyword), 6)
        score += len(set(current_scene) & set(name))
        if score > best_score:
            best_score = score
            best_location = location
    if best_score <= 0:
        return locations[0] if locations else None
    return best_location
