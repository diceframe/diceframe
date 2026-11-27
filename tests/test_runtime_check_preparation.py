"""Production prepare/abort/retry and asynchronous authority boundary coverage."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest

from src.commands.dice_resolver import DiceResolver
from src.commands.round_actions import format_check_results_constraint
from src.commands.round_processor import RoundProcessor
from src.engine.game_instance import GameInstance, GameState
from src.engine.modules import checks
from src.rules.rule_system import RuleSystem
from src.rulesets.contracts import RulesetCapabilities


class Resolver:
    capabilities = RulesetCapabilities(narrative_checks=True)

    def __init__(self):
        self.calls = 0
        self.instances = []

    def resolve_narrative_checks(self, instance, requests, rng=None):
        self.calls += 1
        self.instances.append(instance)
        return self.answers(requests, self.calls + 7)

    @staticmethod
    def answers(requests, roll):
        # No total/constraint: both are optional in the runtime contract.
        return [{
            "check_id": request["check_id"], "dice": "2d8", "roll": roll,
            "rolls": [4, roll - 4], "dc": 12, "verdict": "failure",
            # 可选契约：声明后由 code 渲染，而不是把作者文字当身份
            "verdict_code": "failure",
        } for request in requests]


class BlockingResolver(Resolver):
    def __init__(self):
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.finished = Event()

    def resolve_narrative_checks(self, instance, requests, rng=None):
        self.calls += 1
        self.instances.append(instance)
        self.started.set()
        try:
            assert self.release.wait(5), "test failed to release the RPC"
            # A cancelled worker must never retain a reference into live state.
            instance.players["p1"]["character_sheet"]["attributes"]["str"] = 999
            return self.answers(requests, 8)
        finally:
            self.finished.set()


class PlannerClient:
    calls = 0

    async def call_tools(self, *_args, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            tool_calls=[{"name": "dice_checks", "arguments": {"checks": [{
                "player": "p1", "attribute": "str", "target": 12,
            }]}}], total_tokens=5, provider_used="test", native_tools=True,
        )


def setup(resolver):
    instance = GameInstance(("web", "runtime-preparation", "bot"))
    instance.players["p1"] = {
        "user_id": "p1", "character_name": "Tester",
        "character_sheet": {"attributes": {"str": 8}, "skills": []},
    }
    instance.action_queue = [{"user_id": "p1", "text": "break down the door"}]
    instance.state = GameState.ACTIVE_JUDGMENT
    rule = RuleSystem({
        "rule_id": "test_runtime", "dice_system": "none",
        "attributes": [{"key": "str", "name": "Strength"}],
    })
    # Construct only the collaborators used by the real preparation pipeline.
    processor = RoundProcessor.__new__(RoundProcessor)
    current = {"instance": instance}
    processor.registry = SimpleNamespace(get=lambda _key: current["instance"])
    processor.check_model_enabled = False
    processor.check_llm_client = None
    processor._advance_adventure_world = None
    processor.llm_client = PlannerClient()
    processor._prompt = SimpleNamespace(load_rule_context=lambda *_: SimpleNamespace(
        rule=rule, dice_system="none",
    ))
    processor._load_world_template = None
    processor._ruleset_runtime = lambda _instance: resolver
    processor._deferred_check_indexes = lambda _instance: set()
    processor._dice = DiceResolver()
    return instance, processor, current


async def wait_for(event):
    assert await asyncio.wait_for(asyncio.to_thread(event.wait, 3), 4)


def domain(instance):
    return deepcopy(instance.to_dict()), deepcopy(instance.round_unpriced_purchase_intents)


@pytest.mark.asyncio
async def test_production_abort_retry_reuses_runtime_result_and_check_identity():
    resolver = Resolver()
    instance, processor, _current = setup(resolver)
    first = deepcopy(await processor.prepare_round_checks_ai(instance))
    original_request = deepcopy(instance.action_queue[0]["check_request"])
    original_result = deepcopy(instance.action_queue[0]["check_result"])
    assert await instance.abort_round_processing()
    assert instance.state == GameState.ACTIVE_ACTION
    assert instance.action_queue[0]["dice_value"] == 8
    # Normal retry can replan; planner UUIDs are not settlement identities.
    instance.state = GameState.ACTIVE_JUDGMENT
    retry = await processor.prepare_round_checks_ai(instance)
    assert processor.llm_client.calls == 2
    assert resolver.calls == 1
    assert retry == first
    assert instance.action_queue[0]["check_request"] == original_request
    assert instance.action_queue[0]["check_result"] == original_result
    assert checks.round_checks_prepared(instance)
    assert instance._round_check_generation > 0
    assert "_round_check_generation" not in instance.to_dict()


@pytest.mark.asyncio
async def test_runtime_2d8_prompt_and_regeneration_preserve_actual_dice():
    instance, processor, _current = setup(Resolver())
    result = await processor.prepare_round_checks_ai(instance)
    assert result[0]["planner_source"] == "runtime"
    assert "total" not in result[0] and "constraint" not in result[0]
    blocks = [
        DiceResolver().resolve_action_check(instance, instance.action_queue[0], None),
        format_check_results_constraint(instance, result),
    ]
    for block in blocks:
        assert "2d8=4+4 = 8 vs 12" in block
        assert "失败" in block  # code 渲染为对局语言，而不是原样回显作者文字
        assert "d20" not in block
        assert "必须遵循" in block


@pytest.mark.asyncio
async def test_slow_runtime_does_not_block_the_event_loop():
    resolver = BlockingResolver()
    instance, processor, _current = setup(resolver)
    task = asyncio.create_task(processor.prepare_round_checks_ai(instance))
    try:
        # Waiting for worker entry itself requires a responsive event loop.
        await wait_for(resolver.started)
        await asyncio.wait_for(asyncio.sleep(0.02), 0.3)
        assert not task.done()
        assert "check_result" not in instance.action_queue[0]
        assert resolver.instances[0] is not instance
    finally:
        resolver.release.set()
        await task
    assert instance.action_queue[0]["dice_value"] == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["rpc", "commit"])
async def test_cancelled_runtime_preparation_never_publishes(cancel_at):
    resolver = BlockingResolver()
    instance, processor, _current = setup(resolver)
    before = domain(instance)
    task = asyncio.create_task(processor.prepare_round_checks_ai(instance))
    locked = False
    try:
        await wait_for(resolver.started)
        if cancel_at == "commit":
            await instance._authority_lock.acquire()
            locked = True
            resolver.release.set()
            await wait_for(resolver.finished)
            # Let preparation reach the queued authority gate.
            await asyncio.sleep(0.02)
            assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        resolver.release.set()
        if locked:
            instance._authority_lock.release()
        await wait_for(resolver.finished)
    await asyncio.sleep(0)
    assert domain(instance) == before
    assert not checks.round_checks_prepared(instance)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "run", "round", "action", "player", "world", "replacement", "abort", "abort_retry_aba",
])
async def test_stale_runtime_result_never_changes_live_state(change):
    resolver = BlockingResolver()
    instance, processor, current = setup(resolver)
    task = asyncio.create_task(processor.prepare_round_checks_ai(instance))
    try:
        await wait_for(resolver.started)
        if change == "run":
            instance.rotate_run_identity()
        elif change == "round":
            instance.round_number += 1
        elif change == "action":
            instance.action_queue[0]["text"] = "a different action"
        elif change == "player":
            instance.players["p1"]["character_sheet"]["attributes"]["str"] = 12
        elif change == "world":
            instance.world_state["revision"] += 1
        elif change == "replacement":
            current["instance"] = GameInstance(instance.game_key, run_id=instance.run_id)
        else:
            assert await instance.abort_round_processing()
            if change == "abort_retry_aba":
                instance.state = GameState.ACTIVE_JUDGMENT
        expected = domain(instance)
        replacement_expected = domain(current["instance"])
    finally:
        resolver.release.set()
    assert await task == []
    assert domain(instance) == expected
    assert domain(current["instance"]) == replacement_expected


@pytest.mark.asyncio
async def test_concurrent_preparation_cannot_overwrite_the_committed_roll():
    resolver = BlockingResolver()
    instance, processor, _current = setup(resolver)
    first = asyncio.create_task(processor.prepare_round_checks_ai(instance))
    try:
        await wait_for(resolver.started)
        winner = Resolver()
        winner.answers = lambda requests, _roll: Resolver.answers(requests, 16)
        processor._ruleset_runtime = lambda _instance: winner
        committed = deepcopy(await processor.prepare_round_checks_ai(instance))
        assert committed[0]["roll"] == 16
        expected = domain(instance)
    finally:
        resolver.release.set()
    await first
    assert domain(instance) == expected
    assert checks.last_checks(instance) == committed
