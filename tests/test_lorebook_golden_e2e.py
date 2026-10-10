import sqlite3
from dataclasses import asdict
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from src.engine.modules import narrative_notes
from src.lorebook.activation import migrate_timed_state
from src.lorebook.exporter import export_lorebook_native, export_lorebook_v3
from src.lorebook.importer import preview_lorebook_import, commit_lorebook_import
from src.lorebook.store import LorebookStore
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.retrieval import LoreRetriever
from src.webui.routes.lorebooks import register_lorebooks
from src.webui.api import WebAPI
from src.engine.modules import lorebook_runtime


def test_golden_old_db_import_preview_bind_export_restart(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE worlds (id TEXT PRIMARY KEY, name TEXT NOT NULL); CREATE TABLE lorebook_entries (id TEXT PRIMARY KEY, world_id TEXT NOT NULL, name TEXT NOT NULL, content TEXT); INSERT INTO worlds VALUES ('w', 'Old'); INSERT INTO lorebook_entries VALUES ('e', 'w', 'Old entry', 'legacy');")
    conn.commit(); conn.close()
    store = LorebookStore(path); store.open()
    try:
        assert store.get_lorebook("world:w")
        preview = preview_lorebook_import({"entries": [{"uid": "st", "key": ["harbor"], "content": "rumor", "sticky": 1}]})
        assert preview["format"] == "sillytavern"
        book_id = commit_lorebook_import(store, preview["book"], {"id": "binding:game", "scope_kind": "world", "scope_id": "w"}, book_id="book:st")
        assert store.list_book_entries(book_id)[0]["name"] == ""
        exported = export_lorebook_v3(store, book_id)
        assert exported["spec"] == "lorebook_v3"
        assert migrate_timed_state({"e": {"status": "cooldown", "remaining": 2}})["e"]["cooldown_remaining"] == 2
    finally:
        store.close()


    store = LorebookStore(path); store.open()
    try:
        assert store.list_entries("w")
        assert store.list_book_entries("book:st")
    finally:
        store.close()


def test_export_projections_preserve_settings_extensions_provenance_and_bindings(tmp_path):
    store = LorebookStore(tmp_path / "export.db")
    store.open()
    try:
        store.create_lorebook({
            "id": "book:export", "name": "Configured", "description": "desc",
            "scan_depth": 4, "token_budget": 321, "recursive_scanning": True,
            "settings": {"scan_depth": 4, "token_budget": 321, "custom": "keep"},
            "source_kind": "plugin", "source_id": "source-1",
        })
        store.bind_lorebook({"id": "binding:export", "book_id": "book:export", "scope_kind": "world", "scope_id": "w", "role": "secondary"})
        store.add_entry({
            "id": "entry:export", "book_id": "book:export", "name": "Secret", "content": "hidden",
            "keywords": ["door"], "extensions": {"plugin": {"x": 1}},
            "provenance": {"kind": "plugin", "id": "source-1"},
        })

        v3 = export_lorebook_v3(store, "book:export")
        assert v3["spec"] == "lorebook_v3"
        exported_book = v3["data"]["lorebook"]
        assert exported_book["custom"] == "keep"
        assert exported_book["scan_depth"] == 4
        draft = preview_lorebook_import(v3)["book"]
        assert draft.entries[0].extensions["plugin"] == {"x": 1}

        native = export_lorebook_native(store, "book:export")
        assert native["data"]["book"]["settings"]["custom"] == "keep"
        assert native["data"]["bindings"][0]["id"] == "binding:export"
        assert native["data"]["entries"][0]["provenance"] == {"kind": "plugin", "id": "source-1"}
        assert native["data"]["entries"][0]["extensions"] == {"plugin": {"x": 1}}
        assert native["data"]["provenance"]["entries"]["entry:export"] == {"kind": "plugin", "id": "source-1"}
        api = WebAPI.__new__(WebAPI)
        api._lore = store
        response = api.export_lorebook("book:export")
        assert response["spec"] == "lorebook_v3"
        assert response["data"]["lorebook"]["entries"][0]["id"] == "entry:export"
        assert response["native_backup"]["spec"] == "diceframe_lorebook_native"
    finally:
        store.close()
@pytest.mark.asyncio
async def test_golden_real_route_import_to_multi_book_retrieval(tmp_path):
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world("w", "World")

    class Api:
        def preview_lorebook_import(self, payload):
            result = preview_lorebook_import(payload)
            return result | {"book": asdict(result["book"])}

        def commit_lorebook_import(self, payload, binding=None, book_id=None):
            draft = preview_lorebook_import(payload)["book"]
            imported = commit_lorebook_import(store, draft, binding, book_id=book_id)
            return {"ok": True, "book_id": imported, "entries": len(draft.entries), "warnings": draft.warnings}

    app = web.Application()
    app["api"] = Api()
    register_lorebooks(app)
    try:
        async with TestClient(TestServer(app)) as client:
            preview_response = await client.post(
                "/api/lorebooks/import/preview",
                json={"name": "Primary", "entries": [{"uid": "st", "key": ["harbor"], "keysecondary": ["secret"], "content": "harbor secret"}]},
            )
            assert preview_response.status == 200
            commit_response = await client.post(
                "/api/lorebooks/import",
                json={"payload": {"name": "Primary", "entries": [{"uid": "st", "key": ["harbor"], "keysecondary": ["secret"], "content": "harbor secret"}]}, "book_id": "world:w"},
            )
            assert commit_response.status == 200

        store.create_lorebook({"id": "global-book", "name": "Global"})
        store.bind_lorebook({"id": "binding:global", "book_id": "global-book", "scope_kind": "global", "scope_id": ""})
        store.add_entry({"id": "global-clue", "book_id": "global-book", "name": "Global", "keywords": ["secret"], "content": "global"})
        instance = SimpleNamespace(world_id="w", language="zh-CN", npcs={}, players={}, world_state={}, modules={"narrative_notes": narrative_notes.fresh(), "lorebook_runtime": {**lorebook_runtime.fresh(), "timers": {}}}, lorebook_store=store)
        hits = await LoreRetriever(KeywordMatcher(), store=store).retrieve(instance, "harbor secret")
        assert {entry["content"] for entry in hits} == {"harbor secret", "global"}
    finally:
        store.close()
