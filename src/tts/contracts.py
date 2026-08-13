"""Stable contracts shared by TTS providers and the application layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_AUDIO_FORMATS = frozenset({"mp3", "opus", "aac", "flac", "wav", "pcm"})
SUPPORTED_PROVIDER_IDS = frozenset({"browser", "openai-compatible", "gpt-sovits"})


@dataclass(frozen=True)
class VoiceProfile:
    """A provider-neutral voice descriptor, optionally supplied by a voice pack."""

    id: str
    name: str
    engine: str
    voice_id: str = ""
    language: str = ""
    description: str = ""
    plugin_id: str = ""
    plugin_name: str = ""
    preview_url: str = ""
    reference_audio: Path | None = None
    prompt_text: str = ""
    prompt_language: str = ""
    license: str = ""
    source: str = ""
    reference_audio_path: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "VoiceProfile":
        reference = value.get("_reference_audio_path")
        return cls(
            id=str(value.get("id") or "").strip(),
            name=str(value.get("name") or value.get("id") or "").strip(),
            engine=str(value.get("engine") or "").strip(),
            voice_id=str(value.get("voice_id") or "").strip(),
            language=str(value.get("language") or "").strip(),
            description=str(value.get("description") or "").strip(),
            plugin_id=str(value.get("plugin_id") or "").strip(),
            plugin_name=str(value.get("plugin_name") or "").strip(),
            preview_url=str(value.get("preview_url") or "").strip(),
            reference_audio=Path(reference).resolve() if reference else None,
            prompt_text=str(value.get("prompt_text") or "").strip(),
            prompt_language=str(value.get("prompt_language") or "").strip(),
            license=str(value.get("license") or "").strip(),
            source=str(value.get("source") or "").strip(),
            reference_audio_path=str(value.get("reference_audio_path") or "").strip(),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "engine": self.engine,
            "voice_id": self.voice_id,
            "language": self.language,
            "description": self.description,
            "plugin_id": self.plugin_id,
            "plugin_name": self.plugin_name,
            "preview_url": self.preview_url,
            "license": self.license,
            "source": self.source,
        }


@dataclass(frozen=True)
class SpeechRequest:
    text: str
    voice: str = ""
    language: str = "zh-CN"
    speed: float = 1.0


@dataclass(frozen=True)
class SpeechAudio:
    body: bytes
    content_type: str
    cache_key: str
    cached: bool = False
