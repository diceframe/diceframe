"""WebUI speech domain: access checks, voice catalog and synthesis delegation."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Callable

from src.tts import SpeechRequest, VoiceProfile
from src.llm.parser import sanitize_narration
from src.localization.messages import t

_OPENAI_BUILTIN_VOICES = (
    "alloy", "ash", "ballad", "coral", "echo", "fable",
    "nova", "onyx", "sage", "shimmer", "verse",
)

_EDGE_TTS_BUILTIN_VOICES = (
    ("zh-CN-XiaoxiaoNeural", "晓晓（女声·温柔）"),
    ("zh-CN-XiaoyiNeural", "晓伊（女声·活泼）"),
    ("zh-CN-YunjianNeural", "云健（男声·浑厚）"),
    ("zh-CN-YunxiNeural", "云希（男声·阳光）"),
    ("zh-CN-YunxiaNeural", "云夏（男声·少年）"),
    ("zh-CN-YunyangNeural", "云扬（男声·播音）"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北（东北话·女声）"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮（陕西话·女声）"),
    ("zh-HK-HiuMaanNeural", "曉昐（粤语·女声）"),
    ("zh-HK-WanLungNeural", "雲龍（粤语·男声）"),
    ("zh-TW-HsiaoChenNeural", "曉臻（台湾腔·女声）"),
    ("zh-TW-YunJheNeural", "雲哲（台湾腔·男声）"),
    ("ja-JP-NanamiNeural", "七海（日语·女声）"),
    ("ja-JP-KeitaNeural", "圭太（日语·男声）"),
    ("en-US-AriaNeural", "Aria（英语·女声）"),
    ("en-US-GuyNeural", "Guy（英语·男声）"),
)


GameKey = tuple[str, ...]


@dataclass(frozen=True)
class SpeechDependencies:
    backend: Any
    plugin_host: Any | None
    get_instance: Callable[[GameKey], Any | None]
    parse_game_key: Callable[[str], GameKey]


def _plugin_voice_profiles(
    dependencies: SpeechDependencies,
) -> list[dict[str, Any]]:
    host = dependencies.plugin_host
    if not host or not hasattr(host, "list_voice_profiles"):
        return []
    return list(host.list_voice_profiles())


def _voice_profiles(dependencies: SpeechDependencies) -> list[dict[str, Any]]:
    return [
        *dependencies.backend.personal_voice_profiles(),
        *_plugin_voice_profiles(dependencies),
    ]


def list_voices(dependencies: SpeechDependencies) -> dict[str, Any]:
    service = dependencies.backend
    profiles = _voice_profiles(dependencies)
    voices = [VoiceProfile.from_mapping(item).public_dict() for item in profiles]
    known = {voice["id"] for voice in voices}
    if service.provider_id == "openai-compatible":
        for voice_id in _OPENAI_BUILTIN_VOICES:
            if voice_id not in known:
                voices.append({
                    "id": voice_id,
                    "name": voice_id.title(),
                    "engine": "openai-compatible",
                    "voice_id": voice_id,
                    "language": "",
                    "description": "",
                    "plugin_id": "",
                    "plugin_name": "",
                    "preview_url": "",
                    "license": "provider",
                })
    elif service.provider_id == "edge-tts":
        for voice_id, name in _EDGE_TTS_BUILTIN_VOICES:
            if voice_id not in known:
                voices.append({
                    "id": voice_id,
                    "name": name,
                    "engine": "edge-tts",
                    "voice_id": voice_id,
                    "language": voice_id.rsplit("-", 1)[0],
                    "description": "",
                    "plugin_id": "",
                    "plugin_name": "",
                    "preview_url": "",
                    "license": "provider",
                })
    return {"ok": True, **service.public_config(), "voices": voices}


def list_personal_profiles(dependencies: SpeechDependencies) -> dict[str, Any]:
    profiles = dependencies.backend.editable_voice_profiles()
    return {"ok": True, "profiles": profiles, "total": len(profiles)}


def save_personal_profile(
    dependencies: SpeechDependencies,
    profile_id: str,
    values: dict[str, Any],
    *,
    file_data: str = "",
    file_name: str = "",
) -> dict[str, Any]:
    profile = dependencies.backend.save_voice_profile(
        profile_id,
        values,
        file_data=file_data,
        file_name=file_name,
    )
    return {"ok": True, "profile": profile}


def delete_personal_profile(
    dependencies: SpeechDependencies, profile_id: str,
) -> dict[str, Any]:
    dependencies.backend.delete_voice_profile(profile_id)
    return {"ok": True}


async def synthesize(
    dependencies: SpeechDependencies,
    game_key: str,
    user_id: str,
    text: str,
    voice: str = "",
    language: str = "zh-CN",
    speed: float = 1.0,
    owner: bool = False,
):
    inst = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if inst is None:
        raise KeyError("游戏不存在")
    if not user_id or not (owner or user_id == inst.gm_uid or user_id in inst.players):
        raise PermissionError(t("webui.services.speech.identity_not_in_game"))
    if not _is_public_game_text(inst, text):
        raise PermissionError(t("webui.services.speech.public_timeline_only"))
    return await dependencies.backend.synthesize(
        SpeechRequest(text=text, voice=voice, language=language, speed=speed),
        _voice_profiles(dependencies),
    )


def _is_public_game_text(inst: Any, requested: str) -> bool:
    needle = _speech_signature(requested)
    if not needle:
        return False
    for entry in getattr(inst, "log", []) or []:
        if not isinstance(entry, dict):
            continue
        narration = sanitize_narration(str(entry.get("gm_response") or ""))
        if needle in _speech_signature(narration):
            return True
        actions = entry.get("player_actions") or entry.get("actions") or []
        if isinstance(actions, dict):
            action_texts = actions.values()
        elif isinstance(actions, list):
            action_texts = (
                action.get("text") or action.get("action") or ""
                if isinstance(action, dict) else action
                for action in actions
                if not isinstance(action, dict) or action.get("user_id") != "system"
            )
        else:
            action_texts = []
        if any(needle in _speech_signature(str(value or "")) for value in action_texts):
            return True
    return False


def _speech_signature(value: str) -> str:
    """Compare rendered chunks with source logs while ignoring markup and spacing."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))
    return "".join(char.casefold() for char in plain if char.isalnum())


async def test_synthesis(
    dependencies: SpeechDependencies,
    text: str,
    voice: str = "",
    language: str = "zh-CN",
    speed: float = 1.0,
):
    return await dependencies.backend.synthesize(
        SpeechRequest(text=text, voice=voice, language=language, speed=speed),
        _voice_profiles(dependencies),
    )


class WebSpeechService:
    """Voice catalogs and game-scoped synthesis over explicit dependencies."""

    def __init__(self, dependencies: SpeechDependencies) -> None:
        self._dependencies = dependencies

    def list_voices(self) -> dict[str, Any]:
        return list_voices(self._dependencies)

    def list_personal_profiles(self) -> dict[str, Any]:
        return list_personal_profiles(self._dependencies)

    def save_personal_profile(
        self,
        profile_id: str,
        values: dict[str, Any],
        *,
        file_data: str = "",
        file_name: str = "",
    ) -> dict[str, Any]:
        return save_personal_profile(
            self._dependencies,
            profile_id,
            values,
            file_data=file_data,
            file_name=file_name,
        )

    def delete_personal_profile(self, profile_id: str) -> dict[str, Any]:
        return delete_personal_profile(self._dependencies, profile_id)

    async def synthesize(
        self,
        game_key: str,
        user_id: str,
        text: str,
        voice: str = "",
        language: str = "zh-CN",
        speed: float = 1.0,
        owner: bool = False,
    ):
        return await synthesize(
            self._dependencies,
            game_key,
            user_id,
            text,
            voice,
            language,
            speed,
            owner,
        )

    async def test_synthesis(
        self,
        text: str,
        voice: str = "",
        language: str = "zh-CN",
        speed: float = 1.0,
    ):
        return await test_synthesis(
            self._dependencies, text, voice, language, speed,
        )
