"""WebUI speech domain: access checks, voice catalog and synthesis delegation."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from src.tts import SpeechRequest, VoiceProfile
from src.llm.parser import sanitize_narration

if TYPE_CHECKING:
    from src.webui.api import WebAPI


_OPENAI_BUILTIN_VOICES = (
    "alloy", "ash", "ballad", "coral", "echo", "fable",
    "nova", "onyx", "sage", "shimmer", "verse",
)


def _plugin_voice_profiles(api: "WebAPI") -> list[dict[str, Any]]:
    if not api._plugins or not hasattr(api._plugins, "list_voice_profiles"):
        return []
    return list(api._plugins.list_voice_profiles())


def _voice_profiles(api: "WebAPI") -> list[dict[str, Any]]:
    return [*api._speech.personal_voice_profiles(), *_plugin_voice_profiles(api)]


def list_voices(api: "WebAPI") -> dict[str, Any]:
    service = api._speech
    profiles = _voice_profiles(api)
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
    return {"ok": True, **service.public_config(), "voices": voices}


def list_personal_profiles(api: "WebAPI") -> dict[str, Any]:
    profiles = api._speech.editable_voice_profiles()
    return {"ok": True, "profiles": profiles, "total": len(profiles)}


def save_personal_profile(
    api: "WebAPI",
    profile_id: str,
    values: dict[str, Any],
    *,
    file_data: str = "",
    file_name: str = "",
) -> dict[str, Any]:
    profile = api._speech.save_voice_profile(
        profile_id,
        values,
        file_data=file_data,
        file_name=file_name,
    )
    return {"ok": True, "profile": profile}


def delete_personal_profile(api: "WebAPI", profile_id: str) -> dict[str, Any]:
    api._speech.delete_voice_profile(profile_id)
    return {"ok": True}


async def synthesize(
    api: "WebAPI",
    game_key: str,
    user_id: str,
    text: str,
    voice: str = "",
    language: str = "zh-CN",
    speed: float = 1.0,
):
    inst = api._reg.get(api._parse_key(game_key))
    if inst is None:
        raise KeyError("游戏不存在")
    if not user_id or (user_id != inst.gm_uid and user_id not in inst.players):
        raise PermissionError("当前身份不属于本局游戏")
    if not _is_public_game_text(inst, text):
        raise PermissionError("只能朗读本局公开时间线中的内容")
    return await api._speech.synthesize(
        SpeechRequest(text=text, voice=voice, language=language, speed=speed),
        _voice_profiles(api),
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
    api: "WebAPI",
    text: str,
    voice: str = "",
    language: str = "zh-CN",
    speed: float = 1.0,
):
    return await api._speech.synthesize(
        SpeechRequest(text=text, voice=voice, language=language, speed=speed),
        _voice_profiles(api),
    )
