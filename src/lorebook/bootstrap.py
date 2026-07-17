"""Lorebook bootstrap helpers for built-in world templates."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger("trpg")


def ensure_world_from_template(lorebook_store: Any, world_id: str, template: dict[str, Any]) -> int:
    """Ensure one template world and its starter entries exist in the lorebook DB."""
    if not lorebook_store or not world_id or not template:
        return 0
    if not lorebook_store.get_world(world_id):
        lorebook_store.create_world(
            world_id,
            template.get("world_name", world_id),
            description=template.get("description", ""),
            language=template.get("language", "zh-CN"),
        )

    inserted = 0
    for raw_entry in template.get("starter_lorebook", []):
        if not isinstance(raw_entry, dict) or not raw_entry.get("id"):
            continue
        entry = deepcopy(raw_entry)
        entry["world_id"] = world_id
        entry_id = str(entry["id"])
        existing = lorebook_store.get_entry(entry_id)
        if existing and existing.get("world_id") == world_id:
            continue
        if existing and existing.get("world_id") != world_id:
            entry["id"] = f"{world_id}_{entry_id}"
            if lorebook_store.get_entry(entry["id"]):
                continue
        lorebook_store.add_entry(entry)
        inserted += 1
    return inserted


def seed_builtin_worlds(lorebook_store: Any, worlds_dir: Path) -> int:
    """Import bundled starter lorebooks so the lorebook page is useful on first run."""
    if not lorebook_store or not worlds_dir.is_dir():
        return 0
    total = 0
    for path in sorted(worlds_dir.glob("*.json")):
        if path.name.startswith("ai_") or "_copy_" in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            world_id = str(data.get("world_id") or path.stem).strip()
            if not world_id or not data.get("starter_lorebook"):
                continue
            total += ensure_world_from_template(lorebook_store, world_id, data)
        except Exception:
            logger.warning("内置世界书初始化失败: %s", path, exc_info=True)
    if total:
        logger.info("已初始化内置世界书条目: %d", total)
    return total
