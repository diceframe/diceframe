"""Small, transactional SQLite migration primitives.

The database's ``user_version`` is advanced only after every step succeeds.
Existing databases are intentionally migrated in place; no data is rewritten
unless a migration explicitly requires it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable


class MigrationError(RuntimeError):
    """Raised when a migration cannot be completed."""


Migration = tuple[int, Callable[[sqlite3.Connection], None]]


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> bool:
    """Add a missing column and report whether the schema changed."""
    if column in table_columns(conn, table):
        return False
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
    return True


def run_migrations(
    conn: sqlite3.Connection,
    migrations: Iterable[Migration],
    *,
    target_version: int | None = None,
) -> int:
    """Run pending migrations atomically and return the resulting version."""
    steps = sorted(migrations, key=lambda item: item[0])
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    target = target_version if target_version is not None else (steps[-1][0] if steps else current)
    try:
        conn.execute("BEGIN")
        for version, migrate in steps:
            if version > current:
                migrate(conn)
                conn.execute(f"PRAGMA user_version = {int(version)}")
                current = version
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise MigrationError(f"SQLite migration failed at version {current + 1}") from exc
    if current < target:
        raise MigrationError(f"No migration registered for target version {target}")
    return current
