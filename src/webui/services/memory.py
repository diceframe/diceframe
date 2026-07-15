"""内存服务：记忆检索。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.webui.api import WebAPI


def list_memories(api: "WebAPI", game_key: str, keyword: str = "",
                  limit: int = 20, offset: int = 0) -> dict[str, Any]:
    # game_key 来自 URL（# 分隔），需转为 str(tuple) 与存储路径一致
    gk = str(api._parse_key(game_key))
    if keyword:
        entries = api._mem.recall(gk, [keyword], limit, offset)
    else:
        entries = api._mem.list_entries(gk, limit, offset)
    total = api._mem.count_entries(gk, keyword)
    return {"memories": entries, "total": total}


async def update_memory(api: "WebAPI", game_key: str, entry_id: int, updates: dict[str, Any]) -> dict[str, Any]:
    ok = await api._mem.edit_entry(str(api._parse_key(game_key)), entry_id, updates)
    return {"ok": ok, "error": "记忆不存在" if not ok else ""}


async def delete_memory(api: "WebAPI", game_key: str, entry_id: int) -> dict[str, Any]:
    ok = await api._mem.forget_entry(str(api._parse_key(game_key)), entry_id)
    return {"ok": ok, "error": "记忆不存在" if not ok else ""}
