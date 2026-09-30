from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.content.worlds import (
    load_lorebook_resource,
    load_world_definition,
    load_world_template,
    materialize_lorebook,
)
from src.lorebook.adapters.world_legacy import from_legacy_world_template


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_v3_world_and_book_are_separate_with_independent_locale_overlays(tmp_path: Path):
    worlds = tmp_path / "worlds"
    _write(worlds / "arkham.json", {
        "world_schema_version": 3,
        "world_id": "arkham",
        "world_name": "阿卡姆",
        "description": "核心世界",
        "language": "zh-CN",
        "default_rule": "freeform_coc",
        "primary_lorebook_ref": "core:arkham-lore",
        "gm_style": {"tone": "gothic"},
    })
    _write(worlds / "lorebooks" / "arkham-lore.json", {
        "lorebook_schema_version": 1,
        "id": "arkham-lore",
        "name": "阿卡姆资料",
        "description": "核心条目",
        "entries": [{
            "id": "inn", "name": "旅店", "type": "location",
            "keywords": ["旅店"], "content": "中文内容", "tier": "core",
        }],
    })
    _write(worlds / "locales" / "en" / "arkham.json", {
        "locale_schema_version": 1,
        "locale": "en",
        "target": {"kind": "world", "id": "arkham"},
        "fields": {"world_name": "Arkham"},
    })
    _write(worlds / "lorebooks" / "locales" / "en" / "arkham-lore.json", {
        "locale_schema_version": 1,
        "locale": "en",
        "target": {"kind": "lorebook", "id": "arkham-lore"},
        "fields": {"name": "Arkham Lore"},
        "entries": {"inn": {"name": "The Inn", "keywords": ["inn"], "content": "English content"}},
    })

    definition = load_world_definition(worlds, "arkham", "en-US")
    assert definition["world_name"] == "Arkham"
    assert "starter_lorebook" not in definition
    assert definition["primary_lorebook_ref"] == "core:arkham-lore"

    book = load_lorebook_resource(worlds, definition["primary_lorebook_ref"], "en-US")
    assert book["name"] == "Arkham Lore"
    assert book["entries"][0]["name"] == "The Inn"
    assert book["entries"][0]["type"] == "location"

    # The old loader remains a narrow compatibility facade for callers that
    # still expect starter_lorebook, while the canonical definition is clean.
    legacy_view = load_world_template(worlds, "arkham", "en-US")
    assert legacy_view["_legacy_adapter"] == "world_v3_lorebook"
    assert legacy_view["starter_lorebook"][0]["id"] == "inn"


def test_v3_world_rejects_embedded_lore_and_book_locale_cannot_change_mechanics():
    with pytest.raises(ValueError, match="starter_lorebook"):
        from src.content.worlds import _world_draft_from_definition

        _world_draft_from_definition({
            "world_schema_version": 3,
            "world_id": "w",
            "primary_lorebook_ref": "core:w-lore",
            "starter_lorebook": [],
        })

    core = {
        "lorebook_schema_version": 1,
        "id": "book",
        "entries": [{"id": "e", "name": "E", "type": "npc", "content": "C"}],
    }
    overlay = {
        "locale_schema_version": 1,
        "locale": "en",
        "target": {"kind": "lorebook", "id": "book"},
        "entries": {"e": {"type": "location"}},
    }
    with pytest.raises(ValueError, match="mechanics"):
        materialize_lorebook(core, overlay)


def test_legacy_world_adapter_splits_world_and_lorebook_drafts():
    world, lorebook = from_legacy_world_template({
        "world_id": "legacy-world",
        "world_name": "Legacy World",
        "description": "Description",
        "language": "en",
        "default_rule": "d20",
        "starter_lorebook": [{
            "id": "npc", "name": "Guide", "type": "npc",
            "keywords": ["guide"], "content": "A guide.",
        }],
    })
    assert world.primary_lorebook_ref == "legacy:legacy-world"
    assert "starter_lorebook" not in world.as_dict()
    assert lorebook.language == "en"
    assert lorebook.entries[0].external_id == "npc"
    assert lorebook.source["kind"] == "legacy_world_template"


class _WorldLLM:
    async def call(self, **_kwargs):
        return SimpleNamespace(content=json.dumps({
            "world_name": "Generated Coast",
            "description": "A stormy coast.",
            "world_setting": "A stormy coast.",
            "starter_scene": "At the harbor.",
            "default_rule": "freeform_fantasy",
            "starter_lorebook": [
                {"id": "npc", "name": "Guide", "type": "npc", "content": "A guide", "visibility": "public"},
            ],
        }), total_tokens=0)


@pytest.mark.asyncio
async def test_ai_world_generation_exposes_split_draft_without_removing_legacy_fields(tmp_path: Path):
    from src.generation.creator import generate_world

    result = await generate_world(_WorldLLM(), "a stormy coast", worlds_dir=tmp_path)
    assert result["ok"] is True
    assert result["lorebook_count"] == 1
    assert result["draft"]["world"]["world_schema_version"] == 3
    assert result["draft"]["world"]["source"]["kind"] == "ai_generated"
    assert "starter_lorebook" not in result["draft"]["world"]
    assert result["draft"]["lorebook"]["source"]["kind"] == "ai_generated"
    # Existing API clients still receive the summary shape and persisted v2
    # template until the later commit cutover lands.
    assert (tmp_path / f"{result['world_id']}.json").exists()
