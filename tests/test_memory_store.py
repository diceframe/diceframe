"""MemoryStore 集成测试 —— delta 应用。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.memory.delta import MemoryStore

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
            # WR-06：memory schema 3 增加世界记忆来源列（纯加列，旧行 NULL）。
            assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 3
            table = store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                ("memory_economy_deliveries",),
            ).fetchone()
            assert table is not None
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
