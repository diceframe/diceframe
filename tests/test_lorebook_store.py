"""LorebookStore 集成测试 —— CRUD + 迁移 + 级联删除。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.lorebook.bootstrap import ensure_world_from_template
from src.lorebook.store import LorebookStore


def _temp_store():
    """创建临时存储用于测试。"""
    t = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    t.close()
    store = LorebookStore(Path(t.name))
    store.open()
    return store, Path(t.name)


class TestCreateAndGetWorld:
    def test_create_world(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "测试世界", description="测试描述", author="tester")
            w = store.get_world("w1")
            assert w is not None
            assert w["name"] == "测试世界"
            assert w["description"] == "测试描述"
            assert w["author"] == "tester"
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_list_worlds(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "世界1")
            store.create_world("w2", "世界2")
            worlds = store.list_worlds()
            assert len(worlds) == 2
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_builtin_template_repairs_legacy_world_language_without_losing_entries(self):
        store, path = _temp_store()
        try:
            store.create_world("world_en", "Legacy World", language="zh-CN")
            store.add_entry({
                "id": "entry_1",
                "world_id": "world_en",
                "name": "Existing entry",
                "keywords": [],
                "content": "Keep me",
            })

            inserted = ensure_world_from_template(store, "world_en", {
                "world_name": "English World",
                "language": "en",
                "starter_lorebook": [],
            })

            assert inserted == 0
            assert store.get_world("world_en")["language"] == "en"
            assert store.get_entry("entry_1")["content"] == "Keep me"
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_delete_world_cascade(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "测试")
            store.add_entry({"id": "e1", "world_id": "w1", "name": "条目1",
                            "keywords": ["测试"], "content": "内容"})
            store.delete_world_cascade("w1")
            assert store.get_world("w1") is None
            assert store.get_entry("e1") is None
        finally:
            store.close()
            path.unlink(missing_ok=True)


class TestEntryCRUD:
    def test_add_and_get_entry(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "测试世界")
            store.add_entry({"id": "e1", "world_id": "w1", "name": "龙",
                            "keywords": ["龙", "火"], "content": "一条火龙",
                            "type": "npc", "tier": "core"})
            entry = store.get_entry("e1")
            assert entry is not None
            assert entry["name"] == "龙"
            assert "龙" in entry["keywords"]
            assert "火" in entry["keywords"]
            assert entry["tier"] == "core"
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_update_entry(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "测试")
            store.add_entry({"id": "e1", "world_id": "w1", "name": "旧名称",
                            "keywords": ["旧"], "content": "旧内容"})
            store.update_entry("e1", {"name": "新名称", "content": "新内容"})
            entry = store.get_entry("e1")
            assert entry["name"] == "新名称"
            assert entry["content"] == "新内容"
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_list_entries_by_world(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "世界1")
            store.create_world("w2", "世界2")
            store.add_entry({"id": "e1", "world_id": "w1", "name": "条目1",
                            "keywords": [], "content": "a"})
            store.add_entry({"id": "e2", "world_id": "w2", "name": "条目2",
                            "keywords": [], "content": "b"})
            w1_entries = store.list_entries("w1")
            assert len(w1_entries) == 1
            assert w1_entries[0]["name"] == "条目1"
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_search_entries(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "测试")
            store.add_entry({"id": "e1", "world_id": "w1", "name": "哥布林",
                            "keywords": ["哥布林"], "content": "绿色的小怪物"})
            store.add_entry({"id": "e2", "world_id": "w1", "name": "巨龙",
                            "keywords": ["龙"], "content": "会喷火"})
            results = store.search_entries("w1", "龙")
            assert len(results) == 1
            assert results[0]["name"] == "巨龙"
        finally:
            store.close()
            path.unlink(missing_ok=True)


class TestMigration:
    def test_new_columns_exist(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "测试")
            store.add_entry({
                "id": "e1", "world_id": "w1", "name": "test",
                "keywords": ["test"], "content": "test",
                "sticky": 3, "cooldown": 2, "delay": 1,
                "order": 50, "probability": 80, "group": "g1",
                "group_weight": 10,
            })
            entry = store.get_entry("e1")
            assert entry["sticky"] == 3
            assert entry["cooldown"] == 2
            assert entry["delay"] == 1
            assert entry["order"] == 50
            assert entry["probability"] == 80
            assert entry["group"] == "g1"
            assert entry["group_weight"] == 10
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_default_values(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "测试")
            store.add_entry({"id": "e1", "world_id": "w1", "name": "test",
                            "keywords": [], "content": "test"})
            entry = store.get_entry("e1")
            assert entry["sticky"] == 0
            assert entry["cooldown"] == 0
            assert entry["delay"] == 0
            assert entry["order"] == 100
            assert entry["probability"] == 100
            assert entry["group"] == ""
            assert entry["group_weight"] == 1
        finally:
            store.close()
            path.unlink(missing_ok=True)
