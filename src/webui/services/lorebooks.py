"""Canonical Lorebook book / binding / entry / import / export use cases.

Moved verbatim from WebAPI (Track API-1). Behaviour is unchanged.
Must not import sibling services; cross-service behaviour arrives injected.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.lorebook.activation import DEFAULT_VECTOR_ACTIVATION
from src.lorebook.exporter import export_lorebook_native, export_lorebook_v3

#: Canonical defaults for a *newly created* entry. Legacy adapter drafts and the
#: world-copy / NPC compatibility paths keep their own historical defaults; these
#: apply only at the canonical create-entry boundary.
CANONICAL_ENTRY_DEFAULTS: dict[str, Any] = {
    "priority": 100,
    "order": 100,
    "prompt_slot": "world_background",
    "vector_activation": DEFAULT_VECTOR_ACTIVATION,
}


@dataclass(frozen=True)
class LorebookDependencies:
    lorebook: Any
    get_instance: Callable[[str], Any | None]
    get_lore_retriever: Callable[[], Any | None]


def list_lorebooks(deps: LorebookDependencies, world_id: str = "", game_key: str = "") -> dict[str, Any]:
    """List canonical books visible in a world-management scope.

    Each row carries the binding-derived ``scope`` / ``primary`` facts the
    management UI has to show (name · Primary · scope · enabled). They are
    derived from the canonical bindings rather than stored on the book, so a
    book bound in several scopes reports the most specific one it has here.
    """

    bindings_by_book: dict[str, list[dict[str, Any]]] = {}
    for binding in deps.lorebook.list_bindings():
        bindings_by_book.setdefault(str(binding.get("book_id") or ""), []).append(binding)
    books = deps.lorebook.list_lorebooks(scope_kind="world", scope_id=world_id) if world_id else []
    global_books = deps.lorebook.list_lorebooks(scope_kind="global", scope_id="")
    game_books = _game_scoped_lorebooks(deps, game_key) if game_key else []
    # 完全没有 binding 的 Book 也必须可见：否则「新建世界书」和「暂不绑定」导入
    # 都会产出一本谁也管不到的隐形书，用户再也无法给它加绑定。
    unbound = [
        book for book in deps.lorebook.list_lorebooks()
        if not bindings_by_book.get(str(book.get("id") or ""))
    ]
    seen: set[str] = set()
    merged = []
    for book in [*books, *global_books, *game_books, *unbound]:
        book_id = str(book.get("id") or "")
        if not book_id or book_id in seen:
            continue
        seen.add(book_id)
        row = dict(book)
        bindings = bindings_by_book.get(book_id, [])
        row["bindings"] = bindings
        row["scope"] = _book_scope(bindings)
        row["primary"] = any(str(b.get("role") or "") == "primary" for b in bindings)
        merged.append(row)
    return {"books": merged}

def _game_scoped_lorebooks(deps: LorebookDependencies, game_key: str) -> list[dict[str, Any]]:
    """Books bound to this game, or to one of its characters.

    Without this the workspace's 当前游戏 / 角色 scope filters would be
    decorative: such books exist in the canonical store but would never be
    listed for management.
    """

    rows = list(deps.lorebook.list_lorebooks(scope_kind="game", scope_id=game_key))
    instance = deps.get_instance(game_key) if game_key else None
    players = getattr(instance, "players", None) if instance is not None else None
    for uid in sorted(players or {}):
        rows.extend(deps.lorebook.list_lorebooks(scope_kind="character", scope_id=str(uid)))
    return rows

def _book_scope(bindings: list[dict[str, Any]]) -> str:
    """The scope label for a book: most specific binding wins, else unbound."""

    order = ("character", "game", "world", "global")
    kinds = {str(binding.get("scope_kind") or "") for binding in bindings}
    for kind in order:
        if kind in kinds:
            return kind
    return ""

def create_lorebook(deps: LorebookDependencies, book: dict[str, Any]) -> dict[str, Any]:
    book = dict(book)
    if not str(book.get("id") or "").strip() or not str(book.get("name") or "").strip():
        return {"ok": False, "error": "id and name are required"}
    if deps.lorebook.get_lorebook(book["id"]):
        return {"ok": False, "error": "Lorebook already exists"}
    deps.lorebook.create_lorebook(book)
    return {"ok": True, "book": deps.lorebook.get_lorebook(book["id"])}

def update_lorebook(deps: LorebookDependencies, book_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    if deps.lorebook.get_lorebook(book_id) is None:
        return {"ok": False, "error": "Lorebook not found"}
    deps.lorebook.update_lorebook(book_id, updates)
    return {"ok": True, "book": deps.lorebook.get_lorebook(book_id)}

def delete_lorebook(deps: LorebookDependencies, book_id: str) -> dict[str, Any]:
    try:
        deleted = deps.lorebook.delete_lorebook(book_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": deleted, **({} if deleted else {"error": "Lorebook not found"}), "book_id": book_id}

def list_lorebook_bindings(deps: LorebookDependencies, book_id: str) -> dict[str, Any]:
    if deps.lorebook.get_lorebook(book_id) is None:
        return {"ok": False, "error": "Lorebook not found", "bindings": []}
    return {"ok": True, "bindings": [b for b in deps.lorebook.list_bindings() if b.get("book_id") == book_id]}

def create_lorebook_binding(deps: LorebookDependencies, book_id: str, binding: dict[str, Any]) -> dict[str, Any]:
    if deps.lorebook.get_lorebook(book_id) is None:
        return {"ok": False, "error": "Lorebook not found"}
    payload = dict(binding)
    payload["book_id"] = book_id
    if not str(payload.get("id") or "").strip():
        return {"ok": False, "error": "id is required"}
    deps.lorebook.bind_lorebook(payload)
    return {"ok": True, "binding": next((b for b in deps.lorebook.list_bindings() if b["id"] == payload["id"]), None)}

def update_lorebook_binding(deps: LorebookDependencies, binding_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    try:
        changed = deps.lorebook.update_binding(binding_id, updates)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not changed:
        return {"ok": False, "error": "Binding not found"}
    return {"ok": True, "binding": next((b for b in deps.lorebook.list_bindings() if b["id"] == binding_id), None)}

def delete_lorebook_binding(deps: LorebookDependencies, binding_id: str) -> dict[str, Any]:
    try:
        deleted = deps.lorebook.delete_binding(binding_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": deleted, **({} if deleted else {"error": "Binding not found"}), "binding_id": binding_id}

def list_lorebook_entries(deps: LorebookDependencies, book_id: str) -> dict[str, Any]:
    book = deps.lorebook.get_lorebook(book_id)
    if not book:
        return {"ok": False, "error": "Lorebook not found", "entries": []}
    return {"ok": True, "book": book, "entries": deps.lorebook.list_book_entries(book_id)}

def save_lorebook_entry(deps: LorebookDependencies, book_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    book_id = str(book_id or "")
    if deps.lorebook.get_lorebook(book_id) is None:
        return {"ok": False, "error": "Lorebook not found", "error_code": "book_not_found"}
    payload = dict(entry)
    payload["book_id"] = book_id
    payload.setdefault("id", f"{book_id}:entry:{time.time_ns()}")
    entry_id = str(payload["id"])
    if deps.lorebook.get_entry(entry_id) is not None:
        # Ownership isolation: entry.id is a global canonical PK, but it may
        # only be rewritten through the book that actually owns it.
        if not deps.lorebook.update_book_entry(book_id, entry_id, payload):
            return {"ok": False, "error": "Entry belongs to another lorebook",
                    "error_code": "entry_book_mismatch", "entry_id": entry_id}
        return {"ok": True, "entry": deps.lorebook.get_entry(entry_id)}
    # Canonical new-entry defaults apply on create only. An update must be
    # able to clear a field without it silently snapping back to a default.
    for key, value in CANONICAL_ENTRY_DEFAULTS.items():
        payload.setdefault(key, value)
    deps.lorebook.add_entry(payload)
    return {"ok": True, "entry": deps.lorebook.get_entry(entry_id)}

def move_lorebook_entry(
    deps: LorebookDependencies, book_id: str, entry_id: str, target_book_id: str,
) -> dict[str, Any]:
    """Move an entry to another book, keeping its canonical entry id."""

    target_book_id = str(target_book_id or "")
    if deps.lorebook.get_lorebook(str(book_id or "")) is None:
        return {"ok": False, "error": "Lorebook not found", "error_code": "book_not_found"}
    if not target_book_id or deps.lorebook.get_lorebook(target_book_id) is None:
        return {"ok": False, "error": "Target lorebook not found",
                "error_code": "target_book_not_found"}
    if not deps.lorebook.move_entry(str(book_id), target_book_id, str(entry_id or "")):
        # Distinguish "no such entry" from "that entry belongs to another
        # book": the second is an ownership conflict, not a 404.
        if deps.lorebook.get_entry(str(entry_id or "")) is not None:
            return {"ok": False, "error": "Entry belongs to another lorebook",
                    "error_code": "entry_book_mismatch", "entry_id": str(entry_id)}
        return {"ok": False, "error": "Entry not found in this lorebook",
                "error_code": "entry_not_found", "entry_id": str(entry_id)}
    return {
        "ok": True, "entry": deps.lorebook.get_entry(str(entry_id)),
        "entry_id": str(entry_id), "book_id": target_book_id,
    }

def delete_lorebook_entry(deps: LorebookDependencies, book_id: str, entry_id: str) -> dict[str, Any]:
    # Ownership isolation: a DELETE through book A must never remove an
    # entry that lives in book B.
    book_id = str(book_id or "")
    if deps.lorebook.get_lorebook(book_id) is None:
        return {"ok": False, "error": "Lorebook not found", "error_code": "book_not_found"}
    if not deps.lorebook.delete_book_entry(book_id, str(entry_id or "")):
        return {"ok": False, "error": "Entry not found in this lorebook",
                "error_code": "entry_not_found", "entry_id": entry_id}
    return {"ok": True, "entry_id": entry_id}

def export_lorebook(deps: LorebookDependencies, book_id: str) -> dict[str, Any]:
    book = deps.lorebook.get_lorebook(book_id)
    if not book:
        return {"ok": False, "error": "Lorebook not found"}
    v3 = export_lorebook_v3(deps.lorebook, book_id)
    return {"ok": True, "format": "lorebook_v3", **v3,
            "native_backup": export_lorebook_native(deps.lorebook, book_id)}

async def lorebook_activation_preview(deps: LorebookDependencies, payload: dict[str, Any]) -> dict[str, Any]:
    """Dry-run the same retriever used by rounds and return its safe trace."""
    game_key = str(payload.get("game_key") or "")
    instance = deps.get_instance(game_key) if game_key else None
    retriever = deps.get_lore_retriever()
    if instance is None or retriever is None:
        return {"ok": False, "error": "game_key must reference an active game"}
    viewer: Any = payload.get("viewer") if isinstance(payload.get("viewer"), dict) else {}
    viewer_is_gm = bool(viewer.get("is_gm", True))
    viewer_uid = str(viewer.get("uid") or "")
    matches = await retriever.retrieve(
        instance, str(payload.get("action_text") or ""),
        viewer_is_gm=viewer_is_gm, viewer_uid=viewer_uid or None,
        viewer_name=str(viewer.get("name") or ""), mutate_timers=False,
        action_actor_uids=[viewer_uid] if viewer_uid and not viewer_is_gm else [],
    )
    trace = list(getattr(instance, "lorebook_activation_trace", []) or [])
    return {"ok": True, "entries": matches if viewer_is_gm else [row for row in matches if row.get("id")], "trace": trace}

def preview_lorebook_import(deps: LorebookDependencies, payload: dict[str, Any]) -> dict[str, Any]:
    from dataclasses import asdict
    from src.lorebook.importer import preview_lorebook_import

    result = preview_lorebook_import(payload)
    result["book"] = asdict(result["book"])
    return result

def commit_lorebook_import(deps: LorebookDependencies, payload: dict[str, Any], binding: dict[str, Any] | None = None, book_id: str | None = None) -> dict[str, Any]:
    from src.lorebook.importer import commit_lorebook_import, draft_lorebook_import
    from src.lorebook.store import normalize_scope_kind

    draft = draft_lorebook_import(payload)
    # Validate the requested binding before any canonical write so a bad
    # scope cannot leave a half-imported book behind.
    if binding is not None:
        if not isinstance(binding, dict):
            return {"ok": False, "error": "binding must be an object"}
        if "scope_kind" not in binding:
            return {"ok": False, "error": "binding requires a canonical scope_kind"}
        try:
            normalize_scope_kind(binding.get("scope_kind"))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    try:
        imported_book_id = commit_lorebook_import(deps.lorebook, draft, binding, book_id=book_id)
    except ValueError as exc:
        # The store boundary is the authority for scope validity.
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "book_id": imported_book_id, "entries": len(draft.entries), "warnings": draft.warnings}
