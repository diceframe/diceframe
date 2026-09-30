"""Canonical content references bound to one game run.

The binding slot is deliberately opaque to the generic game aggregate: World,
Book and Adventure remain content-layer identities while the game only keeps
the source-aware refs selected for this run.  The Lorebook database remains the
authority for the actual Book binding rows.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.content_modules.refs import ContentRefError, parse_content_ref
from src.engine.module_state import ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "content_binding"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "world_ref": {},
        "book_refs": [],
        "adventure_refs": [],
    }


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    raw.setdefault("world_ref", {})
    raw.setdefault("book_refs", [])
    raw.setdefault("adventure_refs", [])
    return raw


def _state(instance: Any) -> dict[str, Any]:
    return get_module_state(instance, MODULE_NAME)


def world_ref(instance: Any) -> dict[str, Any]:
    return deepcopy(_state(instance).get("world_ref") or {})


def set_world_ref(instance: Any, ref: dict[str, Any]) -> None:
    parsed = parse_content_ref(ref, default_source="world:unknown")
    if parsed.kind != "world":
        raise ContentRefError("content binding world_ref must have kind 'world'")
    _state(instance)["world_ref"] = parsed.to_portable_dict()


def book_refs(instance: Any) -> list[dict[str, Any]]:
    return deepcopy(list(_state(instance).get("book_refs") or []))


def add_book_ref(instance: Any, ref: dict[str, Any]) -> None:
    parsed = parse_content_ref(ref, default_source="world:unknown")
    if parsed.kind != "lorebook":
        raise ContentRefError("content binding book_refs must have kind 'lorebook'")
    value = parsed.to_portable_dict()
    state = _state(instance)
    refs = state.setdefault("book_refs", [])
    if value not in refs:
        refs.append(value)


def adventure_refs(instance: Any) -> list[dict[str, Any]]:
    return deepcopy(list(_state(instance).get("adventure_refs") or []))


def add_adventure_ref(instance: Any, ref: dict[str, Any]) -> None:
    parsed = parse_content_ref(ref, default_source="adventure:unknown")
    if parsed.kind != "adventure":
        raise ContentRefError("content binding adventure_refs must have kind 'adventure'")
    value = parsed.to_portable_dict()
    state = _state(instance)
    refs = state.setdefault("adventure_refs", [])
    if value not in refs:
        refs.append(value)


SPEC = ModuleStateSpec(
    name=MODULE_NAME,
    schema_version=SCHEMA_VERSION,
    fresh=fresh,
    ensure=ensure,
)
register_module_state(SPEC)


__all__ = [
    "MODULE_NAME",
    "SCHEMA_VERSION",
    "add_adventure_ref",
    "add_book_ref",
    "book_refs",
    "adventure_refs",
    "ensure",
    "fresh",
    "set_world_ref",
    "world_ref",
]
