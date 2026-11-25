"""Per-run counters for the narrative workflow quality gates."""

from __future__ import annotations

from typing import Any

from src.engine import module_hooks
from src.engine.module_state import ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "narrative_quality_metrics"
SCHEMA_VERSION = 1

_COUNTERS = (
    "turns",
    "complex_turns",
    "gate_rejections",
    "repair_attempts",
    "repair_successes",
    "reviewer_unavailable",
)


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, **{key: 0 for key in _COUNTERS}}


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    for key in _COUNTERS:
        if type(raw.get(key)) is not int or raw[key] < 0:
            raw[key] = 0
    return raw


def require_writable(instance: Any) -> None:
    slot = getattr(instance, "modules", {}).get(MODULE_NAME)
    if isinstance(slot, dict) and slot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported narrative quality metrics schema: {slot.get('schema_version')!r}"
        )


def metrics(instance: Any) -> dict[str, int]:
    slot = get_module_state(instance, MODULE_NAME)
    return {key: int(slot.get(key, 0) or 0) for key in _COUNTERS}


def record(
    instance: Any,
    *,
    turn: bool = False,
    complex_turn: bool = False,
    rejection: bool = False,
    repair_attempt: bool = False,
    repair_success: bool = False,
    reviewer_unavailable: bool = False,
) -> None:
    slot = get_module_state(instance, MODULE_NAME)
    values = {
        "turns": turn,
        "complex_turns": complex_turn,
        "gate_rejections": rejection,
        "repair_attempts": repair_attempt,
        "repair_successes": repair_success,
        "reviewer_unavailable": reviewer_unavailable,
    }
    for key, enabled in values.items():
        if enabled:
            slot[key] += 1


def reset(instance: Any) -> None:
    slot = get_module_state(instance, MODULE_NAME)
    for key in _COUNTERS:
        slot[key] = 0


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
module_hooks.register(module_hooks.INSTANCE_RESET, MODULE_NAME, reset)

