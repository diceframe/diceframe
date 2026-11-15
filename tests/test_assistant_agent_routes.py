import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.routes.assistant_agent import register_agent


def sse_events(text):
    """Parse an SSE body into {event_name: data} pairs (last value wins)."""
    events = {}
    name = None
    for line in text.splitlines():
        if line.startswith("event: "):
            name = line[len("event: "):]
        elif line.startswith("data: ") and name:
            events[name] = line[len("data: "):]
            name = None
    return events


async def client_for(owner, api):
    @web.middleware
    async def auth(request, handler):
        request["owner_authenticated"] = owner
        request["user_id"] = "alice"
        return await handler(request)
    app = web.Application(middlewares=[auth])
    app["api"] = api
    register_agent(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_every_agent_endpoint_requires_owner():
    api = SimpleNamespace(assistant_agent_run=AsyncMock(), assistant_agent_confirm=AsyncMock(), assistant_agent_resume=AsyncMock(),
        assistant_agent_review=AsyncMock(), assistant_agent_session=lambda *a: {})
    client = await client_for(False, api)
    try:
        for path in ("run", "confirm", "resume", "review", "plan", "authorization", "cancel"):
            response = await client.post(f"/api/assistant/agent/{path}", json={})
            assert response.status == 401
        response = await client.get("/api/assistant/agent/session/" + "a" * 32)
        assert response.status == 401
        response = await client.get('/api/assistant/agent/content-operations')
        assert response.status == 401
        api.assistant_agent_run.assert_not_called()
        api.assistant_agent_confirm.assert_not_called()
        api.assistant_agent_resume.assert_not_called()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_resume_rejects_target_replacement_and_invalid_digests_before_dispatch():
    api = SimpleNamespace(assistant_agent_resume=AsyncMock())
    client = await client_for(True, api)
    valid = {'session_id': 'a' * 32, 'approval_id': 'b' * 32, 'plan_digest': 'sha256:' + 'c' * 64}
    try:
        for body in ({**valid, 'arguments': {'targets': ['outside']}},
                     {**valid, 'plan_digest': 'sha256:invalid'}, {**valid, 'session_id': 'not-a-session'}):
            response = await client.post('/api/assistant/agent/resume', json=body)
            assert response.status == 400
        api.assistant_agent_resume.assert_not_called()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_resume_route_uses_server_owner_and_real_checkpointed_execution(tmp_path):
    from copy import deepcopy
    from tests.test_assistant_prepared_actions import runtime

    agent, _, writes = runtime(tmp_path, interrupt=True)
    sid = agent.authorize('alice', '', 'task', {}, ['lorebook'], 8)['session_id']
    async def emit(*_):
        pass
    await agent.run('alice', [{'role': 'user', 'content': 'Update both targets'}], 'en', {}, sid, emit)
    plan = deepcopy(agent.snapshot('alice', sid)['pending'])
    assert writes == ['a']
    client = await client_for(True, SimpleNamespace(assistant_agent_resume=agent.resume))
    try:
        response = await client.post('/api/assistant/agent/resume', json={
            'session_id': sid, 'approval_id': plan['approval_id'], 'plan_digest': plan['plan_digest']})
        assert response.status == 200
        events = sse_events(await response.text())
        assert json.loads(events['confirm_result'])['result']['status'] == 'completed'
        assert 'done' in events
        assert writes == ['a', 'b']
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_agent_route_rejects_context_and_confirmation_spoofing_before_service():
    api = SimpleNamespace(assistant_agent_run=AsyncMock(), assistant_agent_confirm=AsyncMock())
    client = await client_for(True, api)
    try:
        response = await client.post("/api/assistant/agent/run", json={"messages": [{"role": "user", "content": "hi"}],
            "context": {"owner_authenticated": True, "api_key": "secret"}})
        assert response.status == 400
        response = await client.post("/api/assistant/agent/confirm", json={"session_id": "a" * 32,
            "approval_id": "b" * 32, "plan_digest": "sha256:" + "c" * 64, "approved": "true", "arguments": {}})
        assert response.status == 400
        api.assistant_agent_confirm.assert_not_called()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_run_stream_uses_server_principal_and_structured_events():
    seen = []
    async def run(owner, messages, language, context, sid, emit):
        seen.append((owner, language, context))
        await emit("session", {"session_id": "a" * 32})
        await emit("delta", {"delta": "hello"})
    client = await client_for(True, SimpleNamespace(assistant_agent_run=run))
    try:
        response = await client.post("/api/assistant/agent/run", json={"messages": [{"role": "user", "content": "hi"}],
            "language": "ja", "context": {"route": "creator"}})
        assert response.status == 200
        text = await response.text()
        assert "event: session" in text and "event: done" in text and "hello" in text
        assert seen == [("alice", "ja", {"route": "creator"})]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_model_retry_exhaustion_streams_specific_error_without_internal_details():
    from src.webui.services.assistant_agent_framework import AgentModelResponseInvalid

    async def run(*args):
        raise AgentModelResponseInvalid("zh-CN")

    client = await client_for(True, SimpleNamespace(assistant_agent_run=run))
    try:
        response = await client.post("/api/assistant/agent/run", json={
            "messages": [{"role": "user", "content": "随便想一个冒险包"}], "language": "zh-CN"})
        text = await response.text()
        assert "event: error" in text and "MODEL_RESPONSE_INVALID" in text
        assert "工具调用" in text and "草稿" in text
        assert "AGENT_FAILED" not in text and "invalid_provider_response" not in text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_first_stream_sets_session_cookie_before_headers_so_confirmation_keeps_identity(tmp_path):
    from src.webui.session import SessionManager, session_middleware
    owners = []
    @web.middleware
    async def auth(request, handler):
        request["owner_authenticated"] = True
        return await handler(request)
    async def run(owner, messages, language, context, sid, emit):
        owners.append(owner)
        await emit("session", {"session_id": "a" * 32})
    async def confirm(owner, *args, **kwargs):
        owners.append(owner)
        return {"ok": True}
    app = web.Application(middlewares=[session_middleware, auth])
    app["session_manager"] = SessionManager(tmp_path)
    app["api"] = SimpleNamespace(assistant_agent_run=run, assistant_agent_confirm=confirm)
    register_agent(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        first = await client.post("/api/assistant/agent/run", json={"messages": [{"role": "user", "content": "hi"}]})
        await first.read()
        assert "trpg_session" in first.cookies
        await client.post("/api/assistant/agent/confirm", json={"session_id": "a" * 32, "approval_id": "b" * 32,
            "plan_digest": "sha256:" + "c" * 64, "approved": True})
        assert owners[0] == owners[1]
    finally:
        await client.close()
