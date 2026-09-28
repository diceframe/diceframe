"""Persisted health events and status; runtime reporting remains in engine.health."""

from __future__ import annotations

from typing import Any

from src.engine.module_state import ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "health"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "health_events": [], "health_status": {}}


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    if not isinstance(raw.get("health_events"), list):
        raw["health_events"] = []
    if not isinstance(raw.get("health_status"), dict):
        raw["health_status"] = {}
    return raw


def health_events(instance: Any) -> list[dict]:
    return get_module_state(instance, MODULE_NAME)["health_events"]


def replace_health_events(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["health_events"] = value if isinstance(value, list) else []


def health_status(instance: Any) -> dict:
    return get_module_state(instance, MODULE_NAME)["health_status"]


def replace_health_status(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["health_status"] = value if isinstance(value, dict) else {}


def persisted_state(instance: Any) -> dict[str, Any]:
    """Retain the former encode-only limit without mutating live event history."""
    slot = instance.modules[MODULE_NAME]
    if slot.get("schema_version") != SCHEMA_VERSION:
        return slot
    return {**slot, "health_events": health_events(instance)[-100:]}


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
