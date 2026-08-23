"""Lorebook database schema migrations."""

from __future__ import annotations

import sqlite3

from .sqlite import ensure_column, run_migrations


def _v1(conn: sqlite3.Connection) -> None:
    for name, definition in (
        ("is_constant", "INTEGER DEFAULT 0"),
        ("match_mode", "TEXT DEFAULT 'any'"),
        ("sticky", "INTEGER DEFAULT 0"),
        ("cooldown", "INTEGER DEFAULT 0"),
        ("delay", "INTEGER DEFAULT 0"),
        ("order", "INTEGER DEFAULT 100"),
        ("probability", "INTEGER DEFAULT 100"),
        ("group", "TEXT DEFAULT ''"),
        ("group_weight", "INTEGER DEFAULT 1"),
        ("connected_to", "TEXT DEFAULT '[]'"),
    ):
        ensure_column(conn, "lorebook_entries", name, definition)
    ensure_column(conn, "worlds", "language", "TEXT DEFAULT 'zh-CN'")
    ensure_column(conn, "lorebook_entries", "source_plugin", "TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lorebook_source ON lorebook_entries(source_plugin)")


def migrate(conn: sqlite3.Connection) -> int:
    return run_migrations(conn, ((1, _v1),))
