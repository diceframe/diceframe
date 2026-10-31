"""Game-scoped Book bindings resolve for tuple game keys stored as 'a|b|c'."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.lorebook.resolver import resolve_active_books


class _Store:
    def __init__(self, bindings):
        self._bindings = bindings

    def list_bindings(self):
        return list(self._bindings)


def test_game_binding_matches_joined_tuple_game_key() -> None:
    store = _Store([{
        "id": "binding:game:web|room|gm:lorebook:b1", "book_id": "b1",
        "scope_kind": "game", "scope_id": "web|room|gm", "role": "runtime", "order": 110,
    }])
    instance = SimpleNamespace(world_id="w1", game_id="", game_key=("web", "room", "gm"))

    refs = resolve_active_books(instance, store=store)

    assert [ref.book_id for ref in refs] == ["b1"]


def test_game_binding_for_another_game_is_ignored() -> None:
    store = _Store([{
        "id": "x", "book_id": "b2", "scope_kind": "game", "scope_id": "web|other|gm",
    }])
    instance = SimpleNamespace(world_id="w1", game_id="", game_key=("web", "room", "gm"))

    assert resolve_active_books(instance, store=store) == []


@pytest.mark.parametrize("viewer,explicit,expected", [
    ("gm", False, {"global", "world", "game", "own", "acting"}),
    ("party", False, {"global", "world", "game"}),
    ("gm", True, {"game", "own", "acting"}),
])
def test_resolver_only_reads_books_for_eligible_bindings(viewer, explicit, expected):
    bindings = [
        {"book_id": "global", "scope_kind": "global"},
        {"book_id": "world", "scope_kind": "world", "scope_id": "w1"},
        {"book_id": "game", "scope_kind": "game", "scope_id": "web|room|gm"},
        {"book_id": "game", "scope_kind": "game", "scope_id": "web|room|gm"},
        {"book_id": "own", "scope_kind": "character", "scope_id": "p1"},
        {"book_id": "acting", "scope_kind": "character", "scope_id": "p2"},
        {"book_id": "disabled-book", "scope_kind": "game", "scope_id": "web|room|gm"},
        {"book_id": "disabled-binding", "scope_kind": "game", "scope_id": "web|room|gm",
         "enabled": False},
    ] + [
        {"book_id": f"other-{i}", "scope_kind": "world", "scope_id": f"other-{i}"}
        for i in range(100)
    ]

    class CountingStore(_Store):
        def __init__(self):
            super().__init__(bindings)
            self.reads = []

        def get_lorebook(self, book_id):
            self.reads.append(book_id)
            return {"enabled": book_id != "disabled-book"}

    store = CountingStore()
    instance = SimpleNamespace(
        world_id="w1", game_key=("web", "room", "gm"),
        modules={"content_binding": {"book_selection": "explicit" if explicit else "auto"}},
    )
    refs = resolve_active_books(instance, viewer, "p1", ["p2"], store=store)
    assert {ref.book_id for ref in refs} == expected
    assert set(store.reads) == expected | {"disabled-book"}
    assert len(store.reads) == len(expected) + 1
