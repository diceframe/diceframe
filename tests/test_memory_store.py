"""MemoryStore 集成测试 —— delta 应用。"""

from __future__ import annotations

import tempfile
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.memory.delta import MemoryStore
from src.memory.embedding_profile import content_hash as embedding_content_hash
from src.memory.models import MemoryEntry
from src.migrations.sqlite import MigrationError

pytestmark = pytest.mark.asyncio


def _temp_store():
    t = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    t.close()
    store = MemoryStore(Path(t.name))
    store.open()
    return store, Path(t.name)


class TestDeltaApplication:
    async def test_open_migrates_economy_delivery_journal(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.db")
        store.open()
        try:
            # Profile/content metadata is additive; legacy rows remain readable
            # and are rebuilt instead of being treated as current vectors.
            assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 5
            table = store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                ("memory_economy_deliveries",),
            ).fetchone()
            assert table is not None
        finally:
            store.close()

    async def test_open_accepts_known_profile_experiment_v4_without_data_loss(self, tmp_path):
        path = tmp_path / "memory-v4.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE memory_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_key TEXT NOT NULL, entity TEXT NOT NULL, relation TEXT NOT NULL,
                value TEXT NOT NULL, confidence REAL DEFAULT 1.0,
                status TEXT DEFAULT 'active', source_round INTEGER, embedding TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                memory_kind TEXT, source_kind TEXT, source_id TEXT,
                world_revision INTEGER, visibility TEXT, embedding_profile TEXT
            );
            CREATE TABLE memory_economy_deliveries (
                game_key TEXT NOT NULL, delivery_id TEXT NOT NULL,
                before_state TEXT NOT NULL, after_state TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'applied', created_at TEXT NOT NULL,
                reversed_at TEXT, PRIMARY KEY (game_key, delivery_id)
            );
            INSERT INTO memory_entries(game_key, entity, relation, value)
            VALUES ('legacy-game', '旧实体', '记录', '保留这条记忆');
            """
        )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()

        reopened = MemoryStore(path)
        reopened.open()
        try:
            assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 5
            entries = reopened.list_entries("legacy-game", viewer_is_gm=True)
            assert [entry["value"] for entry in entries] == ["保留这条记忆"]
        finally:
            reopened.close()

    async def test_legacy_v3_vectors_are_preserved_but_marked_for_rebuild(self, tmp_path):
        path = tmp_path / "legacy-v3.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE memory_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_key TEXT NOT NULL, entity TEXT NOT NULL, relation TEXT NOT NULL,
                value TEXT NOT NULL, confidence REAL DEFAULT 1.0,
                status TEXT DEFAULT 'active', source_round INTEGER, embedding TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                memory_kind TEXT, source_kind TEXT, source_id TEXT,
                world_revision INTEGER, visibility TEXT
            );
            CREATE TABLE memory_economy_deliveries (
                game_key TEXT NOT NULL, delivery_id TEXT NOT NULL,
                before_state TEXT NOT NULL, after_state TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'applied', created_at TEXT NOT NULL,
                reversed_at TEXT, PRIMARY KEY (game_key, delivery_id)
            );
            INSERT INTO memory_entries(game_key, entity, relation, value, embedding)
            VALUES ('legacy-game', '旧实体', '记录', '不丢失', '[1.0, 0.0]');
            """
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit(); conn.close()

        store = MemoryStore(path)
        store.open()
        try:
            assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 5
            entry = store.list_entries("legacy-game", viewer_is_gm=True)[0]
            assert entry["embedding"] == "[1.0, 0.0]"
            assert entry["embedding_profile"] is None
            assert store.embedding_status()["pending"] == 1
        finally:
            store.close()

    async def test_unknown_v4_schema_remains_fail_closed(self, tmp_path):
        path = tmp_path / "unknown-v4.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE memory_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_key TEXT NOT NULL, entity TEXT NOT NULL, relation TEXT NOT NULL,
                value TEXT NOT NULL, confidence REAL DEFAULT 1.0,
                status TEXT DEFAULT 'active', source_round INTEGER, embedding TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()

        with pytest.raises(MigrationError, match="not a recognized"):
            MemoryStore(path).open()

    async def test_vectors_are_isolated_by_profile_and_rebuilt_after_updates(self, tmp_path):
        store = MemoryStore(tmp_path / "profiles.db")
        store.open()
        try:
            await store.apply_delta(
                "profile-game",
                {"add": [{"entity": "城门", "relation": "位置", "value": "北侧"}], "update": [], "forget": []},
                1,
            )
            client_a = SimpleNamespace(
                fingerprint=lambda: "profile-a",
                embed_batch=lambda texts: None,
            )

            async def embed_a(texts):
                return [[1.0, 0.0] for _ in texts]

            client_a.embed_batch = embed_a
            store.embedding_client = client_a
            assert await store.embed_all_pending("profile-game") == 1
            row = store.list_entries("profile-game", viewer_is_gm=True)[0]
            assert row["embedding_profile"] == "profile-a"
            assert store.recall_by_vector(
                "profile-game", [1.0, 0.0], viewer_is_gm=True,
                embedding_profile="profile-a",
            )
            assert store.recall_by_vector(
                "profile-game", [1.0, 0.0], viewer_is_gm=True,
                embedding_profile="profile-b",
            ) == []

            client_b = SimpleNamespace(
                fingerprint=lambda: "profile-b",
                embed_batch=embed_a,
            )
            store.embedding_client = client_b
            assert await store.embed_all_pending("profile-game") == 1
            row = store.list_entries("profile-game", viewer_is_gm=True)[0]
            assert row["embedding_profile"] == "profile-b"

            await store.apply_delta(
                "profile-game",
                {"add": [], "update": [{"entity": "城门", "relation": "位置", "value": "东侧"}], "forget": []},
                2,
            )
            changed = store.list_entries("profile-game", viewer_is_gm=True)[0]
            assert changed["embedding"] is None
            assert changed["embedding_profile"] is None
        finally:
            store.close()

    async def test_add_memory(self):
        store, path = _temp_store()
        try:
            delta = {
                "add": ["玩家遇到了一条龙"],
                "update": [],
                "forget": [],
            }
            await store.apply_delta("game1", delta, 1)
            unembedded = store.get_unembedded("game1", limit=100)
            assert len(unembedded) > 0
        finally:
            store.close()
            path.unlink(missing_ok=True)

    async def test_add_multiple(self):
        store, path = _temp_store()
        try:
            delta = {
                "add": ["记忆A", "记忆B", "记忆C"],
                "update": [],
                "forget": [],
            }
            await store.apply_delta("game2", delta, 1)
            unembedded = store.get_unembedded("game2", limit=100)
            assert len(unembedded) >= 1
        finally:
            store.close()
            path.unlink(missing_ok=True)

    async def test_multiple_sessions(self):
        store, path = _temp_store()
        try:
            await store.apply_delta("session_a", {
                "add": ["A的记忆"], "update": [], "forget": [],
            }, 1)
            await store.apply_delta("session_b", {
                "add": ["B的记忆"], "update": [], "forget": [],
            }, 1)
            a_count = store.get_unembedded_count("session_a")
            b_count = store.get_unembedded_count("session_b")
            assert a_count > 0
            assert b_count > 0
        finally:
            store.close()
            path.unlink(missing_ok=True)

    async def test_failed_delta_rolls_back_earlier_items(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.db")
        store.open()
        try:
            with pytest.raises(ValueError):
                await store.apply_delta("atomic-game", {
                    "add": [
                        "不应留下的第一条",
                        {
                            "entity": "坏数据",
                            "relation": "测试",
                            "value": "触发中途失败",
                            "confidence": "not-a-number",
                        },
                    ],
                    "update": [],
                    "forget": [],
                }, 1)

            assert store.list_entries("atomic-game", viewer_is_gm=True) == []
        finally:
            store.close()

    async def test_get_unembedded_limit_applies_to_profile_mismatch(self, tmp_path):
        store = MemoryStore(tmp_path / "limit-profile.db")
        store.open()
        try:
            # 模拟切换 Embedding 模型后的积压：每条都带旧 profile 的向量，
            # 且内容哈希与当前内容一致（仅 profile 不匹配）。
            for i in range(8):
                entity, relation, value = f"实体{i}", "位置", f"第{i}号地点"
                MemoryEntry.insert(
                    game_key="limit-game",
                    entity=entity, relation=relation, value=value,
                    embedding="[1.0, 0.0]",
                    embedding_profile="old-profile",
                    embedding_content_hash=embedding_content_hash(entity, relation, value),
                ).execute()
            store._conn.commit()

            result = store.get_unembedded("limit-game", limit=5, profile="new-profile")
            assert len(result) == 5
            assert all(e["embedding_profile"] == "old-profile" for e in result)
        finally:
            store.close()


class TestPagination:
    async def test_count_and_offset(self):
        store, path = _temp_store()
        try:
            await store.apply_delta("game1", {
                "add": [f"记忆{i}号实体" for i in range(5)],
                "update": [], "forget": [],
            }, 1)
            # total 计数正确（旧实现返回 limit 后的数量，会少算）
            assert store.count_entries("game1", viewer_is_gm=True) == 5
            # keyword 过滤计数
            assert store.count_entries("game1", "记忆2", viewer_is_gm=True) == 1
            assert store.count_entries("game1", "不存在", viewer_is_gm=True) == 0
            # offset 分页：两页不重叠
            page1 = store.list_entries("game1", limit=2, offset=0, viewer_is_gm=True)
            page2 = store.list_entries("game1", limit=2, offset=2, viewer_is_gm=True)
            assert len(page1) == 2
            assert len(page2) == 2
            assert {e["entity"] for e in page1}.isdisjoint({e["entity"] for e in page2})
            # recall 也支持 offset
            assert len(store.recall("game1", ["记忆"], limit=2, offset=2, viewer_is_gm=True)) <= 2
        finally:
            store.close()
            path.unlink(missing_ok=True)


class TestSessionIsolation:
    async def test_clear_game_removes_only_selected_session(self):
        store, path = _temp_store()
        try:
            await store.apply_delta("game1", {
                "add": ["上一局的线索"], "update": [], "forget": [],
            }, 1)
            await store.apply_delta("game2", {
                "add": ["另一局的线索"], "update": [], "forget": [],
            }, 1)

            removed = await store.clear_game("game1")

            assert removed == 1
            assert store.list_entries("game1", viewer_is_gm=True) == []
            assert len(store.list_entries("game2", viewer_is_gm=True)) == 1
        finally:
            store.close()
            path.unlink(missing_ok=True)
