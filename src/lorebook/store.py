"""Lorebook SQLite 存储 —— 世界书条目的 CRUD 操作。"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

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

CREATE INDEX IF NOT EXISTS idx_lorebook_world ON lorebook_entries(world_id);
CREATE INDEX IF NOT EXISTS idx_lorebook_type  ON lorebook_entries(world_id, type);
CREATE INDEX IF NOT EXISTS idx_lorebook_tier  ON lorebook_entries(world_id, tier);

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
"""
# 迁移后的 lorebook_entries 目标列（新表结构，顺序即 _drop_legacy_type_check_sql 的建表顺序）
_LOREBOOK_NEW_COLUMNS = (
    "id", "world_id", "name", "type", "keywords", "content", "unreliable",
    "sync_on_enter", "tier", "triggers_recursive", "visible_to", "is_constant",
    "match_mode", "sticky", "cooldown", "delay", "order", "probability",
    "group", "group_weight", "connected_to", "source_plugin", "created_at", "updated_at",
)


def _drop_legacy_type_check_sql() -> str:
    """去掉老库 type 列 CHECK 约束的整表重建 SQL。

    老库可能列不全（迁移前的历史版本），不能假设 22 列齐全。先由调用方读
    PRAGMA table_info 取真实列，这里只定义新表结构（含 source_plugin），
    数据复制用显式列名对齐。
    """
    return """
CREATE TABLE lorebook_entries_new (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'other',
    keywords TEXT NOT NULL DEFAULT '[]',
    content TEXT NOT NULL DEFAULT '',
    unreliable INTEGER DEFAULT 0,
    sync_on_enter INTEGER DEFAULT 0,
    tier TEXT DEFAULT 'background',
    triggers_recursive TEXT DEFAULT '[]',
    visible_to TEXT DEFAULT '[]',
    is_constant INTEGER DEFAULT 0,
    match_mode TEXT DEFAULT 'any',
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
        self._conn.executescript(SCHEMA)
        migrate_lorebook(self._conn)
        self._drop_legacy_type_check()
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_lorebook_source ON lorebook_entries(source_plugin)")
        self._conn.commit()
        logger.info("Lorebook 数据库已打开: %s", self.db_path)

    def _drop_legacy_type_check(self) -> None:
        """去掉老库 type 列的 CHECK 约束，允许新类型 spell/class。

        旧版建表语句带 `CHECK(type IN (...))`，无法插入 spell/class。通过检查
        建表 SQL 是否含 CHECK 判断：含则整表重建去掉约束。仅老库触发一次。
        """
        row = self._execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='lorebook_entries'").fetchone()
        table_sql = str(row[0] or "") if row else ""
        if "CHECK" not in table_sql.upper():
            return
        # 整表重建：按新表列显式对齐，老表缺的列用 DEFAULT 补齐
        old_cols = [r["name"] for r in self._execute("PRAGMA table_info(lorebook_entries)")]
        shared = [c for c in _LOREBOOK_NEW_COLUMNS if c in old_cols]
        col_sql = ", ".join(f'"{c}"' for c in shared)
        self._conn.executescript(_drop_legacy_type_check_sql())
        if shared:
            self._execute(
                f"INSERT INTO lorebook_entries_new ({col_sql}) SELECT {col_sql} FROM lorebook_entries"
            )
        self._conn.execute("DROP TABLE lorebook_entries")
        self._conn.execute("ALTER TABLE lorebook_entries_new RENAME TO lorebook_entries")
        self._conn.commit()
        logger.info("已迁移 lorebook_entries 去掉 type CHECK 约束，支持 spell/class")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Lorebook 数据库已关闭")

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        assert self._conn, "数据库未打开"
        with self._lock:
            return self._conn.execute(sql, params)

    # ---- 世界 CRUD ----

    def create_world(self, world_id: str, name: str, **kwargs) -> None:
        self._execute(
            "INSERT OR REPLACE INTO worlds(id, name, description, language, author, version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (world_id, name, kwargs.get("description", ""),
             kwargs.get("language", "zh-CN"), kwargs.get("author", ""), kwargs.get("version", "1.0")),
        )
        self._conn.commit()

    def get_world(self, world_id: str) -> dict | None:
        row = self._execute(
            "SELECT * FROM worlds WHERE id = ?", (world_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_world_language(self, world_id: str, language: str) -> None:
        """Correct world language metadata without replacing the world or its entries."""
        self._execute(
            "UPDATE worlds SET language = ?, updated_at = datetime('now') WHERE id = ?",
            (language, world_id),
        )
        self._conn.commit()

    def list_worlds(self) -> list[dict]:
        rows = self._execute("SELECT * FROM worlds ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_world(self, world_id: str) -> None:
        self._execute("DELETE FROM worlds WHERE id = ?", (world_id,))
        self._conn.commit()

    # ---- 条目 CRUD ----

    def add_entry(self, entry: dict) -> None:
        keywords = json.dumps(entry.get("keywords", []), ensure_ascii=False)
        triggers = json.dumps(entry.get("triggers_recursive", []), ensure_ascii=False)
        visible = json.dumps(entry.get("visible_to", []), ensure_ascii=False)
        connected = json.dumps(entry.get("connected_to", []), ensure_ascii=False)
        self._execute(
            "INSERT OR REPLACE INTO lorebook_entries "
            "(id, world_id, name, type, keywords, content, unreliable, "
            " sync_on_enter, tier, triggers_recursive, visible_to, is_constant, match_mode, "
            " sticky, cooldown, delay, \"order\", probability, \"group\", group_weight, connected_to, source_plugin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry["id"], entry["world_id"], entry["name"], entry.get("type", "other"),
             keywords, entry.get("content", ""),
             int(entry.get("unreliable", False)),
             int(entry.get("sync_on_enter", False)),
             entry.get("tier", "background"),
             triggers, visible,
             int(entry.get("is_constant", False)),
             entry.get("match_mode", "any"),
             int(entry.get("sticky", 0)),
             int(entry.get("cooldown", 0)),
             int(entry.get("delay", 0)),
             int(entry.get("order", 100)),
             int(entry.get("probability", 100)),
             entry.get("group", ""),
             int(entry.get("group_weight", 1)),
             connected,
             entry.get("source_plugin", "")),
        )
        self._conn.commit()

    def get_entry(self, entry_id: str) -> dict | None:
        row = self._execute(
            "SELECT * FROM lorebook_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    def update_entry(self, entry_id: str, updates: dict) -> None:
        allowed = {"name", "type", "content", "unreliable",
                   "sync_on_enter", "tier", "keywords", "triggers_recursive", "visible_to",
                   "is_constant", "match_mode", "sticky", "cooldown", "delay", "order",
                   "probability", "group", "group_weight", "connected_to"}
        fields = {}
        params: list = []
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
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        params.extend(fields.values())
        params.append(entry_id)
        self._execute(
            f"UPDATE lorebook_entries SET {set_clause}, "
            "updated_at = datetime('now') WHERE id = ?",
            tuple(params),
        )
        self._conn.commit()

    def delete_entry(self, entry_id: str) -> None:
        self._execute("DELETE FROM lorebook_entries WHERE id = ?", (entry_id,))
        self._conn.commit()

    def delete_world_cascade(self, world_id: str) -> None:
        """删除世界及其所有条目。"""
        self._execute("DELETE FROM lorebook_entries WHERE world_id = ?", (world_id,))
        self._execute("DELETE FROM worlds WHERE id = ?", (world_id,))
        self._conn.commit()

    def count_entries_by_plugin(self, plugin_id: str) -> int:
        row = self._execute(
            "SELECT COUNT(*) FROM lorebook_entries WHERE source_plugin = ?", (plugin_id,)
        ).fetchone()
        return int(row[0] if row else 0)

    def delete_entries_by_plugin(self, plugin_id: str) -> int:
        """删除该插件来源的全部世界书条目，返回删除条数。"""
        cur = self._execute(
            "DELETE FROM lorebook_entries WHERE source_plugin = ?", (plugin_id,)
        )
        self._conn.commit()
        return cur.rowcount

    def list_plugin_worlds(self, plugin_id: str) -> list[dict]:
        """该插件创建的、仍含其来源条目的世界（用于条件删除判定）。"""
        rows = self._execute(
            "SELECT DISTINCT w.* FROM worlds w "
            "JOIN lorebook_entries e ON e.world_id = w.id "
            "WHERE e.source_plugin = ?",
            (plugin_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_entries(self, world_id: str, entry_type: str | None = None) -> list[dict]:
        if entry_type:
            rows = self._execute(
                "SELECT * FROM lorebook_entries WHERE world_id = ? AND type = ? "
                "ORDER BY tier, name",
                (world_id, entry_type),
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM lorebook_entries WHERE world_id = ? "
                "ORDER BY tier, name",
                (world_id,),
            ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def search_entries(self, world_id: str, keyword: str) -> list[dict]:
        rows = self._execute(
            "SELECT * FROM lorebook_entries WHERE world_id = ? AND "
            "(name LIKE ? OR content LIKE ? OR keywords LIKE ?) "
            "ORDER BY tier, name",
            (world_id, f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]


def _row_to_entry(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["keywords"] = json.loads(d.get("keywords", "[]"))
    d["triggers_recursive"] = json.loads(d.get("triggers_recursive", "[]"))
    d["visible_to"] = json.loads(d.get("visible_to", "[]"))
    d["unreliable"] = bool(d.get("unreliable", 0))
    d["sync_on_enter"] = bool(d.get("sync_on_enter", 0))
    d["is_constant"] = bool(d.get("is_constant", 0))
    d["sticky"] = int(d.get("sticky", 0))
    d["cooldown"] = int(d.get("cooldown", 0))
    d["delay"] = int(d.get("delay", 0))
    d["order"] = int(d.get("order", 100))
    d["probability"] = int(d.get("probability", 100))
    d["group"] = d.get("group", "")
    d["group_weight"] = int(d.get("group_weight", 1))
    d["connected_to"] = json.loads(d.get("connected_to", "[]"))
    d["source_plugin"] = d.get("source_plugin", "") or ""
    return d
