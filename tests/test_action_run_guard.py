"""R4-b2 optional human action run identity and concurrency contracts."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.engine.game_instance import GameState
from src.engine.memory_outbox import queue_memory_delivery
from src.engine.player_control import set_control
from src.webui.api import WebAPI
from src.webui.services import characters, turns
from tests.test_action_gate import instance_with_seats, turn_dependencies


@pytest.mark.asyncio
@pytest.mark.parametrize("paused", [False, True])
async def test_omitted_token_keeps_existing_retry_resume_and_save_behavior(paused) -> None:
    instance = instance_with_seats()
    instance.state = GameState.PAUSED if paused else GameState.ACTIVE_ACTION
    events = []

    async def retry(target):
        events.append("retry")
        target.rotate_run_identity()
        return True

    async def save(target):
        events.append("save")
        assert target.action_queue[0]["text"] == "look around"

    deps = turn_dependencies(instance, drain_economy_outbox=retry, save_instance=save)
    result = await turns.submit_action(deps, "game", "actor", "look around")

    assert result == {
        "status": 200,
        "payload": {
            "narration": "行动已公开，等待 human 行动",
            "advanced": False,
            "phase": "done",
            "multiplayer": instance.multiplayer_status(),
        },
    }
    assert events == ["retry", "save"]
    assert instance.state == GameState.ACTIVE_ACTION
    assert [action["user_id"] for action in instance.action_queue] == ["actor"]


STALE = {
    "status": 409,
    "payload": {"ok": False, "error_code": "STALE_RUN", "error": "对局已重开，请刷新后重试"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["omitted", "empty", "matching"])
@pytest.mark.parametrize("round_number", [0, 7])
async def test_optional_tokens_preserve_normal_response_and_paused_resume(token, round_number) -> None:
    instance = instance_with_seats()
    instance.state = GameState.PAUSED
    instance.round_number = round_number
    drain = AsyncMock(return_value=True)
    deps = turn_dependencies(instance, drain_economy_outbox=drain)
    options = {} if token == "omitted" else {"expected_run_id": instance.run_id if token == "matching" else ""}
    result = await turns.submit_action(deps, "game", "actor", "look around", **options)
    assert result == {
        "status": 200,
        "payload": {"narration": "行动已公开，等待 human 行动", "advanced": False,
                    "phase": "done", "multiplayer": instance.multiplayer_status()},
    }
    assert instance.state == GameState.ACTIVE_ACTION
    assert instance.action_queue[0]["text"] == "look around"
    drain.assert_awaited_once_with(instance)
    deps.save_instance.assert_awaited_once_with(instance)


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [False, True])
async def test_initial_stale_has_no_retry_resume_roll_save_or_action_mutation(confirm) -> None:
    instance = instance_with_seats()
    instance.state = GameState.PAUSED
    instance.action_queue.append({"user_id": "actor", "text": "pending", "dice_pending": True})
    queue_memory_delivery(instance, effect_group_id="eg", memory_delta={"add": ["private fact"]}, round_number=7)
    drain = AsyncMock(side_effect=AssertionError("stale request retried outbox"))
    deps = turn_dependencies(instance, drain_economy_outbox=drain)
    before = deepcopy(instance.to_dict())
    assert await turns.submit_action(
        deps, "game", "actor", "look", expected_run_id="old-run", confirm=confirm,
    ) == STALE
    assert instance.to_dict() == before
    drain.assert_not_called()
    deps.save_instance.assert_not_called()
    deps.resolve_pending_dice.assert_not_called()
    deps.roll_for_game.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("rotate", [False, True])
async def test_async_retry_rotation_or_replacement_rejected_before_resume_and_save(rotate) -> None:
    instance = instance_with_seats()
    instance.state = GameState.PAUSED
    current = [instance]
    entered, release = asyncio.Event(), asyncio.Event()

    async def drain(_instance):
        entered.set()
        await release.wait()
        return True

    deps = turn_dependencies(instance, get_instance=lambda _: current[0], drain_economy_outbox=drain)
    task = asyncio.create_task(turns.submit_action(
        deps, "game", "actor", "look", expected_run_id=instance.run_id,
    ))
    await asyncio.wait_for(entered.wait(), 3)
    if rotate:
        instance.rotate_run_identity()
    else:
        current[0] = instance_with_seats()
    before = deepcopy(instance.to_dict())
    replacement_before = deepcopy(current[0].to_dict())
    release.set()
    assert await asyncio.wait_for(task, 3) == STALE
    assert instance.to_dict() == before
    assert current[0].to_dict() == replacement_before
    deps.save_instance.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_instance", [False, True])
async def test_waiting_authority_rechecks_run_and_registry_before_retry(replace_instance, monkeypatch) -> None:
    instance = instance_with_seats()
    instance.state = GameState.PAUSED
    current = [instance]
    waiting = asyncio.Event()
    authority = instance.authoritative_write

    @asynccontextmanager
    async def observed_authority():
        waiting.set()
        async with authority() as entered:
            yield entered

    monkeypatch.setattr(instance, "authoritative_write", observed_authority)
    drain = AsyncMock()
    deps = turn_dependencies(instance, get_instance=lambda _: current[0], drain_economy_outbox=drain)
    async with authority():
        task = asyncio.create_task(turns.submit_action(
            deps, "game", "actor", "look", expected_run_id=instance.run_id,
        ))
        await asyncio.wait_for(waiting.wait(), 3)
        assert not task.done()
        if replace_instance:
            current[0] = instance_with_seats()
        else:
            instance.rotate_run_identity()
        before = deepcopy(instance.to_dict())
        replacement_before = deepcopy(current[0].to_dict())
    assert await asyncio.wait_for(task, 3) == STALE
    assert instance.to_dict() == before
    assert current[0].to_dict() == replacement_before
    drain.assert_not_called()
    deps.save_instance.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["action", "resume", "start"])
async def test_waiting_state_lock_rejects_without_resuming_queueing_or_saving_new_run(phase) -> None:
    instance = instance_with_seats()
    if phase != "action":
        instance.state = GameState.PAUSED
        instance.round_number = 0 if phase == "start" else 7
    drained = asyncio.Event()

    async def drain(_instance):
        drained.set()
        return True

    deps = turn_dependencies(instance, drain_economy_outbox=drain)
    async with instance._lock:
        task = asyncio.create_task(turns.submit_action(
            deps, "game", "actor", "look", expected_run_id=instance.run_id,
        ))
        await asyncio.wait_for(drained.wait(), 3)
        assert not task.done()
        instance.rotate_run_identity()
        before = deepcopy(instance.to_dict())
    assert await asyncio.wait_for(task, 3) == STALE
    assert instance.to_dict() == before
    deps.save_instance.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["omitted", "empty", "matching", "stale"])
async def test_aggregate_add_action_keeps_boolean_contract(token) -> None:
    instance = instance_with_seats()
    options = {} if token == "omitted" else {
        "expected_run_id": "old-run" if token == "stale" else instance.run_id if token == "matching" else "",
    }
    before = deepcopy(instance.to_dict())
    added = await instance.add_action("actor", "look", **options)
    assert added is (token != "stale")
    if token == "stale":
        assert instance.to_dict() == before
    else:
        assert instance.action_queue[0]["text"] == "look"


@pytest.mark.asyncio
async def test_aggregate_add_action_rechecks_after_authority_wait(monkeypatch) -> None:
    instance = instance_with_seats()
    waiting = asyncio.Event()
    authority = instance.authoritative_write

    @asynccontextmanager
    async def observed_authority():
        waiting.set()
        async with authority() as entered:
            yield entered

    monkeypatch.setattr(instance, "authoritative_write", observed_authority)
    async with authority():
        task = asyncio.create_task(instance.add_action("actor", "look", expected_run_id=instance.run_id))
        await asyncio.wait_for(waiting.wait(), 3)
        instance.rotate_run_identity()
        before = deepcopy(instance.to_dict())
    assert await asyncio.wait_for(task, 3) is False
    assert instance.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "runtime", "member", "control", "structured", "dead"])
@pytest.mark.parametrize("token", ["matching", "stale"])
async def test_existing_pre_retry_errors_keep_priority_over_run_guard(failure, token) -> None:
    instance = instance_with_seats()
    expected = instance.run_id if token == "matching" else "old-run"
    drain = AsyncMock()
    deps = turn_dependencies(instance, drain_economy_outbox=drain)
    if failure == "missing":
        deps = replace(deps, get_instance=lambda _: None)
        response = {"status": 404, "payload": {"error": "游戏不存在，请刷新页面重新开始"}}
    elif failure == "runtime":
        deps = replace(deps, load_rule_for_game=lambda _: SimpleNamespace(template={}),
                       ruleset_registry=SimpleNamespace(resolve=Mock(side_effect=ValueError("unavailable"))))
        response = {"status": 409, "payload": {
            "ok": False, "error_code": "RULESET_RUNTIME_UNAVAILABLE", "error": "unavailable",
        }}
    else:
        code = {"member": "PLAYER_NOT_IN_GAME", "control": "PLAYER_AI_CONTROLLED",
                "structured": "STRUCTURED_INTENT_REQUIRED", "dead": "ACTOR_DECEASED"}[failure]
        if failure == "member":
            instance.players.pop("actor")
        elif failure == "control":
            set_control(instance, "actor", "ai")
        elif failure == "structured":
            deps = replace(deps, load_rule_for_game=lambda _: SimpleNamespace(template={}),
                           ruleset_registry=SimpleNamespace(resolve=lambda _: SimpleNamespace(
                               capabilities=SimpleNamespace(authoritative_intents=True, narrative_turns=False),
                           )))
        else:
            instance.players["actor"]["character_sheet"]["deceased"] = True
        response = turns._gate_rejection(instance, "actor", code)
    before = deepcopy(instance.to_dict())
    assert await turns.submit_action(deps, "game", "actor", "look", expected_run_id=expected) == response
    assert instance.to_dict() == before
    drain.assert_not_called()
    deps.save_instance.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["empty", "matching", "stale"])
async def test_run_precedes_economy_and_phase_without_exposing_private_proposals(token) -> None:
    instance = instance_with_seats()
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.economy["proposals"].append({
        "id": "secret", "run_id": instance.run_id, "status": "pending", "kind": "payment",
        "payer_uid": "human", "visibility": "private", "reason": "private details",
    })
    drain = AsyncMock(return_value=True)
    deps = turn_dependencies(instance, drain_economy_outbox=drain)
    expected = "old-run" if token == "stale" else instance.run_id if token == "matching" else ""
    result = await turns.submit_action(deps, "game", "actor", "look", expected_run_id=expected)
    if token == "stale":
        assert result == STALE
        drain.assert_not_called()
    else:
        assert result == {"status": 409, "payload": {
            "ok": False, "error_code": "ECONOMY_DECISION_PENDING",
            "error": "请先处理待确认的经济提案，再继续本局叙事", "pending_count": 1, "economy_proposals": [],
        }}
        drain.assert_awaited_once()
    assert "private details" not in str(result)
    deps.save_instance.assert_not_called()


@pytest.mark.asyncio
async def test_matching_token_rewrite_rejected_before_retry_and_resume() -> None:
    instance = instance_with_seats()
    instance.state = GameState.PAUSED
    deps = turn_dependencies(instance, drain_economy_outbox=AsyncMock())
    before = deepcopy(instance.to_dict())
    async with instance.historical_rewrite():
        # A different task cannot enter the rewrite owner's reentrant gate.
        result = await asyncio.create_task(turns.submit_action(
            deps, "game", "actor", "look", expected_run_id=instance.run_id,
        ))
    assert result == {"status": 409, "payload": {
        "ok": False, "error_code": "REWRITE_IN_PROGRESS",
        "error": "GM 正在重写历史回合，请等待完成后再提交行动",
    }}
    assert instance.to_dict() == before
    deps.drain_economy_outbox.assert_not_called()
    deps.save_instance.assert_not_called()


@pytest.mark.asyncio
async def test_existing_api_facade_forwards_optional_token_without_route_change() -> None:
    instance = instance_with_seats()
    deps = turn_dependencies(instance)
    facade = SimpleNamespace(_turn_dependencies=deps)
    assert await WebAPI.submit_action(facade, "game", "actor", "look", expected_run_id="old-run") == STALE


@pytest.mark.asyncio
async def test_real_outbox_and_authoritative_replacement_serialize_through_save(monkeypatch) -> None:
    instance = instance_with_seats()
    instance.state = GameState.PAUSED
    current = [instance]
    old_run = instance.run_id
    queue_memory_delivery(instance, effect_group_id="eg", memory_delta={"add": ["fact"]}, round_number=7)
    delivery_entered, release_delivery, replacement_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    saves = []

    async def save(target):
        assert target is current[0]
        assert target.run_id == old_run
        saves.append(target.run_id)

    async def apply_memory(*_args):
        delivery_entered.set()
        await release_delivery.wait()

    char_deps = SimpleNamespace(
        games=SimpleNamespace(get_instance=lambda _: current[0], save_instance=save),
        apply_economy_memory=apply_memory, reverse_economy_memory=None,
    )

    async def drain(target):
        return await characters.drain_economy_outbox(char_deps, target)

    async def replace_run():
        replacement_waiting.set()
        async with instance.authoritative_write():
            replacement = instance_with_seats()
            replacement.state = GameState.PAUSED
            current[0] = replacement

    deps = turn_dependencies(instance, get_instance=lambda _: current[0],
                             drain_economy_outbox=drain, save_instance=save)
    submission = asyncio.create_task(turns.submit_action(
        deps, "game", "actor", "look", expected_run_id=old_run,
    ))
    await asyncio.wait_for(delivery_entered.wait(), 3)
    replacement = asyncio.create_task(replace_run())
    await asyncio.wait_for(replacement_waiting.wait(), 3)
    assert not replacement.done()
    release_delivery.set()
    result = await asyncio.wait_for(submission, 3)
    await asyncio.wait_for(replacement, 3)
    assert result["status"] == 200
    assert saves == [old_run, old_run]
    assert instance.action_queue[0]["text"] == "look"
    assert current[0].state == GameState.PAUSED
    assert current[0].action_queue == []


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["empty", "matching", "stale"])
async def test_revision_limit_does_not_resume_or_save_rejected_request(token) -> None:
    instance = instance_with_seats()
    instance.state = GameState.PAUSED
    instance.action_queue.append({"user_id": "actor", "text": "old", "revision_count": 3})
    drain = AsyncMock(return_value=True)
    deps = turn_dependencies(instance, drain_economy_outbox=drain)
    before = deepcopy(instance.to_dict())
    expected = "old-run" if token == "stale" else instance.run_id if token == "matching" else ""
    result = await turns.submit_action(deps, "game", "actor", "look", expected_run_id=expected)
    assert result == (STALE if token == "stale" else {
        "status": 400, "payload": {"error": "本轮行动已修改 3 次，请等待其他玩家或 GM 推进"},
    })
    assert instance.to_dict() == before
    deps.save_instance.assert_not_called()
