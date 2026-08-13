"""Built-in HTTP TTS provider adapters.

Providers only translate the stable DiceFrame request into an upstream protocol.
Caching, validation and plugin voice resolution remain in ``SpeechService``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp

from .contracts import SpeechRequest, VoiceProfile


MAX_UPSTREAM_AUDIO_BYTES = 20 * 1024 * 1024


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderAudio:
    body: bytes
    content_type: str


class SpeechProvider(ABC):
    provider_id: str

    def __init__(self, *, base_url: str, api_key: str, model: str, audio_format: str, timeout_seconds: float, proxy_url: str = "") -> None:
        self.base_url = base_url.strip()
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.audio_format = audio_format.strip()
        self.timeout_seconds = max(5.0, min(float(timeout_seconds), 300.0))
        self.proxy_url = proxy_url.strip() or None

    @abstractmethod
    async def synthesize(self, request: SpeechRequest, voice: VoiceProfile | None) -> ProviderAudio:
        raise NotImplementedError

    async def _post_json(self, url: str, payload: dict[str, Any]) -> ProviderAudio:
        headers = {"Accept": "audio/*, application/octet-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds, connect=min(15.0, self.timeout_seconds))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers, proxy=self.proxy_url) as response:
                    if response.status >= 400:
                        detail = (await response.text())[:1000]
                        raise ProviderError(f"TTS 服务返回 HTTP {response.status}: {detail or response.reason}")
                    body = await _read_limited(response)
                    if not body:
                        raise ProviderError("TTS 服务返回了空音频")
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if not content_type.startswith("audio/"):
                        content_type = _content_type_for_format(self.audio_format)
                    return ProviderAudio(body=body, content_type=content_type)
        except ProviderError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ProviderError(f"无法连接 TTS 服务：{exc}") from exc


class OpenAICompatibleProvider(SpeechProvider):
    provider_id = "openai-compatible"

    async def synthesize(self, request: SpeechRequest, voice: VoiceProfile | None) -> ProviderAudio:
        voice_id = (voice.voice_id if voice else "") or request.voice or "alloy"
        payload = {
            "model": self.model or "tts-1",
            "input": request.text,
            "voice": voice_id,
            "response_format": self.audio_format,
            "speed": max(0.25, min(float(request.speed), 4.0)),
        }
        return await self._post_json(_openai_speech_url(self.base_url), payload)


class GptSovitsProvider(SpeechProvider):
    provider_id = "gpt-sovits"

    async def synthesize(self, request: SpeechRequest, voice: VoiceProfile | None) -> ProviderAudio:
        if voice is None:
            raise ProviderError("GPT-SoVITS 需要选择个人音色或音色预设")
        reference_path = voice.reference_audio_path or (
            str(voice.reference_audio) if voice.reference_audio is not None else ""
        )
        if not reference_path:
            raise ProviderError("GPT-SoVITS 音色缺少参考音频")
        if voice.reference_audio is not None and not voice.reference_audio.is_file():
            raise ProviderError("音色参考音频不存在")
        media_type = {
            "mp3": "wav",
            "opus": "ogg",
            "flac": "wav",
            "pcm": "raw",
        }.get(self.audio_format, self.audio_format)
        payload = {
            "text": request.text,
            "text_lang": _gpt_language(request.language),
            "ref_audio_path": reference_path,
            "prompt_text": voice.prompt_text,
            "prompt_lang": _gpt_language(voice.prompt_language or voice.language or request.language),
            "text_split_method": "cut5",
            "batch_size": 1,
            "media_type": media_type,
            "streaming_mode": False,
            "speed_factor": max(0.6, min(float(request.speed), 1.65)),
        }
        return await self._post_json(_endpoint_url(self.base_url, "tts"), payload)


async def _read_limited(response: aiohttp.ClientResponse) -> bytes:
    declared = response.content_length
    if declared is not None and declared > MAX_UPSTREAM_AUDIO_BYTES:
        raise ProviderError("TTS 音频超过 20 MB 限制")
    chunks = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(chunks) + len(chunk) > MAX_UPSTREAM_AUDIO_BYTES:
            raise ProviderError("TTS 音频超过 20 MB 限制")
        chunks.extend(chunk)
    return bytes(chunks)


def _openai_speech_url(base_url: str) -> str:
    parsed = urlparse(base_url.strip())
    path = parsed.path.rstrip("/")
    if path.endswith("/audio/speech"):
        target = path
    elif path.endswith("/v1"):
        target = path + "/audio/speech"
    else:
        target = path + "/v1/audio/speech"
    return urlunparse(parsed._replace(path=target))


def _endpoint_url(base_url: str, endpoint: str) -> str:
    parsed = urlparse(base_url.strip())
    path = parsed.path.rstrip("/")
    if not path.endswith("/" + endpoint):
        path += "/" + endpoint
    return urlunparse(parsed._replace(path=path))


def _gpt_language(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("en"):
        return "en"
    if text.startswith("ja") or text.startswith("jp"):
        return "ja"
    return "zh"


def _content_type_for_format(value: str) -> str:
    return {
        "mp3": "audio/mpeg",
        "opus": "audio/ogg",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "wav": "audio/wav",
        "pcm": "audio/L16",
    }.get(value, "application/octet-stream")
