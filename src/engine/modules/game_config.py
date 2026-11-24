"""Kernel game configuration; persisted owner for run-scoped table settings."""
from __future__ import annotations

import copy
from typing import Any
from src.engine.module_state import ModuleStateError, ModuleStateSpec, get_module_state, register_module_state
from src.engine.currency.validation import CurrencySystemError, validate_declared_currency_system
from src.localization.messages import t

MODULE_NAME = "game_config"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "difficulty": "标准",
        "solo_mode": False,
        "seed_code": "",
        "entry_point": "web",
        "currency_label": "",
        "currency_system": None,
    }


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    defaults = fresh()
    if not isinstance(raw.get('difficulty'), str):
        raw['difficulty'] = defaults['difficulty']
    if not isinstance(raw.get('solo_mode'), bool):
        raw['solo_mode'] = defaults['solo_mode']
    if not isinstance(raw.get('seed_code'), str):
        raw['seed_code'] = defaults['seed_code']
    if not isinstance(raw.get('entry_point'), str):
        raw['entry_point'] = defaults['entry_point']
    if not isinstance(raw.get("currency_label"), str):
        raw["currency_label"] = defaults["currency_label"]
    # ``currency_system`` is left exactly as stored, including a value this
    # runtime cannot interpret.  Dropping it here would silently reinterpret
    # every balance of that game under the rule's default currency; the read
    # path below rejects it instead.
    return raw


def require_writable(instance: Any) -> None:
    modules = getattr(instance, "modules", None)
    if not isinstance(modules, dict):
        raise ModuleStateError("instance has no module state container")
    slot = modules.get(MODULE_NAME)
    if isinstance(slot, dict) and slot.get("schema_version") != SCHEMA_VERSION:
        raise ModuleStateError(f"unsupported {MODULE_NAME} module schema: {slot.get('schema_version')!r}")


def difficulty(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["difficulty"]


def replace_difficulty(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["difficulty"] = value


def solo_mode(instance: Any) -> bool:
    return get_module_state(instance, MODULE_NAME)["solo_mode"]


def replace_solo_mode(instance: Any, value: bool) -> None:
    get_module_state(instance, MODULE_NAME)["solo_mode"] = value


def seed_code(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["seed_code"]


def replace_seed_code(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["seed_code"] = value


def entry_point(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["entry_point"]


def replace_entry_point(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["entry_point"] = value


def currency_label(instance: Any) -> str:
    return str(get_module_state(instance, MODULE_NAME).get("currency_label") or "")


def currency_system(instance: Any) -> dict[str, Any] | None:
    """The game's currency snapshot, or ``None`` for a legacy save.

    A stored snapshot this runtime cannot interpret is an error rather than a
    fallback: treating it as "no snapshot" would read the balances of that game
    under another currency system.
    """

    value = get_module_state(instance, MODULE_NAME).get("currency_system")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CurrencySystemError(t("engine.currency.validation.not_object"))
    validate_declared_currency_system(value)
    return copy.deepcopy(value)


def replace_currency_context(
    instance: Any,
    label: str,
    system: dict[str, Any],
) -> None:
    if not isinstance(system, dict):
        raise ValueError("currency_system must be an object")
    validate_declared_currency_system(system)
    slot = get_module_state(instance, MODULE_NAME)
    slot["currency_label"] = str(label or "").strip()
    slot["currency_system"] = copy.deepcopy(system)


def clear_currency_context(instance: Any) -> None:
    slot = get_module_state(instance, MODULE_NAME)
    slot["currency_label"] = ""
    slot["currency_system"] = None


def copy_for_carried_game(candidate: Any, source: Any) -> None:
    """Copy table policy while giving the separate Web game a fresh seed."""
    require_writable(source)
    require_writable(candidate)
    replace_difficulty(candidate, difficulty(source))
    replace_solo_mode(candidate, solo_mode(source))
    source_label = currency_label(source)
    source_system = currency_system(source)
    if source_system is not None:
        replace_currency_context(candidate, source_label, source_system)
    else:
        clear_currency_context(candidate)


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
