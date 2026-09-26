"""Table preferences, retained across resets without changing gameplay policy."""

from __future__ import annotations

from typing import Any

from src.engine.module_state import ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "table_settings"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "difficulty": "标准",
        "narrative_perspective": "auto",
        "gm_style_override": None,
        "solo_mode": False,
        "seed_code": "",
        "entry_point": "web",
        "luck_timeout_seconds": 60,
        "economy_reward_policy": {},
    }


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    defaults = fresh()
    for key in ("difficulty", "narrative_perspective", "seed_code", "entry_point"):
        if not isinstance(raw.get(key), str):
            raw[key] = defaults[key]
    if not isinstance(raw.get("solo_mode"), bool):
        raw["solo_mode"] = False
    # Preserve the codec's distinction between following world style and an
    # explicit (possibly empty) override. Dict contents are opaque here.
    if not isinstance(raw.get("gm_style_override"), dict):
        raw["gm_style_override"] = None
    if not isinstance(raw.get("economy_reward_policy"), dict):
        raw["economy_reward_policy"] = {}
    try:
        raw["luck_timeout_seconds"] = int(raw.get("luck_timeout_seconds", 60) or 0)
    except (TypeError, ValueError, OverflowError):
        raw["luck_timeout_seconds"] = 60
    return raw


def difficulty(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["difficulty"]


def replace_difficulty(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["difficulty"] = value


def narrative_perspective(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["narrative_perspective"]


def replace_narrative_perspective(instance: Any, value: str) -> None:
    get_module_state(instance, MODULE_NAME)["narrative_perspective"] = value


def gm_style_override(instance: Any) -> dict[str, str] | None:
    return get_module_state(instance, MODULE_NAME)["gm_style_override"]


def replace_gm_style_override(instance: Any, value: dict[str, str] | None) -> None:
    get_module_state(instance, MODULE_NAME)["gm_style_override"] = value


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


def luck_timeout_seconds(instance: Any) -> int:
    return get_module_state(instance, MODULE_NAME)["luck_timeout_seconds"]


def replace_luck_timeout_seconds(instance: Any, value: int) -> None:
    get_module_state(instance, MODULE_NAME)["luck_timeout_seconds"] = value


def economy_reward_policy(instance: Any) -> dict:
    return get_module_state(instance, MODULE_NAME)["economy_reward_policy"]


def replace_economy_reward_policy(instance: Any, value: dict) -> None:
    get_module_state(instance, MODULE_NAME)["economy_reward_policy"] = value


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
