"""WebUI ASR domain: membership checks and transcription delegation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from src.asr import TranscriptionRequest, TranscriptionResult
from src.localization.messages import t


GameKey = tuple[str, ...]


class TranscriptionBackend(Protocol):
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult: ...


@dataclass(frozen=True)
class AsrDependencies:
    backend: TranscriptionBackend
    get_instance: Callable[[GameKey], Any | None]
    parse_game_key: Callable[[str], GameKey]


async def transcribe(
    dependencies: AsrDependencies,
    game_key: str,
    user_id: str,
    audio: bytes,
    content_type: str,
    language: str = "",
    owner: bool = False,
) -> dict[str, Any]:
    inst = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if inst is None:
        raise KeyError("游戏不存在")
    if not user_id or not (owner or user_id == inst.gm_uid or user_id in inst.players):
        raise PermissionError(t("asr.identity_not_in_game"))
    result = await dependencies.backend.transcribe(
        TranscriptionRequest(audio=audio, content_type=content_type, language=language),
    )
    return {"ok": True, **result.public_dict()}


async def test_transcription(
    dependencies: AsrDependencies,
    audio: bytes,
    content_type: str,
    language: str = "",
) -> dict[str, Any]:
    result = await dependencies.backend.transcribe(
        TranscriptionRequest(audio=audio, content_type=content_type, language=language),
    )
    return {"ok": True, **result.public_dict()}


class WebAsrService:
    """Game membership checks around the configured transcription backend."""

    def __init__(self, dependencies: AsrDependencies) -> None:
        self._dependencies = dependencies

    async def transcribe(
        self,
        game_key: str,
        user_id: str,
        audio: bytes,
        content_type: str,
        language: str = "",
        owner: bool = False,
    ) -> dict[str, Any]:
        return await transcribe(
            self._dependencies,
            game_key,
            user_id,
            audio,
            content_type,
            language,
            owner,
        )

    async def test_transcription(
        self, audio: bytes, content_type: str, language: str = "",
    ) -> dict[str, Any]:
        return await test_transcription(
            self._dependencies, audio, content_type, language,
        )
