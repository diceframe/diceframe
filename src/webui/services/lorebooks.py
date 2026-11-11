"""Canonical Lorebook book / binding / entry / import / export use cases.

Moved verbatim from WebAPI (Track API-1). Behaviour is unchanged.
Must not import sibling services; cross-service behaviour arrives injected.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.content_modules.projection import ContentProjection
from src.content_modules.refs import ContentRef, ContentRefError, parse_content_ref
from src.engine.world.contracts import SOURCE_REF_KINDS
from src.lorebook.activation import DEFAULT_VECTOR_ACTIVATION
from src.lorebook.exporter import export_lorebook_native, export_lorebook_v3
from src.lorebook.store import LorebookIdentityConflict
from src.localization.messages import t

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


class LorebookRowProjection:
    """Project one canonical Book onto the existing management-row shape.

    The row is a read-only copy assembled from the canonical book and its
    binding facts.  Keeping this implementation behind the shared
    ``ContentProjection`` contract gives later PR D a real caller without
    changing the current HTTP response or moving ownership into the UI.
    """

    def project(
        self,
        content: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = dict(content)
        bindings = [dict(binding) for binding in ((context or {}).get("bindings") or [])]
        row["bindings"] = bindings
        row["scope"] = _book_scope(bindings)
        row["primary"] = any(str(b.get("role") or "") == "primary" for b in bindings)
        return row


_LOREBOOK_ROW_PROJECTION: ContentProjection = LorebookRowProjection()


def list_lorebooks(
    deps: LorebookDependencies,
    world_id: str = "",
    game_key: str = "",
    *,
    include_all: bool = False,
) -> dict[str, Any]:
    """List canonical books visible in a world-management scope.

    Each row carries the binding-derived ``scope`` / ``primary`` facts the
    management UI has to show (name · Primary · scope · enabled). They are
    derived from the canonical bindings rather than stored on the book, so a
    book bound in several scopes reports the most specific one it has here.
    """

    bindings_by_book: dict[str, list[dict[str, Any]]] = {}
    for binding in deps.lorebook.list_bindings():
        bindings_by_book.setdefault(str(binding.get("book_id") or ""), []).append(binding)
    # The management endpoint keeps its historical world/game/global scope by
    # default.  The content hub and package exporter need an explicit catalogue
    # view so a world-bound book is not silently omitted when no world is
    # selected.  Keeping the switch explicit avoids changing create-game
    # binding semantics for existing callers.
    if include_all:
        scoped_books = deps.lorebook.list_lorebooks()
    else:
        game_books = _game_scoped_lorebooks(deps, game_key) if game_key else []
        if world_id and game_books:
            # 浏览某个世界时，绑定在其它世界上的对局书不属于这个书单：
            # 否则 A 局的 runtime 书会混进 B 世界的列表，条目视图和视角
            # 投影从此指向两个世界（列表停在 A 书、可见性按 B 世界算）。
            # 没有 world binding 的对局/角色书保持可见——它们不属于任何
            # 世界，仍是「当前游戏 / 角色」scope 的一部分。
            def _belongs_to_world(book: dict[str, Any]) -> bool:
                worlds = {
                    str(binding.get("scope_id") or "")
                    for binding in bindings_by_book.get(str(book.get("id") or ""), [])
                    if str(binding.get("scope_kind") or "") == "world"
                }
                return not worlds or world_id in worlds

            game_books = [book for book in game_books if _belongs_to_world(book)]
        scoped_books = [
            *(deps.lorebook.list_lorebooks(scope_kind="world", scope_id=world_id) if world_id else []),
            *deps.lorebook.list_lorebooks(scope_kind="global", scope_id=""),
            *game_books,
        ]
    # 完全没有 binding 的 Book 也必须可见：否则「新建世界书」和「暂不绑定」导入
    # 都会产出一本谁也管不到的隐形书，用户再也无法给它加绑定。
    unbound = [
        book for book in deps.lorebook.list_lorebooks()
        if not bindings_by_book.get(str(book.get("id") or ""))
    ]
    seen: set[str] = set()
    merged = []
    for book in [*scoped_books, *unbound]:
        book_id = str(book.get("id") or "")
        if not book_id or book_id in seen:
            continue
        seen.add(book_id)
        bindings = bindings_by_book.get(book_id, [])
        row = _LOREBOOK_ROW_PROJECTION.project(book, context={"bindings": bindings})
        row["entry_count"] = len(deps.lorebook.list_book_entries(book_id))
        ref = _book_content_ref(book, bindings)
        if ref is not None:
            row["ref"] = ref
        merged.append(row)
    return {"books": merged}


def _book_content_ref(book: Mapping[str, Any], bindings: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Expose canonical provenance for create-game bindings.

    Legacy native books have no portable source identity.  The server adapts
    those rows at this read boundary using their owning scope, while refusing
    to invent a ref when neither a scope nor a stable book id exists.
    """
    book_id = str(book.get("id") or "").strip()
    kind = str(book.get("source_kind") or "").strip().lower()
    source_id = str(book.get("source_id") or "").strip()
    if kind == "native" or kind not in SOURCE_REF_KINDS:
        world_binding = next((b for b in bindings if str(b.get("scope_kind") or "") == "world" and str(b.get("scope_id") or "")), None)
        if world_binding:
            kind, source_id = "world", str(world_binding.get("scope_id") or "")
        elif book_id:
            kind, source_id = "user", book_id
    if not kind or not source_id or not book_id:
        return None
    try:
        return parse_content_ref({
            "source_kind": kind, "source_id": source_id, "kind": "lorebook", "id": book_id,
            "digest": str(book.get("source_digest") or ""),
        }, default_source="").to_dict()
    except (ContentRefError, ValueError, TypeError):
        return None

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

#: Provenance and import identity of a Book. Only server-side import code sets
#: them; the generic create/update use cases never accept them from a client,
#: otherwise a caller could claim (or squat) another source's identity.
BOOK_IDENTITY_FIELDS = frozenset({
    "source_kind", "source_id", "external_id", "source_version", "source_digest",
    "import_link", "import_state_digest",
})


def _without_identity(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if key not in BOOK_IDENTITY_FIELDS}


def _identity_conflict(exc: LorebookIdentityConflict) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "error_code": exc.code}


def create_lorebook(deps: LorebookDependencies, book: dict[str, Any]) -> dict[str, Any]:
    book = _without_identity(dict(book))
    if not str(book.get("id") or "").strip() or not str(book.get("name") or "").strip():
        return {"ok": False, "error": "id and name are required"}
    if deps.lorebook.get_lorebook(book["id"]):
        return {"ok": False, "error": "Lorebook already exists"}
    try:
        deps.lorebook.create_lorebook(book)
    except LorebookIdentityConflict as exc:
        return _identity_conflict(exc)
    return {"ok": True, "book": deps.lorebook.get_lorebook(book["id"])}

def update_lorebook(deps: LorebookDependencies, book_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    if deps.lorebook.get_lorebook(book_id) is None:
        return {"ok": False, "error": "Lorebook not found"}
    deps.lorebook.update_lorebook(book_id, _without_identity(dict(updates)))
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
    get_book_entry = getattr(deps.lorebook, "get_book_entry", None)
    if deps.lorebook.get_entry(entry_id) is not None:
        # Ownership isolation: entry.id is a global canonical PK, but it may
        # only be rewritten through the book that actually owns it.
        if not deps.lorebook.update_book_entry(book_id, entry_id, payload):
            return {"ok": False, "error": "Entry belongs to another lorebook",
                    "error_code": "entry_book_mismatch", "entry_id": entry_id}
        saved = get_book_entry(book_id, entry_id) if callable(get_book_entry) else deps.lorebook.get_entry(entry_id)
        return {"ok": True, "entry": saved}
    # Canonical new-entry defaults apply on create only. An update must be
    # able to clear a field without it silently snapping back to a default.
    for key, value in CANONICAL_ENTRY_DEFAULTS.items():
        payload.setdefault(key, value)
    add_book_entry = getattr(deps.lorebook, "add_book_entry", None)
    if callable(add_book_entry):
        add_book_entry(book_id, payload)
    else:
        # Compatibility stores from older integrations still expose only the
        # legacy add_entry adapter; keep this fallback at the service boundary.
        deps.lorebook.add_entry(payload)
    saved = get_book_entry(book_id, entry_id) if callable(get_book_entry) else deps.lorebook.get_entry(entry_id)
    return {"ok": True, "entry": saved}

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

def _legacy_existing_refs(deps: LorebookDependencies) -> list[Any]:
    from src.content_modules.refs import ContentRef

    existing_refs: list[ContentRef] = []
    for book in deps.lorebook.list_lorebooks():
        source_kind = str(book.get("source_kind") or "").strip()
        source_id = str(book.get("source_id") or "").strip()
        if not source_kind or not source_id:
            continue
        # The legacy plan identifies a Book by its source alone: Books that
        # name an external id, or no longer follow their source, are not it.
        if str(book.get("external_id") or "") or str(book.get("import_link") or "") == "detached":
            continue
        try:
            existing_refs.append(ContentRef(
                source_kind=source_kind,
                source_id=source_id,
                kind="lorebook",
                id=str(((book.get("settings") or {}).get("diceframe_import") or {}).get("external_id") or source_id),
                explicit=True,
                digest=str(book.get("source_digest") or ""),
            ))
        except ValueError:
            # Historical books without portable provenance are not silently
            # matched to an external draft; they remain explicit legacy data.
            continue
    return existing_refs


def _import_request(body: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """Split a preview/commit body into the Lorebook document and its identity.

    ``{"payload": {...}, "source": ..., "external_id": ...}`` carries an
    explicit document; any other body *is* the document (legacy preview).
    """

    from src.content_modules.plan import declared_import_source

    if isinstance(body.get("payload"), dict):
        return body["payload"], declared_import_source(body.get("source"), body.get("external_id"))
    return body, None


def _import_plan_view(plan: Any) -> dict[str, Any]:
    from src.content_modules.plan import plan_digest

    return {"plan_digest": plan_digest([plan.item]), "items": [plan.item.to_portable_dict()]}


def preview_lorebook_import(deps: LorebookDependencies, payload: dict[str, Any]) -> dict[str, Any]:
    from dataclasses import asdict
    from src.lorebook.import_plan import plan_lorebook_import
    from src.lorebook.importer import preview_lorebook_import

    try:
        document, declared = _import_request(payload)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": IMPORT_SOURCE_INVALID}
    result = preview_lorebook_import(
        document,
        existing_refs=_legacy_existing_refs(deps),
        duplicate_policy=str(payload.get("duplicate_policy") or "update"),
    )
    result["book"] = asdict(result["book"])
    for key in ("content_draft", "commit_plan"):
        if key in result:
            result[key] = result[key].to_portable_dict()
    result["plan"] = _import_plan_view(
        plan_lorebook_import(deps.lorebook, document, declared=declared)
    )
    return result


def _binding_error(binding: Any) -> dict[str, Any] | None:
    from src.lorebook.store import normalize_scope_kind

    if binding is None:
        return None
    if not isinstance(binding, dict):
        return {"ok": False, "error": "binding must be an object"}
    if "scope_kind" not in binding:
        return {"ok": False, "error": "binding requires a canonical scope_kind"}
    try:
        normalize_scope_kind(binding.get("scope_kind"))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return None


def commit_lorebook_import(
    deps: LorebookDependencies,
    payload: dict[str, Any],
    binding: dict[str, Any] | None = None,
    book_id: str | None = None,
    plan_digest: str = "",
    duplicate_policy: str = "update",
) -> dict[str, Any]:
    from src.lorebook.importer import commit_lorebook_import, draft_lorebook_import

    draft = draft_lorebook_import(payload)
    preview_payload = dict(payload)
    if duplicate_policy not in {"update", "duplicate", "skip"}:
        return {"ok": False, "error": "invalid duplicate policy"}
    # Changing the explicit conflict choice is part of confirmation. Fence the
    # content and catalog against the originally reviewed plan first.
    current = preview_lorebook_import(deps, preview_payload)
    if (current.get("commit_plan") or {}).get("unresolved_references"):
        return {"ok": False, "code": "IMPORT_UNRESOLVED_REFERENCES", "error": "Import has unresolved content references"}
    if plan_digest:
        if str(current.get("plan_digest") or "") != str(plan_digest):
            return {"ok": False, "code": "IMPORT_PLAN_STALE", "error": t("lorebooks.import_preview_expired")}
    operations = (current.get("commit_plan") or {}).get("operations") or []
    matched = [row for row in operations if isinstance(row, dict) and row.get("existing_ref")]
    if matched and duplicate_policy == "skip":
        return {"ok": True, "skipped": True, "entries": 0, "warnings": list(draft.warnings)}
    if duplicate_policy == "duplicate":
        if book_id:
            return {"ok": False, "error": "duplicate requires a new book target"}
        import uuid
        book_id = f"import:copy:{uuid.uuid4().hex}"
    elif matched and not book_id:
        ref = matched[0]["existing_ref"]
        candidates = []
        for book in deps.lorebook.list_lorebooks():
            external = str(((book.get("settings") or {}).get("diceframe_import") or {}).get("external_id") or book.get("source_id") or "")
            if (book.get("source_kind"), book.get("source_id"), external) == (ref["source_kind"], ref["source_id"], ref["id"]):
                candidates.append(str(book["id"]))
        if len(candidates) > 1:
            return {"ok": False, "error": "multiple matching books; select an explicit existing target"}
        if candidates:
            book_id = candidates[0]
    # Validate the requested binding before any canonical write so a bad
    # scope cannot leave a half-imported book behind.
    invalid = _binding_error(binding)
    if invalid is not None:
        return invalid
    try:
        imported_book_id = commit_lorebook_import(deps.lorebook, draft, binding, book_id=book_id)
    except LorebookIdentityConflict as exc:
        return _identity_conflict(exc)
    except ValueError as exc:
        # The store boundary is the authority for scope validity.
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "book_id": imported_book_id, "entries": len(draft.entries), "warnings": draft.warnings}


IMPORT_SOURCE_INVALID = "IMPORT_SOURCE_INVALID"
BINDING_REQUIRES_WRITE = "BINDING_REQUIRES_WRITE"
BOOK_ID_NOT_ALLOWED = "BOOK_ID_NOT_ALLOWED"


def commit_lorebook_plan(deps: LorebookDependencies, body: dict[str, Any]) -> dict[str, Any]:
    """Commit a previewed plan: revalidate it, then apply the user's decision.

    The server recomputes the plan from the re-sent document and the current
    store. If it no longer matches the ``plan_digest`` the user confirmed, the
    commit is refused with ``PLAN_STALE`` and a fresh preview, so a decision is
    never applied to a state the user did not see.
    """

    from src.content_modules.plan import PLAN_STALE, PlanDecisionError, plan_digest, resolve_decision
    from src.lorebook.import_plan import execute_lorebook_plan, plan_lorebook_import

    if not isinstance(body.get("payload"), dict):
        return {"ok": False, "error": "payload must be an object"}
    if body.get("book_id"):
        # A plan targets the Book its identity resolves to; importing into an
        # arbitrary Book stays on the legacy route.
        return {"ok": False, "error": "book_id cannot be combined with a plan",
                "error_code": BOOK_ID_NOT_ALLOWED}
    binding = body.get("binding")
    invalid = _binding_error(binding)
    if invalid is not None:
        return invalid
    try:
        document, declared = _import_request(body)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": IMPORT_SOURCE_INVALID}

    plan = plan_lorebook_import(deps.lorebook, document, declared=declared)
    current_digest = plan_digest([plan.item])
    if str(body.get("plan_digest") or "") != current_digest:
        return {
            "ok": False, "error_code": PLAN_STALE,
            "error": "the server state changed since the preview; review the new plan",
            "preview": preview_lorebook_import(deps, body),
        }
    try:
        decision = resolve_decision(plan.item, body.get("decision"))
        writes_nothing = decision == "skip" or (
            decision == "update" and plan.item.action == "unchanged"
        )
        if binding and writes_nothing:
            # A binding is applied with the Book it belongs to; an answer
            # that writes nothing must not silently drop it.
            return {"ok": False, "error_code": BINDING_REQUIRES_WRITE,
                    "error": "a binding can only be applied with update, duplicate or create"}
        result = execute_lorebook_plan(deps.lorebook, plan, decision, binding=binding)
    except PlanDecisionError as exc:
        return {"ok": False, "error": str(exc), "error_code": exc.code}
    except LorebookIdentityConflict as exc:
        return _identity_conflict(exc)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    book = deps.lorebook.get_lorebook(result["book_id"]) or {}
    return {
        "ok": True, **result,
        "state_token": str(int(book.get("revision") or 0)),
        "entries": len(plan.draft.entries),
        "warnings": plan.draft.warnings,
    }


def create_reviewed_lorebook(deps: LorebookDependencies, payload: dict[str, Any], book_id: str) -> dict[str, Any]:
    """Create a user-owned reviewed book at an explicit identity without upserting."""
    from src.lorebook.importer import commit_lorebook_import, draft_lorebook_import
    from src.engine.world.contracts import canonical_id
    identity = canonical_id(book_id, field="reviewed book id")
    if preview_lorebook_import(deps, payload)["commit_plan"]["unresolved_references"]:
        raise ValueError("unresolved lorebook references")
    with deps.lorebook.transaction():
        if deps.lorebook.get_lorebook(identity) is not None:
            raise ValueError("lorebook target already exists")
        draft = draft_lorebook_import(payload)
        draft.source.update({"kind": "user", "id": identity, "source_id": identity, "external_id": identity})
        commit_lorebook_import(deps.lorebook, draft, book_id=identity)
    return {"ok": True, "book_id": identity, "entries": len(draft.entries), "warnings": draft.warnings}
