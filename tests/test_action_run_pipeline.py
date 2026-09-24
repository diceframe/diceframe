"""R4-b2 run fences through the real WebAPI/handler/round processor pipeline."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.game_instance import GameState
from src.engine.player_control import set_control
from src.webui.services import turns
from tests.test_action_run_guard import STALE
from tests.test_round_failure_recovery import _new_game, _two_player_game
from webapi_harness import web_api  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("spawn_processor", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "preempt"])
async def test_guarded_real_round_task_ownership(web_api, monkeypatch, spawn_processor, outcome):
    api, _lorebook, registry, llm, _worlds = web_api
    game_key, instance, uid = await _new_game(api, registry)
    deps = api._turn_dependencies
    processor_tasks = []
    original_process = deps.process_round

    async def run_processor(*args, **kwargs):
        processor_tasks.append(asyncio.current_task())
        return await original_process(*args, **kwargs)

    async def process(*args, **kwargs):
        if spawn_processor:
            return await asyncio.create_task(run_processor(*args, **kwargs))
        return await run_processor(*args, **kwargs)

    deps = replace(deps, process_round=process)
    entered, release = asyncio.Event(), asyncio.Event()
    original_call = llm.call

    async def call(*args, **kwargs):
        if instance.round_processing_in_flight():
            entered.set()
            await release.wait()
            if outcome == "failure":
                raise RuntimeError("narration failed")
        return await original_call(*args, **kwargs)

    monkeypatch.setattr(llm, "call", call)
    round_before = instance.round_number
    submission = asyncio.create_task(turns.submit_action(
        deps, game_key, uid, "I look around", expected_run_id=instance.run_id,
    ))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert instance._authority_owner is submission
        assert instance._process_task is processor_tasks[0]
        assert (processor_tasks[0] is submission) is (not spawn_processor)
        if outcome == "preempt":
            assert await asyncio.wait_for(instance.cancel_round_processing(), 5)
            result = await asyncio.wait_for(submission, 5)
            assert result["status"] == 409
            assert result["payload"]["reason"] == "preempted"
        elif outcome == "cancel":
            submission.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(submission, 5)
        else:
            release.set()
            result = await asyncio.wait_for(submission, 5)
            assert result["status"] == (200 if outcome == "success" else 502)
            if outcome == "success":
                assert result["payload"]["advanced"] is True
            else:
                assert result["payload"]["rolled_back"] is True
        assert instance.round_number == round_before + (outcome == "success")
        assert instance.state == GameState.ACTIVE_ACTION
        assert not instance.round_processing_in_flight()
        assert not instance._authority_lock.locked()
        assert not instance._process_lock.locked()
        assert not instance._lock.locked()
    finally:
        release.set()
        if not submission.done():
            submission.cancel()
        await asyncio.gather(submission, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["reset_game", "restart_game", "historical_rewrite"])
async def test_guarded_ai_and_narrative_round_serialize_lifecycle(web_api, monkeypatch, transition):
    api, _lorebook, registry, llm, _worlds = web_api
    game_key, instance, human_uid, ai_uid = await _two_player_game(api, registry)
    set_control(instance, ai_uid, "ai")
    old_run = instance.run_id
    entered, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_call = llm.call
    commit = instance.commit_ai_player_action
    commits = []

    async def observed_commit(*args, **kwargs):
        # b1's capability query reaches the locked aggregate unchanged.
        assert callable(kwargs["requires_structured_intent"])
        assert instance._authority_owner is asyncio.current_task()
        result = await commit(*args, **kwargs)
        commits.append(result)
        return result

    async def call(*args, **kwargs):
        if instance.round_processing_in_flight():
            entered.set()
            await release.wait()
        return await original_call(*args, **kwargs)

    authority = instance.authoritative_write

    @asynccontextmanager
    async def observed_authority():
        if asyncio.current_task() is replacement:
            waiting.set()
        async with authority() as granted:
            yield granted

    async def replace_run():
        if transition == "historical_rewrite":
            waiting.set()
            async with instance.historical_rewrite() as granted:
                assert granted
                assert not instance._process_lock.locked()
            return instance
        return await getattr(api._handler, transition)(instance)

    monkeypatch.setattr(instance, "commit_ai_player_action", observed_commit)
    monkeypatch.setattr(llm, "call", call)
    replacement = None
    monkeypatch.setattr(instance, "authoritative_write", observed_authority)
    submission = asyncio.create_task(api.submit_action(
        game_key, human_uid, "I look around", expected_run_id=old_run,
    ))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert commits == [""]
        replacement = asyncio.create_task(replace_run())
        await asyncio.wait_for(waiting.wait(), 5)
        assert not replacement.done()
        assert registry.get(instance.game_key) is instance
        release.set()
        result = await asyncio.wait_for(submission, 5)
        candidate = await asyncio.wait_for(replacement, 5)
        assert result["status"] == 200
        assert result["payload"]["advanced"] is True
        assert {action["user_id"] for action in instance.log[-1]["actions"]} == {human_uid, ai_uid}
        if transition != "historical_rewrite":
            assert candidate is registry.get(instance.game_key)
            assert candidate is not instance
            assert candidate.run_id != old_run
            assert candidate.action_queue == []
        assert not instance._authority_lock.locked()
    finally:
        release.set()
        tasks = [task for task in (submission, replacement) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["fill", "advance", "prepare", "process"])
@pytest.mark.parametrize("change", ["rotate", "replace"])
async def test_progression_stops_after_late_run_change(web_api, monkeypatch, boundary, change):
    api, _lorebook, registry, _llm, _worlds = web_api
    game_key, instance, uid = await _new_game(api, registry)
    deps = api._turn_dependencies
    current = [instance]
    before = []
    late_calls = []

    def wrap(name, callback):
        async def wrapped(*args, **kwargs):
            if before:
                late_calls.append(name)
            result = await callback(*args, **kwargs)
            if name == boundary:
                if change == "rotate":
                    instance.rotate_run_identity()
                else:
                    current[0] = type(instance).from_dict(instance.to_dict())
                before.append(deepcopy(instance.to_dict()))
            return result
        return wrapped

    monkeypatch.setattr(instance, "try_advance", wrap("advance", instance.try_advance))
    monkeypatch.setattr(turns, "_auto_settle_rewards", wrap("settle", turns._auto_settle_rewards))
    deps = replace(
        deps,
        get_instance=lambda _: current[0],
        fill_ai_player_actions=wrap("fill", deps.fill_ai_player_actions),
        prepare_round_checks_ai=wrap("prepare", deps.prepare_round_checks_ai),
        process_round=wrap("process", deps.process_round),
        save_instance=wrap("save", deps.save_instance),
    )
    result = await asyncio.wait_for(turns.submit_action(
        deps, game_key, uid, "I look around", expected_run_id=instance.run_id,
    ), 5)
    assert result == STALE
    assert before
    assert late_calls == []
    assert instance.to_dict() == before[0]


@pytest.mark.asyncio
async def test_stale_processor_exception_does_not_rollback_or_save_new_run(web_api, monkeypatch):
    api, _lorebook, registry, _llm, _worlds = web_api
    game_key, instance, uid = await _new_game(api, registry)
    before = []

    async def failed_processor(*_args, **_kwargs):
        instance.rotate_run_identity()
        before.append(deepcopy(instance.to_dict()))
        raise RuntimeError("old processor failed after run changed")

    async def unexpected_rollback(*_args, **_kwargs):
        pytest.fail("stale processor failure rolled back the new run")

    monkeypatch.setattr(instance, "abort_round_processing", unexpected_rollback)
    deps = replace(api._turn_dependencies, process_round=failed_processor)
    assert await turns.submit_action(
        deps, game_key, uid, "I look around", expected_run_id=instance.run_id,
    ) == STALE
    assert instance.to_dict() == before[0]


@pytest.mark.asyncio
async def test_late_replacement_during_reward_stops_remaining_settlements(web_api):
    from tests.test_turn_service import _reward_proposal

    api, _lorebook, registry, _llm, _worlds = web_api
    game_key, instance, uid = await _new_game(api, registry)
    deps = api._turn_dependencies
    current = [instance]
    resolved = []
    process = deps.process_round
    before = []

    async def process_with_rewards(*args, **kwargs):
        result = await process(*args, **kwargs)
        _reward_proposal(instance, proposal_id="first", amount=1, uid=uid)
        _reward_proposal(instance, proposal_id="second", amount=1, uid=uid)
        return result

    async def resolve_reward(_key, proposal_id, _actor):
        resolved.append(proposal_id)
        current[0] = type(instance).from_dict(instance.to_dict())
        before.append(deepcopy(current[0].to_dict()))
        return {"ok": True}

    deps = replace(
        deps, get_instance=lambda _: current[0], process_round=process_with_rewards,
        economy_auto_reward_settings=lambda _: (True, 50), resolve_reward=resolve_reward,
    )
    assert await turns.submit_action(
        deps, game_key, uid, "I look around", expected_run_id=instance.run_id,
    ) == STALE
    assert resolved == ["first"]
    assert current[0].to_dict() == before[0]
