"""内存服务：记忆检索。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


GameKey = tuple[str, ...]


class MemoryRepository(Protocol):
    def recall(
        self, game_key: str, keywords: list[str], limit: int, offset: int,
        *, viewer_is_gm: bool,
    ) -> list[dict[str, Any]]: ...

    def list_entries(
        self, game_key: str, limit: int, offset: int, *, viewer_is_gm: bool,
    ) -> list[dict[str, Any]]: ...

    def count_entries(
        self, game_key: str, keyword: str = "", *, viewer_is_gm: bool,
    ) -> int: ...
    async def edit_entry(
        self, game_key: str, entry_id: int, updates: dict[str, Any],
    ) -> bool: ...
    async def forget_entry(self, game_key: str, entry_id: int) -> bool: ...


@dataclass(frozen=True)
class MemoryDependencies:
    repository: MemoryRepository
    parse_game_key: Callable[[str], GameKey]
    get_instance: Callable[[GameKey], Any | None] | None = None


def _memory_namespace(dependencies: MemoryDependencies, game_key: str) -> str:
    parsed = dependencies.parse_game_key(game_key)
    instance = dependencies.get_instance(parsed) if dependencies.get_instance else None
    return str(getattr(instance, "memory_namespace", "") or str(parsed))


def list_memories(dependencies: MemoryDependencies, game_key: str, keyword: str = "",
                  limit: int = 20, offset: int = 0, *,
                  viewer_is_gm: bool) -> dict[str, Any]:
    """列出记忆；``viewer_is_gm`` 决定是否包含 GM 私有记忆（FIX-05 §7.4）。"""

    # game_key 来自 URL（# 分隔），需转为 str(tuple) 与存储路径一致
    gk = _memory_namespace(dependencies, game_key)
    if keyword:
        entries = dependencies.repository.recall(
            gk, [keyword], limit, offset, viewer_is_gm=viewer_is_gm,
        )
    else:
        entries = dependencies.repository.list_entries(
            gk, limit, offset, viewer_is_gm=viewer_is_gm,
        )
    total = dependencies.repository.count_entries(
        gk, keyword, viewer_is_gm=viewer_is_gm,
    )
    return {"memories": entries, "total": total}


async def update_memory(dependencies: MemoryDependencies, game_key: str, entry_id: int, updates: dict[str, Any]) -> dict[str, Any]:
    ok = await dependencies.repository.edit_entry(
        _memory_namespace(dependencies, game_key), entry_id, updates,
    )
    return {"ok": ok, "error": "记忆不存在" if not ok else ""}


async def delete_memory(dependencies: MemoryDependencies, game_key: str, entry_id: int) -> dict[str, Any]:
    ok = await dependencies.repository.forget_entry(
        _memory_namespace(dependencies, game_key), entry_id,
    )
    return {"ok": ok, "error": "记忆不存在" if not ok else ""}


class MemoryService:
    """Memory management scoped to an explicit repository and key parser."""

    def __init__(self, dependencies: MemoryDependencies) -> None:
        self._dependencies = dependencies

    def list(
        self, game_key: str, keyword: str = "", limit: int = 20, offset: int = 0,
        *, viewer_is_gm: bool,
    ) -> dict[str, Any]:
        return list_memories(
            self._dependencies, game_key, keyword, limit, offset,
            viewer_is_gm=viewer_is_gm,
        )

    async def update(
        self, game_key: str, entry_id: int, updates: dict[str, Any],
    ) -> dict[str, Any]:
        return await update_memory(
            self._dependencies, game_key, entry_id, updates,
        )

    async def delete(self, game_key: str, entry_id: int) -> dict[str, Any]:
        return await delete_memory(self._dependencies, game_key, entry_id)
