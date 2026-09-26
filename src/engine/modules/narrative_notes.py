"""Narrative summary, remembered facts, confirmed items and legacy game time."""

from __future__ import annotations

from typing import Any

from src.engine.module_state import ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "narrative_notes"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "summary": {}, "key_facts": [], "confirmed_items": [], "game_time": ""}


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    if not isinstance(raw.get("summary"), dict):
        raw["summary"] = {}
    for key in ("key_facts", "confirmed_items"):
        if not isinstance(raw.get(key), list):
            raw[key] = []
    if not isinstance(raw.get("game_time"), str):
        raw["game_time"] = ""
    return raw


def summary(instance: Any) -> dict:
    return get_module_state(instance, MODULE_NAME)["summary"]


def replace_summary(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["summary"] = value if isinstance(value, dict) else {}


def key_facts(instance: Any) -> list:
    return get_module_state(instance, MODULE_NAME)["key_facts"]


def replace_key_facts(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["key_facts"] = value if isinstance(value, list) else []


def confirmed_items(instance: Any) -> list:
    return get_module_state(instance, MODULE_NAME)["confirmed_items"]


def replace_confirmed_items(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["confirmed_items"] = value if isinstance(value, list) else []


def game_time(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["game_time"]


def replace_game_time(instance: Any, value: Any) -> None:
    get_module_state(instance, MODULE_NAME)["game_time"] = value if isinstance(value, str) else ""


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
