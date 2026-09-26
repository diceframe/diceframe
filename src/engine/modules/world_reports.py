"""Round-scoped world adjudication reports; reset retains the existing reports."""

from __future__ import annotations

from typing import Any

from src.engine.module_state import ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "world_reports"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "last_overreach": [],
        "last_world_legality": [],
        "last_world_events": [],
    }


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    for key in ("last_overreach", "last_world_legality", "last_world_events"):
        if not isinstance(raw.get(key), list):
            raw[key] = []
    return raw


def last_overreach(instance: Any) -> list:
    return get_module_state(instance, MODULE_NAME)["last_overreach"]


def replace_last_overreach(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["last_overreach"] = value if isinstance(value, list) else []


def last_world_legality(instance: Any) -> list:
    return get_module_state(instance, MODULE_NAME)["last_world_legality"]


def replace_last_world_legality(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["last_world_legality"] = value if isinstance(value, list) else []


def last_world_events(instance: Any) -> list:
    return get_module_state(instance, MODULE_NAME)["last_world_events"]


def replace_last_world_events(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["last_world_events"] = value if isinstance(value, list) else []


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
