"""Track R1: room policy persistence without changing seat authority."""

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.module_state import ModuleStateError
from src.engine.modules import player_control_state
from src.engine.player_control import (
    PlayerControlError,
    away_control_policy,
    set_away_control_policy,
    set_control,
)
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v17_to_v18,
    migrate_game_state_payload,
)
from src.engine.player_control import normalize_away_control_policy


@pytest.mark.parametrize(("legacy", "expected"), [
    ("pause", "pause"),
    ("ai_takeover", "ai_takeover"),
    (" ai_takeover ", "ai_takeover"),
    (None, "pause"),
    ("invalid", "pause"),
    ({"mode": "ai"}, "pause"),
])
def test_migration_moves_and_normalizes_room_policy(legacy, expected):
    payload = {
        "instance_schema_version": 17,
        "away_control_policy": legacy,
        "players": {"p": {"control": {"mode": "human", "revision": 5}}},
        "modules": {"lorebook_runtime": {"schema_version": 1, "timers": {}}},
    }
    original = deepcopy(payload)
    migrated = migrate_game_state_payload(payload)
    assert payload == original
    assert "away_control_policy" not in migrated
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert migrated["modules"]["player_control"] == {"schema_version": 1, "away_control_policy": expected}
    assert migrated["players"] == original["players"]
    assert migrated["modules"]["lorebook_runtime"] == original["modules"]["lorebook_runtime"]
    assert migrate_game_state_payload(migrated) == migrated
    step = _migrate_v17_to_v18(deepcopy(payload))
    assert _migrate_v17_to_v18(deepcopy(step)) == step


@pytest.mark.parametrize("modules", [None, [], {}, {"player_control": "broken"}])
def test_missing_policy_and_corrupt_slot_default_to_pause(modules):
    payload = {"instance_schema_version": 17, "modules": modules}
    migrated = migrate_game_state_payload(payload)
    assert migrated["modules"]["player_control"] == {"schema_version": 1, "away_control_policy": "pause"}


@pytest.mark.parametrize("slot", [
    {"schema_version": 1, "away_control_policy": "pause", "extra": {"preserved": True}},
    {"schema_version": 99, "opaque": [1, 2]},
])
def test_existing_slot_wins_and_preserves_unknown_data(slot):
    payload = {"instance_schema_version": 17, "away_control_policy": "ai_takeover", "modules": {"player_control": slot}}
    migrated = migrate_game_state_payload(payload)
    assert "away_control_policy" not in migrated
    assert migrated["modules"]["player_control"] == slot


def test_new_instances_have_isolated_room_policy_slots():
    first = GameInstance(game_key=("web", "first", "u"))
    second = GameInstance(game_key=("web", "second", "u"))
    assert first.modules["player_control"] == {"schema_version": 1, "away_control_policy": "pause"}
    set_away_control_policy(first, "ai_takeover")
    assert first.modules["player_control"]["away_control_policy"] == "ai_takeover"
    assert away_control_policy(first) == "ai_takeover"
    assert away_control_policy(second) == "pause"
    player_control_state.set_away_control_policy_value(first, normalize_away_control_policy("invalid"))
    assert away_control_policy(first) == "pause"


def test_public_write_validation_preserves_previous_value_and_seats():
    instance = GameInstance(game_key=("web", "policy", "u"), players={"p": {"name": "P"}})
    set_control(instance, "p", "human")
    players = deepcopy(instance.players)
    assert set_away_control_policy(instance, " ai_takeover ") == "ai_takeover"
    with pytest.raises(PlayerControlError, match="unknown away_control_policy"):
        set_away_control_policy(instance, "invalid")
    assert instance.modules["player_control"]["away_control_policy"] == "ai_takeover"
    assert instance.players == players


def test_policy_round_trip_uses_only_the_module_slot():
    instance = GameInstance(game_key=("web", "roundtrip", "u"), players={"p": {"name": "P"}})
    set_control(instance, "p", "ai")
    set_away_control_policy(instance, "ai_takeover")
    payload = instance.to_dict()
    assert "away_control_policy" not in payload
    assert payload["modules"]["player_control"]["away_control_policy"] == "ai_takeover"
    restored = GameInstance.from_dict(payload)
    assert player_control_state.away_control_policy(restored) == "ai_takeover"
    assert restored.players == instance.players
    set_away_control_policy(restored, "pause")
    assert player_control_state.away_control_policy(instance) == "ai_takeover"


@pytest.mark.parametrize("version", [14, 17])
def test_old_save_load_defaults_to_pause_without_taking_over_seats(version):
    instance = GameInstance.from_dict({
        "instance_schema_version": version,
        "game_key": ["web", "legacy", "u"], "state": "created",
        "players": {"p": {"name": "P"}},
    })
    assert player_control_state.away_control_policy(instance) == "pause"
    assert instance.players["p"]["control"]["mode"] == "human"


@pytest.mark.parametrize("raw", [None, "bad", {"schema_version": 1}, {"schema_version": 1, "away_control_policy": "bad"}])
def test_slot_repair_is_conservative_and_idempotent(raw):
    repaired = player_control_state.ensure(raw)
    assert repaired == {"schema_version": 1, "away_control_policy": "pause"}
    assert player_control_state.ensure(deepcopy(repaired)) == repaired


def test_future_module_schema_round_trips_but_rejects_access():
    slot = {"schema_version": 99, "away_control_policy": "future", "opaque": {"secret": [1]}}
    instance = GameInstance(game_key=("web", "future", "u"), modules={"player_control": deepcopy(slot)})
    restored = GameInstance.from_dict(instance.to_dict())
    assert restored.modules["player_control"] == slot
    with pytest.raises(ModuleStateError, match="unsupported player_control module schema"):
        away_control_policy(restored)
    with pytest.raises(ModuleStateError):
        set_away_control_policy(restored, "pause")
    with pytest.raises(ModuleStateError):
        set_away_control_policy(restored, "pause")
    assert restored.modules["player_control"] == slot


def test_future_instance_schema_is_rejected():
    with pytest.raises(ValueError, match="unsupported game instance schema version"):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})


@pytest.mark.asyncio
async def test_reset_preserves_room_policy():
    instance = GameInstance(game_key=("web", "reset", "u"))
    set_away_control_policy(instance, "ai_takeover")
    await instance.reset()
    assert instance.modules["player_control"]["away_control_policy"] == "ai_takeover"


def test_staged_replacement_copies_policy_without_aliasing():
    instance = GameInstance(game_key=("web", "staged", "u"))
    staged = GameInstance.from_dict(instance.to_dict())
    set_away_control_policy(staged, "ai_takeover")
    instance.replace_persisted_state_from(staged)
    assert player_control_state.away_control_policy(instance) == "ai_takeover"
    set_away_control_policy(staged, "pause")
    assert player_control_state.away_control_policy(instance) == "ai_takeover"
