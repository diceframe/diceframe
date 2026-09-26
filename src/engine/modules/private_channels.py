"""Private messages and public table talk as a persisted module slot."""

from __future__ import annotations

from typing import Any

from src.engine.contracts import TableTalkExchange
from src.engine.module_state import ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "private_channels"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "private_log": {}, "table_talk": []}


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    if not isinstance(raw.get("private_log"), dict):
        raw["private_log"] = {}
    if not isinstance(raw.get("table_talk"), list):
        raw["table_talk"] = []
    # Preserve the old decode filter and limit without breaking live aliases.
    raw["table_talk"][:] = [
        item for item in raw["table_talk"]
        if isinstance(item, dict) and item.get("visibility") == "party"
    ][-50:]
    return raw


def private_log(instance: Any) -> dict[str, list[dict[str, Any]]]:
    return get_module_state(instance, MODULE_NAME)["private_log"]


def replace_private_log(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["private_log"] = value if isinstance(value, dict) else {}


def table_talk(instance: Any) -> list[TableTalkExchange]:
    return get_module_state(instance, MODULE_NAME)["table_talk"]


def replace_table_talk(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["table_talk"] = value if isinstance(value, list) else []


def remove_table_talk_exchange(instance: Any, exchange_id: str) -> None:
    """Undo a failed append/save while retaining the existing list object."""
    entries = table_talk(instance)
    entries[:] = [item for item in entries if item.get("id") != exchange_id]


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
