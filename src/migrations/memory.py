"""Memory database schema migrations."""

from __future__ import annotations

import sqlite3

from .sqlite import MigrationError, ensure_column, run_migrations, table_columns


def _v1(conn: sqlite3.Connection) -> None:
    ensure_column(conn, "memory_entries", "embedding", "TEXT")


def _v2(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS memory_economy_deliveries (
            game_key TEXT NOT NULL,
            delivery_id TEXT NOT NULL,
            before_state TEXT NOT NULL,
            after_state TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'applied'
                CHECK(status IN ('applied','reversed')),
            created_at TEXT NOT NULL,
            reversed_at TEXT,
            PRIMARY KEY (game_key, delivery_id)
        );

        CREATE INDEX IF NOT EXISTS idx_memory_economy_delivery_status
            ON memory_economy_deliveries(game_key, status);
        """
    )


def _v3(conn: sqlite3.Connection) -> None:
    """World Memory provenance columns (母方案 §21/§99，WR-06).

    纯加列迁移：旧行全部为 NULL，读取端按 legacy_soft 分类；不回填、不猜测。
    """

    ensure_column(conn, "memory_entries", "memory_kind", "TEXT")
    ensure_column(conn, "memory_entries", "source_kind", "TEXT")
    ensure_column(conn, "memory_entries", "source_id", "TEXT")
    ensure_column(conn, "memory_entries", "world_revision", "INTEGER")
    ensure_column(conn, "memory_entries", "visibility", "TEXT")


def _validate_known_v4(conn: sqlite3.Connection) -> None:
    """Accept the short-lived profile-column schema without rewriting data.

    A development build briefly wrote ``user_version=4`` after adding the
    optional ``embedding_profile`` column.  The profile feature is not part of
    the current memory contract, but existing databases must still open after
    returning to the stable v3 reader.  Only that exact, known shape is
    accepted; an unrelated future v4 database remains fail-closed.
    """

    columns = table_columns(conn, "memory_entries")
    required = {
        "embedding_profile",
    }
    if not required.issubset(columns):
        raise MigrationError(
            "Database memory schema v4 is not a recognized DiceFrame compatibility shape"
        )
    economy_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        ("memory_economy_deliveries",),
    ).fetchone()
    if economy_table is None:
        raise MigrationError(
            "Database memory schema v4 is missing the economy delivery journal"
        )


def _v4(conn: sqlite3.Connection) -> None:
    """Bind a derived vector to a model profile; old values remain unknown."""

    ensure_column(conn, "memory_entries", "embedding_profile", "TEXT")


def _v5(conn: sqlite3.Connection) -> None:
    """Track the source text and dimension of a derived vector."""

    ensure_column(conn, "memory_entries", "embedding_content_hash", "TEXT")
    ensure_column(conn, "memory_entries", "embedding_dimension", "INTEGER")


def migrate(conn: sqlite3.Connection) -> int:
    # The short-lived v4 profile experiment used the same column and journal
    # shape as the supported migration.  Validate it before continuing to v5;
    # do not lower user_version and do not guess about any other future schema.
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current == 4:
        _validate_known_v4(conn)
    return run_migrations(conn, ((1, _v1), (2, _v2), (3, _v3), (4, _v4), (5, _v5)))
