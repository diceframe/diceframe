"""Exercise the real request paths against a synthetic strict Chat endpoint."""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
import aiohttp

from src.llm.client import LLMClient, LLMToolCallError, ProviderConfig
from src.webui.services.generation import ConnectionDependencies, test_connection as connection_test


class Response:
    headers = {}
    history = ()
    request_info = SimpleNamespace(real_url="https://synthetic.invalid/chat/completions")

    def __init__(self, error=None):
        self.error = error
        self.status = 400 if error else 200
        self.content = self.stream()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return json.dumps({"error": self.error})

    async def json(self):
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": "Final answer", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "example", "arguments": "{}"}}],
        }}], "usage": {"total_tokens": 7}}

    async def stream(self):
        yield b'data: {"choices":[{"delta":{"content":"Final answer"},"finish_reason":"stop"}],"usage":{"total_tokens":7}}\n'
        yield b'data: [DONE]\n'


class StrictSession:
    def __init__(self, reject_temperature=False):
        self.calls = []
        self.reject_temperature = reject_temperature

    def post(self, url, *, json, **kwargs):
        self.calls.append({"url": url, "body": deepcopy(json), **kwargs})
        if "max_tokens" in json:
            return Response({"param": "max_tokens", "code": "unsupported_parameter", "message": "Use max_completion_tokens instead"})
        if self.reject_temperature and "temperature" in json:
            return Response({"param": "temperature", "code": "unsupported_value", "message": "Only the default value is supported"})
        return Response()


def client_with_session(session):
    provider = ProviderConfig("strict", "https://synthetic.invalid/v1", "synthetic", "gpt-5")
    client = LLMClient([provider], "strict")

    async def get_session():
        return session

    client._get_session = get_session
    return client, provider


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["normal", "stream", "tools", "connection"])
async def test_strict_parameter_negotiation_is_shared_by_all_chat_paths(path, monkeypatch):
    monkeypatch.setattr("src.llm.client.BASE_DELAY", 0)
    session = StrictSession(reject_temperature=True)
    client, provider = client_with_session(session)
    if path == "normal":
        result = await client._call_openai_compatible(provider, "system", "input", .7, 123)
        assert result.narration == "Final answer"
    elif path == "stream":
        deltas = []
        async def delta(text):
            deltas.append(text)
        result = await client.call_stream("system", "input", max_tokens=123, on_delta=delta)
        assert result.narration == "Final answer"
        assert "".join(deltas) == "Final answer"
    elif path == "tools":
        result = await client.call_tools("system", "input", max_tokens=123, tools=[{"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}])
        assert result.native_tools is True
        assert len(result.tool_calls) == 1
    else:
        result = await connection_test(ConnectionDependencies(client, lambda: {}), provider.base_url, provider.api_key, provider.model_name)
        assert result["ok"] is True
    final = session.calls[-1]["body"]
    assert "max_tokens" not in final
    assert final["max_completion_tokens"] == (10 if path == "connection" else 123)
    assert "temperature" not in final
    assert len(session.calls) <= 3


@pytest.mark.asyncio
async def test_negotiation_is_cached_for_the_same_endpoint_and_model():
    session = StrictSession()
    client, provider = client_with_session(session)
    await client._call_openai_compatible(provider, "system", "first", .7, 100)
    await client._call_openai_compatible(provider, "system", "second", .7, 200)
    assert len(session.calls) == 3
    assert session.calls[-1]["body"]["max_completion_tokens"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["normal", "stream", "tools"])
async def test_explicit_sampling_overrides_reach_every_chat_path(path):
    session = StrictSession()
    old_client, provider = client_with_session(session)
    client = LLMClient([provider], "strict", generation_options={"temperature": .25, "frequency_penalty": .4})
    client._get_session = old_client._get_session
    if path == "normal":
        await client._call_openai_compatible(provider, "system", "input", .7, 123)
    elif path == "stream":
        async def delta(_text):
            pass
        await client.call_stream("system", "input", max_tokens=123, on_delta=delta)
    else:
        await client.call_tools("system", "input", max_tokens=123, tools=[{"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}])
    assert session.calls[-1]["body"]["temperature"] == .25
    assert session.calls[-1]["body"]["frequency_penalty"] == .4
    assert session.calls[-1]["body"]["max_completion_tokens"] == 123


@pytest.mark.asyncio
async def test_explicit_sampling_is_never_silently_dropped_when_rejected():
    session = StrictSession(reject_temperature=True)
    old_client, provider = client_with_session(session)
    client = LLMClient([provider], "strict", generation_options={"temperature": .25})
    client._get_session = old_client._get_session
    with pytest.raises(aiohttp.ClientResponseError):
        await client._call_openai_compatible(provider, "system", "input", .7, 123)
    assert len(session.calls) == 2
    assert all(call["body"]["temperature"] == .25 for call in session.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error", [
    (400, {"param": "max_tokens", "code": "invalid_value", "message": "Value exceeds the model budget"}),
    (400, {"param": "messages", "code": "invalid_request_error", "message": "Invalid messages"}),
    (401, {"param": "max_tokens", "code": "unsupported_parameter", "message": "Use max_completion_tokens"}),
])
async def test_unrelated_rejections_are_not_rewritten_or_retried(status, error):
    class RejectedSession(StrictSession):
        def post(self, url, *, json, **kwargs):
            self.calls.append(deepcopy(json))
            response = Response(error)
            response.status = status
            return response
    session = RejectedSession()
    client, provider = client_with_session(session)
    with pytest.raises(aiohttp.ClientResponseError) as rejected:
        await client._call_openai_compatible(provider, "system", "input", .7, 100)
    assert rejected.value.status == status
    assert len(session.calls) == 1
    assert session.calls[0]["max_tokens"] == 100


@pytest.mark.asyncio
async def test_anthropic_keeps_its_native_token_contract_even_on_a_chat_shaped_error():
    class RejectedSession(StrictSession):
        def post(self, url, *, json, **kwargs):
            self.calls.append(deepcopy(json))
            return Response({"param": "max_tokens", "code": "unsupported_parameter", "message": "Use max_completion_tokens"})
    session = RejectedSession()
    client, provider = client_with_session(session)
    with pytest.raises(aiohttp.ClientResponseError):
        await client._call_anthropic(provider, "system", "input", .7, 100)
    assert len(session.calls) == 1
    assert session.calls[0]["max_tokens"] == 100


EXAMPLE_TOOL = {"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}


class ReasoningStrictSession:
    """Rejects ``reasoning_effort`` outright and records every request body."""

    def __init__(self):
        self.calls = []

    def post(self, url, *, json, **kwargs):
        self.calls.append(deepcopy(json))
        if "reasoning_effort" in json:
            return Response({"param": "reasoning_effort", "code": "unsupported_parameter",
                             "message": "Unsupported parameter: 'reasoning_effort' is not supported with this model."})
        return Response()


@pytest.mark.asyncio
async def test_automatic_tool_default_is_negotiated_away_and_remembered():
    session = ReasoningStrictSession()
    client, _ = client_with_session(session)
    result = await client.call_tools("system", "input", max_tokens=123, tools=[EXAMPLE_TOOL])
    assert result.native_tools is True and len(result.tool_calls) == 1
    assert session.calls[0]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in session.calls[1]
    await client.call_tools("system", "again", max_tokens=123, tools=[EXAMPLE_TOOL])
    assert len(session.calls) == 3
    assert "reasoning_effort" not in session.calls[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,failure", [
    ("normal", aiohttp.ClientResponseError), ("stream", RuntimeError), ("tools", LLMToolCallError),
])
async def test_configured_level_is_never_silently_dropped_when_rejected(path, failure, monkeypatch):
    monkeypatch.setattr("src.llm.client.BASE_DELAY", 0)
    session = ReasoningStrictSession()
    old_client, _ = client_with_session(session)
    provider = ProviderConfig("strict", "https://synthetic.invalid/v1", "synthetic", "gpt-5", reasoning_effort="high")
    client = LLMClient([provider], "strict")
    client._get_session = old_client._get_session
    with pytest.raises(failure):
        if path == "normal":
            await client._call_openai_compatible(provider, "system", "input", .7, 123)
        elif path == "stream":
            await client.call_stream("system", "input", max_tokens=123)
        else:
            await client.call_tools("system", "input", max_tokens=123, tools=[EXAMPLE_TOOL])
    assert session.calls
    assert all(call["reasoning_effort"] == "high" for call in session.calls)


@pytest.mark.asyncio
async def test_endpoint_memory_of_the_automatic_default_does_not_strip_a_configured_level():
    session = ReasoningStrictSession()
    client, provider = client_with_session(session)
    await client.call_tools("system", "input", max_tokens=123, tools=[EXAMPLE_TOOL])
    assert len(session.calls) == 2
    provider.reasoning_effort = "low"
    with pytest.raises(LLMToolCallError):
        await client.call_tools("system", "input", max_tokens=123, tools=[EXAMPLE_TOOL])
    assert all(call["reasoning_effort"] == "low" for call in session.calls[2:])
