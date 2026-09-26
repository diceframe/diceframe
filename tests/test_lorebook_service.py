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


def test_service_create_save_and_list_match_webapi(store: LorebookStore) -> None:
    api = _api(store)
    deps = _deps(store)
    book = {"id": "book-1", "name": "Book"}
    assert lorebooks.create_lorebook(deps, {"id": ""}) == api.create_lorebook({"id": ""})
    assert lorebooks.create_lorebook(deps, book)["ok"] is True
    assert lorebooks.create_lorebook(deps, book) == api.create_lorebook(book)

    entry = {"id": "entry-1", "name": "Entry"}
    assert lorebooks.save_lorebook_entry(deps, "book-1", entry)["ok"] is True
    assert lorebooks.save_lorebook_entry(deps, "book-1", entry) == api.save_lorebook_entry("book-1", entry)
    assert lorebooks.list_lorebooks(deps, world_id="world-1") == api.list_lorebooks("world-1")
    assert lorebooks.list_lorebooks(deps, game_key="web|room|gm") == api.list_lorebooks(game_key="web|room|gm")


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
