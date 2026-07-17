"""Lorebook SQLite 存储 —— 世界书条目的 CRUD 操作。"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

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
    type TEXT NOT NULL DEFAULT 'other'
        CHECK(type IN ('npc','location','item','event','puzzle','faction','other')),
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_lorebook_world ON lorebook_entries(world_id);
CREATE INDEX IF NOT EXISTS idx_lorebook_type  ON lorebook_entries(world_id, type);
CREATE INDEX IF NOT EXISTS idx_lorebook_tier  ON lorebook_entries(world_id, tier);

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
"""

# 表升级
_MIGRATE_CONSTANT = "ALTER TABLE lorebook_entries ADD COLUMN is_constant INTEGER DEFAULT 0;"
_MIGRATE_MATCH_MODE = "ALTER TABLE lorebook_entries ADD COLUMN match_mode TEXT DEFAULT 'any' CHECK(match_mode IN ('any','all','not_any','not_all'));"
_MIGRATE_STICKY = "ALTER TABLE lorebook_entries ADD COLUMN sticky INTEGER DEFAULT 0;"
_MIGRATE_COOLDOWN = "ALTER TABLE lorebook_entries ADD COLUMN cooldown INTEGER DEFAULT 0;"
_MIGRATE_DELAY = "ALTER TABLE lorebook_entries ADD COLUMN delay INTEGER DEFAULT 0;"
_MIGRATE_ORDER = 'ALTER TABLE lorebook_entries ADD COLUMN "order" INTEGER DEFAULT 100;'
_MIGRATE_PROBABILITY = "ALTER TABLE lorebook_entries ADD COLUMN probability INTEGER DEFAULT 100;"
_MIGRATE_GROUP = 'ALTER TABLE lorebook_entries ADD COLUMN "group" TEXT DEFAULT \'\';'
_MIGRATE_GROUP_WEIGHT = "ALTER TABLE lorebook_entries ADD COLUMN group_weight INTEGER DEFAULT 1;"
_MIGRATE_CONNECTED = "ALTER TABLE lorebook_entries ADD COLUMN connected_to TEXT DEFAULT '[]';"
_MIGRATE_WORLD_LANGUAGE = "ALTER TABLE worlds ADD COLUMN language TEXT DEFAULT 'zh-CN';"


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
        # 运行表升级
        for mig in (_MIGRATE_CONSTANT, _MIGRATE_MATCH_MODE, _MIGRATE_STICKY,
                     _MIGRATE_COOLDOWN, _MIGRATE_DELAY, _MIGRATE_ORDER,
                     _MIGRATE_PROBABILITY, _MIGRATE_GROUP, _MIGRATE_GROUP_WEIGHT,
                      _MIGRATE_CONNECTED, _MIGRATE_WORLD_LANGUAGE):
            try:
                self._conn.execute(mig)
                self._conn.commit()
            except sqlite3.OperationalError:
                pass
        self._conn.commit()
        logger.info("Lorebook 数据库已打开: %s", self.db_path)

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
            " sticky, cooldown, delay, \"order\", probability, \"group\", group_weight, connected_to) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
             connected),
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
    return d
