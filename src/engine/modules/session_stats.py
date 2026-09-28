"""Session usage counters and wall-clock activity timestamps."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.engine.module_state import (
    ModuleStateError, ModuleStateSpec, get_module_state, register_module_state,
)

MODULE_NAME = "session_stats"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "total_llm_calls": 0,
        "total_tokens": 0,
        "started_at": "",
        "last_activity": "",
    }


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    for key in ("total_llm_calls", "total_tokens"):
        value = raw.get(key)
        if type(value) is not int or value < 0:
            raw[key] = 0
    for key in ("started_at", "last_activity"):
        if not isinstance(raw.get(key), str):
            raw[key] = ""
    return raw


def require_writable(instance: Any) -> None:
    """Read-only transaction preflight; never repair or materialize a slot."""
    modules = getattr(instance, "modules", None)
    if not isinstance(modules, dict):
        raise ModuleStateError("instance has no module state container")
    slot = modules.get(MODULE_NAME)
    if not isinstance(slot, dict):
        return  # Missing/corrupt slots have the documented fresh default.
    if slot.get("schema_version") != SCHEMA_VERSION:
        raise ModuleStateError(f"unsupported session_stats module schema: {slot.get('schema_version')!r}")


def total_llm_calls(instance: Any) -> int:
    return get_module_state(instance, MODULE_NAME)["total_llm_calls"]


def replace_total_llm_calls(instance: Any, value: int) -> None:
    get_module_state(instance, MODULE_NAME)["total_llm_calls"] = value


def total_tokens(instance: Any) -> int:
    return get_module_state(instance, MODULE_NAME)["total_tokens"]


def replace_total_tokens(instance: Any, value: int) -> None:
    get_module_state(instance, MODULE_NAME)["total_tokens"] = value


def started_at(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["started_at"]


def replace_started_at(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["started_at"] = value


def last_activity(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["last_activity"]


def replace_last_activity(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["last_activity"] = value


def touch(instance: Any, at: str | None = None) -> None:
    instance.last_activity = datetime.now(timezone.utc).isoformat() if at is None else at


def mark_started(instance: Any) -> None:
    if not instance.started_at:
        instance.started_at = datetime.now(timezone.utc).isoformat()


def record_llm_usage(instance: Any, tokens: int = 0, *, calls: int = 1) -> None:
    instance.total_tokens += max(0, int(tokens or 0))
    instance.total_llm_calls += max(0, int(calls or 0))


def reset(instance: Any) -> None:
    instance.total_llm_calls = 0
    instance.total_tokens = 0
    instance.started_at = ""
    instance.last_activity = ""


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
