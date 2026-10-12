"""#511: abort_round_processing rolls ruleset authority back to judgment entry."""

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance, GameState
from src.engine.modules import legacy_combat, round_safety, ruleset_runtime
from tests.test_legacy_combat_module import bind_dnd_combat

ACTIVE = {"status": "active", "initiative": ["player:p1", "enemy:goblin"], "turn_index": 0}


def _batch(batch_id: str) -> dict:
    return {"batch_id": batch_id, "events": []}


async def _judging_instance(key: str) -> GameInstance:
    instance = GameInstance(game_key=("web", key, "bot"))
    instance.state = GameState.ACTIVE_ACTION
    instance.players = {
        "p1": {"user_id": "p1", "character_name": "Avery", "character_sheet": {"hp": 10}},
    }
    bind_dnd_combat(instance)
    ruleset_runtime.event_ledger(instance).append(_batch("before-round"))
    assert await instance.add_action("p1", "拔剑")
    assert await instance.advance_round()
    assert instance.state == GameState.ACTIVE_JUDGMENT
    return instance


def _write_during_judgment(instance: GameInstance) -> None:
    """What a narrative combat signal / Director batch does before the commit point."""
    state = deepcopy(ruleset_runtime.state(instance))
    state["combat"] = deepcopy(ACTIVE)
    state["encounter_request"] = {"status": "pending"}
    state["version"] = 7
    ruleset_runtime.replace_state(instance, state)
    ruleset_runtime.replace_event_ledger(
        instance, [*ruleset_runtime.event_ledger(instance), _batch("discarded-round")],
    )


@pytest.mark.asyncio
async def test_abort_restores_ruleset_state_and_rewinds_ledger() -> None:
    instance = await _judging_instance("abort-ruleset")
    entry_state = deepcopy(ruleset_runtime.state(instance))
    _write_during_judgment(instance)
    assert legacy_combat.combat_state(instance) == "active"

    assert await instance.abort_round_processing() is True

    assert ruleset_runtime.state(instance) == entry_state
    assert [b["batch_id"] for b in ruleset_runtime.event_ledger(instance)] == ["before-round"]
    assert legacy_combat.combat_state(instance) == "none"
    assert legacy_combat.combat_active(instance) is False


@pytest.mark.asyncio
async def test_abort_keeps_ledger_that_no_longer_extends_the_entry(caplog) -> None:
    instance = await _judging_instance("abort-ledger-replaced")
    entry_state = deepcopy(ruleset_runtime.state(instance))
    _write_during_judgment(instance)
    replaced = [_batch("unrelated")]
    ruleset_runtime.replace_event_ledger(instance, deepcopy(replaced))

    assert await instance.abort_round_processing() is True

    assert ruleset_runtime.state(instance) == entry_state
    assert ruleset_runtime.event_ledger(instance) == replaced
    assert "未回卷账本" in caplog.text


@pytest.mark.asyncio
async def test_abort_with_pre_fix_entity_snapshot_leaves_ruleset_untouched() -> None:
    instance = await _judging_instance("abort-old-snapshot")
    round_safety.round_entity_snapshot(instance).pop("ruleset_runtime")
    _write_during_judgment(instance)
    after = deepcopy(ruleset_runtime.state(instance))
    ledger = deepcopy(ruleset_runtime.event_ledger(instance))

    assert await instance.abort_round_processing() is True

    assert ruleset_runtime.state(instance) == after
    assert ruleset_runtime.event_ledger(instance) == ledger


def test_entity_snapshot_records_ledger_mark_not_a_ledger_copy() -> None:
    instance = GameInstance(game_key=("web", "entry-mark", "bot"))
    bind_dnd_combat(instance, deepcopy(ACTIVE))
    ruleset_runtime.replace_event_ledger(instance, [_batch("a"), _batch("b")])
    instance.capture_round_entity_snapshot()

    entry = round_safety.round_entity_snapshot(instance)["ruleset_runtime"]
    assert entry == {
        "state": ruleset_runtime.state(instance),
        "ledger_length": 2,
        "ledger_last_batch_id": "b",
    }
    assert entry["state"] is not ruleset_runtime.state(instance)

    restored = GameInstance.from_dict(instance.to_dict())
    assert round_safety.round_entity_snapshot(restored)["ruleset_runtime"] == entry


@pytest.mark.parametrize("entry", [None, {}, {"state": []}, "x"])
def test_malformed_entry_is_a_no_op(entry) -> None:
    instance = GameInstance(game_key=("web", "entry-malformed", "bot"))
    bind_dnd_combat(instance, deepcopy(ACTIVE))
    ruleset_runtime.event_ledger(instance).append(_batch("a"))
    state, ledger = deepcopy(ruleset_runtime.state(instance)), deepcopy(ruleset_runtime.event_ledger(instance))

    assert ruleset_runtime.restore_round_entry(instance, entry) is False

    assert ruleset_runtime.state(instance) == state
    assert ruleset_runtime.event_ledger(instance) == ledger


@pytest.mark.parametrize("length", [-1, 5, "1", True])
def test_out_of_range_ledger_mark_restores_state_but_not_ledger(length) -> None:
    instance = GameInstance(game_key=("web", "entry-range", "bot"))
    bind_dnd_combat(instance)
    ruleset_runtime.event_ledger(instance).append(_batch("a"))
    entry = {"state": {"state_schema_version": 1, "marker": 1}, "ledger_length": length, "ledger_last_batch_id": "a"}

    assert ruleset_runtime.restore_round_entry(instance, entry) is False

    assert ruleset_runtime.state(instance) == {"state_schema_version": 1, "marker": 1}
    assert [b["batch_id"] for b in ruleset_runtime.event_ledger(instance)] == ["a"]


def test_empty_entry_ledger_keeps_an_ambiguous_replacement() -> None:
    instance = GameInstance(game_key=("web", "entry-empty", "bot"))
    bind_dnd_combat(instance)
    entry_state = {"state_schema_version": 1, "marker": 1}
    entry = {
        "state": deepcopy(entry_state),
        "ledger_length": 0,
        "ledger_last_batch_id": "",
    }
    replacement = [_batch("replacement")]
    ruleset_runtime.replace_event_ledger(instance, deepcopy(replacement))

    assert ruleset_runtime.restore_round_entry(instance, entry) is False
    assert ruleset_runtime.state(instance) == entry_state
    assert ruleset_runtime.event_ledger(instance) == replacement
