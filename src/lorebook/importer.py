"""Preview-first import service for all Lorebook formats."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from src.lorebook.adapters import from_legacy_entries, from_lorebook_v3, from_sillytavern
from src.lorebook.adapters.legacy import diceframe_compat_fields
from src.lorebook.activation import normalize_primary_match_mode, normalize_selective_logic
from src.lorebook.domain import LorebookDraft


def detect_lorebook_format(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return "diceframe_legacy"
    if payload.get("spec") == "lorebook_v3" or "lorebook" in payload and isinstance(payload.get("lorebook"), dict):
        return "lorebook_v3"
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if isinstance(inner.get("character_book"), dict):
        return "character_card_v3"
    rows = payload.get("entries", [])
    if isinstance(rows, list) and any(isinstance(row, dict) and any(k in row for k in ("key", "keysecondary", "selectiveLogic", "uid")) for row in rows):
        return "sillytavern"
    return "diceframe_legacy"


def draft_lorebook_import(payload: dict[str, Any]) -> LorebookDraft:
    fmt = detect_lorebook_format(payload)
    if fmt == "lorebook_v3":
        return from_lorebook_v3(payload)
    if fmt == "sillytavern":
        return from_sillytavern(payload)
    if fmt == "character_card_v3":
        inner = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        book = inner.get("character_book", {}) if isinstance(inner, dict) else {}
        return from_lorebook_v3({"spec": "lorebook_v3", "data": {"lorebook": book}})
    return from_legacy_entries(payload)


def character_card_identity(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The card's own identity, when the payload is a Character Card with a book.

    The product flow has to tell the user *whose* lore this is before asking where
    to bind it, and a card carries no DiceFrame uid — so only the name is
    reported here. Resolving that name onto a canonical character is the caller's
    decision (it is the side that knows the current game's roster).
    """

    if detect_lorebook_format(payload) != "character_card_v3":
        return None
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(inner, dict):
        return None
    book = inner.get("character_book") if isinstance(inner.get("character_book"), dict) else {}
    rows = book.get("entries") if isinstance(book.get("entries"), list) else []
    return {
        "name": str(inner.get("name") or inner.get("char_name") or "").strip(),
        "book_name": str(book.get("name") or "").strip(),
        "entries": len(rows),
    }


def preview_lorebook_import(payload: dict[str, Any]) -> dict[str, Any]:
    fmt = detect_lorebook_format(payload)
    draft = draft_lorebook_import(payload)
    unsupported = sum(1 for warning in draft.warnings if "unsupported" in warning.lower())
    return {"format": fmt, "book": draft, "counts": {"entries": len(draft.entries), "mapped": len(draft.entries), "warnings": len(draft.warnings), "unsupported": unsupported}, "warnings": draft.warnings, "features": {"timed": any(bool(e.timed) for e in draft.entries), "recursive": any(bool(e.recursion_flags) for e in draft.entries)}, "character": character_card_identity(payload)}


def commit_lorebook_import(store: Any, draft: LorebookDraft, binding: dict[str, Any] | None = None, *, book_id: str | None = None) -> str:
    if not book_id:
        fingerprint = hashlib.sha256(json.dumps({"source": draft.source, "name": draft.name, "entries": [entry.external_id for entry in draft.entries]}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        book_id = str(draft.source.get("id") or f"import:{draft.source.get('kind', 'external')}:{fingerprint}")
    # The canonical Book owns the raw unknown extensions so that
    # import -> DB -> export lorebook_v3 -> reimport can round-trip them.
    # Empty buckets carry nothing and stay out of settings.
    settings = dict(draft.settings)
    preserved = {key: value for key, value in draft.preserved_extensions.items() if value}
    if preserved:
        settings["preserved_extensions"] = preserved

    def _write_all() -> None:
        store.create_lorebook({
            "id": book_id,
            "name": draft.name,
            "description": draft.description,
            "language": draft.language,
            "settings": settings,
            "scan_depth": draft.settings.get("scan_depth", 0),
            "token_budget": draft.settings.get("token_budget", 0),
            "recursive_scanning": draft.settings.get("recursive_scanning", False),
            "source_kind": draft.source.get("kind", "import"),
        })
        if binding:
            store.bind_lorebook({"id": binding.get("id", f"binding:{book_id}"), "book_id": book_id, **{k: v for k, v in binding.items() if k != "id"}})
        for index, entry in enumerate(draft.entries):
            entry_key = entry.external_id or str(index)
            if draft.source.get("entry_id_mode") == "external" and entry.external_id:
                candidate_id = entry.external_id
            else:
                candidate_id = f"{book_id}:entry:{hashlib.sha256(entry_key.encode('utf-8')).hexdigest()[:16]}"
            # entry.id is the DiceFrame global canonical id and the external format
            # id is provenance only. Reusing an external id must never let one book
            # overwrite an entry that already belongs to another book.
            existing = store.get_entry(candidate_id) if hasattr(store, "get_entry") else None
            if existing is not None and str(existing.get("book_id") or "") != str(book_id):
                entry_id = f"{book_id}:entry:{hashlib.sha256(entry_key.encode('utf-8')).hexdigest()[:16]}"
            else:
                entry_id = candidate_id
            provenance = {**draft.source, "external_id": entry.external_id} if entry.external_id else dict(draft.source)
            # Legacy DiceFrame primary matching and the SillyTavern selective
            # secondary logic are two different concepts and stay in their own
            # columns; the latter never gates activation when the primary missed.
            match_mode = normalize_primary_match_mode(entry.match_mode)
            selective_logic = normalize_selective_logic(entry.selective_logic)
            scope_world = (binding or {}).get("scope_id") if (binding or {}).get("scope_kind") == "world" else None
            primary_book_id = (
                store.primary_world_book_id(scope_world)
                if scope_world and hasattr(store, "primary_world_book_id")
                else f"world:{scope_world}" if scope_world else None
            )
            # Only the legacy primary-world-book path carries the compatibility
            # world_id. Independent books retain canonical book_id/binding only.
            world_id = (
                scope_world
                if scope_world
                and book_id == primary_book_id
                and (not hasattr(store, "get_world") or store.get_world(scope_world))
                else None
            )
            payload = {"id": entry_id, "book_id": book_id, "world_id": world_id, "name": entry.name, "content": entry.content, "keywords": entry.keys, "secondary_keys": entry.secondary_keys, "enabled": entry.enabled, "is_constant": entry.constant, "match_mode": match_mode, "selective_logic": selective_logic, "selective": entry.selective, "use_regex": entry.use_regex, "regex_executable": entry.regex_executable, "case_sensitive": entry.case_sensitive, "match_whole_words": entry.match_whole_words, "scan_depth": entry.scan_depth, "priority": entry.priority, "order": entry.insertion_order, "probability": entry.probability, "groups": entry.groups, "group_weight": entry.group_weight, "sticky": entry.timed.get("sticky", 0), "cooldown": entry.timed.get("cooldown", 0), "delay": entry.timed.get("delay", 0), "prompt_slot": entry.prompt_slot, "prioritize_inclusion": entry.prioritize_inclusion, "group_scoring": entry.group_scoring, "vector_activation": entry.vector_activation, "non_recursable": entry.recursion_flags.get("non_recursable", False), "prevent_further_recursion": entry.recursion_flags.get("prevent_further_recursion", False), "delay_until_recursion": entry.recursion_flags.get("delay_until_recursion", False), "recursion_level": int(entry.recursion_flags.get("recursion_level", 0) or 0), "provenance": provenance, "extensions": entry.extensions}
            payload.update(diceframe_compat_fields(entry))
            if hasattr(store, "get_entry") and store.get_entry(entry_id) is not None:
                # Once a store exposes the Book-scoped boundary, never let an
                # import update an entry through its global id alone.  The
                # legacy method remains a compatibility fallback for older
                # integrations that do not yet expose ownership-aware CRUD.
                update_book_entry = getattr(store, "update_book_entry", None)
                if callable(update_book_entry):
                    if not update_book_entry(book_id, entry_id, payload):
                        raise ValueError("entry does not belong to imported book")
                else:
                    store.update_entry(entry_id, payload)
            else:
                add_book_entry = getattr(store, "add_book_entry", None)
                if callable(add_book_entry):
                    add_book_entry(book_id, payload)
                else:
                    store.add_entry(payload)

    # One atomic commit for book + bindings + entries + provenance: a fatal
    # failure must roll back instead of leaving a half-imported book behind.
    if hasattr(store, "transaction"):
        with store.transaction():
            _write_all()
    else:
        _write_all()
    return book_id
