"""LorebookStore 集成测试 —— CRUD + 迁移 + 级联删除。"""

from __future__ import annotations

import tempfile
import time
import sqlite3
from pathlib import Path

import pytest

from src.lorebook.bootstrap import ensure_world_from_template
from src.lorebook.store import LorebookStore
from src.migrations.sqlite import MigrationError


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

    def test_delete_world_cascade_uses_primary_book_ownership(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "测试")
            store.create_lorebook({"id": "book:independent", "name": "Imported"})
            store.add_entry({
                "id": "primary", "book_id": "world:w1", "world_id": "w1",
                "name": "主世界条目",
            })
            # Simulate a pre-cutover row whose compatibility projection was
            # stale even though its canonical owner was already independent.
            store.add_entry({
                "id": "foreign", "book_id": "book:independent",
                "name": "独立条目",
            })
            store._conn.execute(
                "UPDATE lorebook_entries SET world_id = ? WHERE id = ?",
                ("w1", "foreign"),
            )
            store._conn.commit()

            store.delete_world_cascade("w1")
            assert store.get_entry("primary") is None
            foreign = store.get_entry("foreign")
            assert foreign is not None
            assert foreign["book_id"] == "book:independent"
            assert foreign["world_id"] is None
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

    def test_add_book_entry_derives_legacy_world_projection_from_book(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "World One")
            store.create_lorebook({"id": "book:independent", "name": "Imported"})
            store.create_lorebook({"id": "world:standalone", "name": "World-shaped only"})

            store.add_book_entry(
                "book:independent",
                {"id": "e-independent", "world_id": "w1", "name": "Imported entry"},
            )
            independent = store.get_entry("e-independent")
            assert independent and independent["book_id"] == "book:independent"
            assert independent["world_id"] is None

            store.add_book_entry(
                "world:standalone",
                {"id": "e-world-shaped", "name": "Not primary without a binding"},
            )
            world_shaped = store.get_entry("e-world-shaped")
            assert world_shaped and world_shaped["world_id"] is None

            with pytest.raises(ValueError, match="does not exist"):
                store.add_book_entry("book:missing", {"id": "bad", "name": "Bad"})
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_update_entry_supports_reserved_word_columns(self):
        """回归：order / group 是 SQL 保留字，编辑条目必须能保存。

        前端保存会把整条条目 PUT 上来（含 order/group）。修复前列名未加引号，
        UPDATE 会拼出 `SET order = ?` 并抛 sqlite3.OperationalError: near "order"，
        表现为「世界书保存按钮失效」（路由 500）。
        """
        store, path = _temp_store()
        try:
            store.create_world("w1", "测试")
            store.add_entry({"id": "e1", "world_id": "w1", "name": "草药师",
                            "keywords": ["草药"], "content": "旧内容"})
            store.update_entry("e1", {
                "content": "新内容",
                "order": 42,
                "group": "NPC",
                "group_weight": 3,
                "probability": 80,
            })
            entry = store.get_entry("e1")
            assert entry["content"] == "新内容"
            assert entry["order"] == 42
            assert entry["group"] == "NPC"
            assert entry["group_weight"] == 3
            assert entry["probability"] == 80
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
    def test_store_open_migrates_minimal_worlds_and_entries_end_to_end(self):
        path = Path(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE lorebook_entries (
                id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id),
                name TEXT NOT NULL, type TEXT, content TEXT
            );
            INSERT INTO worlds VALUES ('legacy', 'Legacy');
            INSERT INTO lorebook_entries VALUES ('old', 'legacy', 'Old', 'location', 'keep');
            """
        )
        conn.commit()
        conn.close()
        store = LorebookStore(path)
        try:
            store.open()
            assert store.get_world("legacy")["author"] == ""
            assert store.get_entry("old")["content"] == "keep"
            store.add_entry({"id": "new", "world_id": "legacy", "name": "New", "type": "spell"})
            store.update_entry("new", {"content": "updated"})
            assert store.get_entry("new")["content"] == "updated"
            store.delete_entry("new")
            assert store.get_entry("new") is None
        finally:
            store.close()
            store.open()
            try:
                assert store.get_entry("old") is not None
            finally:
                store.close()
                path.unlink(missing_ok=True)

    def test_fresh_and_migrated_schema_have_matching_contract(self):
        fresh, fresh_path = _temp_store()
        legacy_path = Path(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
        conn = sqlite3.connect(legacy_path)
        conn.executescript(
            """
            CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE lorebook_entries (id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id), name TEXT NOT NULL, type TEXT, content TEXT);
            INSERT INTO worlds VALUES ('w', 'W');
            INSERT INTO lorebook_entries VALUES ('e', 'w', 'E', 'location', 'C');
            """
        )
        conn.commit()
        conn.close()
        migrated = LorebookStore(legacy_path)
        try:
            migrated.open()
            for table in ("worlds", "lorebook_entries"):
                fresh_info = [tuple(row[1:6]) for row in fresh._conn.execute(f"PRAGMA table_info({table})")]
                migrated_info = [tuple(row[1:6]) for row in migrated._conn.execute(f"PRAGMA table_info({table})")]
                assert migrated_info == fresh_info
            fresh_indexes = {row[1] for row in fresh._conn.execute("PRAGMA index_list(lorebook_entries)")}
            migrated_indexes = {row[1] for row in migrated._conn.execute("PRAGMA index_list(lorebook_entries)")}
            assert {"idx_lorebook_world", "idx_lorebook_type", "idx_lorebook_tier", "idx_lorebook_source"} <= fresh_indexes & migrated_indexes
            assert migrated._conn.execute("PRAGMA foreign_key_list(lorebook_entries)").fetchone()[2] == "worlds"
        finally:
            fresh.close()
            migrated.close()
            fresh_path.unlink(missing_ok=True)
            legacy_path.unlink(missing_ok=True)

    def test_latest_schema_is_versioned_and_reopen_is_idempotent(self):
        store, path = _temp_store()
        try:
            assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 9
            store.create_world("w1", "测试")
            store.add_entry({"id": "e1", "world_id": "w1", "name": "x", "type": "spell"})
            store.close()
            reopened = LorebookStore(path)
            reopened.open()
            try:
                assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 9
                assert reopened.get_entry("e1")["type"] == "spell"
            finally:
                reopened.close()
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_vector_activation_modes_survive_v6_migration(self):
        store, path = _temp_store()
        try:
            store.create_lorebook({"id": "book:vectors", "name": "Vectors"})
            store.add_entry({"id": "off", "book_id": "book:vectors", "name": "Off", "vector_activation": "off"})
            store.add_entry({"id": "hybrid", "book_id": "book:vectors", "name": "Hybrid", "vector_activation": "hybrid"})
            store.add_entry({"id": "only", "book_id": "book:vectors", "name": "Only", "vector_activation": "vector_only"})
            assert [store.get_entry(item)["vector_activation"] for item in ("off", "hybrid", "only")] == ["off", "hybrid", "vector_only"]
        finally:
            store.close()
            path.unlink(missing_ok=True)

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

    def test_old_db_adds_source_plugin_column(self):
        """老库无 source_plugin 列：打开时迁移加列，老条目 source_plugin 默认空串（不回填）。"""
        import gc
        import sqlite3
        t = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        t.close()
        path = Path(t.name)
        try:
            # 构造旧库：无 source_plugin 列，插入老格式条目
            conn = sqlite3.connect(str(path))
            conn.execute("CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                         "description TEXT DEFAULT '', language TEXT DEFAULT 'zh-CN', "
                         "author TEXT DEFAULT '', version TEXT DEFAULT '1.0', "
                         "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                         "updated_at TEXT NOT NULL DEFAULT (datetime('now')))")
            conn.execute("INSERT INTO worlds (id, name) VALUES ('w1', '测试')")
            conn.execute("CREATE TABLE lorebook_entries ("
                         "id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id), name TEXT NOT NULL, "
                         "type TEXT DEFAULT 'other', keywords TEXT DEFAULT '[]', content TEXT DEFAULT '', "
                         "tier TEXT DEFAULT 'background')")
            conn.execute(
                "INSERT INTO lorebook_entries (id, world_id, name, type, content) VALUES (?,?,?,?,?)",
                ("frieren_journey_world_plugin_frieren-journey_e1", "w1", "P", "location", "c"),
            )
            conn.commit()
            conn.close()
            del conn
            gc.collect()

            store = LorebookStore(path)
            store.open()
            try:
                e1 = store.get_entry("frieren_journey_world_plugin_frieren-journey_e1")
                # 列已迁移加上；老条目不回填，source_plugin 保持空串
                assert e1 is not None
                assert "source_plugin" in e1
                assert e1["source_plugin"] == ""
            finally:
                store.close()
                del store
                gc.collect()
        finally:
            # Windows 上 sqlite WAL 句柄释放是异步的，清理失败不影响测试结果
            for _ in range(20):
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.1)

    def test_open_migrates_minimal_legacy_schema_before_creating_indexes(self):
        """字段不完整的旧库也必须能打开并完成完整 CRUD。"""
        import gc
        import sqlite3

        t = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        t.close()
        path = Path(t.name)
        try:
            conn = sqlite3.connect(str(path))
            conn.execute("CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
            conn.execute("INSERT INTO worlds (id, name) VALUES ('w1', '旧世界')")
            conn.execute(
                "CREATE TABLE lorebook_entries ("
                "id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id), "
                "name TEXT NOT NULL, type TEXT, keywords TEXT, content TEXT)"
            )
            conn.execute(
                "INSERT INTO lorebook_entries (id, world_id, name, type, keywords, content) "
                "VALUES ('e1', 'w1', '旧条目', 'location', '[]', '旧内容')"
            )
            conn.commit()
            conn.close()
            del conn
            gc.collect()

            store = LorebookStore(path)
            store.open()
            try:
                assert store.get_world("w1")["language"] == "zh-CN"
                assert store.get_entry("e1")["tier"] == "background"
                assert store._execute("PRAGMA user_version").fetchone()[0] == 9
                indexes = {
                    row[1] for row in store._execute("PRAGMA index_list('lorebook_entries')")
                }
                assert {
                    "idx_lorebook_world", "idx_lorebook_type",
                    "idx_lorebook_tier", "idx_lorebook_source",
                } <= indexes

                store.create_world("w2", "新世界")
                store.add_entry({
                    "id": "e2", "world_id": "w2", "name": "新条目",
                    "keywords": ["新"], "content": "内容",
                })
                assert store.list_entries("w2")[0]["id"] == "e2"
                store.update_entry("e2", {"name": "更新条目"})
                assert store.get_entry("e2")["name"] == "更新条目"
                store.delete_entry("e2")
                assert store.get_entry("e2") is None
            finally:
                store.close()
                del store
                gc.collect()

            reopened = LorebookStore(path)
            reopened.open()
            try:
                assert reopened.get_entry("e1")["content"] == "旧内容"
                assert reopened.list_worlds()[0]["id"] in {"w1", "w2"}
            finally:
                reopened.close()
                del reopened
                gc.collect()
        finally:
            for _ in range(20):
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.1)

    def test_drop_legacy_type_check_allows_spell_class(self):
        """老库 type 列带 CHECK 约束：打开时重建表去掉约束，能插入 spell/class。"""
        import gc
        import sqlite3
        t = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        t.close()
        path = Path(t.name)
        try:
            # 构造老库：带 CHECK 约束，无 source_plugin 列
            conn = sqlite3.connect(str(path))
            conn.execute("CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                         "description TEXT DEFAULT '', language TEXT DEFAULT 'zh-CN', "
                         "author TEXT DEFAULT '', version TEXT DEFAULT '1.0', "
                         "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                         "updated_at TEXT NOT NULL DEFAULT (datetime('now')))")
            conn.execute("INSERT INTO worlds (id, name) VALUES ('w1', '测试')")
            conn.execute("CREATE TABLE lorebook_entries ("
                         "id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id), name TEXT NOT NULL, "
                         "type TEXT NOT NULL DEFAULT 'other' CHECK(type IN ('npc','location','item','event','puzzle','faction','other')), "
                         "keywords TEXT DEFAULT '[]', content TEXT DEFAULT '', tier TEXT DEFAULT 'background')")
            conn.execute("INSERT INTO lorebook_entries (id, world_id, name, type) VALUES ('e1','w1','老条目','location')")
            conn.commit()
            conn.close()
            del conn
            gc.collect()

            store = LorebookStore(path)
            store.open()
            try:
                assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 9
                index_names = {row[1] for row in store._conn.execute("PRAGMA index_list(lorebook_entries)")}
                assert {"idx_lorebook_world", "idx_lorebook_type", "idx_lorebook_tier", "idx_lorebook_source"} <= index_names
                # 旧库已含 w1；不要 create_world（INSERT OR REPLACE 会级联删 e1）
                # 新类型 spell/class 能插入（CHECK 已去掉）
                store.add_entry({"id": "s1", "world_id": "w1", "name": "火球", "type": "spell", "content": "c"})
                store.add_entry({"id": "c1", "world_id": "w1", "name": "战士", "type": "class", "content": "c"})
                assert store.get_entry("s1")["type"] == "spell"
                assert store.get_entry("c1")["type"] == "class"
                # 老条目保留
                assert store.get_entry("e1")["type"] == "location"
            finally:
                store.close()
                del store
                gc.collect()
        finally:
            for _ in range(20):
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.1)

    def test_old_db_without_type_check_and_missing_columns_converges_to_current_schema(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE lorebook_entries (
                id TEXT PRIMARY KEY, world_id TEXT NOT NULL, name TEXT NOT NULL,
                type TEXT DEFAULT 'other', content TEXT DEFAULT ''
            );
            INSERT INTO worlds VALUES ('w1', '测试');
            INSERT INTO lorebook_entries VALUES ('e1', 'w1', '旧条目', 'location', '保留');
            """
        )
        from src.migrations import lorebook
        migrate = lorebook.migrate
        assert migrate(conn) == 9
        columns = {row[1] for row in conn.execute("PRAGMA table_info(lorebook_entries)")}
        assert set(lorebook._LOREBOOK_COLUMNS) <= columns
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='lorebook_entries'").fetchone()[0].upper()
        assert "TIER IN" in sql and "MATCH_MODE IN" in sql
        assert conn.execute("SELECT content FROM lorebook_entries WHERE id='e1'").fetchone()[0] == "保留"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE lorebook_entries SET tier='invalid' WHERE id='e1'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE lorebook_entries SET match_mode='invalid' WHERE id='e1'")

    def test_database_already_at_v2_still_runs_v3_convergence(self):
        from src.migrations import lorebook
        from src.migrations.sqlite import run_migrations

        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE lorebook_entries (
                id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id),
                name TEXT NOT NULL, type TEXT, keywords TEXT, content TEXT
            );
            INSERT INTO worlds VALUES ('w1', '旧世界');
            INSERT INTO lorebook_entries VALUES ('e1', 'w1', '旧条目', 'location', '[]', '保留');
            """
        )

        assert run_migrations(conn, ((1, lorebook._v1), (2, lorebook._v2))) == 2
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2

        assert lorebook.migrate(conn) == 9
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert conn.execute(
            "SELECT content FROM lorebook_entries WHERE id='e1'"
        ).fetchone()[0] == "保留"
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='lorebook_entries'"
        ).fetchone()[0].upper()
        assert "TIER IN" in schema and "MATCH_MODE IN" in schema
        assert conn.execute(
            "PRAGMA foreign_key_list(lorebook_entries)"
        ).fetchone()[2] == "worlds"

    def test_v3_normalizes_invalid_legacy_tier_and_match_mode(self):
        from src.migrations import lorebook
        from src.migrations.sqlite import run_migrations

        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE lorebook_entries (
                id TEXT PRIMARY KEY, world_id TEXT NOT NULL, name TEXT NOT NULL,
                type TEXT, keywords TEXT, content TEXT
            );
            INSERT INTO worlds VALUES ('w1', '旧世界');
            INSERT INTO lorebook_entries VALUES ('e1', 'w1', '旧条目', 'other', '[]', '保留');
            """
        )
        assert run_migrations(conn, ((1, lorebook._v1), (2, lorebook._v2))) == 2
        conn.execute(
            "UPDATE lorebook_entries SET tier='legacy-special', match_mode='sometimes' WHERE id='e1'"
        )
        conn.commit()

        assert lorebook.migrate(conn) == 9
        assert conn.execute(
            "SELECT tier, match_mode FROM lorebook_entries WHERE id='e1'"
        ).fetchone() == ("background", "any")

    def test_failed_lorebook_rebuild_rolls_back_without_new_table(self, monkeypatch):
        from src.migrations import lorebook

        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE lorebook_entries (
                id TEXT PRIMARY KEY, world_id TEXT NOT NULL, name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('npc','other')),
                content TEXT DEFAULT ''
            );
            INSERT INTO worlds VALUES ('w1', '测试');
            INSERT INTO lorebook_entries VALUES ('e1', 'w1', '旧条目', 'npc', '保留');
            """
        )
        monkeypatch.setattr(lorebook, "_REBUILT_ENTRIES_SQL", "CREATE TABLE lorebook_entries_new (broken")
        with pytest.raises(MigrationError):
            lorebook.migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT content FROM lorebook_entries WHERE id='e1'").fetchone()[0] == "保留"
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lorebook_entries_new'"
        ).fetchone() is None

    def test_v5_creates_primary_book_binding_and_preserves_legacy_facade(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "World One")
            store.add_entry({"id": "e1", "world_id": "w1", "name": "Entry", "content": "body"})
            assert store.primary_world_book_id("w1") == "world:w1"
            assert store.bound_world_projection_for_book("world:w1") == "w1"
            book = store.get_lorebook("world:w1")
            assert book and book["source_kind"] == "world"
            bindings = store.list_bindings(scope_kind="world", scope_id="w1")
            assert [(b["book_id"], b["role"]) for b in bindings] == [("world:w1", "primary")]
            assert store.list_entries("w1")[0]["book_id"] == "world:w1"
            assert store.list_book_entries("world:w1")[0]["id"] == "e1"
            assert store.search_book_entries("world:w1", "bod")[0]["id"] == "e1"
            store.close()
            store.open()
            assert store.get_lorebook("world:w1")
            assert len(store.list_bindings(scope_kind="world", scope_id="w1")) == 1
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_v5_supports_independent_books_without_world_authority(self):
        store, path = _temp_store()
        try:
            store.create_lorebook({"id": "book:import", "name": "Imported", "source_kind": "ccv3"})
            # Only the four canonical scopes are writable; a standalone book that
            # carries no world authority binds globally, not to a session.
            store.bind_lorebook({"id": "binding:import:global", "book_id": "book:import", "scope_kind": "global", "scope_id": ""})
            store.add_entry({"id": "e-import", "book_id": "book:import", "name": "Imported entry", "content": "text"})
            assert store.list_book_entries("book:import")[0]["world_id"] is None
            assert store.list_lorebooks(scope_kind="global")[0]["id"] == "book:import"
        finally:
            store.close()
            path.unlink(missing_ok=True)

    @pytest.mark.parametrize("kind", ["session", "persona", "chat", "unknown", ""])
    def test_non_canonical_scope_kind_is_rejected_at_store_boundary(self, kind):
        """The store is the canonical boundary, so import flows cannot bypass it."""

        store, path = _temp_store()
        try:
            store.create_lorebook({"id": "book:scoped", "name": "Scoped"})
            with pytest.raises(ValueError):
                store.bind_lorebook({"id": "binding:bad", "book_id": "book:scoped", "scope_kind": kind})
            assert store.list_bindings() == []
            # A scope update cannot smuggle in a non-canonical kind either.
            store.bind_lorebook({"id": "binding:ok", "book_id": "book:scoped", "scope_kind": "global"})
            with pytest.raises(ValueError):
                store.update_binding("binding:ok", {"scope_kind": "session"})
            assert store.list_bindings()[0]["scope_kind"] == "global"
        finally:
            store.close()
            path.unlink(missing_ok=True)

    def test_world_projection_excludes_entries_from_independent_books(self):
        store, path = _temp_store()
        try:
            store.create_world("w1", "World One")
            store.add_entry({"id": "primary", "world_id": "w1", "name": "Primary", "content": "primary"})
            store.create_lorebook({"id": "book:independent", "name": "Imported"})
            store.add_entry({
                "id": "independent", "book_id": "book:independent", "world_id": "w1",
                "name": "Independent", "content": "must not enter world projection",
            })

            assert [entry["id"] for entry in store.list_entries("w1")] == ["primary"]
            assert [entry["id"] for entry in store.list_book_entries("book:independent")] == ["independent"]
        finally:
            store.close()
            path.unlink(missing_ok=True)
