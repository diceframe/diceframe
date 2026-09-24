"""Round movement preserves existing coercion, timeline and rollback contracts."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.engine import progression
from src.engine.economy import era_key
from src.engine.game_instance import GameInstance
from src.engine.game_state import GameState
from src.rulesets.automation import append_public_timeline_entry


@pytest.mark.parametrize("value, expected", [(0, 1), (7, 8), (2.5, 3.5)])
def test_open_round_preserves_numeric_addition(value, expected):
    instance = SimpleNamespace(round_number=value)
    assert progression.open_next_round(instance) == expected
    assert instance.round_number == expected


@pytest.mark.parametrize("value", [None, "7"])
def test_open_round_does_not_silently_normalize_invalid_counter(value):
    instance = SimpleNamespace(round_number=value)
    with pytest.raises(TypeError):
        progression.open_next_round(instance)
    assert instance.round_number is value


def test_open_round_requires_existing_counter():
    instance = SimpleNamespace()
    with pytest.raises(AttributeError):
        progression.open_next_round(instance)
    assert not hasattr(instance, "round_number")


@pytest.mark.parametrize("value, expected", [(0, 1), (-2, 1), (7, 7), (2.5, 2.5)])
def test_rollback_floor_preserves_value_without_integer_coercion(value, expected):
    instance = SimpleNamespace(round_number=9)
    assert progression.rewind_after_rollback(instance, value) == expected
    assert instance.round_number == expected


def test_rollback_rejects_string_without_mutating_counter():
    instance = SimpleNamespace(round_number=9)
    with pytest.raises(TypeError):
        progression.rewind_after_rollback(instance, "3")
    assert instance.round_number == 9


def test_replay_preserves_supplied_value_identity():
    class HistoricalRound(int):
        def __int__(self):
            raise AssertionError("replay must not coerce")

    target = HistoricalRound(3)
    instance = SimpleNamespace(round_number=9)
    assert progression.rewind_for_replay(instance, target) is target
    assert instance.round_number is target


@pytest.mark.parametrize("value, expected", [("4", 4), (3.5, 3), (True, 1)])
def test_transaction_restore_keeps_explicit_integer_conversion(value, expected):
    instance = SimpleNamespace(round_number=9)
    assert progression.restore_from_snapshot(instance, value) == expected
    assert instance.round_number == expected
    assert type(instance.round_number) is int


def test_invalid_snapshot_counter_does_not_replace_live_counter():
    instance = SimpleNamespace(round_number=9)
    with pytest.raises(ValueError):
        progression.restore_from_snapshot(instance, "invalid")
    assert instance.round_number == 9


def test_reset_discards_counter_without_reading_or_converting_it():
    instance = SimpleNamespace(round_number=object())
    assert progression.reset(instance) == 0
    assert instance.round_number == 0


@pytest.mark.parametrize("value, expected", [(None, 0), ("", 0), ("7", 7), (2.5, 2), (-1, -1)])
def test_settlement_era_retains_read_normalization(value, expected):
    instance = SimpleNamespace(round_number=value)
    assert era_key(instance) == progression.current_era(instance) == progression.current_round(instance) == expected
    assert instance.round_number is value


def test_missing_counter_reads_as_initial_era():
    assert era_key(SimpleNamespace()) == progression.current_round(SimpleNamespace()) == 0


@pytest.mark.parametrize("live, logged, expected", [
    (2, [1, 9, 4], 10),
    (9, [1, 4], 10),
    (0, [], 1),
    (-5, [-8, -6], -4),
    (-5, [], 1),
    ("2", [None, "9", 4.5], 10),
])
def test_timeline_advances_past_live_and_logged_rounds(live, logged, expected):
    instance = SimpleNamespace(round_number=live)
    log = ({"round": value} for value in logged)
    assert progression.advance_for_public_timeline(instance, log) == expected
    assert instance.round_number == expected


def test_timeline_reads_live_counter_before_consuming_log():
    instance = SimpleNamespace(round_number="4")

    def log():
        # A lazy source may have side effects. The original expression has
        # already captured the live counter before it asks for the first item.
        instance.round_number = 100
        yield {"round": 2}

    assert progression.advance_for_public_timeline(instance, log()) == 5
    assert instance.round_number == 5


def test_invalid_live_counter_fails_before_consuming_log():
    instance = SimpleNamespace(round_number="invalid")
    consumed = []

    def log():
        consumed.append(True)
        yield {"round": 7}

    with pytest.raises(ValueError):
        progression.advance_for_public_timeline(instance, log())
    assert consumed == []
    assert instance.round_number == "invalid"


def test_invalid_log_round_leaves_counter_unchanged():
    instance = SimpleNamespace(round_number=4)
    with pytest.raises(ValueError):
        progression.advance_for_public_timeline(instance, [{"round": "invalid"}])
    assert instance.round_number == 4


class TimelineRuntime:
    def public_timeline_projection(self, batch, language):
        return {"action_text": "Open the gate", "gm_response": "The gate opens."}


def append_timeline(instance):
    append_public_timeline_entry(TimelineRuntime(), instance, {
        "intent_type": "interact",
        "intent_id": "gate-1",
        "events": [{"type": "intent.submitted", "submitted_by": "player"}, {"type": "gate.opened"}],
    })


def test_public_timeline_transaction_rollback_restores_round_and_history():
    instance = GameInstance(
        game_key=("test", "progression-transaction", "bot"),
        state=GameState.ACTIVE_JUDGMENT,
        log=[{"round": 8, "gm_response": "Earlier history"}],
    )
    instance.round_number = 3
    before = deepcopy({
        "ruleset_state": instance.ruleset_state,
        "event_ledger": instance.event_ledger,
        "players": instance.players,
        "combat_state": instance.combat_state,
        "combat_active": instance.combat_active,
        "initiative_order": instance.initiative_order,
        "initiative_current": instance.initiative_current,
        "round_number": instance.round_number,
        "log": instance.log,
        "last_activity": instance.last_activity,
    })
    append_timeline(instance)
    append_timeline(instance)
    assert [entry["round"] for entry in instance.log] == [8, 9, 10]
    assert era_key(instance) == 10
    # Timeline projection intentionally does not transition narrative phase.
    assert instance.state is GameState.ACTIVE_JUDGMENT
    assert instance.log[-1]["actions"][0]["user_id"] == "player"
    instance.restore_ruleset_transaction(before)
    assert instance.round_number == 3
    assert instance.log == before["log"]
    append_timeline(instance)
    assert [entry["round"] for entry in instance.log] == [8, 9]
    assert era_key(instance) == 9


@pytest.mark.asyncio
async def test_historical_rollback_and_reset_keep_era_in_step_with_narrative():
    instance = GameInstance(game_key=("test", "progression-history", "bot"))
    await instance.start_round()
    append_timeline(instance)
    assert instance.round_number == 2
    assert await instance.rollback_last_round() == 2
    assert instance.log == []
    assert instance.state is GameState.ACTIVE_ACTION
    assert era_key(instance) == 2
    await instance.start_round()
    assert era_key(instance) == 3
    await instance.reset()
    assert instance.round_number == era_key(instance) == 0
