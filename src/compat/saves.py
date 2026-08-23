"""Compatibility helpers for persisted game saves."""

from __future__ import annotations

from typing import Any


def normalize_save_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Add a non-destructive schema marker while retaining every legacy field."""
    result = dict(payload)
    result.setdefault("save_schema_version", 1)
    return result


def legacy_chatlog_name() -> str:
    return "chatlog.jsonl"
