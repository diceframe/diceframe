"""Editable FU content tables (monsters / items / skills).

Defaults ship under ``templates/rulesets/fabula_ultima/``; the first edit from
the FU data page writes a user copy under ``<data>/templates/fabula_ultima/``
which then takes precedence (see :mod:`src.rulesets.fabula_ultima.paths`).
Runtime code reads through this module so cache invalidation follows the
resolved file and its mtime.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.rulesets.fabula_ultima import paths
from src.rulesets.fabula_ultima.i18n import render
from src.rulesets.fabula_ultima.runtime_messages import MESSAGES

KINDS = ("monsters", "items", "skills")
_CACHE: dict[str, tuple[Path, float, list[dict[str, Any]]]] = {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def load(kind: str, *, include_disabled: bool = True) -> list[dict[str, Any]]:
    if kind not in KINDS:
        raise KeyError(kind)
    path = paths.table_path(kind)
    stamp = _mtime(path)
    cached = _CACHE.get(kind)
    if cached and cached[0] == path and cached[1] == stamp:
        rows = cached[2]
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("entries") if isinstance(data, dict) else data
            rows = [dict(row) for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []
        except (OSError, ValueError):
            rows = []
        _CACHE[kind] = (path, stamp, rows)
    if include_disabled:
        return rows
    return [row for row in rows if bool(row.get("enabled", True))]


def save(kind: str, rows: list[dict[str, Any]]) -> None:
    if kind not in KINDS:
        raise KeyError(kind)
    path = paths.user_table_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {"schema_version": 1, "entries": rows}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    _CACHE.pop(kind, None)


def validate(
    kind: str, entry: Any, existing_ids: set[str] | None = None, language: object = "",
) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError(render(language, MESSAGES, "content_entry_not_object"))
    item_id = str(entry.get("id") or "").strip()
    if not item_id:
        raise ValueError(render(language, MESSAGES, "content_id_required"))
    if len(item_id) > 120:
        raise ValueError(render(language, MESSAGES, "content_id_too_long"))
    if not str(entry.get("name") or "").strip():
        raise ValueError(render(language, MESSAGES, "content_name_required"))
    clean = dict(entry)
    clean["id"] = item_id
    clean["name"] = str(entry.get("name") or item_id).strip()
    clean["enabled"] = bool(entry.get("enabled", True))
    if kind == "monsters":
        for key in ("hp", "defense", "mdef", "initiative"):
            if clean.get(key) is not None:
                clean[key] = max(0, int(clean[key]))
        if not isinstance(clean.get("attacks"), list):
            clean["attacks"] = []
        if not isinstance(clean.get("skills"), list):
            clean["skills"] = []
        if not isinstance(clean.get("affinities"), dict):
            clean["affinities"] = {}
    elif kind == "items":
        category = str(clean.get("category") or "common")
        if category not in {"common", "consumable", "equipment"}:
            raise ValueError(render(language, MESSAGES, "content_category_invalid"))
        clean["category"] = category
        if clean.get("price") is not None:
            clean["price"] = max(0, int(clean["price"]))
        if clean.get("ip_cost") is not None:
            clean["ip_cost"] = max(0, int(clean["ip_cost"]))
        if not isinstance(clean.get("effect"), (dict, type(None))):
            raise ValueError(render(language, MESSAGES, "content_effect_not_object"))
    elif kind == "skills":
        if not isinstance(clean.get("effects"), list):
            clean["effects"] = []
        if not isinstance(clean.get("target"), dict):
            clean["target"] = {"scope": "enemy", "count": 1}
        for key in ("mp", "hp"):
            if clean.get(key) is not None:
                clean[key] = max(0, int(clean[key]))
    return clean


def upsert(kind: str, entry: dict[str, Any], language: object = "") -> dict[str, Any]:
    rows = load(kind)
    existing_ids = {str(row.get("id") or "") for row in rows}
    clean = validate(kind, entry, existing_ids, language)
    for index, row in enumerate(rows):
        if str(row.get("id") or "") == clean["id"]:
            rows[index] = clean
            break
    else:
        rows.append(clean)
    save(kind, rows)
    return clean


def delete(kind: str, item_id: str) -> None:
    rows = [row for row in load(kind) if str(row.get("id") or "") != item_id]
    save(kind, rows)
