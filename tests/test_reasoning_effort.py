"""Reasoning effort contracts: config persistence and per-format request parameters.

A configured level is an explicit user setting carried by every request on its
route. Unset, each request path keeps its pre-existing default.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.ai_providers import CHAT_REASONING_EFFORT_KEYS
from src.llm.client import LLMClient, ProviderConfig
from src.localization.messages import reset_request_language, set_request_language
from src.webui.composition import RuntimeComposition, RuntimeFactories, RuntimePaths as CompositionPaths
from src.webui.config_update import MODEL_RUNTIME_CONFIG_KEYS, prepare_config_update
from src.webui.runtime_config import ConfigStore, RuntimePaths

TOOL = {"type": "function", "function": {"name": "dice_checks", "description": "plan checks", "parameters": {
    "type": "object", "properties": {"checks": {"type": "array"}}}}}
ROUTES = {
    "ai_providers": [
        {"id": "main", "base_url": "https://synthetic.invalid/v1", "api_format": "openai", "models": ["chat"]},
        {"id": "spare", "base_url": "https://synthetic.invalid/v1", "api_format": "openai", "models": ["chat"]},
        {"id": "judge", "base_url": "https://synthetic.invalid/v1", "api_format": "openai", "models": ["chat"]},
    ],
    "llm_provider_ref": "main", "model": "chat",
    "fallback1_enabled": True, "fallback1_provider_ref": "spare", "fallback1_model": "chat",
    "check_model_enabled": True, "check_provider_ref": "judge", "check_model": "chat",
}


def test_every_chat_route_round_trips_its_reasoning_effort(tmp_path):
    store = ConfigStore(RuntimePaths.from_root(tmp_path, {}), {})
    levels = {"model_reasoning_effort": "high", "fallback1_reasoning_effort": "none",
              "fallback2_reasoning_effort": "minimal", "check_model_reasoning_effort": "max"}
    prepared = prepare_config_update(store.load().state, {**ROUTES, **levels})
    assert not prepared.error
    assert set(levels) <= prepared.changed_keys
    store.save(prepared.state)

    runtime = store.load()
    assert {key: runtime.state[key] for _, key, _ in CHAT_REASONING_EFFORT_KEYS} == levels
    # Saving a level must rebuild the model runtime like the route it belongs to.
    assert set(levels) <= MODEL_RUNTIME_CONFIG_KEYS
    assert {key: store.public_view(runtime)[key] for key in levels} == levels


def test_fresh_install_loads_every_level_unset(tmp_path):
    state = ConfigStore(RuntimePaths.from_root(tmp_path, {}), {}).load().state
    assert [state[key] for _, key, _ in CHAT_REASONING_EFFORT_KEYS] == ["", "", "", ""]


@pytest.mark.parametrize("value", ["high effort", "-lead", "a" * 65, "off", "OFF", 5, True, ["high"]])
def test_unusable_values_are_rejected_instead_of_ignored(value):
    updated = prepare_config_update({}, {"model_reasoning_effort": value})
    assert updated.error and "model_reasoning_effort" in updated.error
    assert "model_reasoning_effort" not in updated.state


def test_levels_are_normalized_like_other_string_settings():
    updated = prepare_config_update({}, {"model_reasoning_effort": " HIGH "})
    assert not updated.error
    assert updated.state["model_reasoning_effort"] == "high"


def test_custom_identifiers_reach_their_route_and_survive_a_reload(tmp_path):
    store = ConfigStore(RuntimePaths.from_root(tmp_path, {}), {})
    prepared = prepare_config_update(store.load().state, {**ROUTES, "model_reasoning_effort": " Provider-Ultra "})
    assert not prepared.error
    store.save(prepared.state)
    # Case is for the provider to judge, so a custom identifier is passed on untouched.
    assert store.load().state["model_reasoning_effort"] == "Provider-Ultra"


ANTHROPIC_ROUTE = {
    "ai_providers": [{"id": "claude", "base_url": "https://synthetic.invalid", "api_format": "anthropic",
                      "models": ["chat"]}],
    "llm_provider_ref": "claude", "model": "chat",
}


@pytest.mark.parametrize("level,accepted", [
    *[(level, True) for level in ("low", "medium", "high", "xhigh", "max")],
    ("none", False), ("minimal", False),
    ("ultra", True),
])
def test_anthropic_routes_reject_only_presets_their_format_cannot_express(level, accepted):
    updated = prepare_config_update(ANTHROPIC_ROUTE, {"model_reasoning_effort": level})
    assert bool(updated.error) is not accepted
    if not accepted:
        assert "主模型" in updated.error and "Anthropic" in updated.error


def test_rejections_are_served_in_the_request_language():
    token = set_request_language("en")
    try:
        invalid = prepare_config_update({}, {"model_reasoning_effort": "high effort"})
        assert "model_reasoning_effort must be empty" in invalid.error
        mismatch = prepare_config_update(ANTHROPIC_ROUTE, {"model_reasoning_effort": "none"})
        assert "Reasoning effort none for the main model" in mismatch.error
    finally:
        reset_request_language(token)
    assert "主模型的推理强度 none" in prepare_config_update(ANTHROPIC_ROUTE, {"model_reasoning_effort": "none"}).error


def test_level_on_an_unassigned_route_stays_dormant():
    updated = prepare_config_update({}, {"fallback2_reasoning_effort": "none"})
    assert not updated.error
    assert updated.state["fallback2_reasoning_effort"] == "none"


def test_unusable_value_on_disk_falls_back_to_unset_with_a_warning(tmp_path, caplog):
    paths = RuntimePaths.from_root(tmp_path, {})
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(json.dumps({
        "model_reasoning_effort": "off", "check_model_reasoning_effort": "low",
        "fallback1_reasoning_effort": "provider-ultra",
    }), encoding="utf-8")
    with caplog.at_level("WARNING"):
        state = ConfigStore(paths, {}).load().state
    assert state["model_reasoning_effort"] == ""
    assert state["check_model_reasoning_effort"] == "low"
    assert state["fallback1_reasoning_effort"] == "provider-ultra"
    assert any("model_reasoning_effort" in record.getMessage() for record in caplog.records)


def test_composition_passes_each_route_level_to_its_provider(tmp_path):
    paths = CompositionPaths(*(tmp_path / name for name in ("data", "prompts", "rules", "worlds", "adventures")))
    composition = RuntimeComposition(
        paths=paths, state={}, save_config=lambda: None,
        factories=RuntimeFactories(create_subsystems=lambda **kwargs: kwargs),
    )
    built = composition.build_subsystems(config={
        **ROUTES, "model_reasoning_effort": "high", "fallback1_reasoning_effort": "low",
        "check_model_reasoning_effort": "max",
    })
    assert [provider.reasoning_effort for provider in built["providers"]] == ["high", "low"]
    assert built["check_provider"].reasoning_effort == "max"
    unset = composition.build_subsystems(config=ROUTES)
    assert all(provider.reasoning_effort is None for provider in unset["providers"])
    assert unset["check_provider"].reasoning_effort is None


class _Lines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _Response:
    headers = {}
    history = ()
    request_info = SimpleNamespace(real_url="https://synthetic.invalid")
    status = 200

    def __init__(self, payload=None, lines=()) -> None:
        self.payload = payload
        self.content = _Lines(list(lines))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self.payload

    async def text(self):
        return ""


def _sse(event: dict) -> bytes:
    return ("data: " + json.dumps(event, ensure_ascii=False) + "\n").encode()


class _ReplyingSession:
    """Answers every request with a complete reply and records each request body."""

    def __init__(self, api_format: str) -> None:
        self.api_format = api_format
        self.calls = []

    def post(self, url, **kwargs):
        body = kwargs["json"]
        self.calls.append(body)
        return _Response(*self._reply(body))

    def _reply(self, body: dict):
        anthropic = self.api_format == "anthropic"
        usage = {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
        if "input" in body:  # OpenAI Responses
            if body.get("tools"):
                return {"status": "completed", "output": [{
                    "type": "function_call", "name": "dice_checks", "call_id": "call_1",
                    "status": "completed", "arguments": json.dumps({"checks": []}),
                }], "usage": usage}, ()
            return {"status": "completed", "output": [{
                "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "正文。"}],
            }], "usage": usage}, ()
        if anthropic:
            if body.get("tools"):
                return {"content": [{"type": "tool_use", "name": "dice_checks", "input": {"checks": []}}],
                        "stop_reason": "tool_use", "usage": usage}, ()
            if body.get("stream"):
                return None, [
                    _sse({"type": "message_start", "message": {"usage": {"input_tokens": 1}}}),
                    _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "正文。"}}),
                    _sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                          "usage": {"output_tokens": 2}}),
                ]
            return {"content": [{"type": "text", "text": "正文。"}], "stop_reason": "end_turn", "usage": usage}, ()
        if body.get("tools"):
            return {"choices": [{"message": {"content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "dice_checks", "arguments": json.dumps({"checks": []})},
            }]}, "finish_reason": "tool_calls"}], "usage": usage}, ()
        if body.get("stream"):
            return None, [
                _sse({"choices": [{"delta": {"content": "正文。"}, "finish_reason": "stop"}]}),
                b"data: [DONE]\n",
            ]
        return {"choices": [{"message": {"content": "正文。"}, "finish_reason": "stop"}], "usage": usage}, ()


async def _request_bodies(monkeypatch, provider: ProviderConfig, paths: list[str]) -> list[dict]:
    session = _ReplyingSession(provider.api_format or "openai")
    client = LLMClient([provider], provider.provider_name)

    async def get_session():
        return session

    monkeypatch.setattr(client, "_get_session", get_session)
    for path in paths:
        if path == "narration":
            await client.call("system", "input", max_tokens=64)
        elif path == "stream":
            await client.call_stream("system", "input", max_tokens=64)
        else:
            await client.call_tools("system", "actions", tools=[TOOL], max_tokens=64)
    return session.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["narration", "stream", "tools"])
async def test_chat_requests_carry_the_configured_level(monkeypatch, path):
    provider = ProviderConfig("main", "https://synthetic.invalid/v1", "key", "chat", reasoning_effort="high")
    body = (await _request_bodies(monkeypatch, provider, [path]))[0]
    assert body["reasoning_effort"] == "high"
    assert "thinking" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("path,controls", [
    ("narration", {}), ("stream", {}), ("tools", {"reasoning_effort": "none"}),
])
async def test_unset_level_keeps_each_chat_default(monkeypatch, path, controls):
    provider = ProviderConfig("main", "https://synthetic.invalid/v1", "key", "chat")
    body = (await _request_bodies(monkeypatch, provider, [path]))[0]
    assert {key: body[key] for key in ("reasoning_effort", "thinking") if key in body} == controls


@pytest.mark.asyncio
async def test_configured_level_replaces_the_deepseek_thinking_default(monkeypatch):
    provider = ProviderConfig("deepseek", "https://api.deepseek.com/v1", "key", "deepseek-v4-flash",
                              reasoning_effort="low")
    body = (await _request_bodies(monkeypatch, provider, ["narration"]))[0]
    assert body["reasoning_effort"] == "low"
    assert "thinking" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["narration", "stream", "tools"])
async def test_anthropic_requests_carry_output_config_effort_without_touching_thinking(monkeypatch, path):
    provider = ProviderConfig("claude", "https://synthetic.invalid", "key", "claude-test",
                              api_format="anthropic", reasoning_effort="max")
    body = (await _request_bodies(monkeypatch, provider, [path]))[0]
    assert body["output_config"] == {"effort": "max"}
    assert "thinking" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["narration", "stream", "tools"])
async def test_unset_anthropic_level_sends_no_thinking_controls(monkeypatch, path):
    provider = ProviderConfig("claude", "https://synthetic.invalid", "key", "claude-test", api_format="anthropic")
    body = (await _request_bodies(monkeypatch, provider, [path]))[0]
    assert not {"output_config", "thinking", "reasoning_effort"} & body.keys()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["narration", "tools"])
async def test_responses_requests_use_the_configured_level(monkeypatch, path):
    provider = ProviderConfig("official", "https://api.openai.com/v1", "key", "gpt-6.2-astra",
                              reasoning_effort="xhigh")
    body = (await _request_bodies(monkeypatch, provider, [path]))[0]
    assert body["reasoning"] == {"effort": "xhigh"}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,expected", [
    (ProviderConfig("main", "https://synthetic.invalid/v1", "key", "chat", reasoning_effort="provider-ultra"),
     ("reasoning_effort", "provider-ultra")),
    (ProviderConfig("official", "https://api.openai.com/v1", "key", "gpt-6.2-sol", reasoning_effort="provider-ultra"),
     ("reasoning", {"effort": "provider-ultra"})),
    (ProviderConfig("claude", "https://synthetic.invalid", "key", "claude-test", api_format="anthropic",
                    reasoning_effort="provider-ultra"),
     ("output_config", {"effort": "provider-ultra"})),
], ids=["chat", "responses", "anthropic"])
async def test_custom_identifiers_are_sent_as_is_on_every_format(monkeypatch, provider, expected):
    key, value = expected
    body = (await _request_bodies(monkeypatch, provider, ["narration"]))[0]
    assert body[key] == value
