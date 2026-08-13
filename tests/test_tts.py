from __future__ import annotations

import base64
import io
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.tts import SpeechRequest, SpeechService, SpeechServiceError
from src.tts.providers import ProviderAudio, _openai_speech_url
from src.webui.services.speech import _is_public_game_text


def _config(**changes):
    value = {
        "tts_provider": "openai-compatible",
        "tts_base_url": "http://127.0.0.1:8880/v1",
        "tts_api_key": "",
        "tts_model": "kokoro",
        "tts_audio_format": "mp3",
        "tts_default_voice": "alloy",
        "tts_timeout_seconds": 30,
        "tts_cache_mb": 16,
    }
    value.update(changes)
    return value


class _FakeProvider:
    def __init__(self):
        self.calls = 0
        self.voices = []

    async def synthesize(self, request, voice):
        self.calls += 1
        self.voices.append(voice)
        return ProviderAudio(body=f"audio:{request.text}".encode(), content_type="audio/mpeg")


def _wav_payload(seconds: float = 1.0) -> str:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * int(16000 * seconds))
    return base64.b64encode(output.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_speech_service_caches_identical_requests(tmp_path, monkeypatch):
    service = SpeechService(_config(), tmp_path / "cache")
    provider = _FakeProvider()
    monkeypatch.setattr(service, "_provider", lambda: provider)
    request = SpeechRequest(text="篝火亮了", voice="alloy", language="zh-CN", speed=1.0)

    first = await service.synthesize(request)
    second = await service.synthesize(request)

    assert first.body == "audio:篝火亮了".encode()
    assert first.cached is False
    assert second.cached is True
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_gpt_sovits_requires_personal_voice_or_optional_preset(tmp_path):
    service = SpeechService(
        _config(tts_provider="gpt-sovits", tts_base_url="http://127.0.0.1:9880"),
        tmp_path / "cache",
    )

    with pytest.raises(SpeechServiceError, match="个人音色或已安装的音色预设"):
        await service.synthesize(SpeechRequest(text="测试", voice="missing"), [])


@pytest.mark.asyncio
async def test_personal_gpt_sovits_reference_works_without_plugin_pack(tmp_path, monkeypatch):
    service = SpeechService(
        _config(tts_provider="gpt-sovits", tts_base_url="http://127.0.0.1:9880"),
        tmp_path / "cache",
    )
    saved = service.save_voice_profile(
        "",
        {
            "name": "个人旁白",
            "engine": "gpt-sovits",
            "prompt_text": "篝火已经点亮。",
            "prompt_language": "zh-CN",
        },
        file_data=_wav_payload(),
        file_name="narrator.wav",
    )
    provider = _FakeProvider()
    monkeypatch.setattr(service, "_provider", lambda: provider)

    result = await service.synthesize(
        SpeechRequest(text="新的冒险开始了。", voice=saved["id"]),
        service.personal_voice_profiles(),
    )

    assert result.body.startswith(b"audio:")
    assert provider.voices[0].source == "personal"
    assert provider.voices[0].reference_audio.is_file()
    assert service.editable_voice_profiles()[0]["has_reference_audio"] is True
    assert "_reference_audio_path" not in service.editable_voice_profiles()[0]


def test_personal_openai_voice_id_and_server_reference_are_provider_native(tmp_path):
    service = SpeechService(_config(), tmp_path / "cache")
    openai_voice = service.save_voice_profile(
        "",
        {"name": "AllTalk Alice", "engine": "openai-compatible", "voice_id": "alice.wav"},
    )
    gpt_voice = service.save_voice_profile(
        "",
        {
            "name": "容器旁白",
            "engine": "gpt-sovits",
            "prompt_text": "测试文本",
            "server_reference_path": "/reference/narrator.wav",
        },
    )

    runtime = {profile["id"]: profile for profile in service.personal_voice_profiles()}
    assert runtime[openai_voice["id"]]["voice_id"] == "alice.wav"
    assert runtime[gpt_voice["id"]]["reference_audio_path"] == "/reference/narrator.wav"


def test_deleting_personal_voice_removes_unused_reference(tmp_path):
    service = SpeechService(_config(), tmp_path / "cache")
    saved = service.save_voice_profile(
        "",
        {
            "name": "临时音色",
            "engine": "gpt-sovits",
            "prompt_text": "测试文本",
        },
        file_data=_wav_payload(),
        file_name="voice.wav",
    )
    reference = Path(service.personal_voice_profiles()[0]["_reference_audio_path"])

    service.delete_voice_profile(saved["id"])

    assert service.editable_voice_profiles() == []
    assert not reference.exists()


@pytest.mark.asyncio
async def test_removed_plugin_voice_does_not_leak_runtime_id_upstream(tmp_path):
    service = SpeechService(_config(), tmp_path / "cache")

    with pytest.raises(SpeechServiceError, match="未安装或未启用"):
        await service.synthesize(
            SpeechRequest(text="测试", voice="plugin:gone:voice:narrator"),
            [],
        )


def test_browser_provider_needs_no_server_url(tmp_path):
    service = SpeechService(_config(tts_provider="browser", tts_base_url=""), tmp_path / "cache")

    assert service.backend_enabled is False
    assert service.public_config()["provider"] == "browser"


def test_local_tts_endpoint_bypasses_global_proxy(tmp_path):
    service = SpeechService(
        _config(tts_base_url="http://192.168.1.12:8880/v1"),
        tmp_path / "cache",
        proxy_url="http://proxy.example:8080",
    )

    assert service.proxy_url == ""


def test_game_speech_accepts_rendered_public_chunks_only():
    instance = SimpleNamespace(log=[{
        "gm_response": "**火焰**升起。\n---\nSTATE:heat:+1",
        "actions": [{"user_id": "p1", "text": "我检查门锁 [d20=12]"}],
    }])

    assert _is_public_game_text(instance, "火焰升起") is True
    assert _is_public_game_text(instance, "我检查门锁") is True
    assert _is_public_game_text(instance, "请替我朗读任意付费文本") is False


def test_openai_speech_url_accepts_root_v1_and_full_endpoint():
    assert _openai_speech_url("https://example.test") == "https://example.test/v1/audio/speech"
    assert _openai_speech_url("https://example.test/v1") == "https://example.test/v1/audio/speech"
    assert _openai_speech_url("https://example.test/v1/audio/speech") == "https://example.test/v1/audio/speech"
