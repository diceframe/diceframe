"""Content V2 world-template loader with legacy V1 fallback."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any
from src.engine.language import content_locale_candidates

_DISPLAY_FIELDS = frozenset({"world_name", "description", "world_setting", "starter_scene"})
_LORE_DISPLAY_FIELDS = frozenset({"name", "keywords", "content"})


def _safe_lore_id_part(value: Any) -> str:
    """Mirror the persisted plugin-lore id contract without importing plugin code."""
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_\-一-鿿]+", "_", text)
    return text.strip("_")[:48] or "content"


def localize_lorebook_entries(
    entries: list[dict[str, Any]],
    world_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Overlay starter-lore display fields onto persisted entries for one game.

    Lorebook storage is shared by every game using a world, so localized starter
    text must never be written back to it.  This function keeps the persisted id
    and all mechanics while replacing only the three locale-owned display fields.
    User-created entries are returned unchanged.
    """
    localized = (world_data or {}).get("starter_lorebook", [])
    if not isinstance(localized, list) or not localized:
        return [copy.deepcopy(entry) for entry in entries]

    world_id = str((world_data or {}).get("world_id") or (world_data or {}).get("id") or "")
    by_persisted_id: dict[str, dict[str, Any]] = {}
    for candidate in localized:
        if not isinstance(candidate, dict) or not candidate.get("id"):
            continue
        canonical_id = str(candidate["id"])
        by_persisted_id[canonical_id] = candidate
        if world_id:
            by_persisted_id[f"{world_id}_{canonical_id}"] = candidate

    result: list[dict[str, Any]] = []
    for raw in entries:
        entry = copy.deepcopy(raw)
        entry_id = str(entry.get("id") or "")
        candidate = by_persisted_id.get(entry_id)
        source_plugin = str(entry.get("source_plugin") or "")
        if candidate is None and source_plugin and world_id:
            prefix = f"{_safe_lore_id_part(world_id)}_plugin_{_safe_lore_id_part(source_plugin)}_"
            if entry_id.startswith(prefix):
                persisted_tail = entry_id[len(prefix):]
                candidate = next(
                    (
                        item for item in localized
                        if isinstance(item, dict)
                        and _safe_lore_id_part(item.get("id")) == persisted_tail
                    ),
                    None,
                )
        if candidate is not None:
            for field in _LORE_DISPLAY_FIELDS:
                if field in candidate:
                    entry[field] = copy.deepcopy(candidate[field])
        result.append(entry)
    return result


def materialize_world(core: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Apply a typed world locale without changing canonical lore identity/mechanics."""
    if not isinstance(overlay, dict):
        raise ValueError("world locale overlay must be an object")
    allowed_top = {"locale_schema_version", "locale", "target", "fields", "starter_lorebook"}
    unknown_top = set(overlay) - allowed_top
    if unknown_top:
        raise ValueError(f"world locale contains unknown top-level fields: {sorted(unknown_top)}")
    if overlay.get("locale_schema_version") != 1 or not isinstance(overlay.get("locale"), str) or not overlay["locale"].strip():
        raise ValueError("world locale schema is invalid")
    fields = overlay.get("fields")
    if not isinstance(fields, dict) or set(fields) - _DISPLAY_FIELDS:
        raise ValueError("world locale fields contain mechanics or unknown fields")
    canonical_id = str(core.get("world_id") or core.get("id") or "")
    target = overlay.get("target")
    if not isinstance(target, dict) or target.get("kind") not in {"world", "world_template"}:
        raise ValueError("world locale target kind is invalid")
    if str(target.get("id") or "") != canonical_id:
        raise ValueError("world locale target id is invalid")
    localized_entries = overlay.get("starter_lorebook", {})
    if not isinstance(localized_entries, dict):
        raise ValueError("world locale starter_lorebook must be an object keyed by canonical entry id")
    result = copy.deepcopy(core)
    result.update(copy.deepcopy(fields))
    entries = result.get("starter_lorebook", [])
    if not isinstance(entries, list):
        raise ValueError("world core starter_lorebook must be a list")
    by_id = {str(entry.get("id")): entry for entry in entries if isinstance(entry, dict) and entry.get("id")}
    for entry_id, values in localized_entries.items():
        if str(entry_id) not in by_id or not isinstance(values, dict):
            raise ValueError("world locale references an unknown starter lore entry")
        forbidden = set(values) - _LORE_DISPLAY_FIELDS
        if forbidden:
            raise ValueError(f"world locale lore entry contains mechanics fields: {sorted(forbidden)}")
        if "keywords" in values and not isinstance(values["keywords"], list):
            raise ValueError("world locale lore keywords must be a list")
        by_id[str(entry_id)].update(copy.deepcopy(values))
    result["active_locale"] = str(overlay.get("locale") or result.get("default_locale") or "")
    return result


def load_world_template(worlds_dir: str | Path, world_id: str, locale: str = "") -> dict[str, Any] | None:
    root = Path(worlds_dir)
    world_id = str(world_id or "").strip()
    if not world_id:
        return None
    path = root / f"{world_id}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or int(raw.get("world_schema_version", 1) or 1) < 2:
        return raw
    requested = str(locale or raw.get("default_locale") or "").replace("_", "-")
    if requested:
        for candidate in content_locale_candidates(requested):
            overlay_path = root / "locales" / candidate / f"{world_id}.json"
            if not overlay_path.exists():
                continue
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            if not isinstance(overlay, dict):
                raise ValueError("world locale overlay must be an object")
            if overlay.get("locale_schema_version") != 1 or not str(overlay.get("locale") or "").strip():
                raise ValueError("world locale schema is invalid")
            return materialize_world(raw, overlay)
    raw["active_locale"] = raw.get("default_locale", "")
    return raw
