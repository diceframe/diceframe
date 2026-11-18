"""Settings probes exercise the same selected transport as model requests."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.webui.services import generation


class Response:
    status = 200
    headers = {}
    request_info = None
    history = ()

    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self):
        return deepcopy(self.payload)


class Session:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.payload)

    async def close(self):
        self.closed = True


def completed_response():
    return {
        "status": "completed",
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "OK"}]}],
        "usage": {"input_tokens": 12, "output_tokens": 28, "total_tokens": 40},
    }


def dependencies(session, timeout=30):
    async def get_session():
        return session
    return generation.ConnectionDependencies(
        llm_client=SimpleNamespace(_get_session=get_session, proxy_url="http://proxy.invalid:8080"),
        config_state=lambda: {"test_timeout_seconds": timeout},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-6.2-astra", "gpt-6.2-sol"])
async def test_official_gpt6_connection_probe_uses_responses(model):
    session = Session(completed_response())

    result = await generation.test_connection(
        dependencies(session, timeout=5), "https://api.openai.com/v1", "fake-key", model,
    )

    assert result["ok"] is True
    assert result["response"] == "OK"
    assert result["tokens"] == 40
    assert len(session.calls) == 1
    url, request = session.calls[0]
    assert url == "https://api.openai.com/v1/responses"
    assert request["json"]["model"] == model
    assert request["json"]["max_output_tokens"] == 4096
    assert request["json"]["store"] is False
    assert request["timeout"].total == 5
    assert request["proxy"] == "http://proxy.invalid:8080"
    assert not session.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["incomplete", "failed"])
async def test_connection_probe_does_not_accept_incomplete_or_failed_responses(status):
    payload = completed_response()
    payload["status"] = status
    payload["incomplete_details"] = {"reason": "max_output_tokens"}
    session = Session(payload)

    result = await generation.test_connection(
        dependencies(session), "https://api.openai.com/v1", "fake-key", "gpt-6.2-astra",
    )

    assert result["ok"] is False
    assert "error" in result
    assert len(session.calls) == 1
    assert not session.closed


def test_explicit_responses_url_lists_models_at_v1():
    assert generation._model_list_url("https://api.openai.com/v1/responses") == "https://api.openai.com/v1/models"


@pytest.mark.asyncio
async def test_other_providers_keep_chat_connection_test():
    session = Session({"choices": [{"message": {"content": "OK"}}], "usage": {"total_tokens": 9}})
    result = await generation.test_connection(
        dependencies(session), "https://api.moonshot.cn/v1", "fake-key", "kimi-k3.1-pro-highspeed",
    )

    assert result["ok"] is True
    assert result["response"] == "OK"
    assert session.calls[0][0] == "https://api.moonshot.cn/v1/chat/completions"
    assert session.calls[0][1]["json"]["max_tokens"] == 10
    # The legacy probe borrows the application's existing HTTP session.
    assert not session.closed
