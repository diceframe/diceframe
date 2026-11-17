"""Adversarial coverage for the AgentRuntime authorization and write fences."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from src.llm.client import LLMToolResponse

from src.application_actions import ActionRegistry
from src.webui.services.assistant_agent import AgentDependencies, AgentRuntime, AgentTool
from src.webui.services.assistant_authorization import make_authorization, permits
from tests.test_assistant_agent import Planner


def action(name="edit", *, handler=None, domain="games", effect="write", automatic=True, version=1):
    schema = {
        "type": "object",
        "properties": {
            "game_key": {"type": "string", "minLength": 1},
            "world_id": {"type": "string", "minLength": 1},
            "book_id": {"type": "string", "minLength": 1},
            "value": {"type": "string"},
        },
        "required": ["game_key", "value"],
        "additionalProperties": False,
    }
    return AgentTool(name, name, schema, handler or (lambda args, owner: {"ok": True}),
                     mutation=True, domain=domain, effect=effect, automatic=automatic,
                     version=version, guard=lambda args, owner: "stable")


def build(tmp_path, planner, actions, *, clock=lambda: 100):
    return AgentRuntime(AgentDependencies(
        llm_client=lambda: planner,
        configuration_error=lambda language: None,
        tools=(), registry=ActionRegistry(actions), storage_dir=tmp_path,
        clock=clock,
    ))


async def run(agent, session_id="", owner="owner", text="do it", context=None):
    events = []

    async def emit(name, payload):
        events.append((name, deepcopy(payload)))

    await agent.run(owner, [{"role": "user", "content": text}], "en", context or {"route": "play"}, session_id, emit)
    return events


def approval(events):
    return next(payload for name, payload in events if name == "approval")


@pytest.mark.asyncio
async def test_task_grant_expiry_and_every_scoped_target_field_are_fail_closed(tmp_path):
    calls = []
    async def handler(args, owner):
        calls.append(args)
        return {"ok": True}
    now = [100]
    tool = action(handler=handler)
    planner = Planner({"tool": "edit", "arguments": {
        "game_key": "g", "world_id": "w", "book_id": "b", "value": "x"}},
        {"tool": "respond", "answer": "expired"})
    agent = build(tmp_path, planner, [tool], clock=lambda: now[0])
    grant = agent.authorize("owner", "", "task", {"game_key": "g", "world_id": "w", "book_id": "b"}, ["games"], 8)
    now[0] = 3701
    events = await run(agent, grant["session_id"])
    assert calls == []
    assert agent.snapshot("owner", grant["session_id"])["authorization"] is None

    grant = {"mode": "task", "domains": ["games"], "scope": {
        "game_key": "g", "world_id": "w", "book_id": "b"}}
    assert permits(grant, tool, {"game_key": "g", "world_id": "w", "book_id": "b", "value": "x"})
    for key in ("game_key", "world_id", "book_id"):
        target = {"game_key": "g", "world_id": "w", "book_id": "b", "value": "x"}
        target[key] = "different"
        assert not permits(grant, tool, target)
    assert not permits(grant, tool, {"game_key": "g", "world_id": "w", "value": "x"})


def test_routine_grants_never_permit_disclosure_or_external_actions():
    grant = {"mode": "routine", "domains": ["games"], "scope": {}}
    args = {"game_key": "g", "value": "x"}
    assert not permits(grant, action(effect="disclosure", automatic=True), args)
    assert not permits(grant, action(effect="external", automatic=True), args)
    assert permits(grant, action(effect="write", automatic=True), args)


@pytest.mark.asyncio
async def test_dynamic_registered_action_is_discovered_and_executed(tmp_path):
    calls = []
    async def handler(args, owner):
        calls.append((owner, args["value"]))
        return {"ok": True, "value": args["value"]}
    planner = Planner(
        {"tool": "search_capabilities", "arguments": {"query": "edit", "domain": "games"}},
        {"tool": "describe_capability", "arguments": {"name": "edit"}},
        {"tool": "edit", "arguments": {"game_key": "g", "value": "dynamic"}},
        {"tool": "respond", "answer": "done"},
    )
    agent = build(tmp_path, planner, [action(handler=handler)])
    grant = agent.authorize("owner", "", "task", {"game_key": "g"}, ["games"], 8)
    events = await run(agent, grant["session_id"])
    assert calls == [("owner", "dynamic")]
    assert not any(name == "approval" for name, _ in events)


@pytest.mark.asyncio
async def test_unregistered_or_replaced_action_invalidates_pending_plan(tmp_path):
    async def forbidden(args, owner):
        pytest.fail("invalidated action must not execute")
    registry = ActionRegistry([action(handler=forbidden)])
    agent = AgentRuntime(AgentDependencies(
        llm_client=lambda: Planner({"tool": "edit", "arguments": {"game_key": "g", "value": "x"}}),
        configuration_error=lambda language: None, tools=(), registry=registry, storage_dir=tmp_path,
    ))
    events = await run(agent)
    plan = approval(events)
    registry.unregister("edit")
    with pytest.raises(ValueError, match="unavailable"):
        await agent.confirm("owner", next(payload["session_id"] for name, payload in events if name == "session"),
                            plan["approval_id"], plan["plan_digest"], True)


@pytest.mark.asyncio
async def test_cancel_while_provider_awaits_aborts_model_loop(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()

    class WaitingPlanner:
        async def call_tools(self, system, user, **kwargs):
            entered.set()
            await release.wait()
            return LLMToolResponse(tool_calls=[{"name": "final_result", "arguments": {"answer": "late"}}],
                                   total_tokens=0, provider_used="test")

    agent = build(tmp_path, WaitingPlanner(), [])
    task = asyncio.create_task(run(agent))
    await entered.wait()
    session_id = next(iter(agent.sessions))
    assert agent.cancel("owner", session_id)["cancelled"]
    release.set()
    await task
    assert agent.snapshot("owner", session_id)["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_at_tool_start_discards_persisted_undispatched_plan(tmp_path):
    calls, proposed = [], []
    async def write(args, owner):
        calls.append(args)
        return {"ok": True}
    agent = build(tmp_path, Planner({"tool": "edit", "arguments": {"game_key": "g", "value": "x"}}),
                  [action(handler=write)])
    grant = agent.authorize("owner", "", "task", {"game_key": "g"}, ["games"], 8)
    sid = grant['session_id']
    async def emit(name, payload):
        if name == 'tool_start':
            proposed.append(deepcopy(agent.snapshot('owner', sid)['pending']))
            agent.cancel('owner', sid)
    await agent.run('owner', [{'role': 'user', 'content': 'edit'}], 'en', {'route': 'play'}, sid, emit)
    await agent.close()
    assert proposed and proposed[0]['tool'] == 'edit'
    assert calls == []
    restored = build(tmp_path, Planner(), [action(handler=write)]).snapshot('owner', sid)
    assert restored['pending'] is None and restored['status'] == 'cancelled'
    assert restored['task']['completed_steps'] == 0
    with pytest.raises(ValueError, match='stale'):
        await agent.confirm('owner', sid, proposed[0]['approval_id'], proposed[0]['plan_digest'], True)


@pytest.mark.asyncio
async def test_cancel_while_dispatch_authorization_waits_never_calls_handler(tmp_path):
    from dataclasses import replace
    entered, release = asyncio.Event(), asyncio.Event()
    calls, authorization_checks = [], []
    async def write(args, owner):
        calls.append(args)
        return {'ok': True}
    async def authorize(args, owner):
        authorization_checks.append(owner)
        if len(authorization_checks) == 2:
            entered.set()
            await release.wait()
        return True
    tool = replace(action(handler=write), authorize=authorize)
    agent = build(tmp_path, Planner({'tool': 'edit', 'arguments': {'game_key': 'g', 'value': 'x'}}), [tool])
    grant = agent.authorize('owner', '', 'task', {'game_key': 'g'}, ['games'], 8)
    sid = grant['session_id']
    running = asyncio.create_task(run(agent, sid))
    await asyncio.wait_for(entered.wait(), timeout=5)
    agent.cancel('owner', sid)
    await running
    release.set()
    await agent.close()
    assert calls == []
    assert agent.snapshot('owner', sid)['pending'] is None


@pytest.mark.asyncio
async def test_cancel_during_authoritative_write_keeps_receipt_and_blocks_next_write(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def write(args, owner):
        calls.append(args)
        entered.set()
        await release.wait()
        return {"ok": True}
    planner = Planner({"tool": "edit", "arguments": {"game_key": "g", "value": "x"}})
    agent = build(tmp_path, planner, [action(handler=write)])
    grant = agent.authorize("owner", "", "task", {"game_key": "g"}, ["games"], 8)
    task = asyncio.create_task(run(agent, grant["session_id"]))
    await entered.wait()
    agent.cancel("owner", grant["session_id"])
    release.set()
    await task
    snapshot = agent.snapshot("owner", grant["session_id"])
    assert len(calls) == 1
    assert len(snapshot["receipts"]) == 1
    assert snapshot["receipts"][0]["response"]["ok"]


@pytest.mark.asyncio
async def test_recovery_required_result_stops_replay_and_preserves_uncertain_plan(tmp_path):
    calls = []
    async def uncertain(args, owner):
        calls.append(args)
        return {"ok": False, "recovery_required": True}
    agent = build(tmp_path, Planner({"tool": "edit", "arguments": {"game_key": "g", "value": "x"}}),
                  [action(handler=uncertain)])
    events = await run(agent)
    plan = approval(events)
    sid = next(payload["session_id"] for name, payload in events if name == "session")
    result = await agent.confirm("owner", sid, plan["approval_id"], plan["plan_digest"], True)
    assert result["recovery_required"]
    assert len(calls) == 1
    with pytest.raises(ValueError, match="uncertain"):
        await agent.confirm("owner", sid, plan["approval_id"], plan["plan_digest"], True)


@pytest.mark.asyncio
async def test_pending_plan_owner_reload_and_concurrent_confirm_are_once_only(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def write(args, owner):
        calls.append(args)
        entered.set()
        await release.wait()
        return {"ok": True}
    planner = Planner({"tool": "edit", "arguments": {"game_key": "g", "value": "x"}})
    agent = build(tmp_path, planner, [action(handler=write)])
    events = await run(agent)
    plan = approval(events)
    sid = next(payload["session_id"] for name, payload in events if name == "session")
    restored = build(tmp_path, Planner(), [action(handler=write)])
    first = asyncio.create_task(restored.confirm("owner", sid, plan["approval_id"], plan["plan_digest"], True))
    await entered.wait()
    second = asyncio.create_task(restored.confirm("owner", sid, plan["approval_id"], plan["plan_digest"], True))
    release.set()
    assert await first == await second
    assert len(calls) == 1


def test_project_scope_is_supported_for_creator_agent_grants():
    grant = make_authorization("task", {"project_id": "project-demo"}, ["creator"], 4, 100)
    creator_action = action(name="creator_project_update", domain="creator")
    assert permits(grant, creator_action, {"project_id": "project-demo"})
    assert not permits(grant, creator_action, {"project_id": "project-other"})


def test_reauthorizing_same_scope_creates_a_distinct_grant_identity():
    first = make_authorization("task", {"project_id": "project-demo"}, ["creator"], 4, 100)
    second = make_authorization("task", {"project_id": "project-demo"}, ["creator"], 4, 100)
    assert first["grant_id"] != second["grant_id"]
