from __future__ import annotations

import pytest

pytest_plugins = ["tests.webapi_harness"]


@pytest.mark.asyncio
async def test_canonical_game_creation_binds_books_without_copying(web_api):
    api, lorebook, registry, _llm, _worlds_dir = web_api
    lorebook.ensure_primary_world_book("template_world")
    result = await api.create_game(
        "canonical_target",
        world_ref={
            "source_kind": "world", "source_id": "canonical_target",
            "kind": "world", "id": "canonical_target", "digest": "",
        },
        create_lorebook=True,
        source_world_id="template_world",
        book_bindings=[{
            "ref": {
                "source_kind": "world", "source_id": "template_world",
                "kind": "lorebook", "id": "world:template_world", "digest": "",
            },
        }],
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )

    assert result["ok"] is True
    game_key = result["game_key"]
    instance = registry.get(api._parse_key(game_key))
    assert instance is not None
    assert instance.modules["content_binding"]["world_ref"]["id"] == "canonical_target"
    assert instance.modules["content_binding"]["book_refs"][0]["id"] == "world:template_world"
    assert lorebook.list_entries("canonical_target") == []
    assert lorebook.list_bindings(scope_kind="game", scope_id=game_key)

    deleted = api.delete_game(game_key)
    assert deleted["ok"] is True
    assert lorebook.list_bindings(scope_kind="game", scope_id=game_key) == []
