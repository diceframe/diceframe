"""世界书视角预览服务：只读投影计算，不包含任何写入。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypedDict

from src.content_modules.projection import ContentProjectionService
from src.knowledge.projection import Viewer, project_entries
from src.knowledge.visibility import classify_audience

logger = logging.getLogger("trpg")
GameKey = tuple[str, ...]


class LorePreviewStore(Protocol):
    def get_world(self, world_id: str) -> dict[str, Any] | None: ...
    def list_entries(self, world_id: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class LorePreviewDependencies:
    lorebook: LorePreviewStore | None
    get_instance: Callable[[GameKey], Any | None]
    parse_game_key: Callable[[str], GameKey]
    content_projection: ContentProjectionService | None = None


class LorePreviewResult(TypedDict):
    payload: dict[str, Any]
    status: int


def _result(payload: dict[str, Any], status: int = 200) -> LorePreviewResult:
    return {"payload": payload, "status": status}


def preview(
    dependencies: LorePreviewDependencies,
    world_id: str,
    viewer: str,
    game_key: str | None = None,
) -> LorePreviewResult:
    """按 ``viewer=gm|party|{uid}`` 投影指定世界的全部条目。

    ``gm``/``party`` 视角不需要游戏上下文；``uid`` 视角必须带 ``game_key``，
    以便从 ``instance.players`` 派生角色名作为次级匹配令牌（同问 GM 流程）。
    """
    viewer = str(viewer or "").strip()
    if not viewer:
        return _result({"ok": False, "code": "INVALID_VIEWER", "error": "视角无效"}, 400)

    world = dependencies.lorebook.get_world(world_id) if dependencies.lorebook else None
    if not world:
        return _result({"ok": False, "code": "WORLD_NOT_FOUND", "error": "世界不存在"}, 404)

    if viewer in {"gm", "party"}:
        resolved = Viewer(viewer)
    else:
        if not game_key:
            return _result({"ok": False, "code": "INVALID_VIEWER", "error": "视角无效"}, 400)
        instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
        if not instance:
            return _result({"ok": False, "code": "GAME_NOT_FOUND", "error": "游戏不存在"}, 404)
        player = instance.players.get(viewer)
        if player is None:
            return _result({"ok": False, "code": "PLAYER_NOT_IN_GAME", "error": "未加入本局"}, 403)
        resolved = Viewer("character", viewer, str(player.get("character_name") or viewer))

    assert dependencies.lorebook is not None
    projection = dependencies.content_projection
    entries = (
        projection.for_world_authoring(world_id)
        if projection is not None
        else dependencies.lorebook.list_entries(world_id)
    )
    projections = project_entries(entries, resolved)
    audience_counts = {"public": 0, "character": 0, "gm": 0}
    for entry in entries:
        audience_counts[classify_audience(entry)] += 1
    summary = {
        "total": len(entries),
        "visible": sum(1 for p in projections.values() if p["visible"]),
        "public": audience_counts["public"],
        "character_only": audience_counts["character"],
        "gm_secret": audience_counts["gm"],
    }
    return _result({
        "ok": True,
        "world_id": world_id,
        "viewer": {"kind": resolved.kind, "uid": resolved.uid, "name": resolved.name},
        "projections": projections,
        "summary": summary,
    })


class LorePreviewService:
    """Read-only lore visibility projections with explicit data boundaries."""

    def __init__(self, dependencies: LorePreviewDependencies) -> None:
        self._dependencies = dependencies

    def preview(
        self, world_id: str, viewer: str, game_key: str | None = None,
    ) -> LorePreviewResult:
        return preview(self._dependencies, world_id, viewer, game_key)
