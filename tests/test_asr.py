from __future__ import annotations

import pytest

from src.asr import AsrService, AsrServiceError, TranscriptionRequest
from src.asr.providers import (
    AsrProviderError,
    _openai_transcription_url,
    build_openai_transcription_request,
    normalize_content_type,
)
from src.webui.config_update import prepare_config_update


def _config(**changes):
    value = {
        "asr_provider": "openai-compatible",
        "asr_base_url": "https://api.example.com/v1",
        "asr_api_key": "sk-test",
        "asr_model": "whisper-1",
        "asr_timeout_seconds": 30,
    }
    value.update(changes)
    return value


class _FakeProvider:
    def __init__(self, text: str = "推开石门"):
        self.text = text
        self.calls = []

    async def transcribe(self, request):
        self.calls.append(request)
        return self.text


def _form_fields(form):
    fields = {}
    for options, headers, value in form._fields:
        fields[options["name"]] = {
            "value": value,
            "filename": options.get("filename", ""),
            "content_type": headers.get("Content-Type", ""),
        }
    return fields


def test_openai_transcription_url_variants():
    assert _openai_transcription_url("https://api.openai.com") == "https://api.openai.com/v1/audio/transcriptions"
    assert _openai_transcription_url("https://api.example.com/v1") == "https://api.example.com/v1/audio/transcriptions"
    assert _openai_transcription_url("https://api.example.com/v1/audio/transcriptions") == "https://api.example.com/v1/audio/transcriptions"


def test_normalize_content_type_strips_codecs_parameter():
    assert normalize_content_type("audio/webm;codecs=opus") == "audio/webm"
    assert normalize_content_type("Audio/MP4 ") == "audio/mp4"
    assert normalize_content_type("") == "audio/webm"


def test_build_openai_transcription_request_assembles_multipart():
    request = TranscriptionRequest(audio=b"clip", content_type="audio/webm;codecs=opus", language="zh-CN")

    url, form = build_openai_transcription_request(
        base_url="https://api.example.com/v1", model="whisper-1", request=request,
    )

    assert url == "https://api.example.com/v1/audio/transcriptions"
    fields = _form_fields(form)
    assert fields["file"]["value"] == b"clip"
    assert fields["file"]["filename"] == "audio.webm"
    assert fields["file"]["content_type"] == "audio/webm"
    assert fields["model"]["value"] == "whisper-1"
    assert fields["language"]["value"] == "zh-CN"


def test_build_openai_transcription_request_omits_blank_language():
    request = TranscriptionRequest(audio=b"clip", content_type="audio/mp4", language="")

    _url, form = build_openai_transcription_request(
        base_url="https://api.example.com", model="", request=request,
    )

    fields = _form_fields(form)
    assert fields["file"]["filename"] == "audio.mp4"
    assert fields["model"]["value"] == "whisper-1"
    assert "language" not in fields


@pytest.mark.asyncio
async def test_transcribe_wraps_provider_result(monkeypatch):
    service = AsrService(_config())
    provider = _FakeProvider("推开石门")
    monkeypatch.setattr(service, "_provider", lambda: provider)

    result = await service.transcribe(
        TranscriptionRequest(audio=b"clip", content_type="audio/webm;codecs=opus", language="zh-CN"),
    )

    assert result.text == "推开石门"
    assert result.provider == "openai-compatible"
    assert result.model == "whisper-1"
    assert provider.calls[0].content_type == "audio/webm"
    assert provider.calls[0].language == "zh-CN"


@pytest.mark.asyncio
async def test_transcribe_rejects_audio_when_engine_disabled():
    service = AsrService(_config(asr_provider="disabled"))

    with pytest.raises(AsrServiceError, match="未启用"):
        await service.transcribe(TranscriptionRequest(audio=b"clip"))


@pytest.mark.asyncio
async def test_transcribe_rejects_empty_audio():
    service = AsrService(_config())

    with pytest.raises(AsrServiceError, match="录音内容为空"):
        await service.transcribe(TranscriptionRequest(audio=b""))


@pytest.mark.asyncio
async def test_transcribe_wraps_provider_errors(monkeypatch):
    class _Broken:
        async def transcribe(self, request):
            raise AsrProviderError("ASR 服务返回 HTTP 401")

    service = AsrService(_config())
    monkeypatch.setattr(service, "_provider", lambda: _Broken())

    with pytest.raises(AsrServiceError, match="401"):
        await service.transcribe(TranscriptionRequest(audio=b"clip"))


def test_service_defaults_to_disabled():
    service = AsrService({})

    assert service.backend_enabled is False
    assert service.public_config()["backend_enabled"] is False


def test_service_validates_enabled_base_url():
    with pytest.raises(ValueError, match="Base URL"):
        AsrService(_config(asr_base_url="ftp://bad"))
    with pytest.raises(ValueError, match="Base URL"):
        AsrService(_config(asr_base_url="https://user:pass@api.example.com"))


def test_config_update_accepts_asr_keys():
    prepared = prepare_config_update(_config(), {
        "asr_provider": "openai-compatible",
        "ai_providers": [{"id": "local", "base_url": "https://api.example.com"}],
        "asr_provider_ref": "local",
        "asr_model": "whisper-1",
        "asr_timeout_seconds": 90,
    })

    assert prepared.error == ""
    assert prepared.state["asr_provider"] == "openai-compatible"
    assert prepared.state["asr_provider_ref"] == "local"
    assert "asr_base_url" not in prepared.state
    assert prepared.state["asr_timeout_seconds"] == 90


def test_config_update_rejects_invalid_asr_provider():
    prepared = prepare_config_update(_config(), {"asr_provider": "azure"})

    assert prepared.error == "ASR Provider 无效"


def test_config_update_rejects_out_of_range_asr_timeout():
    prepared = prepare_config_update(_config(), {"asr_timeout_seconds": 2})

    assert prepared.error == "ASR 超时必须在 5–300 秒之间"


def test_config_update_rejects_old_asr_secret_even_when_blank():
    prepared = prepare_config_update(_config(asr_api_key="sk-old"), {"asr_api_key": ""})

    assert "unsupported" in prepared.error
    assert "asr_api_key" in prepared.error
    assert not prepared.changed_keys
