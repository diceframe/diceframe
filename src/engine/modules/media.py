"""Scene-image and map-background references in a persisted module slot."""

from __future__ import annotations

from typing import Any

from src.engine.module_state import ModuleStateError, ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "media"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "scene_image": {}, "map_background": {}}


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    for key in ("scene_image", "map_background"):
        if not isinstance(raw.get(key), dict):
            raw[key] = {}
    return raw


def scene_image(instance: Any) -> dict[str, str]:
    return get_module_state(instance, MODULE_NAME)["scene_image"]


def replace_scene_image(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["scene_image"] = value if isinstance(value, dict) else {}


def map_background(instance: Any) -> dict[str, str]:
    return get_module_state(instance, MODULE_NAME)["map_background"]


def replace_map_background(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["map_background"] = value if isinstance(value, dict) else {}


def payload_container(payload: dict[str, Any]) -> dict[str, Any]:
    """Locate supported references without repairing or mutating raw package data.

    Only an absent slot permits legacy fallback. Unlike live-state access,
    package access must reject corrupt slots rather than repair them, so it
    cannot discard opaque data or revive stale top-level references.
    """
    modules = payload.get("modules")
    if not isinstance(modules, dict) or MODULE_NAME not in modules:
        return payload
    slot = modules[MODULE_NAME]
    if not isinstance(slot, dict):
        raise ModuleStateError("invalid media module slot: expected an object")
    if slot.get("schema_version") != SCHEMA_VERSION:
        raise ModuleStateError(f"unsupported media module schema: {slot.get('schema_version')!r}")
    return slot


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
