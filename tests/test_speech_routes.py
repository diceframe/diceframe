from __future__ import annotations

import json

import pytest

from src.tts import SpeechAudio
from src.webui.routes import speech


class _FakeApi:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls = []
        self.profiles = []

    def list_speech_voices(self):
        return {"ok": True, "provider": "browser", "voices": []}

    async def synthesize_speech(self, game_key, user_id, text, voice, language, speed):
        self.calls.append((game_key, user_id, text, voice, language, speed))
        if self.error:
            raise self.error
        return SpeechAudio(b"audio", "audio/mpeg", "cache-key", cached=True)

    def list_personal_speech_profiles(self):
        return {"ok": True, "profiles": self.profiles, "total": len(self.profiles)}

    def save_personal_speech_profile(self, profile_id, values, *, file_data="", file_name=""):
        profile = {"id": profile_id or "personal:test", "name": values.get("name", "")}
        self.profiles.append(profile)
        return {"ok": True, "profile": profile}

    def delete_personal_speech_profile(self, profile_id):
        self.profiles = [profile for profile in self.profiles if profile["id"] != profile_id]
        return {"ok": True}


class _Request:
    def __init__(self, api, body=None):
        self.app = {"api": api}
        self.match_info = {"game_key": "web|room|bot"}
        self._body = body or {"text": "公开叙事", "voice": "alloy", "language": "zh-CN", "speed": 1.2}
        self.headers = {"X-TRPG-Confirm": "true"}
        self.query = {}

    def get(self, key, default=None):
        return "player" if key == "user_id" else default

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_game_speech_route_returns_cached_audio():
    api = _FakeApi()

    response = await speech.api_game_speech(_Request(api))

    assert response.status == 200
    assert response.body == b"audio"
    assert response.content_type == "audio/mpeg"
    assert response.headers["X-DiceFrame-TTS-Cache"] == "hit"
    assert api.calls == [("web|room|bot", "player", "公开叙事", "alloy", "zh-CN", 1.2)]


@pytest.mark.asyncio
async def test_game_speech_route_rejects_non_public_text():
    response = await speech.api_game_speech(_Request(
        _FakeApi(error=PermissionError("只能朗读本局公开时间线中的内容")),
    ))

    assert response.status == 403
    assert "公开时间线" in json.loads(response.text)["error"]


@pytest.mark.asyncio
async def test_personal_voice_profile_routes_use_admin_facade():
    api = _FakeApi()
    request = _Request(api, {"name": "本地 Alice", "engine": "openai-compatible", "voice_id": "alice"})

    created = await speech.api_speech_profile_create(request)
    listed = await speech.api_speech_profiles(request)

    assert created.status == 200
    assert json.loads(created.text)["profile"]["name"] == "本地 Alice"
    assert json.loads(listed.text)["total"] == 1


@pytest.mark.asyncio
async def test_player_share_cannot_manage_or_test_personal_tts():
    request = _Request(_FakeApi())
    request.query = {"user": "player"}

    profiles = await speech.api_speech_profiles(request)
    test_audio = await speech.api_test_speech(request)

    assert profiles.status == 403
    assert test_audio.status == 403
