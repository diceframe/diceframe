"""通用战斗扩展状态的持久化契约测试（Issue 212 phase 2 Slice 2.2）。"""

from __future__ import annotations

import pytest

from src.engine.game_instance import GameInstance


def _payload_with_extension() -> dict:
    return {
        "schema_version": 1,
        "scheduler": {"kind": "threshold", "order": ["player:x"], "round": 3},
        "pools": {
            "player:x": {
                "qi": {"current": 60, "maximum": 100, "minimum": 0},
                "barrier": {"current": 0, "maximum": 50, "minimum": 0},
            },
        },
    }


def test_combat_extension_round_trips_through_save() -> None:
    instance = GameInstance(game_key=("web", "wuxia", "bot"), gm_uid="gm")
    instance.combat_extension = _payload_with_extension()
    recovered = GameInstance.from_dict(instance.to_dict())
    assert recovered.combat_extension == _payload_with_extension()


def test_legacy_save_without_field_defaults_to_empty() -> None:
    payload = GameInstance(game_key=("web", "wuxia", "bot")).to_dict()
    payload.pop("combat_extension", None)
    recovered = GameInstance.from_dict(payload)
    assert recovered.combat_extension == {}


def test_invalid_payload_shapes_fail_closed_to_empty() -> None:
    payload = GameInstance(game_key=("web", "wuxia", "bot")).to_dict()
    payload["combat_extension"] = "threshold"
    recovered = GameInstance.from_dict(payload)
    assert recovered.combat_extension == {}


def test_default_instances_start_disabled() -> None:
    instance = GameInstance(game_key=("web", "wuxia", "bot"))
    assert instance.combat_extension == {}
