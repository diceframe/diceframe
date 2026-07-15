"""地图服务：地点列表渲染。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.webui.api import WebAPI


def get_map_locations(api: "WebAPI", game_key: str) -> dict[str, Any]:
    """获取游戏世界的地点列表（供地图渲染）。"""
    inst = api._reg.get(api._parse_key(game_key))
    if not inst or not inst.world_id:
        return {"locations": [], "current_scene": ""}
    entries = api._lore.list_entries(inst.world_id, "location")
    locations = []
    for e in entries:
        locations.append({
            "id": e.get("id", ""),
            "name": e.get("name", ""),
            "connected_to": e.get("connected_to", []),
            "tier": e.get("tier", "background"),
            "content": e.get("content", "")[:120],
            "keywords": e.get("keywords", []),
        })
    current_scene = inst.scene or ""
    if current_scene and locations:
        matched = any(
            loc["name"] == current_scene
            or loc["name"] in current_scene
            or current_scene in loc["name"]
            for loc in locations
        )
        if not matched:
            anchor = _find_map_anchor(current_scene, locations)
            locations.append({
                "id": "__current_scene__",
                "name": current_scene,
                "connected_to": [anchor["id"]] if anchor else [],
                "tier": "current",
                "content": "当前剧情场景，尚未写入世界书地点条目。",
                "keywords": [],
            })
    return {
        "locations": locations,
        "current_scene": current_scene,
    }


def _find_map_anchor(current_scene: str, locations: list[dict]) -> dict | None:
    """为未入库的当前场景寻找最相关的既有地点。"""
    best_loc = None
    best_score = 0
    for loc in locations:
        score = 0
        name = loc.get("name", "")
        if name and (name in current_scene or current_scene in name):
            score += 20
        for kw in loc.get("keywords", []):
            if kw and kw in current_scene:
                score += 8 + min(len(kw), 6)
        # 轻量字符重合，处理「前往冒险者公会途中」匹配「冒险者公会总部」。
        overlap = len(set(current_scene) & set(name))
        score += overlap
        if score > best_score:
            best_score = score
            best_loc = loc
    if best_score <= 0:
        return locations[0] if locations else None
    return best_loc
