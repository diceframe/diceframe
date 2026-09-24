"""Round presentation state, owned by GameInstance's existing mutation methods."""

from __future__ import annotations

from typing import Any

from src.engine.contracts import TokenBudgetBump
from src.engine.module_state import ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "round_presentation"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "gm_directives": [],
        "quick_actions": [],
        "last_state_update": None,
        "last_token_budget_bump": None,
        "pending_combat_results": [],
    }


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    for key in ("gm_directives", "quick_actions", "pending_combat_results"):
        if not isinstance(raw.get(key), list):
            raw[key] = []
    for key in ("last_state_update", "last_token_budget_bump"):
        if not isinstance(raw.get(key), dict):
            raw[key] = None
    return raw


def gm_directives(instance: Any) -> list[dict]:
    return get_module_state(instance, MODULE_NAME)["gm_directives"]


def replace_gm_directives(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["gm_directives"] = value if isinstance(value, list) else []


def quick_actions(instance: Any) -> list[str]:
    return get_module_state(instance, MODULE_NAME)["quick_actions"]


def replace_quick_actions(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["quick_actions"] = value if isinstance(value, list) else []


def last_state_update(instance: Any) -> dict | None:
    return get_module_state(instance, MODULE_NAME)["last_state_update"]


def replace_last_state_update(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["last_state_update"] = value if isinstance(value, dict) else None


def last_token_budget_bump(instance: Any) -> TokenBudgetBump | None:
    return get_module_state(instance, MODULE_NAME)["last_token_budget_bump"]


def replace_last_token_budget_bump(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["last_token_budget_bump"] = value if isinstance(value, dict) else None


def pending_combat_results(instance: Any) -> list[dict]:
    return get_module_state(instance, MODULE_NAME)["pending_combat_results"]


def replace_pending_combat_results(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["pending_combat_results"] = value if isinstance(value, list) else []


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
