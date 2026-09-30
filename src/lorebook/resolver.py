from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class BookRef:
    book_id: str
    order: int = 100
    binding_id: str = ""
    role: str = ""
    scan_depth: int = 0
    token_budget: int = 0
    recursive_scanning: bool = False
    fuzzy_enabled: bool = False
    settings: dict[str, Any] | None = None
    updated_at: str = ""
    revision: int = 0

def resolve_active_books(instance: Any, viewer_kind: str = "gm", viewer_uid: str = "", action_actor_uids: list[str] | None = None, *, store: Any | None = None) -> list[BookRef]:
    store = store or getattr(instance, "lorebook_store", None) or getattr(instance, "lorebook", None)
    world_id = str(getattr(instance, "world_id", "") or "")
    if not store:
        return []
    bindings = store.list_bindings()
    actors = set(str(uid) for uid in action_actor_uids or [])
    game_scopes = {str(getattr(instance, "game_id", "") or "")}
    raw_game_key = getattr(instance, "game_key", "")
    if raw_game_key:
        game_scopes.add(str(raw_game_key))
        if isinstance(raw_game_key, (tuple, list)):
            game_scopes.add("|".join(str(part) for part in raw_game_key))
    refs = []
    books = {}
    if hasattr(store, "get_lorebook"):
        for binding in bindings:
            book_id = str(binding.get("book_id") or "")
            if book_id and book_id not in books:
                books[book_id] = store.get_lorebook(book_id) or {}
    for binding in bindings:
        if not binding.get("enabled", True):
            continue
        kind, scope = str(binding.get("scope_kind", "")), str(binding.get("scope_id", ""))
        book = books.get(str(binding.get("book_id") or ""), {})
        # Disabling a Book is a runtime decision, not just a Sidebar label: a
        # disabled Book must stop contributing candidates entirely. Missing Book
        # rows stay tolerated (legacy bindings) and default to enabled.
        if not book.get("enabled", True):
            continue
        settings = book.get("settings") if isinstance(book.get("settings"), dict) else {}
        explicit_fuzzy = None
        for key in ("fuzzy_enabled", "fuzzy_matching"):
            if key in settings:
                explicit_fuzzy = bool(settings[key])
                break
        source_kind = str(book.get("source_kind") or "").strip().lower()
        fuzzy_enabled = (
            explicit_fuzzy
            if explicit_fuzzy is not None
            else source_kind in {"world", "legacy", "legacy_world"}
        )
        common = dict(
            scan_depth=int(book.get("scan_depth", 0) or 0),
            token_budget=int(book.get("token_budget", 0) or 0),
            recursive_scanning=bool(book.get("recursive_scanning", False)),
            fuzzy_enabled=fuzzy_enabled,
            settings=settings,
            updated_at=str(book.get("updated_at") or ""),
            revision=int(book.get("revision", 0) or 0),
        )
        if kind == "global":
            refs.append(BookRef(str(binding["book_id"]), int(binding.get("order", 100)), str(binding.get("id", "")), str(binding.get("role", "")), **common))
        elif kind == "world" and scope == world_id:
            refs.append(BookRef(str(binding["book_id"]), int(binding.get("order", 100)), str(binding.get("id", "")), str(binding.get("role", "")), **common))
        elif kind == "game" and scope in game_scopes:
            refs.append(BookRef(str(binding["book_id"]), int(binding.get("order", 100)), str(binding.get("id", "")), str(binding.get("role", "")), **common))
        elif kind == "character" and viewer_kind != "party" and (scope == viewer_uid or scope in actors):
            refs.append(BookRef(str(binding["book_id"]), int(binding.get("order", 100)), str(binding.get("id", "")), str(binding.get("role", "")), **common))
    return sorted({ref.book_id: ref for ref in refs}.values(), key=lambda ref: (ref.order, ref.book_id, ref.binding_id))
