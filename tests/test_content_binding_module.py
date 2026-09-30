from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.modules import content_binding


def test_content_binding_keeps_source_aware_refs_detached() -> None:
    instance = SimpleNamespace(modules={})
    content_binding.set_world_ref(instance, {
        "source_kind": "world", "source_id": "ashen", "kind": "world", "id": "ashen",
    })
    content_binding.add_book_ref(instance, {
        "source_kind": "module", "source_id": "ruins", "kind": "lorebook", "id": "ruins-book",
    })
    content_binding.add_adventure_ref(instance, {
        "source_kind": "plugin", "source_id": "sample", "kind": "adventure", "id": "intro",
    })

    world = content_binding.world_ref(instance)
    books = content_binding.book_refs(instance)
    adventures = content_binding.adventure_refs(instance)
    world["id"] = "changed"
    books[0]["id"] = "changed"
    adventures[0]["id"] = "changed"

    assert content_binding.world_ref(instance)["id"] == "ashen"
    assert content_binding.book_refs(instance)[0]["id"] == "ruins-book"
    assert content_binding.adventure_refs(instance)[0]["id"] == "intro"


@pytest.mark.parametrize("method, ref", [
    (content_binding.set_world_ref, {"kind": "lorebook", "id": "book"}),
    (content_binding.add_book_ref, {"kind": "world", "id": "world"}),
    (content_binding.add_adventure_ref, {"kind": "world", "id": "world"}),
])
def test_content_binding_rejects_wrong_kind(method, ref) -> None:
    instance = SimpleNamespace(modules={})
    with pytest.raises(ValueError):
        method(instance, ref)
