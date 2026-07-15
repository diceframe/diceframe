"""剧情日志服务：日志分页 / 统计。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.webui.api import WebAPI


def get_log(api: "WebAPI", game_key: str, page: int = 1, per_page: int = 50) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"log": [], "total": 0, "page": page}
    log = inst.log
    total = len(log)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "log": log[-end:-start] if start else log[-end:],
        "total": total,
        "page": page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


def get_statistics(api: "WebAPI", game_key: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {}
    battles = sum(1 for e in inst.log if "combat" in e.get("actions", ""))

    player_stats: dict[str, dict] = {}
    for uid, p in inst.players.items():
        name = p.get("character_name", uid)
        player_stats[name] = {"name": name, "actions": 0}
    for entry in inst.log:
        for a in entry.get("actions", []):
            uid = a.get("user_id", "")
            if uid in inst.players:
                name = inst.players[uid].get("character_name", uid)
                player_stats.setdefault(name, {"name": name, "actions": 0})
                player_stats[name]["actions"] = player_stats[name].get("actions", 0) + 1

    return {
        "total_rounds": inst.round_number,
        "total_battles": battles,
        "total_llm_calls": inst.total_llm_calls,
        "total_tokens": inst.total_tokens,
        "player_stats": list(player_stats.values()),
    }
