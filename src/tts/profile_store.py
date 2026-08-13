"""Persistent personal voice profiles, independent from plugin packages."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import threading
import uuid
import wave
from pathlib import Path
from typing import Any


PROFILE_SCHEMA_VERSION = 1
MAX_REFERENCE_AUDIO_BYTES = 10 * 1024 * 1024
MAX_REFERENCE_DURATION_SECONDS = 120
PROFILE_ID_RE = re.compile(r"^personal:[a-f0-9]{32}$")
SUPPORTED_ENGINES = frozenset({"openai-compatible", "gpt-sovits"})


class VoiceProfileStore:
    """Store user-created provider aliases and GPT-SoVITS references."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.state_path = self.root / "profiles.json"
        self.references_dir = self.root / "references"
        self._lock = threading.RLock()

    def runtime_profiles(self) -> list[dict[str, Any]]:
        """Return profiles with private paths for synthesis, never for JSON output."""
        with self._lock:
            return [self._runtime_profile(item) for item in self._load()]

    def editable_profiles(self) -> list[dict[str, Any]]:
        """Return admin-safe metadata used by the profile editor."""
        with self._lock:
            return [self._editable_profile(item) for item in self._load()]

    def save(
        self,
        profile_id: str,
        values: dict[str, Any],
        *,
        file_data: str = "",
        file_name: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            profiles = self._load()
            existing = next((item for item in profiles if item.get("id") == profile_id), None)
            if profile_id and existing is None:
                raise KeyError("个人音色不存在")
            normalized = self._normalize(values, existing)
            normalized["id"] = profile_id or f"personal:{uuid.uuid4().hex}"
            if file_data:
                asset_id = self._save_reference(file_data, file_name)
                normalized["reference_asset"] = asset_id
                normalized["server_reference_path"] = ""
            elif normalized.get("server_reference_path"):
                normalized.pop("reference_asset", None)
            if normalized["engine"] == "gpt-sovits":
                if not normalized.get("reference_asset") and not normalized.get("server_reference_path"):
                    raise ValueError("GPT-SoVITS 音色需要上传参考 WAV，或填写服务端可见路径")
                if not normalized.get("prompt_text"):
                    raise ValueError("GPT-SoVITS 音色需要填写参考音频对应文本")
            else:
                normalized.pop("reference_asset", None)
                normalized["server_reference_path"] = ""
                normalized["prompt_text"] = ""
                normalized["prompt_language"] = ""
            if existing is None:
                profiles.append(normalized)
            else:
                profiles[profiles.index(existing)] = normalized
            self._write(profiles)
            self._remove_unused_references(profiles)
            return self._editable_profile(normalized)

    def delete(self, profile_id: str) -> None:
        if not PROFILE_ID_RE.fullmatch(str(profile_id or "")):
            raise KeyError("个人音色不存在")
        with self._lock:
            profiles = self._load()
            kept = [item for item in profiles if item.get("id") != profile_id]
            if len(kept) == len(profiles):
                raise KeyError("个人音色不存在")
            self._write(kept)
            self._remove_unused_references(kept)

    def _normalize(
        self,
        values: dict[str, Any],
        existing: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise ValueError("个人音色必须是 JSON 对象")
        engine = _bounded(values.get("engine"), 32).lower()
        if engine not in SUPPORTED_ENGINES:
            raise ValueError("个人音色引擎无效")
        name = _bounded(values.get("name"), 80)
        if not name:
            raise ValueError("请填写音色名称")
        voice_id = _bounded(values.get("voice_id"), 200)
        if engine == "openai-compatible" and not voice_id:
            raise ValueError("OpenAI 兼容音色需要填写服务端 voice ID")
        server_path = _bounded(values.get("server_reference_path"), 1024)
        if "\x00" in server_path:
            raise ValueError("参考音频路径无效")
        normalized = {
            "id": str((existing or {}).get("id") or ""),
            "name": name,
            "engine": engine,
            "voice_id": voice_id,
            "language": _bounded(values.get("language"), 32),
            "description": _bounded(values.get("description"), 500),
            "prompt_text": _bounded(values.get("prompt_text"), 2000),
            "prompt_language": _bounded(values.get("prompt_language"), 32),
            "server_reference_path": server_path,
        }
        reference_asset = str((existing or {}).get("reference_asset") or "")
        if reference_asset:
            normalized["reference_asset"] = reference_asset
        return normalized

    def _save_reference(self, file_data: str, file_name: str) -> str:
        if len(file_data) > (MAX_REFERENCE_AUDIO_BYTES * 4 // 3) + 32:
            raise ValueError("参考 WAV 不能超过 10 MB")
        try:
            raw = base64.b64decode(file_data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("参考 WAV 数据无效") from exc
        if not raw or len(raw) > MAX_REFERENCE_AUDIO_BYTES:
            raise ValueError("参考 WAV 不能超过 10 MB")
        if file_name and Path(file_name).suffix.lower() != ".wav":
            raise ValueError("参考音频仅支持 WAV")
        try:
            with wave.open(io.BytesIO(raw), "rb") as audio:
                rate = audio.getframerate()
                frames = audio.getnframes()
                channels = audio.getnchannels()
                sample_width = audio.getsampwidth()
        except (wave.Error, EOFError) as exc:
            raise ValueError("无法读取参考 WAV") from exc
        if rate < 8000 or rate > 96000 or channels not in {1, 2} or sample_width not in {1, 2, 3, 4}:
            raise ValueError("参考 WAV 的采样率、声道数或位深不受支持")
        duration = frames / rate if rate else 0
        if duration < 0.5 or duration > MAX_REFERENCE_DURATION_SECONDS:
            raise ValueError("参考 WAV 时长必须在 0.5–120 秒之间")
        asset_id = hashlib.sha256(raw).hexdigest()
        self.references_dir.mkdir(parents=True, exist_ok=True)
        path = self.references_dir / f"{asset_id}.wav"
        if not path.exists():
            temporary = path.with_suffix(".wav.tmp")
            temporary.write_bytes(raw)
            temporary.replace(path)
        return asset_id

    def _runtime_profile(self, item: dict[str, Any]) -> dict[str, Any]:
        profile = dict(item)
        profile["source"] = "personal"
        profile["license"] = "personal"
        asset_id = str(item.get("reference_asset") or "")
        if asset_id:
            path = self.references_dir / f"{asset_id}.wav"
            if path.is_file():
                profile["_reference_audio_path"] = str(path)
        server_path = str(item.get("server_reference_path") or "")
        if server_path:
            profile["reference_audio_path"] = server_path
        return profile

    @staticmethod
    def _editable_profile(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or ""),
            "engine": str(item.get("engine") or ""),
            "voice_id": str(item.get("voice_id") or ""),
            "language": str(item.get("language") or ""),
            "description": str(item.get("description") or ""),
            "prompt_text": str(item.get("prompt_text") or ""),
            "prompt_language": str(item.get("prompt_language") or ""),
            "server_reference_path": str(item.get("server_reference_path") or ""),
            "has_reference_audio": bool(item.get("reference_asset")),
            "source": "personal",
        }

    def _load(self) -> list[dict[str, Any]]:
        if not self.state_path.is_file():
            return []
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict) or payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
            return []
        profiles = payload.get("profiles")
        if not isinstance(profiles, list):
            return []
        return [item for item in profiles if isinstance(item, dict) and PROFILE_ID_RE.fullmatch(str(item.get("id") or ""))]

    def _write(self, profiles: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": PROFILE_SCHEMA_VERSION, "profiles": profiles}
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def _remove_unused_references(self, profiles: list[dict[str, Any]]) -> None:
        if not self.references_dir.is_dir():
            return
        used = {str(item.get("reference_asset") or "") for item in profiles}
        for path in self.references_dir.glob("*.wav"):
            if path.stem not in used:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue


def _bounded(value: Any, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise ValueError(f"个人音色字段不能超过 {maximum} 个字符")
    return text
