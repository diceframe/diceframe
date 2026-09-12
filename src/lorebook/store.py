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
from typing import Any

from peewee import SQL

from src.lorebook.models import LorebookEntry, World
from src.lorebook.models import database as _models_database
from src.migrations.lorebook import migrate as migrate_lorebook

logger = logging.getLogger("trpg")

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
            self._conn.commit()

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
            self._conn.commit()

    def list_worlds(self) -> list[dict]:
        with self._lock:
            rows = list(World.select().order_by(World.updated_at.desc()))
        return [dict(w.__data__) for w in rows]

    def delete_world(self, world_id: str) -> None:
        with self._lock:
            World.delete().where(World.id == world_id).execute()
            self._conn.commit()

    # ---- 条目 CRUD ----

    def add_entry(self, entry: dict) -> None:
        with self._lock:
            LorebookEntry.insert(
                id=entry["id"],
                world_id=entry["world_id"],
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
            ).on_conflict_replace().execute()
            self._conn.commit()

    def get_entry(self, entry_id: str) -> dict | None:
        with self._lock:
            entry = LorebookEntry.get_or_none(LorebookEntry.id == entry_id)
        return _entry_to_dict(entry) if entry else None

    def update_entry(self, entry_id: str, updates: dict) -> None:
        allowed = {"name", "type", "content", "unreliable",
                   "sync_on_enter", "tier", "keywords", "triggers_recursive", "visible_to",
                   "is_constant", "match_mode", "sticky", "cooldown", "delay", "order",
                   "probability", "group", "group_weight", "connected_to"}
        fields = {}
        for k, v in updates.items():
            if k not in allowed:
                continue
            if k in ("keywords", "triggers_recursive", "visible_to", "connected_to"):
                v = json.dumps(v, ensure_ascii=False)
            elif k in ("unreliable", "sync_on_enter", "is_constant",
                       "sticky", "cooldown", "delay", "order",
                       "probability", "group_weight"):
                v = int(v)
            fields[k] = v
        if not fields:
            return
        with self._lock:
            LorebookEntry.update(
                **fields, updated_at=SQL("datetime('now')"),
            ).where(LorebookEntry.id == entry_id).execute()
            self._conn.commit()

    def delete_entry(self, entry_id: str) -> None:
        with self._lock:
            LorebookEntry.delete().where(LorebookEntry.id == entry_id).execute()
            self._conn.commit()

    def delete_world_cascade(self, world_id: str) -> None:
        """删除世界及其所有条目。"""
        with self._lock:
            LorebookEntry.delete().where(LorebookEntry.world_id == world_id).execute()
            World.delete().where(World.id == world_id).execute()
            self._conn.commit()

    def count_entries_by_plugin(self, plugin_id: str) -> int:
        with self._lock:
            return LorebookEntry.select().where(
                LorebookEntry.source_plugin == plugin_id,
            ).count()

    def delete_entries_by_plugin(self, plugin_id: str) -> int:
        """删除该插件来源的全部世界书条目，返回删除条数。"""
        with self._lock:
            rowcount = LorebookEntry.delete().where(
                LorebookEntry.source_plugin == plugin_id,
            ).execute()
            self._conn.commit()
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
            query = LorebookEntry.select().where(LorebookEntry.world_id == world_id)
            if entry_type:
                query = query.where(LorebookEntry.type == entry_type)
            rows = list(query.order_by(LorebookEntry.tier, LorebookEntry.name))
        return [_entry_to_dict(e) for e in rows]

    def search_entries(self, world_id: str, keyword: str) -> list[dict]:
        # peewee 的 SQLite 方言把 ilike 编译为 SQL LIKE（like 会被编译成 GLOB，
        # 通配符语义不同，不要改用 like）。
        pattern = f"%{keyword}%"
        with self._lock:
            rows = list(
                LorebookEntry.select()
                .where(
                    (LorebookEntry.world_id == world_id)
                    & (
                        LorebookEntry.name.ilike(pattern)
                        | LorebookEntry.content.ilike(pattern)
                        | LorebookEntry.keywords.ilike(pattern)
                    )
                )
                .order_by(LorebookEntry.tier, LorebookEntry.name)
            )
        return [_entry_to_dict(e) for e in rows]


def _entry_to_dict(entry: LorebookEntry) -> dict:
    d = dict(entry.__data__)
    d["keywords"] = json.loads(d.get("keywords", "[]"))
    d["triggers_recursive"] = json.loads(d.get("triggers_recursive", "[]"))
    d["visible_to"] = json.loads(d.get("visible_to", "[]"))
    d["connected_to"] = json.loads(d.get("connected_to", "[]"))
    d["source_plugin"] = d.get("source_plugin", "") or ""
    return d
