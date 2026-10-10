"""Storage for a compatibility projection, NOT combat authority.

D&D 2024 authority remains in ruleset_state["combat"], projected here by its
combat engine. Generic combat, round snapshots, automation and context
projectors consume these compatibility fields.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.engine.module_state import (
    ModuleStateError, ModuleStateSpec, get_module_state, register_module_state,
)

MODULE_NAME = "legacy_combat"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "combat_active": False,
        "combat_enemies": [],
        "combat_state": "none",
        "initiative_order": [],
        "initiative_current": 0,
    }


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    if not isinstance(raw.get("combat_active"), bool):
        raw["combat_active"] = bool(raw.get("combat_active", False))
    for key in ("combat_enemies", "initiative_order"):
        if not isinstance(raw.get(key), list):
            raw[key] = []
    if not isinstance(raw.get("combat_state"), str):
        raw["combat_state"] = "none"
    if type(raw.get("initiative_current")) is not int:
        raw["initiative_current"] = 0
    return raw


def require_writable(instance: Any) -> None:
    """Read-only transaction preflight; never repair or materialize a slot."""
    modules = getattr(instance, "modules", None)
    if not isinstance(modules, dict):
        raise ModuleStateError("instance has no module state container")
    slot = modules.get(MODULE_NAME)
    if not isinstance(slot, dict):
        return
    if slot.get("schema_version") != SCHEMA_VERSION:
        raise ModuleStateError(f"unsupported legacy_combat module schema: {slot.get('schema_version')!r}")


def combat_active(instance: Any) -> bool:
    return get_module_state(instance, MODULE_NAME)["combat_active"]


def replace_combat_active(instance: Any, value: bool) -> None:
    get_module_state(instance, MODULE_NAME)["combat_active"] = value


def combat_enemies(instance: Any) -> list:
    return get_module_state(instance, MODULE_NAME)["combat_enemies"]


def replace_combat_enemies(instance: Any, value: list) -> None:
    get_module_state(instance, MODULE_NAME)["combat_enemies"] = value


def combat_state(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["combat_state"]


def replace_combat_state(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["combat_state"] = value


def initiative_order(instance: Any) -> list[str]:
    return get_module_state(instance, MODULE_NAME)["initiative_order"]


def replace_initiative_order(instance: Any, value: list[str]) -> None:
    get_module_state(instance, MODULE_NAME)["initiative_order"] = value


def initiative_current(instance: Any) -> int:
    return get_module_state(instance, MODULE_NAME)["initiative_current"]


def replace_initiative_current(instance: Any, value: int) -> None:
    get_module_state(instance, MODULE_NAME)["initiative_current"] = value


def begin(instance: Any, order: list[str]) -> None:
    require_writable(instance)
    state = get_module_state(instance, MODULE_NAME)
    state["initiative_order"] = list(order)
    state["initiative_current"] = 0
    state["combat_state"] = "active"
    state["combat_active"] = True


def end(instance: Any) -> None:
    require_writable(instance)
    state = get_module_state(instance, MODULE_NAME)
    state["combat_state"] = "none"
    state["combat_active"] = False
    state["initiative_order"].clear()
    state["initiative_current"] = 0


def project_from_ruleset(instance: Any, combat: dict[str, Any]) -> None:
    require_writable(instance)
    state = get_module_state(instance, MODULE_NAME)
    state["combat_state"] = "active" if combat.get("status") == "active" else "none"
    state["combat_active"] = state["combat_state"] == "active"
    state["initiative_order"] = list(combat.get("initiative") or [])
    state["initiative_current"] = int(combat.get("turn_index", 0) or 0)


def restore_from_entity_snapshot(instance: Any, snapshot: dict[str, Any]) -> None:
    require_writable(instance)
    state = get_module_state(instance, MODULE_NAME)
    state["combat_enemies"] = deepcopy(snapshot.get("combat_enemies") or [])
    state["combat_state"] = str(snapshot.get("combat_state") or "none")
    state["combat_active"] = bool(snapshot.get("combat_active"))
    state["initiative_order"] = deepcopy(list(snapshot.get("initiative_order") or []))
    state["initiative_current"] = int(snapshot.get("initiative_current") or 0)


def restore_from_transaction(instance: Any, snapshot: dict[str, Any]) -> None:
    require_writable(instance)
    state = get_module_state(instance, MODULE_NAME)
    state["combat_state"] = str(snapshot["combat_state"])
    state["combat_active"] = bool(snapshot["combat_active"])
    state["initiative_order"] = deepcopy(snapshot["initiative_order"])
    state["initiative_current"] = int(snapshot["initiative_current"])


def reset(instance: Any) -> None:
    require_writable(instance)
    state = get_module_state(instance, MODULE_NAME)
    state["combat_active"] = False
    state["combat_enemies"].clear()
    state["combat_state"] = "none"
    state["initiative_order"].clear()
    state["initiative_current"] = 0


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
