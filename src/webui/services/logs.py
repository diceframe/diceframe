"""剧情日志服务：日志分页 / 统计。"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from src.llm.parser import sanitize_narration

GameKey = tuple[str, ...]

PUBLIC_LOG_FIELDS = frozenset({
    "round", "actions", "player_actions", "gm_response", "state_changes",
    "check_results", "swipes", "current_swipe", "timestamp",
    "story_recaps", "scene_image",
})

# Historical action renderers use the actor identity and display text only.
# Live-action revision/dice status and internal adjudication are separate contracts.
PUBLIC_ACTION_FIELDS = frozenset({"user_id", "text"})


def _public_action(action: Any) -> Any:
    if not isinstance(action, dict):
        return action
    return {
        key: copy.deepcopy(value)
        for key, value in action.items()
        if key in PUBLIC_ACTION_FIELDS
    }


class LogRegistry(Protocol):
    def get(self, game_key: GameKey) -> Any | None: ...


@dataclass(frozen=True)
class LogDependencies:
    registry: LogRegistry
    parse_game_key: Callable[[str], GameKey]


def get_log(
    dependencies: LogDependencies,
    game_key: str,
    page: int = 1,
    per_page: int = 50,
    include_internal: bool = False,
) -> dict[str, Any]:
    inst = dependencies.registry.get(dependencies.parse_game_key(game_key))
    if not inst:
        return {"log": [], "total": 0, "page": page}
    log = inst.log
    total = len(log)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = copy.deepcopy(log[-end:-start] if start else log[-end:])
    for entry in page_items:
        entry["gm_response"] = sanitize_narration(entry.get("gm_response", ""))
        swipes = entry.get("swipes")
        if isinstance(swipes, list):
            entry["swipes"] = [
                sanitize_narration(item) if isinstance(item, str) else item
                for item in swipes
            ]
    if not include_internal:
        for entry in page_items:
            for field in ("actions", "player_actions"):
                actions = entry.get(field)
                if not isinstance(actions, list):
                    continue
                entry[field] = [
                    _public_action(action) for action in actions
                    if not (
                        isinstance(action, dict)
                        and action.get("user_id") == "system"
                        and str(action.get("text") or "").lstrip().startswith(("【GM指令】", "[GM Directive]"))
                    )
                ]
        page_items = [
            {key: value for key, value in entry.items() if key in PUBLIC_LOG_FIELDS}
            for entry in page_items
        ]
    return {
        "log": page_items,
        "total": total,
        "page": page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


def get_statistics(dependencies: LogDependencies, game_key: str) -> dict[str, Any]:
    inst = dependencies.registry.get(dependencies.parse_game_key(game_key))
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


class GameLogService:
    """Read-only public log and statistics projections."""

    def __init__(self, dependencies: LogDependencies) -> None:
        self._dependencies = dependencies

    def get_log(
        self,
        game_key: str,
        page: int = 1,
        per_page: int = 50,
        include_internal: bool = False,
    ) -> dict[str, Any]:
        return get_log(
            self._dependencies, game_key, page, per_page, include_internal,
        )

    def get_statistics(self, game_key: str) -> dict[str, Any]:
        return get_statistics(self._dependencies, game_key)
