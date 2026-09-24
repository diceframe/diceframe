"""R5-c2: judgment commit and next-round opening form one state-lock write."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance, GameState
from src.engine.module_state import ModuleStateError
from tests.test_progression_module import UNKNOWN_SLOTS, instance_with_state
from tests.test_round_failure_recovery import _new_game
from webapi_harness import web_api  # noqa: F401


async def _drain(*tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_finish", [False, True])
async def test_queued_writer_sees_complete_transition(cancel_finish):
    instance = instance_with_state()
    instance.state = GameState.ACTIVE_JUDGMENT
    pending = deepcopy(instance.pending_actions)
    queued = asyncio.Event()
    observations = []

    async def writer():
        queued.set()
        async with instance._lock:
            observations.append((instance.state, instance.round_number, deepcopy(instance.log)))
            if cancel_finish:
                finish.cancel()
            instance.scene = "queued writer"

    async with instance._lock:
        finish = asyncio.create_task(instance.finish_judgment("narrative"))
        # The real Lock acquire suspends completion; no artificial await in start_round.
        await asyncio.sleep(0)
        following = asyncio.create_task(writer())
        await asyncio.wait_for(queued.wait(), 5)
    try:
        await asyncio.wait_for(asyncio.gather(finish, following), 5)
        state, round_number, log = observations[0]
        assert (state, round_number) == (GameState.ACTIVE_ACTION, 8)
        assert [(entry["round"], entry["gm_response"]) for entry in log] == [(6, "Before"), (7, "narrative")]
        assert instance.action_queue == pending
        assert instance.pending_actions == []
        assert instance.scene == "queued writer"
        assert not finish.cancelled()
        assert not instance._lock.locked()
    finally:
        await _drain(finish, following)


@pytest.mark.asyncio
async def test_cancel_while_waiting_for_state_lock_does_not_commit():
    instance = instance_with_state()
    instance.state = GameState.ACTIVE_JUDGMENT
    before = deepcopy(instance.to_dict())
    async with instance._lock:
        finish = asyncio.create_task(instance.finish_judgment("must not commit"))
        await asyncio.sleep(0)
        finish.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(finish, 5)
        assert instance.to_dict() == before
    # Cancellation does not poison the lock or prevent a later completion.
    await asyncio.wait_for(instance.finish_judgment("retry"), 5)
    assert instance.state == GameState.ACTIVE_ACTION
    assert instance.round_number == 8
    assert instance.log[-1]["gm_response"] == "retry"


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_completion_preserves_log_snapshots_checks_and_timer_semantics(pending, caplog):
    instance = instance_with_state()
    instance.state = GameState.ACTIVE_JUDGMENT
    if not pending:
        instance.action_queue.clear()
        instance.pending_actions.clear()
    actions = deepcopy(instance.action_queue)
    next_actions = deepcopy(instance.pending_actions)
    snapshot = deepcopy(instance.round_start_snapshot)
    instance.round_checks_prepared = True
    instance.last_checks = [{"id": "check", "result": "success"}]
    instance.death_save_outcomes = {"7": {"gm": "stable"}, "8": {"gm": "next"}}
    instance.combat_extension = {"schema_version": 1, "pending_summaries": ["hit", "hit"]}
    instance.combat_extension_round_snapshots["7"] = {"schema_version": 1, "phase": "before"}
    timer = asyncio.create_task(asyncio.Event().wait())
    instance._luck_timers["check"] = timer
    calls_before = instance.total_llm_calls
    pre_state = {"gm": {"hp": 18}}
    pre_combat = {"schema_version": 1, "phase": "pre-update"}
    try:
        with caplog.at_level("INFO", logger="trpg"):
            await instance.finish_judgment("finished", pre_state, ["visible", "hit"], pre_combat)
        entry = instance.log[-1]
        assert entry["round"] == 7
        assert entry["actions"] == actions
        assert entry["gm_response"] == "finished"
        assert entry["state_changes"] == ["visible", "hit"]
        assert entry["check_results"] == instance.last_checks
        assert entry["round_start_snapshot"] == snapshot
        assert entry["combat_extension_round_start"] == {"schema_version": 1, "phase": "before"}
        assert entry["pre_state_snapshot"] == pre_state
        assert entry["pre_combat_extension_snapshot"] == pre_combat
        assert entry["pre_world_state"] == instance.world_state
        assert entry["pre_adventure_progress"] == instance.adventure_progress
        assert entry["swipes"] == [] and entry["current_swipe"] == 0
        assert entry["timestamp"] <= instance.last_activity
        assert instance.total_llm_calls == calls_before + 1
        assert "pending_summaries" not in instance.combat_extension
        assert "7" not in instance.combat_extension_round_snapshots
        assert instance.state == GameState.ACTIVE_ACTION
        assert instance.round_number == 8
        assert not instance.round_checks_prepared
        assert instance.round_start_snapshot == instance.round_entity_snapshot == {}
        assert instance.action_queue == next_actions
        assert instance.pending_actions == [] and instance.ready_players == set()
        assert instance.death_save_outcomes == {"8": {"gm": "next"}}
        assert instance._luck_timers == {"check": timer} and not timer.done()
        assert sum("Round 8 开始" in record.message for record in caplog.records) == 1
    finally:
        await _drain(timer)


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
async def test_queued_finish_rechecks_progression_before_any_mutation(slot):
    instance = instance_with_state()
    instance.state = GameState.ACTIVE_JUDGMENT
    async with instance._lock:
        finish = asyncio.create_task(instance.finish_judgment("must not commit"))
        await asyncio.sleep(0)
        instance.modules["progression"] = deepcopy(slot)
        before = deepcopy(instance.to_dict())
    with pytest.raises(ModuleStateError):
        await asyncio.wait_for(finish, 5)
    assert instance.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["economy", "combat_extension"])
async def test_queued_finish_rechecks_other_module_guards_before_any_mutation(module):
    instance = instance_with_state()
    instance.state = GameState.ACTIVE_JUDGMENT
    async with instance._lock:
        finish = asyncio.create_task(instance.finish_judgment("must not commit"))
        await asyncio.sleep(0)
        instance.modules[module]["schema_version"] = 99
        before = deepcopy(instance.to_dict())
    with pytest.raises(ModuleStateError):
        await asyncio.wait_for(finish, 5)
    assert instance.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["omitted", "current", "stale", "rotated"])
async def test_standalone_start_round_retains_run_fence_under_lock(token):
    instance = instance_with_state()
    expected = "stale" if token == "stale" else instance.run_id
    kwargs = {} if token == "omitted" else {"expected_run_id": expected}
    async with instance._lock:
        opening = asyncio.create_task(instance.start_round(**kwargs))
        await asyncio.sleep(0)
        if token == "rotated":
            instance.rotate_run_identity()
        if token in {"stale", "rotated"}:
            # A stale fence wins even over unsupported-module validation.
            instance.modules["progression"] = deepcopy(UNKNOWN_SLOTS[0])
            instance.modules["economy"]["schema_version"] = 99
        before = deepcopy(instance.to_dict())
    await asyncio.wait_for(opening, 5)
    if token in {"stale", "rotated"}:
        assert instance.to_dict() == before
    else:
        assert instance.round_number == 8
        assert instance.state == GameState.ACTIVE_ACTION


@pytest.mark.asyncio
@pytest.mark.parametrize("guarded", [False, True])
async def test_real_submission_pipeline_commits_and_opens_before_queued_writer(web_api, monkeypatch, guarded):
    api, _lorebook, registry, _llm, _worlds = web_api
    game_key, instance, uid = await _new_game(api, registry)
    round_before = instance.round_number
    run_before = instance.run_id
    entered, release, committing, queued = (asyncio.Event() for _ in range(4))
    original_finish = instance.finish_judgment
    observations = []

    async def finish(*args, **kwargs):
        entered.set()
        await release.wait()
        committing.set()
        return await original_finish(*args, **kwargs)

    async def writer():
        queued.set()
        async with instance._lock:
            observations.append((instance.state, instance.round_number, deepcopy(instance.log[-1])))
            instance.scene = "after atomic completion"

    monkeypatch.setattr(instance, "finish_judgment", finish)
    kwargs = {"expected_run_id": run_before} if guarded else {}
    submission = asyncio.create_task(api.submit_action(game_key, uid, "I look around", **kwargs))
    following = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with instance._lock:
            release.set()
            await asyncio.wait_for(committing.wait(), 5)
            following = asyncio.create_task(writer())
            await asyncio.wait_for(queued.wait(), 5)
        result, _ = await asyncio.wait_for(asyncio.gather(submission, following), 5)
        assert result["status"] == 200
        assert result["payload"]["advanced"] is True
        state, round_number, entry = observations[0]
        assert (state, round_number) == (GameState.ACTIVE_ACTION, round_before + 1)
        assert entry["round"] == round_before
        assert entry["gm_response"]
        assert [action["user_id"] for action in entry["actions"]] == [uid]
        assert len([item for item in instance.log if item["round"] == round_before]) == 1
        assert instance.run_id == run_before
        assert instance.round_number == round_before + 1
        assert not instance.round_processing_in_flight()
        assert not any(lock.locked() for lock in (instance._authority_lock, instance._process_lock, instance._lock))
        restored = GameInstance.from_dict(deepcopy(instance.to_dict()))
        assert restored.round_number == round_before + 1
        assert restored.log[-1]["round"] == round_before
    finally:
        release.set()
        await _drain(*[task for task in (submission, following) if task is not None])
