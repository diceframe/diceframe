"""Track C PR A projection contract tests."""

from __future__ import annotations

from src.content_modules.projection import ContentProjection
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
