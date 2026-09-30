"""Lorebook SQLite 存储 —— 世界书条目的 CRUD 操作。

查询构造走 peewee（src.lorebook.models）；连接、PRAGMA、SCHEMA 建表、
user_version 迁移与事务提交仍由本类持有，行为契约与迁移前一致。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator
from contextlib import contextmanager

from peewee import SQL

from src.lorebook.activation import (
    CANONICAL_VECTOR_ACTIVATION,
    DEFAULT_VECTOR_ACTIVATION,
)
from src.lorebook.models import Lorebook, LorebookBinding, LorebookEntry, LorebookEmbedding, World
from src.lorebook.models import database as _models_database
from src.migrations.lorebook import migrate as migrate_lorebook

logger = logging.getLogger("trpg")

#: Book fields that change what retrieval actually produces. Mutating any of
#: them must advance the Book's monotonic ``revision`` so the resolver/retriever
#: cache cannot keep serving the previous annotation.
_RUNTIME_BOOK_FIELDS = frozenset({
    "enabled", "scan_depth", "token_budget", "recursive_scanning", "settings_json",
})

# 单次 IN(...) 查询的 entry 数量上限，避免撞 SQLite 的参数个数限制。
_CACHE_CHUNK = 400

# Canonical binding scope vocabulary (docs/ARCHITECTURE §3823).
# This is the single source of truth: every write path that accepts a
# ``scope_kind`` must validate here at the store boundary, so import flows and
# non-HTTP callers cannot bypass the REST routes' local check.
CANONICAL_SCOPE_KINDS: frozenset[str] = frozenset({"global", "world", "game", "character"})


def normalize_scope_kind(value: Any) -> str:
    """Validate and normalize a binding scope kind, raising on non-canonical input."""

    kind = str(value or "").strip().lower()
    if kind not in CANONICAL_SCOPE_KINDS:
        raise ValueError(
            f"invalid scope_kind {value!r}: must be one of "
            f"{sorted(CANONICAL_SCOPE_KINDS)}"
        )
    return kind


def normalize_vector_activation(value: Any) -> str:
    """Normalize a vector activation mode.

    Unknown / missing values resolve to the canonical default (``hybrid``), the
    same direction ``LoreRetriever._vector_mode`` inherits in. A malformed value
    must not silently mean "semantic disabled": ``off`` is reserved for an
    explicit author decision, and a write-path fallback that disagreed with the
    read path is what let ``off`` become unreachable in the first place.
    """

    mode = str(value or "").strip().lower()
    return mode if mode in CANONICAL_VECTOR_ACTIVATION else DEFAULT_VECTOR_ACTIVATION


SCHEMA = """
CREATE TABLE IF NOT EXISTS worlds (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    language TEXT DEFAULT 'zh-CN',
    author TEXT DEFAULT '',
    version TEXT DEFAULT '1.0',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lorebook_entries (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'other',
    keywords TEXT NOT NULL DEFAULT '[]',
    content TEXT NOT NULL DEFAULT '',
    unreliable INTEGER DEFAULT 0,
    sync_on_enter INTEGER DEFAULT 0,
    tier TEXT DEFAULT 'background'
        CHECK(tier IN ('core','background','archived')),
    triggers_recursive TEXT DEFAULT '[]',
    visible_to TEXT DEFAULT '[]',
    is_constant INTEGER DEFAULT 0,
    match_mode TEXT DEFAULT 'any' CHECK(match_mode IN ('any','all','not_any','not_all')),
    sticky INTEGER DEFAULT 0,
    cooldown INTEGER DEFAULT 0,
    delay INTEGER DEFAULT 0,
    "order" INTEGER DEFAULT 100,
    probability INTEGER DEFAULT 100,
    "group" TEXT DEFAULT '',
    group_weight INTEGER DEFAULT 1,
    connected_to TEXT DEFAULT '[]',
    source_plugin TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lorebook_embeddings (
    entry_id TEXT NOT NULL,
    language TEXT NOT NULL,
    embedding_profile TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (entry_id, language, embedding_profile)
);
"""

_CURRENT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_lorebook_world ON lorebook_entries(world_id)",
    "CREATE INDEX IF NOT EXISTS idx_lorebook_type ON lorebook_entries(world_id, type)",
    "CREATE INDEX IF NOT EXISTS idx_lorebook_tier ON lorebook_entries(world_id, tier)",
    "CREATE INDEX IF NOT EXISTS idx_lorebook_source ON lorebook_entries(source_plugin)",
)


class LorebookStore:
    """世界书 SQLite 存储管理器。

    V1 使用单连接 + threading.Lock，读多写少的场景足够。
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._tx_depth = 0

    def _commit_locked(self) -> None:
        """Commit per-method, unless an explicit canonical transaction owns it."""

        if self._tx_depth == 0:
            self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Explicit transaction seam for multi-write canonical paths (import).

        Wrapping ``book + bindings + entries + provenance`` makes the whole
        import commit atomic: any fatal failure rolls everything back instead of
        leaving a half-imported book behind. Nested use is allowed and the
        outermost transaction owns the commit. This is a seam, not an ORM
        rewrite — per-method commits stay the default everywhere else.
        """

        if self._tx_depth:
            self._tx_depth += 1
            try:
                yield
            finally:
                self._tx_depth -= 1
            return
        self._tx_depth = 1
        try:
            yield
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._tx_depth = 0
        self._conn.commit()

    def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        migrate_lorebook(self._conn)
        for statement in _CURRENT_INDEXES:
            self._conn.execute(statement)
        if self._conn.execute("PRAGMA foreign_key_check").fetchone():
            raise sqlite3.IntegrityError("lorebook foreign key check failed")
        self._conn.commit()
        _models_database.attach(self._conn)
        logger.info("Lorebook 数据库已打开: %s", self.db_path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            _models_database.detach()
            logger.info("Lorebook 数据库已关闭")

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        assert self._conn, "数据库未打开"
        with self._lock:
            return self._conn.execute(sql, params)

    # ---- 世界 CRUD ----

    def create_world(self, world_id: str, name: str, **kwargs) -> None:
        # INSERT OR REPLACE 会重置 created_at 并按外键级联清掉旧条目，
        # 与迁移前行为一致，属既有语义（模板导入依赖）。
        with self._lock:
            World.insert(
                id=world_id,
                name=name,
                description=kwargs.get("description", ""),
                language=kwargs.get("language", "zh-CN"),
                author=kwargs.get("author", ""),
                version=kwargs.get("version", "1.0"),
            ).on_conflict_replace().execute()
            self._ensure_primary_book_locked(world_id, name=name, language=kwargs.get("language", "zh-CN"))
            self._commit_locked()

    def get_world(self, world_id: str) -> dict | None:
        with self._lock:
            world = World.get_or_none(World.id == world_id)
        return dict(world.__data__) if world else None

    def update_world_language(self, world_id: str, language: str) -> None:
        """Correct world language metadata without replacing the world or its entries."""
        with self._lock:
            World.update(
                language=language, updated_at=SQL("datetime('now')"),
            ).where(World.id == world_id).execute()
            self._commit_locked()

    def list_worlds(self) -> list[dict]:
        with self._lock:
            rows = list(World.select().order_by(World.updated_at.desc()))
        return [dict(w.__data__) for w in rows]

    def delete_world(self, world_id: str) -> None:
        with self._lock:
            Lorebook.delete().where(Lorebook.id == self.primary_world_book_id(world_id)).execute()
            World.delete().where(World.id == world_id).execute()
            self._commit_locked()

    # ---- canonical lorebook/book bindings ----

    @staticmethod
    def primary_world_book_id(world_id: str) -> str:
        return f"world:{world_id}"

    def _ensure_primary_book_locked(self, world_id: str, *, name: str | None = None,
                                    language: str = "zh-CN") -> str:
        book_id = self.primary_world_book_id(world_id)
        world = self._conn.execute(
            "SELECT name, description, language FROM worlds WHERE id = ?", (world_id,)
        ).fetchone()
        name = name or (str(world[0]) if world else world_id)
        description = str(world[1]) if world else ""
        language = str(world[2] or language) if world else language
        self._conn.execute(
            "INSERT OR IGNORE INTO lorebooks "
            "(id, name, description, language, source_kind, source_id) VALUES (?, ?, ?, ?, 'world', ?)",
            (book_id, name, description, language, world_id),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO lorebook_bindings "
            "(id, book_id, scope_kind, scope_id, role) VALUES (?, ?, 'world', ?, 'primary')",
            (f"binding:{book_id}:primary", book_id, world_id),
        )
        return book_id

    def ensure_primary_world_book(self, world_id: str) -> str:
        with self._lock:
            book_id = self._ensure_primary_book_locked(world_id)
            self._commit_locked()
            return book_id

    def _bump_book_revision_locked(self, book_id: str | None) -> None:
        """Advance the owning Book's monotonic revision after an entry mutation.

        Callers must already hold ``self._lock`` and stay inside the same
        transaction, so an import rollback also rolls the bump back. Entries with
        no owning Book (legacy rows) are ignored.
        """

        if not book_id:
            return
        Lorebook.update(revision=Lorebook.revision + 1).where(
            Lorebook.id == str(book_id)
        ).execute()

    def create_lorebook(self, book: dict) -> None:
        with self._lock:
            Lorebook.insert(
                id=book["id"], name=book.get("name", book["id"]),
                description=book.get("description", ""), language=book.get("language", "zh-CN"),
                enabled=int(book.get("enabled", True)), scan_depth=int(book.get("scan_depth", 0)),
                token_budget=int(book.get("token_budget", 0)),
                recursive_scanning=int(book.get("recursive_scanning", False)),
                settings_json=json.dumps(book.get("settings", book.get("settings_json", {})), ensure_ascii=False)
                if not isinstance(book.get("settings_json"), str) else book["settings_json"],
                source_kind=book.get("source_kind", "native"), source_id=book.get("source_id", ""),
                source_version=book.get("source_version", ""), source_digest=book.get("source_digest", ""),
            ).on_conflict_ignore().execute()
            self._commit_locked()

    def update_lorebook(self, book_id: str, updates: dict) -> bool:
        """Update editable book metadata; unknown fields are ignored."""
        allowed = {"name", "description", "language", "enabled", "scan_depth",
                   "token_budget", "recursive_scanning", "settings_json",
                   "source_version", "source_digest"}
        fields = {key: value for key, value in updates.items() if key in allowed}
        if "settings_json" in fields and not isinstance(fields["settings_json"], str):
            fields["settings_json"] = json.dumps(fields["settings_json"], ensure_ascii=False)
        for key in ("enabled", "recursive_scanning"):
            if key in fields:
                fields[key] = int(bool(fields[key]))
        for key in ("scan_depth", "token_budget"):
            if key in fields:
                fields[key] = int(fields[key])
        if not fields:
            return self.get_lorebook(book_id) is not None
        with self._lock:
            changed = Lorebook.update(**fields, updated_at=SQL("datetime('now')")).where(
                Lorebook.id == book_id).execute()
            # A Book's own retrieval settings change what the matcher produces just
            # as much as its entries do, so they must move the monotonic revision
            # too. ``updated_at`` alone is second-resolution and would let a
            # same-second settings edit keep serving the previous annotation.
            if changed and _RUNTIME_BOOK_FIELDS & set(fields):
                self._bump_book_revision_locked(book_id)
            self._commit_locked()
        return bool(changed)

    def delete_lorebook(self, book_id: str) -> bool:
        """Delete a non-primary book, its bindings/entries, and derived embeddings."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM lorebooks WHERE id = ?", (book_id,)
            ).fetchone()
            if row is None:
                return False
            primary = self._conn.execute(
                "SELECT 1 FROM lorebook_bindings WHERE book_id = ? "
                "AND role = 'primary' AND scope_kind = 'world' LIMIT 1", (book_id,)
            ).fetchone()
            # Primary is a relationship owned by the Binding row.  The
            # ``world:<id>`` book id is only a compatibility projection and
            # must not become a second authority for deletion semantics.
            if primary is not None:
                raise ValueError("primary world lorebook cannot be deleted")
            entry_ids = [r[0] for r in self._conn.execute(
                "SELECT id FROM lorebook_entries WHERE book_id = ?", (book_id,)
            ).fetchall()]
            self._conn.execute("DELETE FROM lorebooks WHERE id = ?", (book_id,))
            self._delete_embeddings_locked(entry_ids)
            self._commit_locked()
            return True

    def get_lorebook(self, book_id: str) -> dict | None:
        with self._lock:
            row = Lorebook.get_or_none(Lorebook.id == book_id)
        return _book_to_dict(row) if row else None

    def list_lorebooks(self, *, scope_kind: str | None = None, scope_id: str | None = None) -> list[dict]:
        with self._lock:
            query = Lorebook.select()
            if scope_kind is not None or scope_id is not None:
                query = query.join(LorebookBinding, on=(LorebookBinding.book_id == Lorebook.id))
                if scope_kind is not None:
                    query = query.where(LorebookBinding.scope_kind == scope_kind)
                if scope_id is not None:
                    query = query.where(LorebookBinding.scope_id == scope_id)
                query = query.distinct()
            rows = list(query.order_by(Lorebook.updated_at.desc()))
        return [_book_to_dict(row) for row in rows]

    def bind_lorebook(self, binding: dict) -> None:
        # Canonical boundary: reject non-canonical scopes here so import flows
        # and service-layer callers cannot persist rogue bindings.
        scope_kind = normalize_scope_kind(binding.get("scope_kind"))
        with self._lock:
            LorebookBinding.insert(
                id=binding["id"], book_id=binding["book_id"], scope_kind=scope_kind,
                scope_id=binding.get("scope_id", ""), role=binding.get("role", ""),
                enabled=int(binding.get("enabled", True)), order=int(binding.get("order", 100)),
            ).on_conflict_replace().execute()
            self._commit_locked()

    def update_binding(self, binding_id: str, updates: dict) -> bool:
        allowed = {"scope_kind", "scope_id", "role", "enabled", "order"}
        fields = {key: value for key, value in updates.items() if key in allowed}
        # Canonical boundary: a scope change cannot introduce a non-canonical kind.
        if "scope_kind" in fields:
            fields["scope_kind"] = normalize_scope_kind(fields["scope_kind"])
        if "enabled" in fields:
            fields["enabled"] = int(bool(fields["enabled"]))
        if "order" in fields:
            fields["order"] = int(fields["order"])
        if not fields:
            with self._lock:
                return LorebookBinding.get_or_none(LorebookBinding.id == binding_id) is not None
        with self._lock:
            existing = self._conn.execute(
                "SELECT book_id, role, scope_kind FROM lorebook_bindings WHERE id = ?",
                (binding_id,),
            ).fetchone()
            if existing is None:
                return False
            if existing[1] == "primary" and any(key in fields for key in ("scope_kind", "scope_id", "role")):
                raise ValueError("primary world binding cannot be changed")
            changed = LorebookBinding.update(**fields, updated_at=SQL("datetime('now')")).where(
                LorebookBinding.id == binding_id).execute()
            self._commit_locked()
        return bool(changed)

    def delete_binding(self, binding_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT role, scope_kind FROM lorebook_bindings WHERE id = ?", (binding_id,)
            ).fetchone()
            if row is None:
                return False
            if row[0] == "primary" and row[1] == "world":
                raise ValueError("primary world binding cannot be deleted")
            self._conn.execute("DELETE FROM lorebook_bindings WHERE id = ?", (binding_id,))
            self._commit_locked()
            return True

    def list_bindings(self, *, scope_kind: str | None = None, scope_id: str | None = None) -> list[dict]:
        with self._lock:
            query = LorebookBinding.select()
            if scope_kind is not None:
                query = query.where(LorebookBinding.scope_kind == scope_kind)
            if scope_id is not None:
                query = query.where(LorebookBinding.scope_id == scope_id)
            rows = list(query.order_by(LorebookBinding.order, LorebookBinding.id))
        return [dict(row.__data__) for row in rows]

    # ---- 条目 CRUD ----

    def add_entry(self, entry: dict) -> None:
        with self._lock:
            requested_book_id = entry.get("book_id")
            # Legacy world-copy callers clone a row verbatim, including the old
            # ``world:<source>`` book id. A target world remains authoritative for
            # that compatibility path; explicit standalone imports have no world_id
            # and keep their canonical book id.
            if entry.get("world_id") and requested_book_id and str(requested_book_id).startswith("world:") and requested_book_id != self.primary_world_book_id(entry["world_id"]):
                requested_book_id = None
            book_id = requested_book_id or self._ensure_primary_book_locked(entry["world_id"])
            # ``world_id`` is a compatibility projection, never a second
            # ownership input.  Derive it from the primary Binding so a
            # standalone Book whose id merely starts with ``world:`` remains
            # independent.
            projected_world_id = self._bound_world_projection_for_book_locked(book_id)
            LorebookEntry.insert(
                id=entry["id"],
                book_id=book_id,
                world_id=projected_world_id,
                name=entry["name"],
                type=entry.get("type", "other"),
                keywords=json.dumps(entry.get("keywords", []), ensure_ascii=False),
                content=entry.get("content", ""),
                unreliable=int(entry.get("unreliable", False)),
                sync_on_enter=int(entry.get("sync_on_enter", False)),
                tier=entry.get("tier", "background"),
                triggers_recursive=json.dumps(
                    entry.get("triggers_recursive", []), ensure_ascii=False),
                visible_to=json.dumps(entry.get("visible_to", []), ensure_ascii=False),
                is_constant=int(entry.get("is_constant", False)),
                match_mode=entry.get("match_mode", "any"),
                sticky=int(entry.get("sticky", 0)),
                cooldown=int(entry.get("cooldown", 0)),
                delay=int(entry.get("delay", 0)),
                order=int(entry.get("order", 100)),
                probability=int(entry.get("probability", 100)),
                group=entry.get("group", ""),
                group_weight=int(entry.get("group_weight", 1)),
                connected_to=json.dumps(entry.get("connected_to", []), ensure_ascii=False),
                source_plugin=entry.get("source_plugin", ""),
                enabled=int(entry.get("enabled", True)),
                secondary_keys=json.dumps(entry.get("secondary_keys", []), ensure_ascii=False),
                selective_logic=entry.get("selective_logic", "and"),
                selective=int(entry.get("selective", True)),
                use_regex=int(entry.get("use_regex", False)),
                regex_executable=int(entry.get("regex_executable", True)),
                case_sensitive=int(entry.get("case_sensitive", False)),
                match_whole_words=int(entry.get("match_whole_words", False)),
                scan_depth=int(entry.get("scan_depth", 0)), priority=int(entry.get("priority", 0)),
                vector_activation=normalize_vector_activation(
                    entry.get("vector_activation", DEFAULT_VECTOR_ACTIVATION)
                ),
                non_recursable=int(entry.get("non_recursable", False)),
                prevent_further_recursion=int(entry.get("prevent_further_recursion", False)),
                delay_until_recursion=int(entry.get("delay_until_recursion", False)),
                recursion_level=int(entry.get("recursion_level", 0)),
                groups=json.dumps(entry.get("groups", []), ensure_ascii=False),
                prioritize_inclusion=int(entry.get("prioritize_inclusion", False)),
                group_scoring=entry.get("group_scoring", ""), prompt_slot=entry.get("prompt_slot", ""),
                extensions_json=json.dumps(entry.get("extensions", entry.get("extensions_json", {})), ensure_ascii=False)
                if not isinstance(entry.get("extensions_json"), str) else entry["extensions_json"],
                provenance_json=json.dumps(entry.get("provenance", entry.get("provenance_json", {})), ensure_ascii=False)
                if not isinstance(entry.get("provenance_json"), str) else entry["provenance_json"],
            ).on_conflict_replace().execute()
            self._bump_book_revision_locked(book_id)
            self._commit_locked()

    def add_book_entry(self, book_id: str, entry: dict) -> None:
        """Add an entry through its canonical Book owner.

        New content paths must name the owning Book explicitly.  The legacy
        ``add_entry`` method remains as an adapter for world/template callers;
        this boundary deliberately derives the compatibility ``world_id``
        projection from the Book instead of accepting a second owner from the
        payload.
        """

        book_id = str(book_id or "")
        if not book_id or self.get_lorebook(book_id) is None:
            raise ValueError("lorebook book does not exist")
        payload = dict(entry)
        payload["book_id"] = book_id
        payload["world_id"] = self.bound_world_projection_for_book(book_id)
        self.add_entry(payload)

    def get_entry(self, entry_id: str) -> dict | None:
        with self._lock:
            entry = LorebookEntry.get_or_none(LorebookEntry.id == entry_id)
        return _entry_to_dict(entry) if entry else None

    def update_entry(self, entry_id: str, updates: dict) -> None:
        allowed = {"name", "type", "content", "unreliable",
                   "sync_on_enter", "tier", "keywords", "triggers_recursive", "visible_to",
                   "is_constant", "match_mode", "sticky", "cooldown", "delay", "order",
                   "probability", "group", "group_weight", "connected_to", "enabled",
                   "secondary_keys", "selective_logic", "selective", "use_regex", "case_sensitive",
                   "regex_executable",
                   "match_whole_words", "scan_depth", "priority", "vector_activation",
                   "non_recursable", "prevent_further_recursion", "delay_until_recursion",
                   "recursion_level", "groups", "prioritize_inclusion", "group_scoring",
                   "prompt_slot", "extensions_json", "provenance_json"}
        fields = {}
        for k, v in updates.items():
            if k not in allowed:
                continue
            if k in ("keywords", "triggers_recursive", "visible_to", "connected_to", "secondary_keys", "groups"):
                v = json.dumps(v, ensure_ascii=False)
            elif k in ("extensions_json", "provenance_json") and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            elif k == "vector_activation":
                v = normalize_vector_activation(v)
            elif k in ("unreliable", "sync_on_enter", "is_constant", "enabled", "use_regex",
                       "case_sensitive", "match_whole_words", "non_recursable",
                       "prevent_further_recursion", "delay_until_recursion", "prioritize_inclusion",
                       "selective", "regex_executable",
                       "sticky", "cooldown", "delay", "order",
                       "probability", "group_weight", "scan_depth", "priority", "recursion_level"):
                v = int(v)
            fields[k] = v
        if not fields:
            return
        with self._lock:
            LorebookEntry.update(
                **fields, updated_at=SQL("datetime('now')"),
            ).where(LorebookEntry.id == entry_id).execute()
            self._bump_book_revision_locked(self._entry_book_id_locked(entry_id))
            self._commit_locked()

    def _entry_book_id_locked(self, entry_id: str) -> str | None:
        """Owning book id for an entry (caller holds ``self._lock``)."""

        row = self._conn.execute(
            "SELECT book_id FROM lorebook_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def _bound_world_projection_for_book_locked(self, book_id: str) -> str | None:
        """Return the world scope of a primary Binding (caller holds the lock)."""

        row = self._conn.execute(
            "SELECT scope_id FROM lorebook_bindings "
            "WHERE book_id = ? AND role = 'primary' AND scope_kind = 'world' "
            "LIMIT 1",
            (str(book_id or ""),),
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def bound_world_projection_for_book(self, book_id: str) -> str | None:
        """Return the compatibility ``world_id`` owned by a Book binding."""

        with self._lock:
            return self._bound_world_projection_for_book_locked(book_id)

    def delete_entry(self, entry_id: str) -> None:
        with self._lock:
            book_id = self._entry_book_id_locked(entry_id)
            LorebookEntry.delete().where(LorebookEntry.id == entry_id).execute()
            # 派生缓存跟着条目走，避免删除后残留向量行。
            self._delete_embeddings_locked([entry_id])
            self._bump_book_revision_locked(book_id)
            self._commit_locked()

    # ---- Book 作用域条目访问（ownership isolation） --------------------------
    # entry.id 仍是全局 canonical PK；这里只是把「URL 里的 book_id」与「entry 真正
    # 归属的 book_id」绑定成同一个查询条件。归属不匹配一律 fail closed（返回
    # None/False），绝不允许跨 Book 修改或删除。

    def get_book_entry(self, book_id: str, entry_id: str) -> dict | None:
        """Read an entry only when it really belongs to ``book_id``."""

        with self._lock:
            entry = LorebookEntry.get_or_none(
                LorebookEntry.id == entry_id,
                LorebookEntry.book_id == str(book_id or ""),
            )
        return _entry_to_dict(entry) if entry else None

    def update_book_entry(self, book_id: str, entry_id: str, updates: dict) -> bool:
        """Update an entry scoped to ``book_id``.

        Returns False when the entry does not exist inside that book, so the
        caller can translate it into an explicit 404/409 ownership error.
        """

        with self._lock:
            owned = LorebookEntry.get_or_none(
                LorebookEntry.id == entry_id,
                LorebookEntry.book_id == str(book_id or ""),
            )
            if owned is None:
                return False
        # update_entry re-acquires the non-reentrant lock, so it must run outside.
        self.update_entry(entry_id, updates)
        return True

    @classmethod
    def world_projection_for_book(cls, book_id: str) -> str | None:
        """The legacy ``world_id`` projection a Book implies, or None.

        Only the deterministic primary world book ``world:<world_id>`` carries the
        compatibility projection; independent / global / game / character books
        never do. Keeping this in sync on move matters for more than metadata:
        ``delete_world_cascade`` still deletes by ``LorebookEntry.world_id``, so a
        stale projection would let deleting a world remove an entry that had
        already been moved into an independent Book.
        """

        text = str(book_id or "")
        prefix = "world:"
        if not text.startswith(prefix):
            return None
        world_id = text[len(prefix):]
        if not world_id:
            return None
        return world_id if cls.primary_world_book_id(world_id) == text else None

    def move_entry(self, source_book_id: str, target_book_id: str, entry_id: str) -> bool:
        """Move an entry between books, keeping its canonical ``entry.id``.

        Ownership isolation still applies: the entry must live in
        ``source_book_id``. Both books get a revision bump so a cached matcher
        fingerprint for either one is invalidated. The ``world_id`` compatibility
        projection is re-derived from the target Book so the canonical invariant
        (primary world book <-> world_id, everything else NULL) never drifts.
        Returns False when the entry is not in the source book or the target book
        does not exist.
        """

        source_book_id = str(source_book_id or "")
        target_book_id = str(target_book_id or "")
        if not source_book_id or not target_book_id:
            return False
        if source_book_id == target_book_id:
            # Nothing to move; still require that the entry is really owned here.
            with self._lock:
                return LorebookEntry.get_or_none(
                    LorebookEntry.id == entry_id,
                    LorebookEntry.book_id == source_book_id,
                ) is not None
        with self._lock:
            if Lorebook.get_or_none(Lorebook.id == target_book_id) is None:
                return False
            owned = LorebookEntry.get_or_none(
                LorebookEntry.id == entry_id,
                LorebookEntry.book_id == source_book_id,
            )
            if owned is None:
                return False
            LorebookEntry.update(
                book_id=target_book_id,
                world_id=self._bound_world_projection_for_book_locked(target_book_id),
                updated_at=SQL("datetime('now')"),
            ).where(LorebookEntry.id == entry_id).execute()
            self._bump_book_revision_locked(source_book_id)
            self._bump_book_revision_locked(target_book_id)
            self._commit_locked()
        return True

    def delete_book_entry(self, book_id: str, entry_id: str) -> bool:
        """Delete an entry scoped to ``book_id``; False when it is not in that book."""

        with self._lock:
            owned = LorebookEntry.get_or_none(
                LorebookEntry.id == entry_id,
                LorebookEntry.book_id == str(book_id or ""),
            )
            if owned is None:
                return False
            LorebookEntry.delete().where(
                LorebookEntry.id == entry_id,
                LorebookEntry.book_id == str(book_id or ""),
            ).execute()
            self._delete_embeddings_locked([entry_id])
            self._bump_book_revision_locked(book_id)
            self._commit_locked()
        return True

    def delete_world_cascade(self, world_id: str) -> None:
        """Delete a world through its primary Book ownership boundary.

        ``world_id`` on an entry is only a compatibility projection.  Delete
        the entries owned by the primary ``world:<id>`` Book, and detach any
        stale projection on another/unknown Book before removing the World so
        SQLite's legacy foreign-key cascade cannot delete foreign content.
        """
        with self._lock:
            entry_ids = [
                str(row.id) for row in
                LorebookEntry.select(LorebookEntry.id).where(
                    LorebookEntry.book_id == self.primary_world_book_id(world_id)
                )
            ]
            self._conn.execute(
                "UPDATE lorebook_entries SET world_id = NULL "
                "WHERE world_id = ? AND (book_id IS NULL OR book_id != ?)",
                (world_id, self.primary_world_book_id(world_id)),
            )
            LorebookEntry.delete().where(
                LorebookEntry.book_id == self.primary_world_book_id(world_id)
            ).execute()
            self._delete_embeddings_locked(entry_ids)
            World.delete().where(World.id == world_id).execute()
            self._bump_book_revision_locked(self.primary_world_book_id(world_id))
            self._commit_locked()

    def count_entries_by_plugin(self, plugin_id: str) -> int:
        with self._lock:
            return LorebookEntry.select().where(
                LorebookEntry.source_plugin == plugin_id,
            ).count()

    def delete_entries_by_plugin(self, plugin_id: str) -> int:
        """删除该插件来源的全部世界书条目，返回删除条数。"""
        with self._lock:
            book_ids = [
                str(row[0]) for row in self._conn.execute(
                    "SELECT DISTINCT book_id FROM lorebook_entries WHERE source_plugin = ?",
                    (plugin_id,),
                )
            ]
            entry_ids = [
                str(row.id) for row in
                LorebookEntry.select(LorebookEntry.id).where(
                    LorebookEntry.source_plugin == plugin_id,
                )
            ]
            rowcount = LorebookEntry.delete().where(
                LorebookEntry.source_plugin == plugin_id,
            ).execute()
            self._delete_embeddings_locked(entry_ids)
            for book_id in book_ids:
                self._bump_book_revision_locked(book_id)
            self._commit_locked()
        return rowcount

    def list_plugin_worlds(self, plugin_id: str) -> list[dict]:
        """该插件创建的、仍含其来源条目的世界（用于条件删除判定）。"""
        with self._lock:
            rows = list(
                World.select()
                .join(LorebookEntry, on=(LorebookEntry.world_id == World.id))
                .where(LorebookEntry.source_plugin == plugin_id)
                .distinct()
            )
        return [dict(w.__data__) for w in rows]

    def list_entries(self, world_id: str, entry_type: str | None = None) -> list[dict]:
        with self._lock:
            book_id = self._ensure_primary_book_locked(world_id)
            # Compatibility projection is intentionally limited to the primary
            # world book. Independent books may be bound to this world, but
            # their canonical entries must only be read through list_book_entries.
            query = LorebookEntry.select().where(LorebookEntry.book_id == book_id)
            if entry_type:
                query = query.where(LorebookEntry.type == entry_type)
            rows = list(query.order_by(LorebookEntry.tier, LorebookEntry.name))
        return [_entry_to_dict(e) for e in rows]

    def list_book_entries(self, book_id: str, entry_type: str | None = None) -> list[dict]:
        with self._lock:
            query = LorebookEntry.select().where(LorebookEntry.book_id == book_id)
            if entry_type:
                query = query.where(LorebookEntry.type == entry_type)
            rows = list(query.order_by(LorebookEntry.tier, LorebookEntry.name))
        return [_entry_to_dict(e) for e in rows]

    # ---- embedding 派生缓存（migration v4） ----

    def load_embedding_cache(
        self, entry_ids: list[str], language: str, embedding_profile: str,
    ) -> dict[str, dict]:
        """读取 (entry_id, language, profile) 命中的向量缓存。

        返回 ``entry_id -> {"content_hash": str, "embedding": list[float]}``。缓存行损坏
        （不是合法 JSON 数组，或含非数字 / NaN / Inf）一律按缺失处理，由调用方重新
        embedding —— 派生缓存的坏数据不能让正常回合抛异常。
        """

        ids = [str(entry_id).strip() for entry_id in entry_ids or [] if str(entry_id).strip()]
        if not ids:
            return {}
        result: dict[str, dict] = {}
        with self._lock:
            for start in range(0, len(ids), _CACHE_CHUNK):
                chunk = ids[start:start + _CACHE_CHUNK]
                rows = list(
                    LorebookEmbedding.select().where(
                        (LorebookEmbedding.language == str(language or ""))
                        & (LorebookEmbedding.embedding_profile == str(embedding_profile or ""))
                        & (LorebookEmbedding.entry_id.in_(chunk))
                    )
                )
                for row in rows:
                    data = dict(row.__data__)
                    try:
                        vector = json.loads(data.get("embedding") or "[]")
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(vector, list) or not vector:
                        continue
                    # 坏向量（非数字 / NaN / Inf）按缺失处理，绝不抛给调用方。
                    try:
                        numbers = [float(value) for value in vector]
                    except (TypeError, ValueError):
                        continue
                    if any(
                        number != number or number in (float("inf"), float("-inf"))
                        for number in numbers
                    ):
                        continue
                    result[str(data.get("entry_id") or "")] = {
                        "content_hash": str(data.get("content_hash") or ""),
                        "embedding": numbers,
                    }
        return result

    def save_embedding_cache(self, rows: list[dict]) -> None:
        """写入 / 覆盖派生缓存行；坏行（无 id 或空向量）直接跳过。"""

        payload = []
        for row in rows or []:
            entry_id = str(row.get("entry_id") or "").strip()
            vector = row.get("embedding")
            if not entry_id or not isinstance(vector, (list, tuple)) or not vector:
                continue
            payload.append({
                "entry_id": entry_id,
                "language": str(row.get("language") or ""),
                "embedding_profile": str(row.get("embedding_profile") or ""),
                "content_hash": str(row.get("content_hash") or ""),
                "embedding": json.dumps([float(value) for value in vector]),
            })
        if not payload:
            return
        with self._lock:
            for row in payload:
                LorebookEmbedding.insert(**row).on_conflict_replace().execute()
            self._commit_locked()

    def _delete_embeddings_locked(self, entry_ids: list[str]) -> None:
        """删除若干 entry 的缓存行（调用方必须已持有 ``self._lock``）。"""

        ids = [str(entry_id).strip() for entry_id in entry_ids or [] if str(entry_id).strip()]
        for start in range(0, len(ids), _CACHE_CHUNK):
            chunk = ids[start:start + _CACHE_CHUNK]
            LorebookEmbedding.delete().where(
                LorebookEmbedding.entry_id.in_(chunk),
            ).execute()

    def search_entries(self, world_id: str, keyword: str) -> list[dict]:
        # peewee 的 SQLite 方言把 ilike 编译为 SQL LIKE（like 会被编译成 GLOB，
        # 通配符语义不同，不要改用 like）。
        pattern = f"%{keyword}%"
        with self._lock:
            book_id = self._ensure_primary_book_locked(world_id)
            rows = list(
                LorebookEntry.select()
                .where(
                    (LorebookEntry.book_id == book_id)
                    & (
                        LorebookEntry.name.ilike(pattern)
                        | LorebookEntry.content.ilike(pattern)
                        | LorebookEntry.keywords.ilike(pattern)
                    )
                )
                .order_by(LorebookEntry.tier, LorebookEntry.name)
            )
        return [_entry_to_dict(e) for e in rows]

    def search_book_entries(self, book_id: str, keyword: str) -> list[dict]:
        pattern = f"%{keyword}%"
        with self._lock:
            rows = list(LorebookEntry.select().where(
                (LorebookEntry.book_id == book_id)
                & (LorebookEntry.name.ilike(pattern)
                   | LorebookEntry.content.ilike(pattern)
                   | LorebookEntry.keywords.ilike(pattern))
            ).order_by(LorebookEntry.tier, LorebookEntry.name))
        return [_entry_to_dict(e) for e in rows]


def _entry_to_dict(entry: LorebookEntry) -> dict:
    d = dict(entry.__data__)
    d["keywords"] = json.loads(d.get("keywords", "[]"))
    d["triggers_recursive"] = json.loads(d.get("triggers_recursive", "[]"))
    d["visible_to"] = json.loads(d.get("visible_to", "[]"))
    d["connected_to"] = json.loads(d.get("connected_to", "[]"))
    for key, default in (("secondary_keys", "[]"), ("groups", "[]"),
                         ("extensions_json", "{}"), ("provenance_json", "{}")):
        raw = d.get(key, default)
        try:
            d[key.removesuffix("_json") if key.endswith("_json") else key] = json.loads(raw or default)
        except (TypeError, json.JSONDecodeError):
            d[key.removesuffix("_json") if key.endswith("_json") else key] = [] if default == "[]" else {}
    d["source_plugin"] = d.get("source_plugin", "") or ""
    return d


def _book_to_dict(book: Lorebook) -> dict:
    d = dict(book.__data__)
    try:
        d["settings"] = json.loads(d.get("settings_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        d["settings"] = {}
    return d
