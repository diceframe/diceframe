"""Built-in visual presets for the location map.

Presets provide only a background and a default viewport. Locations still come
from the current Lorebook or a declarative map package, so existing saves do
not need a location-data migration.
"""

from __future__ import annotations

from typing import Any


_PRESETS = {
    "fantasy": {
        "asset_id": "fantasy-region-v1",
        "name": "奇幻地域图",
        "description": "适合中世纪、异世界与传统奇幻冒险的地域底图。",
    },
    "occult": {
        "asset_id": "occult-town-v1",
        "name": "调查城镇图",
        "description": "适合克苏鲁、现代悬疑与都市怪谈的调查城镇底图。",
    },
    "cyber": {
        "asset_id": "cyber-city-v1",
        "name": "赛博都市图",
        "description": "适合赛博朋克与近未来都市冒险的城市底图。",
    },
}

# Rules select one of three broad visual themes; they do not own individual
# map assets. ``tavern_free`` is deliberately omitted because its sandbox
# world may not be fantasy themed.
_RULE_THEMES = {
    "base_d20": "fantasy",
    "dnd5e": "fantasy",
    "freeform_fantasy": "fantasy",
    "freeform_wuxia": "fantasy",
    "freeform_coc": "occult",
    "freeform_cyberpunk": "cyber",
}

_WORLD_THEMES = {
    "default_fantasy": "fantasy",
    "zhongshi_fantasy": "fantasy",
    "jp_isekai": "fantasy",
    "tavern_generic": "fantasy",
    "coc_horror": "occult",
    "scifi_cyberpunk": "cyber",
}


def builtin_map_preset(world_id: str, rule_id: str = "") -> dict[str, Any] | None:
    """Return the read-only visual preset recommended for a world and rule."""
    theme = _RULE_THEMES.get(str(rule_id or "")) or _WORLD_THEMES.get(str(world_id or ""))
    return _preset_for_theme(theme or "")


def builtin_map_preset_by_asset(asset_id: str) -> dict[str, Any] | None:
    """Return one built-in preset by its stable asset ID."""
    theme = next(
        (key for key, value in _PRESETS.items() if value["asset_id"] == str(asset_id or "")),
        "",
    )
    return _preset_for_theme(theme)


def builtin_map_options() -> list[dict[str, str]]:
    """Return the three user-selectable built-in visual themes."""
    return [
        {
            "id": value["asset_id"],
            "name": value["name"],
            "description": value["description"],
            "url": f"/v2-assets/ui/maps/{value['asset_id']}.webp",
        }
        for value in _PRESETS.values()
    ]


def _preset_for_theme(theme: str) -> dict[str, Any] | None:
    preset = _PRESETS.get(theme)
    if not preset:
        return None

    asset_id = preset["asset_id"]
    return {
        "id": f"builtin:map:{asset_id}",
        "source_id": asset_id,
        "name": preset["name"],
        "description": preset["description"],
        "mode": "graph",
        "plugin_id": "",
        "plugin_name": "DiceFrame",
        "background": {
            "id": asset_id,
            "ref": f"builtin:scene:{asset_id}",
            "name": preset["name"],
            "url": f"/v2-assets/ui/maps/{asset_id}.webp",
        },
        "default_view": {"x": 0, "y": 0, "zoom": 1},
    }
