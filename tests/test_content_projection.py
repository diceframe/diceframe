"""Track C PR A projection contract tests."""

from __future__ import annotations

from types import SimpleNamespace

from src.content_modules.projection import ContentProjection
from src.content_modules.projection import ContentProjectionService
from src.lorebook.store import LorebookStore
from src.webui.services.lorebooks import LorebookRowProjection


def test_lorebook_row_projection_is_a_content_projection() -> None:
    projector = LorebookRowProjection()
    assert isinstance(projector, ContentProjection)

    book = {"id": "book-1", "name": "Book", "source_kind": "native"}
    bindings = [{"scope_kind": "world", "scope_id": "world-1", "role": "primary"}]
    row = projector.project(book, context={"bindings": bindings})

    assert row == {
        **book,
        "bindings": bindings,
        "scope": "world",
        "primary": True,
    }
    assert row["bindings"] is not bindings
    assert row["bindings"][0] is not bindings[0]
    # Projection must not mutate the canonical book mapping or its binding list.
    assert book == {"id": "book-1", "name": "Book", "source_kind": "native"}
    assert bindings == [{"scope_kind": "world", "scope_id": "world-1", "role": "primary"}]


def test_lorebook_row_projection_defaults_to_unbound_shape() -> None:
    row = LorebookRowProjection().project({"id": "book-2"})
    assert row == {"id": "book-2", "bindings": [], "scope": "", "primary": False}


def test_content_projection_service_uses_book_bindings_for_authoring_and_runtime(tmp_path) -> None:
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    try:
        store.create_world("w1", "World")
        store.create_lorebook({"id": "book-side", "name": "Side"})
        store.bind_lorebook({
            "id": "binding:side",
            "book_id": "book-side",
            "scope_kind": "world",
            "scope_id": "w1",
            "role": "secondary",
            "order": 105,
        })
        store.add_book_entry("world:w1", {"id": "primary-entry", "name": "Primary"})
        store.add_book_entry("book-side", {"id": "side-entry", "name": "Side"})

        service = ContentProjectionService(store)
        authoring = service.for_world_authoring("w1")
        assert [entry["id"] for entry in authoring] == ["primary-entry", "side-entry"]

        instance = SimpleNamespace(
            world_id="w1",
            game_id="game-1",
            game_key="game-1",
            lorebook_store=store,
            action_actor_uids=[],
        )
        runtime = service.for_game(instance)
        assert [entry["id"] for entry in runtime] == ["primary-entry", "side-entry"]
        assert {entry["_lorebook_id"] for entry in runtime} == {"world:w1", "book-side"}

        # Runtime metadata is detached projection data, not canonical storage.
        assert "_lorebook_id" not in store.get_entry("primary-entry")
        assert "_lorebook_order" not in store.get_entry("side-entry")

        store.update_binding("binding:side", {"enabled": False})
        assert [entry["id"] for entry in service.for_game(instance)] == ["primary-entry"]
    finally:
        store.close()
