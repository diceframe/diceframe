"""Direct canonical lorebook service contracts and import boundary."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.lorebook.store import LorebookStore
from src.webui.api import WebAPI
from src.webui.services import lorebooks


@pytest.fixture
def store(tmp_path: Path):
    value = LorebookStore(tmp_path / "lorebook.db")
    value.open()
    try:
        value.create_world("world-1", "World One")
        yield value
    finally:
        value.close()


def _api(store: LorebookStore) -> WebAPI:
    api = WebAPI.__new__(WebAPI)
    api._lore = store
    return api


def _deps(store: LorebookStore) -> lorebooks.LorebookDependencies:
    return lorebooks.LorebookDependencies(
        lorebook=store,
        get_instance=lambda _key: None,
        get_lore_retriever=lambda: None,
    )


def _without_timestamps(value):
    if isinstance(value, dict):
        return {
            key: _without_timestamps(item)
            for key, item in value.items()
            if key not in {"created_at", "updated_at"}
        }
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def test_service_create_save_and_list_match_webapi(store: LorebookStore) -> None:
    api = _api(store)
    deps = _deps(store)
    book = {"id": "book-1", "name": "Book"}
    assert lorebooks.create_lorebook(deps, {"id": ""}) == api.create_lorebook({"id": ""})
    created = lorebooks.create_lorebook(deps, book)
    assert created["ok"] is True
    assert created["book"]["updated_at"]
    service_duplicate = lorebooks.create_lorebook(deps, book)
    api_duplicate = api.create_lorebook(book)
    assert _without_timestamps(service_duplicate) == _without_timestamps(api_duplicate)

    entry = {"id": "entry-1", "name": "Entry"}
    assert lorebooks.save_lorebook_entry(deps, "book-1", entry)["ok"] is True
    created_entry = lorebooks.save_lorebook_entry(deps, "book-1", entry)
    assert created_entry["ok"] is True
    service_entry = lorebooks.save_lorebook_entry(deps, "book-1", entry)
    api_entry = api.save_lorebook_entry("book-1", entry)
    assert _without_timestamps(service_entry) == _without_timestamps(api_entry)
    service_worlds = lorebooks.list_lorebooks(deps, world_id="world-1")
    api_worlds = api.list_lorebooks("world-1")
    assert _without_timestamps(service_worlds) == _without_timestamps(api_worlds)
    service_games = lorebooks.list_lorebooks(deps, game_key="web|room|gm")
    api_games = api.list_lorebooks(game_key="web|room|gm")
    assert _without_timestamps(service_games) == _without_timestamps(api_games)


def test_service_entry_create_uses_book_owner_for_legacy_world_projection(
    store: LorebookStore,
) -> None:
    deps = _deps(store)
    store.create_lorebook({"id": "book:standalone", "name": "Standalone"})

    result = lorebooks.save_lorebook_entry(
        deps,
        "book:standalone",
        {"id": "entry:standalone", "world_id": "world-1", "name": "Entry"},
    )

    assert result["ok"] is True
    entry = store.get_book_entry("book:standalone", "entry:standalone")
    assert entry and entry["world_id"] is None


def test_orphan_primary_book_remains_editable_without_creating_or_rebinding_world(
    store: LorebookStore,
) -> None:
    deps = _deps(store)
    missing_world = "custom_book_world_missing"
    book_id = store.ensure_primary_world_book(missing_world)
    bindings_before = store.list_bindings()
    worlds_before = store.list_worlds()

    assert lorebooks.list_lorebook_entries(deps, book_id)["entries"] == []
    saved = lorebooks.save_lorebook_entry(deps, book_id, {
        "id": "orphan-entry", "name": "Orphan entry", "content": "Original",
        "world_id": "world-1",  # A different valid world is not its owner.
    })
    assert saved["ok"] is True
    assert saved["entry"]["book_id"] == book_id
    assert saved["entry"]["world_id"] is None
    assert lorebooks.save_lorebook_entry(
        deps, book_id, {"id": "orphan-entry", "content": "Edited"},
    )["entry"]["content"] == "Edited"
    assert lorebooks.export_lorebook(deps, book_id)["ok"] is True
    assert lorebooks.list_lorebook_entries(deps, book_id)["entries"][0]["id"] == "orphan-entry"
    assert lorebooks.delete_lorebook_entry(deps, book_id, "orphan-entry")["ok"] is True
    assert store.get_world(missing_world) is None
    assert store.list_worlds() == worlds_before
    assert store.list_bindings() == bindings_before
    assert lorebooks.delete_lorebook(deps, book_id)["ok"] is False


def test_move_to_orphan_primary_book_keeps_canonical_owner_and_null_projection(
    store: LorebookStore,
) -> None:
    deps = _deps(store)
    book_id = store.ensure_primary_world_book("custom_book_world_missing")
    source_book_id = store.primary_world_book_id("world-1")
    lorebooks.save_lorebook_entry(deps, source_book_id, {"id": "move-orphan", "name": "Move me"})
    result = lorebooks.move_lorebook_entry(deps, source_book_id, "move-orphan", book_id)
    assert result["ok"] is True
    assert result["entry"]["book_id"] == book_id
    assert result["entry"]["world_id"] is None
    assert lorebooks.save_lorebook_entry(
        deps, source_book_id, {"id": "move-orphan", "content": "Wrong owner"},
    )["error_code"] == "entry_book_mismatch"
    store.delete_world_cascade("world-1")
    assert store.get_book_entry(book_id, "move-orphan") is not None


def test_explicit_catalogue_scope_includes_books_bound_outside_current_world(
    store: LorebookStore,
) -> None:
    store.create_lorebook({"id": "book:world", "name": "World book"})
    store.bind_lorebook({"id": "binding:world", "book_id": "book:world", "scope_kind": "world", "scope_id": "other-world"})
    store.create_lorebook({"id": "book:game", "name": "Game book"})
    store.bind_lorebook({"id": "binding:game", "book_id": "book:game", "scope_kind": "game", "scope_id": "game-1"})
    rows = lorebooks.list_lorebooks(_deps(store), include_all=True)["books"]
    assert {row["id"] for row in rows} >= {"book:world", "book:game"}


def test_world_book_list_does_not_leak_other_worlds_game_books(store: LorebookStore) -> None:
    """Browsing world A with an active game on world B must not list B's runtime books.

    Otherwise the workspace keeps the stale B book selected while visibility
    projections follow world A — the entry list and the badges stop matching.
    """
    deps = _deps(store)
    store.create_world("world-2", "World Two")
    store.create_lorebook({"id": "world:world-1", "name": "Primary One"})
    store.create_lorebook({"id": "world:world-2", "name": "Primary Two"})
    store.create_lorebook({"id": "book:game-world-2", "name": "Runtime of world-2"})
    store.create_lorebook({"id": "book:game-world-1", "name": "Runtime of world-1"})
    store.create_lorebook({"id": "book:game-agnostic", "name": "Game book without a world"})
    store.bind_lorebook({"id": "b:primary-1", "book_id": "world:world-1", "scope_kind": "world", "scope_id": "world-1", "role": "primary"})
    store.bind_lorebook({"id": "b:primary-2", "book_id": "world:world-2", "scope_kind": "world", "scope_id": "world-2", "role": "primary"})
    store.bind_lorebook({"id": "b:game-2", "book_id": "book:game-world-2", "scope_kind": "game", "scope_id": "game-1"})
    store.bind_lorebook({"id": "b:world-2-runtime", "book_id": "book:game-world-2", "scope_kind": "world", "scope_id": "world-2"})
    store.bind_lorebook({"id": "b:game-1", "book_id": "book:game-world-1", "scope_kind": "game", "scope_id": "game-1"})
    store.bind_lorebook({"id": "b:world-1-runtime", "book_id": "book:game-world-1", "scope_kind": "world", "scope_id": "world-1"})
    store.bind_lorebook({"id": "b:agnostic", "book_id": "book:game-agnostic", "scope_kind": "game", "scope_id": "game-1"})

    rows = lorebooks.list_lorebooks(deps, world_id="world-1", game_key="game-1")["books"]
    ids = {row["id"] for row in rows}
    assert "book:game-world-2" not in ids  # 别的世界的对局书不出现
    assert {"world:world-1", "book:game-world-1", "book:game-agnostic"} <= ids

    # 不带 world 浏览（游戏 scope）时仍然完整可见。
    game_rows = lorebooks.list_lorebooks(deps, game_key="game-1")["books"]
    assert "book:game-world-2" in {row["id"] for row in game_rows}


@pytest.mark.asyncio
async def test_missing_retriever_keeps_original_activation_error(store: LorebookStore) -> None:
    api = _api(store)
    api._reg = SimpleNamespace(get=lambda _key: SimpleNamespace(players={}))
    deps = lorebooks.LorebookDependencies(
        lorebook=store,
        get_instance=lambda _key: SimpleNamespace(players={}),
        get_lore_retriever=lambda: None,
    )
    payload = {"game_key": "web|room|gm"}
    expected = {"ok": False, "error": "game_key must reference an active game"}
    assert await lorebooks.lorebook_activation_preview(deps, payload) == expected
    assert await api.lorebook_activation_preview(payload) == expected


def test_service_import_boundary() -> None:
    path = Path(lorebooks.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(
                name.name != "src.webui.api"
                and (not name.name.startswith("src.webui.services.") or name.name == "src.webui.services._common")
                for name in node.names
            )
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "src.webui.api"
            assert not node.module.startswith("src.webui.services.") or node.module == "src.webui.services._common"
            if node.module == "src.webui.services":
                assert all(name.name == "_common" for name in node.names)
