"""Legacy World-template adapter for the World/Lorebook split.

This module is the only place where the old ``starter_lorebook`` shape is
turned into two drafts.  Canonical World services should receive a
``WorldDraft`` and a separate ``LorebookDraft`` instead of reading that field
directly.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from src.content.worlds import WorldDraft

from ..domain import LorebookDraft
from .legacy import from_legacy_entries


def from_legacy_world_template(
    payload: dict[str, Any],
) -> tuple[WorldDraft, LorebookDraft]:
    """Convert a v1/v2 World template into independent World/Book drafts."""

    if not isinstance(payload, dict):
        raise ValueError("legacy world template must be an object")
    world_id = str(payload.get("world_id") or payload.get("id") or "").strip()
    if not world_id:
        raise ValueError("legacy world template requires world_id")
    entries = payload.get("starter_lorebook", [])
    if not isinstance(entries, list):
        raise ValueError("legacy starter_lorebook must be a list")
    book_id = f"legacy:{world_id}"
    lorebook = from_legacy_entries({
        "name": f"{payload.get('world_name') or world_id} Lorebook",
        "entries": entries,
    })
    lorebook.language = str(payload.get("language") or "zh-CN")
    lorebook.source = {
        "kind": "legacy_world_template",
        "world_id": world_id,
        "book_id": book_id,
    }
    world = WorldDraft(
        world_id=world_id,
        name=str(payload.get("world_name") or payload.get("name") or world_id),
        description=str(payload.get("description") or ""),
        language=str(payload.get("language") or "zh-CN"),
        default_rule=str(payload.get("default_rule") or ""),
        recommended_rules=tuple(
            str(item) for item in payload.get("recommended_rules", [])
            if item
        ),
        primary_lorebook_ref=book_id,
        fields={
            key: value for key, value in payload.items()
            if key not in {
                "world_schema_version", "world_id", "id", "world_name", "name",
                "description", "language", "default_rule", "recommended_rules",
                "starter_lorebook",
            }
        },
        source={"kind": "legacy_world_template", "world_id": world_id},
        warnings=("starter_lorebook converted by LegacyWorldAdapter",),
    )
    return world, lorebook


def from_generated_world_payload(
    payload: dict[str, Any],
) -> tuple[WorldDraft, LorebookDraft]:
    """Split an AI-generated legacy-shaped payload without committing it.

    The generator still exposes its historical response for API compatibility,
    but callers that need canonical content can consume these drafts and decide
    when/how to commit the World and Book atomically.
    """

    world, lorebook = from_legacy_world_template(payload)
    world = replace(
        world,
        primary_lorebook_ref=f"ai:{world.world_id}",
        source={"kind": "ai_generated", "world_id": world.world_id},
    )
    lorebook.source = {
        "kind": "ai_generated",
        "world_id": world.world_id,
        "book_id": world.primary_lorebook_ref,
    }
    return world, lorebook


__all__ = ["from_generated_world_payload", "from_legacy_world_template"]
