"""Room access settings and credentials, retained across game resets."""

from __future__ import annotations

from typing import Any

from src.engine.module_state import ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "room_access"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "max_players": 6,
        "player_access_open": True,
        "bot_bind_token": "",
        "room_password": "",
        "room_token": "",
    }


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    # The old codec used data.get(key, default): only missing keys default.
    # Preserve present values, including None and unconventional types.
    for key, default in fresh().items():
        raw.setdefault(key, default)
    return raw


def max_players(instance: Any) -> int:
    return get_module_state(instance, MODULE_NAME)["max_players"]


def replace_max_players(instance: Any, value: int) -> None:
    get_module_state(instance, MODULE_NAME)["max_players"] = value


def player_access_open(instance: Any) -> bool:
    return get_module_state(instance, MODULE_NAME)["player_access_open"]


def replace_player_access_open(instance: Any, value: bool) -> None:
    get_module_state(instance, MODULE_NAME)["player_access_open"] = value


def bot_bind_token(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["bot_bind_token"]


def replace_bot_bind_token(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["bot_bind_token"] = value


def room_password(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["room_password"]


def replace_room_password(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["room_password"] = value


def room_token(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["room_token"]


def replace_room_token(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["room_token"] = value


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
