"""Memory database schema migrations."""

from __future__ import annotations

import sqlite3

from .sqlite import ensure_column, run_migrations


def _v1(conn: sqlite3.Connection) -> None:
    ensure_column(conn, "memory_entries", "embedding", "TEXT")


def migrate(conn: sqlite3.Connection) -> int:
    return run_migrations(conn, ((1, _v1),))
