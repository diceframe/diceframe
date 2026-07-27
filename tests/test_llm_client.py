from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.llm.client import LLMClient, ProviderConfig, OutputTruncatedError


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


@pytest.mark.asyncio
async def test_length_truncation_retries_with_larger_max_tokens(monkeypatch):
    """finish_reason=length 时，call() 应逐步放大 max_tokens 重试，而非用相同预算原地重试。"""
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
    monkeypatch.setattr("src.llm.client.BASE_DELAY", 0.0)

    with pytest.raises(RuntimeError):
        await client.call("system", "user", max_tokens=512)

    # 3 次尝试的 max_tokens 应为 512 -> 1024 -> 2048（2x 步进，4x 上限）
    sent_budgets = [call["json"]["max_tokens"] for call in session.calls]
    assert sent_budgets == [512, 1024, 2048]


class _FakeStreamContent:
    """模拟 aiohttp StreamReader 的按行异步迭代（每行含 \\n）。"""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._i]
        self._i += 1
        return line


class _FakeStreamResponse:
    def __init__(self, lines: list[bytes], status: int = 200) -> None:
        self.status = status
        self.headers = {}
        # aiohttp ClientResponseError.__str__ 会读 request_info.real_url，给个最小占位避免格式化报错
        self.request_info = SimpleNamespace(real_url="https://test.local/v1", url="https://test.local/v1", method="POST", headers={})
        self.history = ()
        self.content = _FakeStreamContent(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return "error body"


class _FakeStreamSession:
    def __init__(self, lines: list[bytes], status: int = 200) -> None:
        self.lines = lines
        self.status = status
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _FakeStreamResponse(self.lines, status=self.status)


class _SequentialStreamSession:
    """按顺序返回不同响应，用于测 provider fallback。"""

    def __init__(self, responses: list[_FakeStreamResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def _openai_sse_lines(deltas: list[tuple[str, str]]) -> list[bytes]:
    """构造 OpenAI 流式 SSE：deltas = [(content, finish_reason), ...]，最后附 usage 与 [DONE]。"""
    lines: list[bytes] = []
    for content, finish in deltas:
        delta_obj = {"content": content} if content is not None else {}
        choice: dict = {"delta": delta_obj}
        if finish:
            choice["finish_reason"] = finish
        lines.append(("data: " + json.dumps({"choices": [choice]}) + "\n").encode())
    lines.append(b'data: {"usage":{"total_tokens":42}}\n')
    lines.append(b"data: [DONE]\n")
    return lines


@pytest.mark.asyncio
async def test_call_stream_openai_yields_deltas_and_returns_response(monkeypatch):
    session = _FakeStreamSession(_openai_sse_lines([("Hello", ""), (", ", ""), ("world!", "stop")]))
    provider = ProviderConfig(
        provider_name="openai",
        base_url="https://api.example.com",
        api_key="k",
        model_name="m",
    )
    client = LLMClient(providers=[provider], default="openai")

    async def fake_get_session():
        return session

    monkeypatch.setattr(client, "_get_session", fake_get_session)

    deltas: list[str] = []

    async def on_delta(text):
        deltas.append(text)

    response = await client.call_stream("system", "hello", max_tokens=64, on_delta=on_delta)

    assert deltas == ["Hello", ", ", "world!"]
    assert response.content == "Hello, world!"
    assert response.total_tokens == 42
    assert response.provider_used == "openai"
    body = session.calls[0]["json"]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_call_stream_anthropic_yields_deltas_and_returns_response(monkeypatch):
    lines = [
        b'event: message_start\n',
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}\n',
        b'event: content_block_delta\n',
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi "}}\n',
        b'event: content_block_delta\n',
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"there"}}\n',
        b'event: message_delta\n',
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}\n',
        b'event: message_stop\n',
        b'data: {"type":"message_stop"}\n',
    ]
    session = _FakeStreamSession(lines)
    provider = ProviderConfig(
        provider_name="claude",
        base_url="https://api.anthropic.com",
        api_key="k",
        model_name="claude-test",
        api_format="anthropic",
    )
    client = LLMClient(providers=[provider], default="claude")

    async def fake_get_session():
        return session

    monkeypatch.setattr(client, "_get_session", fake_get_session)

    deltas: list[str] = []

    async def on_delta(text):
        deltas.append(text)

    response = await client.call_stream("system", "hello", max_tokens=64, on_delta=on_delta)

    assert deltas == ["Hi ", "there"]
    assert response.content == "Hi there"
    assert response.total_tokens == 8
    assert response.provider_used == "claude"
    assert session.calls[0]["json"]["stream"] is True


@pytest.mark.asyncio
async def test_call_stream_openai_length_truncation_raises(monkeypatch):
    session = _FakeStreamSession(_openai_sse_lines([("", "length")]))
    provider = ProviderConfig(
        provider_name="reasoning-model",
        base_url="https://api.example.com",
        api_key="k",
        model_name="m",
    )
    client = LLMClient(providers=[provider], default="reasoning-model")

    async def fake_get_session():
        return session

    monkeypatch.setattr(client, "_get_session", fake_get_session)

    with pytest.raises(OutputTruncatedError):
        await client.call_stream("system", "hello", max_tokens=512)


@pytest.mark.asyncio
async def test_call_stream_falls_back_to_next_provider(monkeypatch):
    """主供应商 HTTP 500（不可重试）时，应跳到 fallback 供应商完成流式。"""
    primary = ProviderConfig(
        provider_name="primary",
        base_url="https://api.example.com",
        api_key="k",
        model_name="m",
    )
    fallback = ProviderConfig(
        provider_name="backup",
        base_url="https://api.backup.com",
        api_key="k",
        model_name="m",
        fallback=True,
    )
    client = LLMClient(providers=[primary, fallback], default="primary")

    session = _SequentialStreamSession([
        _FakeStreamResponse([], status=500),  # 主供应商失败
        _FakeStreamResponse(_openai_sse_lines([("ok", "stop")])),  # fallback 成功
    ])

    async def fake_get_session():
        return session

    monkeypatch.setattr(client, "_get_session", fake_get_session)

    deltas: list[str] = []

    async def on_delta(text):
        deltas.append(text)

    response = await client.call_stream("system", "hello", max_tokens=64, on_delta=on_delta)

    assert deltas == ["ok"]
    assert response.content == "ok"
    assert response.provider_used == "backup"
    assert len(session.calls) == 2
    assert session.calls[0]["url"].startswith("https://api.example.com")
    assert session.calls[1]["url"].startswith("https://api.backup.com")
