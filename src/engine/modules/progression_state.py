"""Progression slot: narrative round state, preserving uninterpretable data."""

from __future__ import annotations

from typing import Any

from src.engine.module_state import (
    ModuleStateError, ModuleStateSpec, get_module_state, register_module_state,
)

MODULE_NAME = "progression"
SCHEMA_VERSION = 1
NARRATIVE_ROUND = "narrative_round"
# GAL 轨道 P7（ADR-0010 §4）：scene 推进——无回合屏障、按场景/事件推进。
# round 字段在 scene 模式下继续作为推进计数（经济纪元沿用同一单点）；
# 旧运行时不认识 scene 时按未知 mode fail closed 拒绝写入。
SCENE = "scene"
KNOWN_MODES = (NARRATIVE_ROUND, SCENE)


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "mode": NARRATIVE_ROUND, "round": 0}


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    if raw.get("mode") is None:
        raw["mode"] = NARRATIVE_ROUND
    if raw["mode"] not in KNOWN_MODES:
        return raw
    value = raw.get("round", 0)
    raw["round"] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
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
        raise ModuleStateError(f"unsupported progression module schema: {slot.get('schema_version')!r}")
    current = slot.get("mode")
    if current is not None and current not in KNOWN_MODES:
        raise ModuleStateError(f"unsupported progression mode: {current!r}")


def mode(instance: Any) -> str:
    return str(get_module_state(instance, MODULE_NAME).get("mode", NARRATIVE_ROUND))


def round_value(instance: Any) -> int:
    slot = get_module_state(instance, MODULE_NAME)
    return _round_value(slot)


def peek_round_value(instance: Any) -> int:
    """Read a fence without materializing absent state during a preview."""
    modules = getattr(instance, "modules", None)
    if not isinstance(modules, dict):
        raise ModuleStateError("instance has no module state container")
    slot = modules.get(MODULE_NAME)
    if not isinstance(slot, dict):
        return 0
    if slot.get("schema_version") != SCHEMA_VERSION:
        raise ModuleStateError(f"unsupported progression module schema: {slot.get('schema_version')!r}")
    return _round_value(slot)


def _round_value(slot: dict[str, Any]) -> int:
    value = slot.get("round", 0)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if slot.get("mode") not in (None, *KNOWN_MODES):
        raise ModuleStateError("unsupported progression round value")
    return 0


def set_round_value(instance: Any, value: int) -> None:
    require_writable(instance)
    converted = int(value)
    get_module_state(instance, MODULE_NAME)["round"] = converted


def set_mode(instance: Any, value: str) -> None:
    """Install the progression mode (creation-time authority, GAL P7).

    Only known modes are accepted; an unknown value fails closed rather than
    being guessed into the slot.
    """

    require_writable(instance)
    mode = str(value or "")
    if mode not in KNOWN_MODES:
        raise ModuleStateError(f"unsupported progression mode: {mode!r}")
    get_module_state(instance, MODULE_NAME)["mode"] = mode


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
