"""世界编辑器服务：世界书 CRUD + 条目管理 + 索引重建 + 世界模板列表。"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from src.generation import creator

if TYPE_CHECKING:
    from src.webui.api import WebAPI

logger = logging.getLogger("trpg")

_LOREBOOK_ENTRY_TYPES = {"npc", "location", "item", "event", "puzzle", "faction", "other"}
_LOREBOOK_TIERS = {"core", "background", "archived"}
_MAX_GENERATED_LOREBOOK_CONTENT = 2000


def list_worlds(api: "WebAPI") -> dict[str, Any]:
    worlds = api._lore.list_worlds()
    for w in worlds:
        entries = api._lore.list_entries(w["id"])
        w["entry_count"] = len(entries)
    return {"worlds": worlds, "total": len(worlds)}


def create_world(api: "WebAPI", name: str, description: str = "") -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "世界书名称不能为空"}
    base = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_") or "world"
    world_id = f"custom_book_{base}_{int(time.time())}"
    api._lore.create_world(world_id, name, description=description or "")
    return {"ok": True, "world_id": world_id, "name": name}


def list_entries(api: "WebAPI", world_id: str, entry_type: str | None = None) -> dict[str, Any]:
    entries = api._lore.list_entries(world_id, entry_type)
    return {"entries": entries, "total": len(entries)}


def search_entries(api: "WebAPI", world_id: str, keyword: str) -> dict[str, Any]:
    entries = api._lore.search_entries(world_id, keyword)
    return {"entries": entries, "total": len(entries)}


def get_entry(api: "WebAPI", entry_id: str) -> dict[str, Any] | None:
    return api._lore.get_entry(entry_id)


def save_entry(api: "WebAPI", entry: dict) -> dict[str, Any]:
    api._lore.add_entry(entry)
    rebuild_lorebook_index(api, entry.get("world_id", ""))
    return {"ok": True, "entry_id": entry["id"]}


def _entry_id_from_name(world_id: str, name: str, existing_ids: set[str], index: int) -> str:
    base = "".join(ch if ch.isalnum() else "_" for ch in (name or "entry").lower()).strip("_")
    base = base[:40] or f"entry_{index + 1}"
    entry_id = f"{world_id}_gen_{base}"
    if entry_id not in existing_ids:
        existing_ids.add(entry_id)
        return entry_id
    suffix = int(time.time() * 1000)
    while f"{entry_id}_{suffix}" in existing_ids:
        suffix += 1
    entry_id = f"{entry_id}_{suffix}"
    existing_ids.add(entry_id)
    return entry_id


def _normalize_generated_entry(raw: dict, world_id: str, existing_ids: set[str], index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    content = str(raw.get("content") or "").strip()
    if not name or not content:
        return None
    entry_type = str(raw.get("type") or "other").strip()
    if entry_type not in _LOREBOOK_ENTRY_TYPES:
        entry_type = "other"
    tier = str(raw.get("tier") or "background").strip()
    if tier not in _LOREBOOK_TIERS:
        tier = "background"
    keywords = raw.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = [str(keywords)]
    keywords = [str(k).strip() for k in keywords if str(k).strip()]
    if name not in keywords:
        keywords.insert(0, name)
    return {
        "id": _entry_id_from_name(world_id, name, existing_ids, index),
        "world_id": world_id,
        "name": name,
        "type": entry_type,
        "keywords": keywords[:12],
        "content": content[:_MAX_GENERATED_LOREBOOK_CONTENT],
        "tier": tier,
        "unreliable": bool(raw.get("unreliable", False)),
        "match_mode": "any",
        "order": 100 + index,
    }


async def generate_lorebook_entries(api: "WebAPI", world_id: str, prompt: str) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "请输入要生成的世界书设定"}
    if not api._llm_client:
        return {"ok": False, "error": "当前未配置 AI，无法自动生成世界书条目"}
    world = api._lore.get_world(world_id)
    if not world:
        return {"ok": False, "error": "世界书不存在"}

    existing_entries = api._lore.list_entries(world_id)
    raw_entries = await creator.generate_lorebook_entries(
        api._llm_client,
        prompt,
        world_name=world.get("name", ""),
        existing_names=[e.get("name", "") for e in existing_entries],
        max_tokens=api.character_gen_max_tokens,
    )
    if not raw_entries:
        return {"ok": False, "error": "AI 返回内容解析失败，请换一种描述重试"}

    existing_ids = {e.get("id", "") for e in existing_entries}
    saved = []
    for index, raw in enumerate(raw_entries[:8]):
        entry = _normalize_generated_entry(raw, world_id, existing_ids, index)
        if not entry:
            continue
        api._lore.add_entry(entry)
        saved.append(entry)
    if not saved:
        return {"ok": False, "error": "AI 没有生成可保存的条目，请补充更具体的设定"}
    rebuild_lorebook_index(api, world_id)
    return {"ok": True, "entries": saved, "count": len(saved)}


def update_entry(api: "WebAPI", entry_id: str, updates: dict) -> dict[str, Any]:
    api._lore.update_entry(entry_id, updates)
    # 获取条目所属世界以重建索引
    entry = api._lore.get_entry(entry_id)
    if entry:
        rebuild_lorebook_index(api, entry.get("world_id", ""))
    return {"ok": True}


def delete_entry(api: "WebAPI", entry_id: str) -> dict[str, Any]:
    entry = api._lore.get_entry(entry_id)
    world_id = entry.get("world_id", "") if entry else ""
    api._lore.delete_entry(entry_id)
    if world_id:
        rebuild_lorebook_index(api, world_id)
    return {"ok": True}


def delete_world(api: "WebAPI", world_id: str) -> dict[str, Any]:
    """删除世界及其所有条目。"""
    api._lore.delete_world_cascade(world_id)
    return {"ok": True}


def rebuild_lorebook_index(api: "WebAPI", world_id: str) -> None:
    """重建关键词匹配索引（CRUD 后自动调用）。"""
    if not api._handler or not world_id:
        return
    try:
        entries = api._lore.list_entries(world_id)
        api._handler.matcher.build(entries)
    except Exception:
        logger.exception("重建世界书索引失败: world_id=%s", world_id)


def list_world_templates(api: "WebAPI") -> dict[str, Any]:
    """列出所有可用的世界模板。"""
    templates = []
    worlds_dir = api._worlds_dir
    if worlds_dir and worlds_dir.is_dir():
        for f in sorted(worlds_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                world_id = data.get("world_id", f.stem)
                if "_blank_" in str(world_id) and not data.get("starter_lorebook", []):
                    continue
                templates.append({
                    "world_id": world_id,
                    "world_name": data.get("world_name", f.stem),
                    "description": data.get("description", ""),
                    "language": data.get("language", ""),
                    "suggested_difficulty": data.get("suggested_difficulty", "标准"),
                    "default_rule": data.get("default_rule", "freeform_fantasy"),
                    "lorebook_count": len(data.get("starter_lorebook", [])),
                })
            except Exception:
                logger.warning("世界模板读取失败: %s", f, exc_info=True)
    return {"templates": templates, "total": len(templates)}
