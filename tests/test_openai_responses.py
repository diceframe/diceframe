"""Contracts for official OpenAI routing, tool planning and streamed GM output."""
from __future__ import annotations

from copy import deepcopy
import asyncio
import json
from types import SimpleNamespace

import pytest

from src.llm.client import LLMClient, OutputTruncatedError, ProviderConfig
from src.llm.openai_responses import (
    OpenAIResponsesTransport, ResponsesIncompleteError, ResponsesStreamInterruptedError,
    parse_response, responses_url,
)
from src.llm.tools import DICE_CHECKS_TOOL


def message(text="船靠近港口。", *, phase=None):
    item = {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text}]}
    if phase:
        item["phase"] = phase
    return item


def completed(*items):
    return {"status": "completed", "output": list(items), "usage": {
        "input_tokens": 30, "output_tokens": 12, "total_tokens": 42,
        "input_tokens_details": {"cached_tokens": 10},
        "output_tokens_details": {"reasoning_tokens": 8},
    }}


def function_call(arguments, name="dice_checks"):
    return {"type": "function_call", "name": name, "call_id": "call_123", "status": "completed",
            "arguments": json.dumps(arguments, ensure_ascii=False)}


class Response:
    headers = {}
    request_info = SimpleNamespace(real_url="https://api.openai.com/v1/responses")
    history = ()

    def __init__(self, payload=None, events=(), status=200):
        self.payload = payload
        self.events = events
        self.status = status
        self.content = SimpleNamespace(iter_any=self.iter_content)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def json(self):
        return self.payload

    async def text(self):
        return json.dumps(self.payload)

    async def iter_content(self):
        data = b"".join(("event: " + event["type"] + "\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
                        for event in self.events)
        # Network chunks are not SSE frame or UTF-8 character boundaries.
        for offset in range(0, len(data), 7):
            yield data[offset:offset + 7]


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def client_with(monkeypatch, *responses, model="gpt-6.2-astra", options=None, fallback=False):
    provider = ProviderConfig("official", "https://api.openai.com/v1", "test-key", model)
    providers = [provider]
    if fallback:
        providers.append(ProviderConfig("other", "https://api.example.com/v1", "test", "other", fallback=True))
    client = LLMClient(providers, "official", proxy_url="http://proxy.example:8080", request_timeout_seconds=240,
                       generation_options=options)
    session = Session(*responses)
    async def get_session():
        return session
    monkeypatch.setattr(client, "_get_session", get_session)
    return client, session, provider


@pytest.mark.parametrize("base", ["https://api.openai.com", "https://api.openai.com/v1/",
                                 "https://api.openai.com/v1/chat/completions", "https://api.openai.com/v1/responses"])
def test_official_gpt6_routes_without_changing_saved_provider(base):
    assert responses_url(base, "gpt-6.2-astra") == "https://api.openai.com/v1/responses"


@pytest.mark.parametrize("base,model,fmt", [
    ("https://api.openai.com.evil.test/v1", "gpt-6.2-astra", "openai"),
    ("http://api.openai.com/v1", "gpt-6.2-astra", "openai"),
    ("https://api.openai.com:8443/v1", "gpt-6.2-astra", "openai"),
    ("https://user@api.openai.com/v1", "gpt-6.2-astra", "openai"),
    ("https://api.openai.com/v1?key=x", "gpt-6.2-astra", "openai"),
    ("https://api.openai.com/v1/unknown", "gpt-6.2-astra", "openai"),
    ("https://api.moonshot.cn/v1", "kimi-k3.1-pro-highspeed", "openai"),
    ("https://api.openai.com/v1", "gpt-7-astra", "openai"),
    ("https://api.openai.com/v1", "gpt-6.2-astra", "anthropic"),
])
def test_route_does_not_guess_other_providers_or_models(base, model, fmt):
    assert responses_url(base, model, fmt) is None


@pytest.mark.asyncio
async def test_sol_text_and_json_use_stateless_responses_with_preserved_budget(monkeypatch):
    client, session, _ = client_with(monkeypatch, Response(completed(message('{"ok":true}'))),
                                      model="gpt-6.2-sol", options={"temperature": 1})
    response = await client.call("stable instructions", "return JSON", max_tokens=8192, json_mode=True)
    request = session.calls[0]
    assert request["url"].endswith("/responses")
    assert request["proxy"] == "http://proxy.example:8080"
    assert request["timeout"].total == 240
    body = request["json"]
    assert body["store"] is False
    assert body["reasoning"] == {"effort": "medium"}
    assert body["input"] == [{"role": "developer", "content": "stable instructions"}, {"role": "user", "content": "return JSON"}]
    assert body["text"] == {"format": {"type": "json_object"}}
    assert body["max_output_tokens"] == 8192
    assert not {"temperature", "top_p", "previous_response_id", "conversation", "instructions"} & body.keys()
    assert response.content == '{"ok":true}'
    assert response.provider_used == "official" and response.total_tokens == 42
    assert response.token_budget_initial == response.token_budget_used == 8192


@pytest.mark.parametrize("options", [{"temperature": .7}, {"top_p": 1}, {"frequency_penalty": 0}, {"presence_penalty": .2}])
def test_unsupported_explicit_sampling_is_not_silently_discarded(options):
    with pytest.raises(ValueError, match="sampling"):
        OpenAIResponsesTransport(options).body("gpt-6.2-sol", "system", "user", 8192)


@pytest.mark.asyncio
async def test_astra_real_dice_schema_is_strict_and_optional_nulls_restore_omission(monkeypatch):
    original = deepcopy(DICE_CHECKS_TOOL)
    args = {"checks": [{"player": "p1", "attribute": "str", "modifier": None, "kind": None}],
            "economy": None, "world_requirements": None, "overreach": None, "time_advance": None}
    # Only fields present in the real schema may be populated by the model.
    args = {key: value for key, value in args.items() if key in original["function"]["parameters"]["properties"]}
    client, session, _ = client_with(monkeypatch, Response(completed(function_call(args))))
    result = await client.call_tools("system", "actions", tools=[DICE_CHECKS_TOOL], max_tokens=4096)
    assert result.native_tools is True and result.total_tokens == 42
    assert result.tool_calls == [{"name": "dice_checks", "call_id": "call_123",
                                  "arguments": {"checks": [{"player": "p1", "attribute": "str"}]}}]
    tool = session.calls[0]["json"]["tools"][0]
    assert tool["strict"] is True
    assert set(tool["parameters"]["required"]) == set(tool["parameters"]["properties"])
    assert session.calls[0]["json"]["tool_choice"] == {"type": "function", "name": "dice_checks"}
    assert session.calls[0]["json"]["parallel_tool_calls"] is False
    assert DICE_CHECKS_TOOL == original


def test_open_maps_keep_schema_and_nullable_fields_keep_explicit_null():
    transport = OpenAIResponsesTransport({})
    open_tool = {"type": "function", "function": {"name": "map", "parameters": {
        "type": "object", "additionalProperties": {"type": "integer"}}}}
    sent = transport.body("gpt-6.2-astra", "s", "u", 1000, tools=[open_tool])["tools"][0]
    assert sent["strict"] is False and sent["parameters"] == open_tool["function"]["parameters"]
    nullable = {"type": "function", "function": {"name": "optional", "parameters": {
        "type": "object", "additionalProperties": False, "properties": {
            "kept": {"type": ["string", "null"]}, "omitted": {"type": "string"}}, "required": []}}}
    result = parse_response(completed(function_call({"kept": None, "omitted": None}, "optional")), [nullable])
    assert result.tool_calls[0]["arguments"] == {"kept": None}


@pytest.mark.asyncio
async def test_forced_tool_rejects_different_registered_function(monkeypatch):
    other = {"type": "function", "function": {"name": "other", "parameters": {"type": "object"}}}
    client, _, provider = client_with(monkeypatch, Response(completed(function_call({}, "other"))))
    with pytest.raises(ValueError, match="selected function"):
        await client._call_tools_one(provider, "system", "user", [DICE_CHECKS_TOOL, other], 0.1, 4096)


@pytest.mark.asyncio
async def test_auto_final_answer_without_tools_remains_legal(monkeypatch):
    client, session, _ = client_with(monkeypatch, Response(completed(message("完成。"))))
    result = await client.call_tools("system", "user", tools=[DICE_CHECKS_TOOL], force_tool_choice=False)
    assert result.tool_calls == [] and result.content == "完成。"
    assert session.calls[0]["json"]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_tool_request_respects_provider_reasoning_and_request_count(monkeypatch):
    client, session, provider = client_with(monkeypatch, Response(completed(function_call({"checks": []}))))
    provider.reasoning_effort = "low"
    result = await client.call_tools("system", "actions", tools=[DICE_CHECKS_TOOL])
    assert result.attempted_requests == len(session.calls) == 1
    assert session.calls[0]["json"]["reasoning"] == {"effort": "low"}


@pytest.mark.parametrize("status", ["incomplete", "failed", "in_progress", "cancelled", None])
def test_incomplete_outputs_never_become_success(status):
    with pytest.raises(ResponsesIncompleteError):
        parse_response({"status": status, "output": [message("partial")]})


def stream_events(*, terminal="response.completed", reason=None):
    end = completed(message("抵达港口。", phase="final_answer"))
    if reason:
        end = {"status": "incomplete", "incomplete_details": {"reason": reason}}
    events = [
        {"type": "response.reasoning_summary_text.delta", "delta": "private reasoning"},
        {"type": "response.output_item.added", "output_index": 1, "item": message("", phase="commentary")},
        {"type": "response.output_text.delta", "output_index": 1, "delta": "I will think"},
        {"type": "response.output_item.added", "output_index": 2, "item": message("", phase="final_answer")},
        {"type": "response.output_text.delta", "output_index": 2, "delta": "抵达"},
        {"type": "response.output_text.delta", "output_index": 2, "delta": "港口。"},
    ]
    if terminal:
        events.append({"type": terminal, "response": end})
    return events


@pytest.mark.asyncio
async def test_sol_stream_uses_only_final_text_and_terminal_usage(monkeypatch):
    wire = Response(events=stream_events())
    client, session, _ = client_with(monkeypatch, wire, model="gpt-6.2-sol")
    deltas = []
    async def receive(text):
        deltas.append(text)
    response = await client.call_stream("system", "actions", on_delta=receive, max_tokens=8192)
    assert deltas == ["抵达", "港口。"]
    assert response.content == "抵达港口。" and response.total_tokens == 42
    assert wire.closed and session.calls[0]["json"]["stream"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [None, "error", "response.failed"])
async def test_stream_failure_after_output_never_retries_or_falls_back(monkeypatch, terminal):
    wire = Response(events=stream_events(terminal=terminal))
    client, session, _ = client_with(monkeypatch, wire, fallback=True)
    deltas = []
    async def receive(text):
        deltas.append(text)
    with pytest.raises(ResponsesStreamInterruptedError):
        await client.call_stream("system", "actions", on_delta=receive)
    assert len(session.calls) == 1 and deltas == ["抵达", "港口。"] and wire.closed


@pytest.mark.asyncio
async def test_stream_truncation_keeps_existing_caller_retry_contract(monkeypatch):
    wire = Response(events=stream_events(terminal="response.incomplete", reason="max_output_tokens"))
    client, session, _ = client_with(monkeypatch, wire, fallback=True)
    async def receive(_text):
        pass
    with pytest.raises(OutputTruncatedError):
        await client.call_stream("system", "actions", on_delta=receive)
    assert len(session.calls) == 1 and wire.closed


@pytest.mark.asyncio
async def test_http_failure_before_visible_output_can_use_retained_provider(monkeypatch):
    failed = Response({"error": {"code": "rate_limit_exceeded"}}, status=429)
    retained = Response({"choices": [{"message": {"content": "备用正文"}, "finish_reason": "stop"}], "usage": {"total_tokens": 9}})
    client, session, _ = client_with(monkeypatch, failed, retained, fallback=True)
    result = await client.call("system", "user")
    assert result.provider_used == "other" and result.content == "备用正文"
    assert [request["url"] for request in session.calls] == ["https://api.openai.com/v1/responses", "https://api.example.com/v1/chat/completions"]


@pytest.mark.asyncio
async def test_stream_rejects_unknown_message_phase_before_output(monkeypatch):
    wire = Response(events=[
        {"type": "response.output_item.added", "output_index": 0, "item": message("", phase="analysis")},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "private"},
    ])
    client, session, provider = client_with(monkeypatch, wire)
    deltas = []
    async def receive(text):
        deltas.append(text)
    with pytest.raises(ValueError, match="phase"):
        await client._stream_one(provider, "system", "user", .7, 4096, False, receive)
    assert not deltas and len(session.calls) == 1


@pytest.mark.asyncio
async def test_stream_cancellation_closes_response_without_retry(monkeypatch):
    wire = Response()
    async def interrupted():
        for event in stream_events(terminal=None):
            yield ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
        raise asyncio.CancelledError()
    wire.content = SimpleNamespace(iter_any=interrupted)
    client, session, _ = client_with(monkeypatch, wire, fallback=True)
    async def receive(_text):
        pass
    with pytest.raises(asyncio.CancelledError):
        await client.call_stream("system", "actions", on_delta=receive)
    assert wire.closed and len(session.calls) == 1
