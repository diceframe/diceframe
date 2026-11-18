"""Usage belongs to HTTP attempts, including failed parsing and negotiation."""
import json
from copy import deepcopy

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.llm.client import LLMClient, LLMToolCallError, LLMToolResponse, ProviderConfig
from src.webui.services.assistant_agent import AgentDependencies, AgentRuntime, AgentTool
from src.webui.services.assistant_agent_framework import AgentProviderUnavailable

TOOL = {"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}


def completion(content="done", *, usage=None, finish="stop", tool=False):
    message = ({"tool_calls": [{"type": "function", "id": "call", "function": {
        "name": "read", "arguments": "{}"}}]} if tool else {"content": content})
    result = {"choices": [{"message": message, "finish_reason": finish}]}
    if usage is not None:
        result["usage"] = usage
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected_total,expected_attempts,expected_unknown", [
    ("forced-empty", 150, 2, 0),
    ("auto-truncated", 130, 3, 1),
    ("native-truncated", 130, 2, 0),
    ("negotiate", 50, 2, 1),
    ("missing", 0, 1, 1),
    ("reported-zero", 0, 1, 0),
    ("split-only", 50, 1, 0),
])
async def test_usage_is_counted_once_per_http_response(monkeypatch, case, expected_total, expected_attempts, expected_unknown):
    async def no_sleep(_):
        pass
    monkeypatch.setattr("src.llm.client.asyncio.sleep", no_sleep)
    requests = []

    async def provider(request):
        body = await request.json()
        requests.append(body)
        number = len(requests)
        if case == "forced-empty" and number == 1:
            return web.json_response(completion(usage={"total_tokens": 100}))
        if case == "auto-truncated" and number == 1:
            return web.json_response({"error": "native unsupported"}, status=400)
        if (case == "auto-truncated" and number == 2) or (case == "native-truncated" and number == 1):
            return web.json_response(completion("partial", usage={"total_tokens": 80}, finish="length"))
        if case == "negotiate" and number == 1:
            return web.json_response({"error": {"param": "max_tokens", "code": "unsupported_parameter",
                                               "message": "use max_completion_tokens"}}, status=400)
        usage = (None if case == "missing" else {"total_tokens": 0} if case == "reported-zero"
                 else {"prompt_tokens": 40, "completion_tokens": 10})
        return web.json_response(completion('{"tool_calls":[{"name":"read","arguments":{}}]}',
                                             tool="tools" in body, usage=usage))

    app = web.Application()
    app.router.add_post("/{path:.*}", provider)
    async with TestServer(app) as server:
        client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "not-logged-key", "model")], "test")
        try:
            result = await client.call_tools("private-prompt", "private-project", tools=[TOOL],
                                             force_tool_choice=case == "forced-empty")
        finally:
            await client.close()
    assert result.total_tokens == expected_total
    assert result.attempted_requests == expected_attempts == len(requests)
    assert result.usage["unknown_usage_requests"] == expected_unknown
    assert result.usage["usage_complete"] is (expected_unknown == 0)
    if case == "split-only":
        assert result.usage["input_tokens"] == 40
        assert result.usage["output_tokens"] == 10


@pytest.mark.asyncio
async def test_all_failed_attempts_retain_known_usage_in_agent_task(tmp_path, monkeypatch, caplog):
    async def no_sleep(_):
        pass
    monkeypatch.setattr("src.llm.client.asyncio.sleep", no_sleep)
    caplog.set_level("INFO", logger="trpg.llm_usage")
    async def provider(request):
        await request.json()
        return web.json_response(completion("private-invalid-output", usage={"total_tokens": 80}, finish="length"))
    app = web.Application()
    app.router.add_post("/{path:.*}", provider)
    async with TestServer(app) as server:
        client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "not-logged-key", "model")], "test")
        agent = AgentRuntime(AgentDependencies(lambda: client, lambda lang: None, (), tmp_path / "agent"))
        events = []
        async def emit(event, value):
            events.append((event, deepcopy(value)))
        try:
            with pytest.raises(AgentProviderUnavailable):
                await agent.run("owner", [{"role": "user", "content": "private-project"}], "en", {}, "", emit)
        finally:
            await client.close()
        sid = next(value["session_id"] for event, value in events if event == "session")
        task = agent.snapshot("owner", sid)["task"]
    assert task["used_tokens"] == 320  # native + three JSON truncations
    assert task["provider_requests"] == 4
    assert task["usage_complete"] is True
    traces = [record for record in caplog.records if record.message.startswith('tool_request_usage ')]
    assert len(traces) == 4
    assert all("private-project" not in record.message and "private-invalid-output" not in record.message
               and "not-logged-key" not in record.message for record in traces)
    assert all('"run_id"' in record.message and '"request_id"' in record.message for record in traces)


@pytest.mark.asyncio
async def test_all_failed_calls_expose_partial_usage_and_real_attempt_count(caplog):
    async def provider(request):
        return web.json_response({'error': 'provider-echoed-private-content', 'usage': {'total_tokens': 20}}, status=403)
    app = web.Application()
    app.router.add_post("/{path:.*}", provider)
    async with TestServer(app) as server:
        client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "k", "model")], "test")
        try:
            with pytest.raises(LLMToolCallError) as caught:
                await client.call_tools("system", "data", tools=[TOOL])
        finally:
            await client.close()
    assert caught.value.usage["total_tokens"] == 40
    assert caught.value.usage["attempted_requests"] == 2
    assert caught.value.status_codes == frozenset({403})
    assert 'provider-echoed-private-content' not in str(caught.value) + caplog.text


@pytest.mark.asyncio
async def test_anthropic_usage_includes_cache_input_and_truncated_native_reply():
    requests = []

    async def provider(request):
        body = await request.json()
        requests.append(body)
        if 'tools' in body:
            return web.json_response({'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': 'partial'}],
                                      'usage': {'input_tokens': 10, 'output_tokens': 20,
                                                'cache_read_input_tokens': 30, 'cache_creation_input_tokens': 40}})
        return web.json_response({'stop_reason': 'end_turn', 'content': [{'type': 'text',
            'text': '{"tool_calls":[{"name":"read","arguments":{}}]}'}],
            'usage': {'input_tokens': 10, 'output_tokens': 20}})

    app = web.Application()
    app.router.add_post('/{path:.*}', provider)
    async with TestServer(app) as server:
        client = LLMClient([ProviderConfig('test', str(server.make_url('/')), 'k', 'model', api_format='anthropic')], 'test')
        try:
            result = await client.call_tools('system', 'data', tools=[TOOL], force_tool_choice=False)
        finally:
            await client.close()
    assert result.total_tokens == 130
    assert result.usage['input_tokens'] == 90 and result.usage['output_tokens'] == 40
    assert result.attempted_requests == len(requests) == 2


@pytest.mark.asyncio
async def test_creator_trace_correlates_repeat_snapshot_with_bounded_model_history(tmp_path, caplog):
    caplog.set_level('INFO', logger='trpg.llm_usage')
    calls = iter([{'name': 'creator_project_read', 'arguments': {}}] * 2)

    class FakeProvider:
        async def call_tools(self, *args, **kwargs):
            call = next(calls, None)
            return LLMToolResponse([call] if call else [], 10, 'test', content='Done')

    async def read(args, owner):
        return {'ok': True, 'project': {'id': 'private-project-id', 'revision': 3, 'digest': 'same',
                                        'files': {'private-path.json': 'private-file' * 2000}}}

    tool = AgentTool('creator_project_read', 'Read', {'type': 'object', 'additionalProperties': False}, read)
    agent = AgentRuntime(AgentDependencies(FakeProvider, lambda lang: None, (tool,), tmp_path / 'agent'))

    async def emit(event, value):
        pass

    await agent.run('owner', [{'role': 'user', 'content': 'private-prompt'}], 'en', {}, '', emit)
    traces = [record.message for record in caplog.records if record.name == 'trpg.llm_usage']
    for private in ('private-project-id', 'private-path.json', 'private-file', 'private-prompt'):
        assert all(private not in trace for trace in traces)
    snapshots = [json.loads(trace.split(' ', 1)[1]) for trace in traces if trace.startswith('creator_snapshot_read ')]
    histories = [json.loads(trace.split(' ', 1)[1]) for trace in traces if trace.startswith('agent_request_history ')]
    assert [item['snapshot_reads'] for item in snapshots] == [1, 2]
    assert [item['creator_observations'] for item in histories] == [0, 1, 2]
    assert len({item['run_id'] for item in snapshots + histories}) == 1
    assert snapshots[0]['ui_chars'] > snapshots[0]['observation_chars'] > 0
