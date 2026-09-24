"""R5-c1: structured writes cannot interleave with narrative judgment."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

from aiohttp.test_utils import TestClient, TestServer

import pytest

from src.engine.game_instance import GameInstance, GameState
from src.engine.player_control import set_control
from src.webui.services import ruleset_gameplay, turns
from src.webui.services.game_controls import GameControlDependencies, GameControlService
from tests.rulesets.test_dnd2024_m5_http import (
    _EnabledRuntime, _M5Api, _app, _character, _ready_story_encounter,
)
from webapi_harness import web_api  # noqa: F401


def _game(registry):
    runtime = _EnabledRuntime()
    instance = GameInstance(
        game_key=("web", "judgment-intent", "web_bot"),
        world_id="greymoor", rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    character = _character(runtime, "stalwart_guardian", "Guardian")
    instance.players["gm"] = {"character_name": "Guardian", "character_sheet": character}
    assert instance.bind_ruleset_runtime(character["rule_binding"])
    encounter = _ready_story_encounter(runtime, instance)
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 3
    registry.register(instance)
    api = _M5Api(registry, runtime)
    body = {**encounter, "intent_id": "concurrent-start"}
    return instance, runtime, api, body


@pytest.mark.asyncio
@pytest.mark.parametrize("guarded", [False, True])
async def test_real_narration_and_concurrent_milestone(web_api, monkeypatch, guarded):
    api, _lorebook, registry, llm, _worlds = web_api
    instance, runtime, gameplay, body = _game(registry)
    game_key = "|".join(instance.game_key)
    (api._handler.rules_dir / "dnd2024_srd.json").write_text(
        json.dumps(gameplay._rule.template), encoding="utf-8",
    )
    monkeypatch.setattr(api._handler._prompt, "ruleset_registry", gameplay._ruleset_registry)
    deps = replace(
        api._turn_dependencies,
        load_rule_for_game=gameplay._load_rule_for_game,
        ruleset_registry=gameplay._ruleset_registry,
    )
    entered, release = asyncio.Event(), asyncio.Event()
    original_call = llm.call

    async def call(*args, **kwargs):
        if instance.round_processing_in_flight():
            entered.set()
            await release.wait()
        return await original_call(*args, **kwargs)

    monkeypatch.setattr(llm, "call", call)
    save, memory = AsyncMock(), AsyncMock()
    intent_deps = replace(gameplay._gameplay_dependencies, save_instance=save, apply_memory_delta=memory)
    narrative = asyncio.create_task(turns.submit_action(
        deps, game_key, "gm", "I look around",
        **({"expected_run_id": instance.run_id} if guarded else {}),
    ))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert instance.state == GameState.ACTIVE_JUDGMENT
        before = deepcopy(instance.to_dict())
        result = await asyncio.wait_for(asyncio.create_task(ruleset_gameplay.submit_intent(
            intent_deps, game_key, "gm", True, body,
        )), 5)
        assert result == {
            "ok": False, "code": "ROUND_PROCESSING", "error": "回合正在处理中，请稍后重试",
        }
        assert instance.to_dict() == before
        save.assert_not_awaited()
        memory.assert_not_awaited()
        release.set()
        completed = await asyncio.wait_for(narrative, 10)
        assert completed["status"] == 200, completed
        assert completed["payload"]["advanced"] is True
        assert instance.state == GameState.ACTIVE_ACTION
        assert instance.round_number == 4
        assert [entry["round"] for entry in instance.log] == [3]
        assert instance.ruleset_state["combat"]["status"] != "active"
        assert instance.event_ledger == before["event_ledger"]
        # Once narration completes, the same milestone is legal and gets its own
        # public round, rather than relabeling the in-flight narrative entry.
        accepted = await ruleset_gameplay.submit_intent(
            intent_deps, game_key, "gm", True, body,
        )
        assert accepted["ok"] is True, accepted
        assert instance.round_number == 5
        assert [entry["round"] for entry in instance.log] == [3, 5]
    finally:
        release.set()
        if not narrative.done():
            narrative.cancel()
        await asyncio.gather(narrative, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["intent", "adventure", "automatic"])
@pytest.mark.parametrize("queued", [False, True])
async def test_write_admission_after_concurrent_phase_transition(web_api, operation, queued):
    _api, _lorebook, registry, _llm, _worlds = web_api
    instance, runtime, gameplay, body = _game(registry)
    key = "|".join(instance.game_key)
    if operation == "automatic":
        started = await gameplay.ruleset_submit_intent(key, "gm", True, body)
        assert started["ok"] is True
        set_control(instance, "gm", "ai")
        assert runtime.next_automatic_intent(instance)["actor_id"] == "player:gm"

    complete = Mock(side_effect=AssertionError("completion must not run"))
    save, memory = AsyncMock(), AsyncMock()
    binding = Mock(side_effect=AssertionError("binding migration must not run"))
    checked = asyncio.Event()

    def load_rule(target):
        checked.set()
        return gameplay._load_rule_for_game(target)

    deps = replace(
        gameplay._gameplay_dependencies, save_instance=save, apply_memory_delta=memory,
        complete_adventure_node=complete, resolve_adventure_binding=binding,
        load_rule_for_game=load_rule,
    )

    async def submit():
        if operation == "automatic":
            return await ruleset_gameplay.resume_authoritative_combat(deps, key, "gm")
        intent = {"type": "adventure.node.complete", "node_id": "node"} if operation == "adventure" else body
        return await ruleset_gameplay.submit_intent(deps, key, "gm", True, intent)

    async def transition():
        assert await instance.advance_round()
        assert instance.state == GameState.ACTIVE_JUDGMENT
        return deepcopy(instance.to_dict())

    if queued:
        # Queue the real narrative phase transition first, while the intent's
        # context still sees ACTIVE_ACTION. FIFO state-lock acquisition makes
        # this a deterministic TOCTOU regression without sleeps in production.
        async with instance._lock:
            advancing = asyncio.create_task(transition())
            await asyncio.sleep(0)
            submission = asyncio.create_task(submit())
            await asyncio.wait_for(checked.wait(), 5)
            assert instance.state == GameState.ACTIVE_ACTION
        before = await asyncio.wait_for(advancing, 5)
        result = await asyncio.wait_for(submission, 5)
    else:
        before = await transition()
        result = await asyncio.wait_for(submit(), 5)
    assert result["ok"] is False
    assert result["error_code" if operation == "automatic" else "code"] == "ROUND_PROCESSING"
    if operation == "automatic":
        assert result["handled"] is True
        assert result["resumed"] is False
    assert instance.to_dict() == before
    complete.assert_not_called()
    binding.assert_not_called()
    save.assert_not_awaited()
    memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_saved_before_judgment_does_not_resume_automatic_writes(web_api):
    api, _lorebook, registry, _llm, _worlds = web_api
    instance, runtime, gameplay, body = _game(registry)
    key = "|".join(instance.game_key)
    assert (await gameplay.ruleset_submit_intent(key, "gm", True, body))["ok"] is True
    before_resume = []
    intent_save, memory = AsyncMock(), AsyncMock()
    intent_deps = replace(gameplay._gameplay_dependencies, save_instance=intent_save, apply_memory_delta=memory)
    turn_deps = replace(
        api._turn_dependencies,
        resume_authoritative_combat=lambda key, uid: ruleset_gameplay.resume_authoritative_combat(intent_deps, key, uid),
    )

    async def save_control(target):
        # A narrative transition wins during the control service's save await,
        # before its normal immediate-resume callback starts.
        assert await target.advance_round()
        assert runtime.next_automatic_intent(target)["actor_id"] == "player:gm"
        before_resume.append(deepcopy(target.to_dict()))

    controls = GameControlService(GameControlDependencies(
        get_instance=registry.get, parse_game_key=gameplay._parse_key,
        save_instance=save_control, load_rule=gameplay._load_rule_for_game,
        resume_after_control_change=lambda key, uid: turns.resume_after_control_change(turn_deps, key, seat_uid=uid),
    ))
    result = await controls.set_player_control(key, "gm", "ai")
    assert result["ok"] is True  # the saved control change remains successful
    assert result["mode"] == "ai"
    assert result["resume"]["error_code"] == "ROUND_PROCESSING"
    assert result["resume"]["resumed"] is False
    assert instance.to_dict() == before_resume[0]
    intent_save.assert_not_awaited()
    memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_waiting_intent_accepts_current_phase_after_judgment_finishes(web_api):
    _api, _lorebook, registry, _llm, _worlds = web_api
    instance, _runtime, gameplay, body = _game(registry)
    instance.state = GameState.ACTIVE_JUDGMENT
    checked = asyncio.Event()

    def load_rule(target):
        checked.set()
        return gameplay._load_rule_for_game(target)

    deps = replace(gameplay._gameplay_dependencies, load_rule_for_game=load_rule)
    async with instance._lock:
        submission = asyncio.create_task(ruleset_gameplay.submit_intent(
            deps, "|".join(instance.game_key), "gm", True, body,
        ))
        await asyncio.wait_for(checked.wait(), 5)
        # Simulates completion before the pending writer acquires the lock.
        instance.state = GameState.ACTIVE_ACTION
    assert (await asyncio.wait_for(submission, 5))["ok"] is True
    assert instance.round_number == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["intent", "adventure"])
@pytest.mark.parametrize("uid, gm, expected", [
    ("", True, "AUTH_REQUIRED"),
    ("outsider", False, "PLAYER_NOT_IN_GAME"),
    ("player", False, None),
    ("owner-session", True, "ROUND_PROCESSING"),
])
async def test_membership_and_gm_authorization_precede_phase(web_api, operation, uid, gm, expected):
    _api, _lorebook, registry, _llm, _worlds = web_api
    instance, _runtime, gameplay, body = _game(registry)
    instance.players["player"] = deepcopy(instance.players["gm"])
    instance.state = GameState.ACTIVE_JUDGMENT
    complete, save = Mock(), AsyncMock()
    deps = replace(gameplay._gameplay_dependencies, complete_adventure_node=complete, save_instance=save)
    if operation == "adventure":
        body = {"type": "adventure.node.complete", "node_id": "node"}
    if expected is None:
        expected = "GM_ONLY" if operation == "adventure" else "ROUND_PROCESSING"
    before = deepcopy(instance.to_dict())
    result = await ruleset_gameplay.submit_intent(deps, "|".join(instance.game_key), uid, gm, body)
    assert result["code"] == expected
    assert instance.to_dict() == before
    complete.assert_not_called()
    save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [state for state in GameState if state != GameState.ACTIVE_JUDGMENT])
async def test_other_phases_still_accept_structured_milestones(web_api, state):
    _api, _lorebook, registry, _llm, _worlds = web_api
    instance, _runtime, gameplay, body = _game(registry)
    instance.state = state
    result = await gameplay.ruleset_submit_intent("|".join(instance.game_key), "gm", True, body)
    assert result["ok"] is True, result
    assert instance.round_number == 4
    assert [entry["round"] for entry in instance.log] == [4]


@pytest.mark.asyncio
async def test_http_conflict_and_queries_during_judgment(web_api):
    _api, _lorebook, registry, _llm, _worlds = web_api
    instance, runtime, _gameplay, body = _game(registry)
    instance.players["player"] = deepcopy(instance.players["gm"])
    instance.state = GameState.ACTIVE_JUDGMENT
    app = _app(registry, runtime)
    complete, save = Mock(), AsyncMock()
    app["api"]._gameplay_dependencies = replace(
        app["api"]._gameplay_dependencies, complete_adventure_node=complete, save_instance=save,
    )
    path = "/api/games/web%7Cjudgment-intent%7Cweb_bot"
    before = deepcopy(instance.to_dict())
    async with TestClient(TestServer(app)) as client:
        for intent in (body, {"type": "adventure.node.complete", "node_id": "node"}):
            rejected = await client.post(f"{path}/intents", headers={"X-Test-User": "gm"}, json=intent)
            assert rejected.status == 409
            assert (await rejected.json())["code"] == "ROUND_PROCESSING"
        denied = await client.post(
            f"{path}/intents", headers={"X-Test-User": "player"},
            json={"type": "adventure.node.complete", "node_id": "node"},
        )
        assert denied.status == 403
        assert (await denied.json())["code"] == "GM_ONLY"
        query = await client.get(f"{path}/available-actions", headers={"X-Test-User": "gm"})
        assert query.status == 200
        assert (await query.json())["ok"] is True
    assert instance.to_dict() == before
    complete.assert_not_called()
    save.assert_not_awaited()
