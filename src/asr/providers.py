"""Built-in HTTP ASR provider adapters.

Providers only translate the stable DiceFrame request into an upstream
protocol. Membership checks and audio size validation remain in
``TranscriptionService`` (src/asr/service.py).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

import aiohttp

from src.openai_endpoints import openai_endpoint_url

from .contracts import TranscriptionRequest


MAX_TRANSCRIPTION_RESPONSE_BYTES = 1024 * 1024

_AUDIO_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/mpeg": "mpeg",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/flac": "flac",
    "audio/aac": "aac",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
}


class AsrProviderError(RuntimeError):
    pass


class AsrProvider(ABC):
    provider_id: str

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float, proxy_url: str = "") -> None:
        self.base_url = base_url.strip()
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout_seconds = max(5.0, min(float(timeout_seconds), 300.0))
        self.proxy_url = proxy_url.strip() or None

    @abstractmethod
    async def transcribe(self, request: TranscriptionRequest) -> str:
        raise NotImplementedError


class OpenAICompatibleAsrProvider(AsrProvider):
    provider_id = "openai-compatible"

    async def transcribe(self, request: TranscriptionRequest) -> str:
        url, form = build_openai_transcription_request(
            base_url=self.base_url,
            model=self.model,
            request=request,
        )
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds, connect=min(15.0, self.timeout_seconds))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=form, headers=headers, proxy=self.proxy_url) as response:
                    if response.status >= 400:
                        detail = (await response.text())[:1000]
                        raise AsrProviderError(f"ASR 服务返回 HTTP {response.status}: {detail or response.reason}")
                    body = await _read_limited(response)
        except AsrProviderError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AsrProviderError(f"无法连接 ASR 服务：{exc}") from exc
        return _extract_transcription_text(body)


def build_openai_transcription_request(
    *, base_url: str, model: str, request: TranscriptionRequest,
) -> tuple[str, aiohttp.FormData]:
    """Assemble URL + multipart form without touching the network (unit-testable)."""
    content_type = normalize_content_type(request.content_type)
    extension = _AUDIO_EXTENSIONS.get(content_type, "webm")
    form = aiohttp.FormData()
    form.add_field("file", request.audio, filename=f"audio.{extension}", content_type=content_type)
    form.add_field("model", model or "whisper-1")
    if request.language:
        form.add_field("language", request.language)
    return _openai_transcription_url(base_url), form


def normalize_content_type(value: str) -> str:
    return str(value or "").split(";", 1)[0].strip().lower() or "audio/webm"


def _openai_transcription_url(base_url: str) -> str:
    return openai_endpoint_url(base_url, "audio/transcriptions")


async def _read_limited(response: aiohttp.ClientResponse) -> bytes:
    declared = response.content_length
    if declared is not None and declared > MAX_TRANSCRIPTION_RESPONSE_BYTES:
        raise AsrProviderError("ASR 响应超过 1 MB 限制")
    chunks = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(chunks) + len(chunk) > MAX_TRANSCRIPTION_RESPONSE_BYTES:
            raise AsrProviderError("ASR 响应超过 1 MB 限制")
        chunks.extend(chunk)
    return bytes(chunks)


def _extract_transcription_text(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError as exc:
        raise AsrProviderError("ASR 服务返回了无法解析的响应") from exc
    if not isinstance(payload, dict):
        raise AsrProviderError("ASR 服务返回了无法解析的响应")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise AsrProviderError("ASR 服务没有识别到任何内容")
    return text
