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


def test_malformed_snapshot_envelope_does_not_replace_live_state() -> None:
    instance = GameInstance(game_key=("web", "wuxia", "bot"))
    instance.combat_extension = _payload_with_extension()

    restored = instance.restore_combat_extension_snapshot({
        "schema_version": True,
        "combat_extension": "bad",
        "entity_fields": {},
    })

    assert restored is False
    assert instance.combat_extension == _payload_with_extension()


def test_capture_repairs_malformed_entity_tracking_without_crashing() -> None:
    instance = GameInstance(game_key=("web", "wuxia", "bot"))
    instance.players["p1"] = {
        "character_name": "侠客",
        "character_sheet": {"hp": 20, "qi": 30},
    }
    instance.combat_extension_round_snapshots = {
        "0": {
            "schema_version": 1,
            "combat_extension": {},
            "entity_fields": {"player:p1": "malformed"},
        },
    }

    instance.capture_combat_extension_snapshot({
        "player:p1": ("hp", "qi", 7),
        3: ("hp",),
    })

    entry = instance.combat_extension_round_snapshots["0"]["entity_fields"][
        "player:p1"
    ]
    assert entry == {
        "values": {"hp": 20, "qi": 30},
        "missing": [],
    }


def test_restore_malformed_entity_tracking_is_atomic() -> None:
    instance = GameInstance(game_key=("web", "wuxia", "bot"))
    instance.players["p1"] = {
        "character_name": "侠客",
        "character_sheet": {"hp": 20, "qi": 30},
    }
    instance.combat_extension = _payload_with_extension()

    restored = instance.restore_combat_extension_snapshot({
        "schema_version": 1,
        "combat_extension": {"schema_version": 1, "pools": {}},
        "entity_fields": {
            "player:p1": {
                "values": {"hp": 1},
                "missing": [],
            },
            "npc:broken": "malformed",
        },
    })

    assert restored is False
    assert instance.combat_extension == _payload_with_extension()
    assert instance.get_character_sheet("p1")["hp"] == 20


def test_discard_combat_snapshots_removes_branch_and_malformed_keys() -> None:
    instance = GameInstance(game_key=("web", "wuxia", "bot"))
    instance.combat_extension_round_snapshots = {
        "2": {"combat_extension": {}},
        "3": {"combat_extension": {}},
        "future": {"combat_extension": {}},
    }

    instance.discard_combat_extension_snapshots_from(3)

    assert set(instance.combat_extension_round_snapshots) == {"2"}


def test_combat_round_snapshot_round_trips_through_save() -> None:
    instance = GameInstance(game_key=("web", "wuxia", "bot"), gm_uid="gm")
    instance.combat_extension = _payload_with_extension()
    instance.players["p1"] = {
        "character_name": "侠客",
        "character_sheet": {"hp": 20, "qi": 30, "inventory": []},
    }
    instance.capture_combat_extension_snapshot({
        "player:p1": ("hp", "qi", "inventory"),
    })

    recovered = GameInstance.from_dict(instance.to_dict())

    assert recovered.combat_extension_round_snapshots == (
        instance.combat_extension_round_snapshots
    )


@pytest.mark.asyncio
async def test_reset_clears_combat_extension_and_round_snapshots() -> None:
    instance = GameInstance(game_key=("web", "wuxia", "bot"), gm_uid="gm")
    instance.combat_extension = _payload_with_extension()
    instance.capture_combat_extension_snapshot()

    await instance.reset()

    assert instance.combat_extension == {}
    assert instance.combat_extension_round_snapshots == {}


@pytest.mark.asyncio
async def test_finished_round_records_combat_snapshots_for_rollback_and_swipe() -> None:
    instance = GameInstance(game_key=("web", "wuxia", "bot"), gm_uid="gm")
    instance.round_number = 2
    instance.players["p1"] = {
        "character_name": "侠客",
        "character_sheet": {"hp": 20, "qi": 30, "inventory": []},
    }
    instance.capture_combat_extension_snapshot({
        "player:p1": ("hp", "qi", "inventory"),
    })
    instance.combat_extension = _payload_with_extension()
    instance.get_character_sheet("p1")["qi"] = 22
    pre_swipe = instance.current_combat_extension_snapshot()

    await instance.finish_judgment(
        "本轮结束",
        pre_combat_extension_snapshot=pre_swipe,
    )

    entry = instance.log[-1]
    assert entry["combat_extension_round_start"]["combat_extension"] == {}
    assert entry["combat_extension_round_start"]["entity_fields"]["player:p1"][
        "values"
    ]["qi"] == 30
    assert entry["pre_combat_extension_snapshot"] == pre_swipe

    instance.combat_extension = {}
    instance.get_character_sheet("p1")["qi"] = 1
    assert instance.restore_combat_extension_snapshot(
        entry["pre_combat_extension_snapshot"],
    )
    assert instance.combat_extension == _payload_with_extension()
    assert instance.get_character_sheet("p1")["qi"] == 22
