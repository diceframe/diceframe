import sqlite3

import pytest

from src.migrations.sqlite import MigrationError, run_migrations


def test_migration_is_idempotent_and_sets_version():
    conn = sqlite3.connect(":memory:")
    conn.execute("create table t (id integer)")
    calls = []
    step = lambda db: (calls.append(1), db.execute("alter table t add column value text"))
    assert run_migrations(conn, ((1, step),)) == 1
    assert run_migrations(conn, ((1, step),)) == 1
    assert len(calls) == 1


def test_failed_migration_rolls_back_version():
    conn = sqlite3.connect(":memory:")
    conn.execute("create table t (id integer)")
    with pytest.raises(MigrationError):
        run_migrations(conn, ((1, lambda db: db.execute("alter table missing add column x text")),))
    assert conn.execute("pragma user_version").fetchone()[0] == 0
