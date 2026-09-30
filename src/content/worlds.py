"""Content V2 world-template loader with legacy V1 fallback."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DISPLAY_FIELDS = frozenset({"world_name", "description", "world_setting", "starter_scene"})
_LORE_DISPLAY_FIELDS = frozenset({"name", "keywords", "content"})
_BOOK_DISPLAY_FIELDS = frozenset({"name", "description"})
_WORLD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


@dataclass(frozen=True)
class WorldDraft:
    """Portable World definition produced before canonical persistence.

    A WorldDraft deliberately carries only the World definition and a
    source-aware reference to its primary Book.  Entries belong to a separate
    LorebookDraft in the legacy adapter or import pipeline; keeping the two
    drafts separate prevents a template from reintroducing ``starter_lorebook``
    as a second ownership authority.
    """

    world_id: str = ""
    name: str = ""
    description: str = ""
    language: str = "zh-CN"
    default_rule: str = ""
    recommended_rules: tuple[str, ...] = ()
    primary_lorebook_ref: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-portable draft without embedding Lorebook entries."""

        result = {
            "world_schema_version": 3,
            "world_id": self.world_id,
            "world_name": self.name,
            "description": self.description,
            "language": self.language,
            "default_rule": self.default_rule,
            "recommended_rules": list(self.recommended_rules),
            "primary_lorebook_ref": self.primary_lorebook_ref,
        }
        result.update(copy.deepcopy(self.fields))
        if self.source:
            result["source"] = copy.deepcopy(self.source)
        if self.warnings:
            result["warnings"] = list(self.warnings)
        return result


def _safe_resource_id(reference: Any) -> str:
    """Resolve a portable ``kind:id`` ref to a filename-safe terminal id."""

    text = str(reference or "").strip()
    resource_id = text.rsplit(":", 1)[-1]
    if not resource_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", resource_id):
        raise ValueError("content reference id is invalid")
    return resource_id


def _world_draft_from_definition(data: dict[str, Any]) -> WorldDraft:
    version = int(data.get("world_schema_version", 1) or 1)
    if version != 3:
        raise ValueError("WorldDraft requires world_schema_version 3")
    if "starter_lorebook" in data:
        raise ValueError("world schema v3 cannot embed starter_lorebook")
    world_id = str(data.get("world_id") or data.get("id") or "").strip()
    primary_ref = str(data.get("primary_lorebook_ref") or "").strip()
    if not world_id or not _WORLD_ID_PATTERN.fullmatch(world_id):
        raise ValueError("world schema v3 requires a valid world_id")
    if not primary_ref:
        raise ValueError("world schema v3 requires primary_lorebook_ref")
    recommended = data.get("recommended_rules", [])
    if isinstance(recommended, str):
        recommended = [recommended]
    if not isinstance(recommended, list) or not all(isinstance(item, str) for item in recommended):
        raise ValueError("world schema v3 recommended_rules must be a list of strings")
    reserved = {
        "world_schema_version", "world_id", "id", "world_name", "name", "description",
        "language", "default_rule", "recommended_rules", "primary_lorebook_ref",
        "source", "warnings",
    }
    fields = {key: copy.deepcopy(value) for key, value in data.items() if key not in reserved}
    return WorldDraft(
        world_id=world_id,
        name=str(data.get("world_name") or data.get("name") or world_id),
        description=str(data.get("description") or ""),
        language=str(data.get("language") or "zh-CN"),
        default_rule=str(data.get("default_rule") or ""),
        recommended_rules=tuple(recommended),
        primary_lorebook_ref=primary_ref,
        fields=fields,
        source=copy.deepcopy(data.get("source") or {}),
        warnings=tuple(str(item) for item in data.get("warnings", []) if item),
    )


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


def materialize_lorebook(core: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Apply a locale overlay to an independent Book resource.

    The overlay is intentionally narrower than a World overlay: it may change
    display/linguistic fields of the Book and its entries, but never ownership,
    retrieval mechanics, bindings, or entry ids.
    """

    if not isinstance(core, dict) or not isinstance(overlay, dict):
        raise ValueError("lorebook locale resources must be objects")
    if overlay.get("locale_schema_version") != 1 or not str(overlay.get("locale") or "").strip():
        raise ValueError("lorebook locale schema is invalid")
    target = overlay.get("target")
    if not isinstance(target, dict) or target.get("kind") not in {"lorebook", "book"}:
        raise ValueError("lorebook locale target kind is invalid")
    book_id = str(core.get("id") or core.get("book_id") or "")
    if str(target.get("id") or "") != book_id:
        raise ValueError("lorebook locale target id is invalid")
    fields = overlay.get("fields", {})
    if not isinstance(fields, dict) or set(fields) - _BOOK_DISPLAY_FIELDS:
        raise ValueError("lorebook locale fields contain mechanics or unknown fields")
    raw_entries = core.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ValueError("lorebook entries must be a list")
    localized_entries = overlay.get("entries", {})
    if not isinstance(localized_entries, dict):
        raise ValueError("lorebook locale entries must be keyed by canonical entry id")
    result = copy.deepcopy(core)
    result.update(copy.deepcopy(fields))
    by_id = {
        str(entry.get("id")): entry
        for entry in result.get("entries", [])
        if isinstance(entry, dict) and entry.get("id")
    }
    for entry_id, values in localized_entries.items():
        if str(entry_id) not in by_id or not isinstance(values, dict):
            raise ValueError("lorebook locale references an unknown entry")
        forbidden = set(values) - _LORE_DISPLAY_FIELDS
        if forbidden:
            raise ValueError(f"lorebook locale entry contains mechanics fields: {sorted(forbidden)}")
        if "keywords" in values and not isinstance(values["keywords"], list):
            raise ValueError("lorebook locale keywords must be a list")
        by_id[str(entry_id)].update(copy.deepcopy(values))
    result["active_locale"] = str(overlay.get("locale") or result.get("language") or "")
    return result


def load_world_definition(worlds_dir: str | Path, world_id: str, locale: str = "") -> dict[str, Any] | None:
    """Load only the canonical World definition.

    v3 worlds reference a separate Book resource and never receive an embedded
    ``starter_lorebook``.  v1/v2 documents remain readable here for the legacy
    adapter and are returned unchanged apart from their existing locale view.
    """

    root = Path(worlds_dir)
    world_id = str(world_id or "").strip()
    if not world_id:
        return None
    path = root / f"{world_id}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("world template must be a JSON object")
    version = int(raw.get("world_schema_version", 1) or 1)
    requested = str(locale or raw.get("default_locale") or "").replace("_", "-")
    if version >= 3:
        _world_draft_from_definition(raw)
        if requested:
            exact = root / "locales" / requested / f"{world_id}.json"
            fallback = root / "locales" / requested.split("-", 1)[0] / f"{world_id}.json"
            overlay_path = exact if exact.exists() else fallback
            if overlay_path.exists():
                overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
                if not isinstance(overlay, dict):
                    raise ValueError("world locale overlay must be an object")
                raw = materialize_world(raw, overlay)
        result = copy.deepcopy(raw)
        result["active_locale"] = str(raw.get("active_locale") or raw.get("default_locale") or "")
        return result
    if version < 2:
        return raw
    if requested:
        exact = root / "locales" / requested / f"{world_id}.json"
        fallback = root / "locales" / requested.split("-", 1)[0] / f"{world_id}.json"
        overlay_path = exact if exact.exists() else fallback
        if overlay_path.exists():
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            if not isinstance(overlay, dict):
                raise ValueError("world locale overlay must be an object")
            return materialize_world(raw, overlay)
    raw["active_locale"] = raw.get("default_locale", "")
    return raw


def load_lorebook_resource(
    worlds_dir: str | Path,
    book_ref: str,
    locale: str = "",
) -> dict[str, Any] | None:
    """Load a v3 Book resource from ``templates/lorebooks`` plus its locale."""

    root = Path(worlds_dir)
    book_id = _safe_resource_id(book_ref)
    path = root / "lorebooks" / f"{book_id}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or int(raw.get("lorebook_schema_version", 1) or 1) != 1:
        raise ValueError("lorebook resource schema is invalid")
    if str(raw.get("id") or book_id) != book_id:
        raise ValueError("lorebook resource id does not match its filename")
    raw.setdefault("id", book_id)
    requested = str(locale or raw.get("default_locale") or "").replace("_", "-")
    if requested:
        exact = root / "lorebooks" / "locales" / requested / f"{book_id}.json"
        fallback = root / "lorebooks" / "locales" / requested.split("-", 1)[0] / f"{book_id}.json"
        overlay_path = exact if exact.exists() else fallback
        if overlay_path.exists():
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            raw = materialize_lorebook(raw, overlay)
    raw["active_locale"] = str(raw.get("active_locale") or raw.get("default_locale") or "")
    return raw


def load_world_template(worlds_dir: str | Path, world_id: str, locale: str = "") -> dict[str, Any] | None:
    root = Path(worlds_dir)
    world = load_world_definition(root, world_id, locale)
    if world is None:
        return None
    if int(world.get("world_schema_version", 1) or 1) < 3:
        return world
    # This function is the compatibility facade used by pre-v3 callers.  New
    # services should consume ``load_world_definition`` and the Book resource
    # separately instead of treating the returned list as World-owned data.
    book = load_lorebook_resource(root, str(world.get("primary_lorebook_ref") or ""), locale)
    if book is None:
        raise ValueError("world primary_lorebook_ref resource is missing")
    result = copy.deepcopy(world)
    result["starter_lorebook"] = copy.deepcopy(book.get("entries", []))
    result["_legacy_adapter"] = "world_v3_lorebook"
    return result
