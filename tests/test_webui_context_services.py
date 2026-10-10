from __future__ import annotations

import pytest

from src.asr import TranscriptionResult
from src.engine.game_instance import GameInstance
from src.webui.services.asr import AsrDependencies, WebAsrService
from src.webui.services.knowledge import LorePreviewDependencies, LorePreviewService
from src.webui.services.kp_questions import KPQuestionDependencies, KPQuestionService
from src.engine.modules import private_channels


class FakeRegistry:
    def __init__(self, instance: GameInstance) -> None:
        self.instance = instance
        self.saved = 0

    def get(self, game_key):
        return self.instance if game_key == self.instance.game_key else None

    async def save(self, instance):
        assert instance is self.instance
        self.saved += 1


class FakeTranscriptionBackend:
    async def transcribe(self, request):
        return TranscriptionResult(
            text=f"heard:{request.language}", provider="fake", model="test",
        )


class FakeLorebook:
    def get_world(self, world_id):
        return {"id": world_id} if world_id == "world" else None

    def list_entries(self, world_id):
        assert world_id == "world"
        return [
            {"id": "public", "visible_to": ["public"]},
            {"id": "secret", "visible_to": []},
        ]


def _parse_key(raw: str) -> tuple[str, ...]:
    return tuple(raw.split("|"))


@pytest.mark.asyncio
async def test_web_asr_checks_membership_before_using_backend():
    instance = GameInstance(("web", "room", "bot"), gm_uid="gm")
    instance.players["player"] = {"character_name": "Player"}
    registry = FakeRegistry(instance)
    service = WebAsrService(AsrDependencies(
        backend=FakeTranscriptionBackend(),
        get_instance=registry.get,
        parse_game_key=_parse_key,
    ))

    result = await service.transcribe(
        "web|room|bot", "player", b"audio", "audio/webm", "zh-CN",
    )

    assert result == {
        "ok": True,
        "text": "heard:zh-CN",
        "provider": "fake",
        "model": "test",
    }
    with pytest.raises(PermissionError):
        await service.transcribe(
            "web|room|bot", "outsider", b"audio", "audio/webm",
        )


def test_lore_preview_uses_explicit_lorebook_and_game_lookup():
    instance = GameInstance(("web", "room", "bot"))
    registry = FakeRegistry(instance)
    service = LorePreviewService(LorePreviewDependencies(
        lorebook=FakeLorebook(),
        get_instance=registry.get,
        parse_game_key=_parse_key,
    ))

    result = service.preview("world", "party")

    assert result["status"] == 200
    assert result["payload"]["summary"] == {
        "total": 2,
        "visible": 1,
        "public": 1,
        "character_only": 0,
        "gm_secret": 1,
    }


@pytest.mark.asyncio
async def test_party_kp_question_persists_only_the_public_exchange():
    instance = GameInstance(("web", "room", "bot"))
    instance.players["player"] = {"character_name": "Player"}
    registry = FakeRegistry(instance)

    async def answer(_snapshot, _actor, _question, *, visibility):
        return {"answer": f"answer:{visibility}", "total_tokens": 7}

    service = KPQuestionService(KPQuestionDependencies(
        registry=registry,
        parse_game_key=_parse_key,
        answer_question=answer,
    ))

    private = await service.ask("web|room|bot", "player", "secret?")
    party = await service.ask(
        "web|room|bot", "player", "public?", visibility="party",
    )

    assert private["payload"]["exchange"] is None
    assert registry.saved == 1
    assert party["payload"]["exchange"]["question"] == "public?"
    assert private_channels.table_talk(instance)[-1]["visibility"] == "party"
