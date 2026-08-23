"""Compatibility boundary for legacy character-sheet fields."""

from __future__ import annotations

from typing import Any

from src.engine.character_utils import migrate_legacy_character_sheet, normalize_character_sheet

__all__ = ["migrate_legacy_character_sheet", "normalize_character_sheet", "normalize_character_payload"]


def normalize_character_payload(character: dict[str, Any], rule: object | None = None) -> dict[str, Any]:
    """Normalize a character without deleting legacy aliases."""
    return normalize_character_sheet(character, rule)  # type: ignore[arg-type]
