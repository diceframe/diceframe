from __future__ import annotations

import pytest

from src.llm.client import LLMClient, ProviderConfig


class _FakeResponse:
    status = 200
    headers = {}
    request_info = None
    history = ()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return {
            "content": [{"type": "text", "text": "OK"}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }

    async def text(self):
        return ""


class _FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _FakeResponse()


class _EmptyOpenAIResponse(_FakeResponse):
    async def json(self):
        return {
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning_content": "首先，任务是压缩给定的 TRPG GM 正文，只输出压缩后的正文。",
                },
                "finish_reason": "length",
            }],
            "usage": {"total_tokens": 512},
        }


class _EmptyOpenAISession(_FakeSession):
    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _EmptyOpenAIResponse()


@pytest.mark.asyncio
async def test_anthropic_provider_uses_messages_api(monkeypatch):
    session = _FakeSession()
    client = LLMClient(
        providers=[
            ProviderConfig(
                provider_name="claude",
                base_url="https://api.anthropic.com",
                api_key="test-key",
                model_name="claude-test",
                api_format="anthropic",
            )
        ],
        default="claude",
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(client, "_get_session", fake_get_session)

    response = await client.call("system prompt", "hello", max_tokens=12, json_mode=True)

    assert response.content == "OK"
    assert response.total_tokens == 5
    call = session.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "test-key"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["json"]["model"] == "claude-test"
    assert call["json"]["messages"] == [{"role": "user", "content": "hello"}]
    assert "temperature" not in call["json"]
    assert "system prompt" in call["json"]["system"]
    assert "Return only valid JSON" in call["json"]["system"]


@pytest.mark.asyncio
async def test_openai_provider_never_exposes_reasoning_as_final_content(monkeypatch):
    session = _EmptyOpenAISession()
    provider = ProviderConfig(
        provider_name="reasoning-model",
        base_url="https://api.example.com",
        api_key="test-key",
        model_name="reasoning-test",
    )
    client = LLMClient(providers=[provider], default=provider.provider_name)

    async def fake_get_session():
        return session

    monkeypatch.setattr(client, "_get_session", fake_get_session)

    with pytest.raises(ValueError, match=r"finish_reason=length"):
        await client._call_openai_compatible(
            provider,
            "compress the narration",
            "original narration",
            temperature=0.2,
            max_tokens=512,
        )
