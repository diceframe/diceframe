"""Provider-neutral speech synthesis with bounded on-disk caching."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .contracts import (
    SUPPORTED_AUDIO_FORMATS,
    SUPPORTED_PROVIDER_IDS,
    SpeechAudio,
    SpeechRequest,
    VoiceProfile,
)
from .providers import GptSovitsProvider, OpenAICompatibleProvider, ProviderError, SpeechProvider
from .profile_store import VoiceProfileStore


MAX_SPEECH_TEXT_CHARS = 2000
MIN_CACHE_BYTES = 16 * 1024 * 1024
MAX_CACHE_BYTES = 2048 * 1024 * 1024


class SpeechServiceError(RuntimeError):
    pass


class SpeechService:
    def __init__(
        self,
        config: dict[str, Any],
        cache_dir: Path,
        *,
        proxy_url: str = "",
        profiles_dir: Path | None = None,
    ) -> None:
        self.provider_id = str(config.get("tts_provider") or "browser").strip()
        self.base_url = str(config.get("tts_base_url") or "").strip()
        self.api_key = str(config.get("tts_api_key") or "").strip()
        self.model = str(config.get("tts_model") or "tts-1").strip()
        self.audio_format = str(config.get("tts_audio_format") or "mp3").strip().lower()
        self.default_voice = str(config.get("tts_default_voice") or "alloy").strip()
        self.gm_voice = str(config.get("tts_gm_voice") or "").strip()
        self.player_voice = str(config.get("tts_player_voice") or "").strip()
        self.timeout_seconds = float(config.get("tts_timeout_seconds") or 60)
        cache_mb = max(16, min(int(config.get("tts_cache_mb") or 256), 2048))
        self.cache_limit_bytes = max(MIN_CACHE_BYTES, min(cache_mb * 1024 * 1024, MAX_CACHE_BYTES))
        self.cache_dir = cache_dir.resolve()
        self.profile_store = VoiceProfileStore(profiles_dir or (self.cache_dir.parent / "tts-profiles"))
        self.proxy_url = "" if _is_local_endpoint(self.base_url) else proxy_url
        self._locks: dict[str, asyncio.Lock] = {}
        self._validate_config()

    @property
    def backend_enabled(self) -> bool:
        return self.provider_id != "browser"

    def public_config(self) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "backend_enabled": self.backend_enabled,
            "model": self.model,
            "audio_format": self.audio_format,
            "default_voice": self.default_voice,
            "gm_voice": self.gm_voice,
            "player_voice": self.player_voice,
            "max_text_chars": MAX_SPEECH_TEXT_CHARS,
        }

    def personal_voice_profiles(self) -> list[dict[str, Any]]:
        return self.profile_store.runtime_profiles()

    def editable_voice_profiles(self) -> list[dict[str, Any]]:
        return self.profile_store.editable_profiles()

    def save_voice_profile(
        self,
        profile_id: str,
        values: dict[str, Any],
        *,
        file_data: str = "",
        file_name: str = "",
    ) -> dict[str, Any]:
        return self.profile_store.save(
            profile_id,
            values,
            file_data=file_data,
            file_name=file_name,
        )

    def delete_voice_profile(self, profile_id: str) -> None:
        self.profile_store.delete(profile_id)

    async def synthesize(
        self,
        request: SpeechRequest,
        voice_profiles: list[dict[str, Any]] | None = None,
    ) -> SpeechAudio:
        if not self.backend_enabled:
            raise SpeechServiceError("当前使用浏览器语音，不需要服务端合成")
        clean_text = _normalize_text(request.text)
        if not clean_text:
            raise SpeechServiceError("朗读文本不能为空")
        if len(clean_text) > MAX_SPEECH_TEXT_CHARS:
            raise SpeechServiceError(f"单段朗读不能超过 {MAX_SPEECH_TEXT_CHARS} 个字符")
        normalized = SpeechRequest(
            text=clean_text,
            voice=str(request.voice or self.default_voice).strip(),
            language=str(request.language or "zh-CN").strip(),
            speed=max(0.5, min(float(request.speed), 5.0)),
        )
        profile = self._resolve_voice(normalized.voice, voice_profiles or [])
        cache_key = self._cache_key(normalized, profile)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return SpeechAudio(cached[0], cached[1], cache_key, cached=True)

        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        try:
            async with lock:
                cached = self._read_cache(cache_key)
                if cached is not None:
                    return SpeechAudio(cached[0], cached[1], cache_key, cached=True)
                try:
                    result = await self._provider().synthesize(normalized, profile)
                except ProviderError as exc:
                    raise SpeechServiceError(str(exc)) from exc
                self._write_cache(cache_key, result.body, result.content_type)
                return SpeechAudio(result.body, result.content_type, cache_key, cached=False)
        finally:
            if not lock.locked():
                self._locks.pop(cache_key, None)

    def _provider(self) -> SpeechProvider:
        kwargs = {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "audio_format": self.audio_format,
            "timeout_seconds": self.timeout_seconds,
            "proxy_url": self.proxy_url,
        }
        if self.provider_id == "openai-compatible":
            return OpenAICompatibleProvider(**kwargs)
        if self.provider_id == "gpt-sovits":
            return GptSovitsProvider(**kwargs)
        raise SpeechServiceError(f"不支持的 TTS provider：{self.provider_id}")

    def _resolve_voice(self, voice_id: str, profiles: list[dict[str, Any]]) -> VoiceProfile | None:
        match = next((item for item in profiles if str(item.get("id") or "") == voice_id), None)
        if match is None:
            if voice_id.startswith(("plugin:", "personal:")):
                raise SpeechServiceError("选择的音色已删除、未安装或未启用")
            if self.provider_id == "gpt-sovits":
                raise SpeechServiceError("GPT-SoVITS 必须选择个人音色或已安装的音色预设")
            return None
        profile = VoiceProfile.from_mapping(match)
        if profile.engine != self.provider_id:
            raise SpeechServiceError(
                f"语音包 {profile.name or profile.id} 适用于 {profile.engine}，与当前 provider 不匹配"
            )
        return profile

    def _cache_key(self, request: SpeechRequest, profile: VoiceProfile | None) -> str:
        reference_mtime = 0
        if profile and profile.reference_audio:
            try:
                reference_mtime = profile.reference_audio.stat().st_mtime_ns
            except OSError:
                reference_mtime = 0
        value = {
            "schema": 1,
            "provider": self.provider_id,
            "base_url": self.base_url,
            "model": self.model,
            "format": self.audio_format,
            "text": request.text,
            "voice": request.voice,
            "language": request.language,
            "speed": request.speed,
            "profile": {
                "id": profile.id,
                "engine": profile.engine,
                "voice_id": profile.voice_id,
                "prompt_text": profile.prompt_text,
                "prompt_language": profile.prompt_language,
                "reference_audio_path": profile.reference_audio_path,
            } if profile else None,
            "profile_mtime": reference_mtime,
        }
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _read_cache(self, cache_key: str) -> tuple[bytes, str] | None:
        meta_path = self.cache_dir / f"{cache_key}.json"
        audio_path = self.cache_dir / f"{cache_key}.audio"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = audio_path.read_bytes()
            if not body or not isinstance(meta, dict):
                return None
            now = time.time()
            os.utime(audio_path, (now, now))
            os.utime(meta_path, (now, now))
            return body, str(meta.get("content_type") or "application/octet-stream")
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, cache_key: str, body: bytes, content_type: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        audio_path = self.cache_dir / f"{cache_key}.audio"
        meta_path = self.cache_dir / f"{cache_key}.json"
        audio_tmp = audio_path.with_suffix(".audio.tmp")
        meta_tmp = meta_path.with_suffix(".json.tmp")
        audio_tmp.write_bytes(body)
        meta_tmp.write_text(json.dumps({"content_type": content_type}), encoding="utf-8")
        audio_tmp.replace(audio_path)
        meta_tmp.replace(meta_path)
        self._trim_cache()

    def _trim_cache(self) -> None:
        try:
            files = [path for path in self.cache_dir.glob("*.audio") if path.is_file()]
            total = sum(path.stat().st_size for path in files)
            if total <= self.cache_limit_bytes:
                return
            for path in sorted(files, key=lambda item: item.stat().st_mtime):
                size = path.stat().st_size
                path.unlink(missing_ok=True)
                path.with_suffix(".json").unlink(missing_ok=True)
                total -= size
                if total <= self.cache_limit_bytes:
                    break
        except OSError:
            # Cache maintenance must never make a successful synthesis fail.
            return

    def _validate_config(self) -> None:
        if self.provider_id not in SUPPORTED_PROVIDER_IDS:
            raise ValueError(f"不支持的 TTS provider：{self.provider_id}")
        if self.audio_format not in SUPPORTED_AUDIO_FORMATS:
            raise ValueError(f"不支持的 TTS 音频格式：{self.audio_format}")
        if self.backend_enabled:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("TTS Base URL 必须是无内嵌凭据的 http(s) 地址")


def _normalize_text(value: str) -> str:
    return "\n".join(line.strip() for line in str(value or "").replace("\x00", "").splitlines() if line.strip())


def _is_local_endpoint(value: str) -> bool:
    hostname = (urlparse(value).hostname or "").strip().lower()
    if hostname in {"localhost", "host.docker.internal"} or hostname.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_private
    except ValueError:
        return False
