"""Plugin contribution registry for static DiceFrame plugin resources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .map_validation import (
    MAP_IMAGE_KINDS,
    MAP_KINDS,
    validate_map_definition,
    validate_map_image,
    validate_map_references,
)
from .support import plugin_type_descriptor

_IMAGE_KINDS = frozenset({"portrait_asset", "scene_image_asset"}) | MAP_IMAGE_KINDS
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".ogg", ".opus", ".flac", ".m4a", ".aac"})
_VOICE_ENGINES = frozenset({"openai-compatible", "gpt-sovits"})
_NAMESPACED_KINDS = MAP_KINDS | frozenset({"voice_profile", "voice_asset"})


@dataclass(frozen=True)
class PluginContribution:
    plugin_id: str
    plugin_name: str
    plugin_type: str
    kind: str
    key: str
    path: Path
    relative_path: str
    title: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "plugin_name": self.plugin_name,
            "plugin_type": self.plugin_type,
            "kind": self.kind,
            "key": self.key,
            "path": self.relative_path,
            "title": self.title,
            "description": self.description,
        }


class ContributionRegistry:
    def __init__(self) -> None:
        self._items: list[PluginContribution] = []
        self._by_kind_key: dict[tuple[str, str], PluginContribution] = {}

    def clear(self) -> None:
        self._items.clear()
        self._by_kind_key.clear()

    def clear_plugin(self, plugin_id: str) -> None:
        kept = [item for item in self._items if item.plugin_id != plugin_id]
        self._items = []
        self._by_kind_key = {}
        for item in kept:
            self._add(item)

    def register_static_plugin(self, manifest: dict[str, Any], plugin_dir: Path) -> list[PluginContribution]:
        plugin_type = str(manifest.get("plugin_type") or "")
        mapping = plugin_type_descriptor(plugin_type).get("contributes")
        if not mapping:
            return []
        contributes = manifest.get("contributes")
        if contributes is None:
            return []
        if not isinstance(contributes, dict):
            raise ValueError("contributes 必须是对象")

        plugin_id = str(manifest.get("id") or "")
        self.clear_plugin(plugin_id)
        registered: list[PluginContribution] = []
        try:
            for field, value in contributes.items():
                kind = mapping.get(str(field))
                if not kind:
                    raise ValueError(f"{plugin_type} 不支持 contributes.{field}")
                for path in _expand_contribution_paths(plugin_dir, value):
                    item = _contribution_from_path(manifest, plugin_dir, kind, path)
                    self._add(item)
                    registered.append(item)
            if any(item.kind in MAP_KINDS for item in registered):
                validate_map_references(registered, plugin_dir)
            if any(item.kind == "voice_profile" for item in registered):
                _validate_voice_references(registered, plugin_dir)
        except Exception:
            self.clear_plugin(plugin_id)
            raise
        return registered

    def list(self, kind: str = "") -> list[PluginContribution]:
        if not kind:
            return list(self._items)
        return [item for item in self._items if item.kind == kind]

    def find(self, kind: str, key: str, *, plugin_id: str = "") -> PluginContribution | None:
        if kind not in _NAMESPACED_KINDS:
            return self._by_kind_key.get((kind, key))
        if plugin_id:
            return self._by_kind_key.get((kind, f"{plugin_id}:{key}"))
        matches = [item for item in self._items if item.kind == kind and item.key == key]
        return matches[0] if len(matches) == 1 else None

    def _add(self, item: PluginContribution) -> None:
        lookup_key = f"{item.plugin_id}:{item.key}" if item.kind in _NAMESPACED_KINDS else item.key
        existing = self._by_kind_key.get((item.kind, lookup_key))
        if existing:
            if item.kind in _NAMESPACED_KINDS:
                raise ValueError(f"插件内资源 ID 重复：{item.kind} {item.key}")
            if existing.plugin_id != item.plugin_id:
                raise ValueError(
                    f"插件资源冲突：{item.kind} {item.key} 已由 {existing.plugin_id} 提供"
                )
        self._items.append(item)
        self._by_kind_key[(item.kind, lookup_key)] = item


def validate_contributes(manifest: dict[str, Any], plugin_dir: Path) -> None:
    registry = ContributionRegistry()
    registry.register_static_plugin(manifest, plugin_dir)


def _expand_contribution_paths(plugin_dir: Path, value: Any) -> list[Path]:
    patterns = value if isinstance(value, list) else [value]
    if not all(isinstance(pattern, str) and pattern.strip() for pattern in patterns):
        raise ValueError("contributes 路径必须是非空字符串或字符串数组")
    paths: list[Path] = []
    for pattern in patterns:
        normalized = pattern.strip().replace("\\", "/")
        _validate_pattern(normalized)
        matches = sorted(plugin_dir.glob(normalized))
        for match in matches:
            resolved = match.resolve()
            _ensure_inside(plugin_dir, resolved)
            if resolved.is_file() and not resolved.is_symlink():
                paths.append(resolved)
    return paths


def _validate_pattern(pattern: str) -> None:
    candidate = Path(pattern)
    if candidate.is_absolute() or any(part in ("..", "") for part in candidate.parts):
        raise ValueError("contributes 路径不能是绝对路径或包含 ..")


def _contribution_from_path(
    manifest: dict[str, Any],
    plugin_dir: Path,
    kind: str,
    path: Path,
) -> PluginContribution:
    if kind in _IMAGE_KINDS and path.suffix.lower() not in _IMAGE_SUFFIXES:
        labels = {
            "portrait_asset": "头像",
            "scene_image_asset": "冒险头图",
            "map_icon": "地图图标",
            "map_scene": "地图底图",
        }
        raise ValueError(f"{labels[kind]}仅支持 PNG、JPEG 或 WebP：{path.relative_to(plugin_dir)}")
    if kind == "voice_asset" and path.suffix.lower() not in _AUDIO_SUFFIXES:
        raise ValueError(f"语音素材格式不受支持：{path.relative_to(plugin_dir)}")
    if kind in {"map_icon", "map_scene"}:
        validate_map_image(kind, path, path.relative_to(plugin_dir))
    key = path.stem
    if kind == "voice_asset":
        key = path.relative_to(plugin_dir).as_posix()
    title = path.stem
    description = ""
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"插件资源 JSON 无效：{path.relative_to(plugin_dir)}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"插件资源必须是 JSON 对象：{path.relative_to(plugin_dir)}")
        if kind == "rule":
            key = str(data.get("rule_id") or path.stem)
            title = str(data.get("rule_name") or key)
        elif kind == "world_template":
            key = str(data.get("world_id") or path.stem)
            title = str(data.get("world_name") or key)
        elif kind == "theme":
            key = str(data.get("id") or manifest.get("id") or path.stem)
            title = str(data.get("name") or manifest.get("name") or key)
        elif kind == "character_template":
            key = str(data.get("id") or data.get("card_id") or path.stem)
            title = str(data.get("character_name") or data.get("name") or key)
        else:
            key = str(data.get("id") or path.stem)
            title = str(data.get("name") or key)
        if kind == "map_definition":
            validate_map_definition(data, path.relative_to(plugin_dir))
        elif kind == "voice_profile":
            _validate_voice_profile(data, path.relative_to(plugin_dir))
        description = str(data.get("description") or "")
    if not key.strip():
        raise ValueError(f"插件资源 ID 不能为空：{path.relative_to(plugin_dir)}")
    return PluginContribution(
        plugin_id=str(manifest.get("id") or ""),
        plugin_name=str(manifest.get("name") or manifest.get("id") or ""),
        plugin_type=str(manifest.get("plugin_type") or ""),
        kind=kind,
        key=key.strip(),
        path=path,
        relative_path=path.relative_to(plugin_dir).as_posix(),
        title=title.strip(),
        description=description.strip(),
    )


def _ensure_inside(root: Path, target: Path) -> None:
    root = root.resolve()
    target = target.resolve()
    if target != root and root not in target.parents:
        raise ValueError("contributes 路径越界")


def _validate_voice_profile(data: dict[str, Any], relative_path: Path) -> None:
    if data.get("schema_version") != 1:
        raise ValueError(f"音色预设 schema_version 必须为 1：{relative_path}")
    voice_id = str(data.get("id") or "").strip()
    if not voice_id or len(voice_id) > 96:
        raise ValueError(f"音色预设 ID 不能为空且不能超过 96 字符：{relative_path}")
    if not str(data.get("name") or "").strip():
        raise ValueError(f"音色预设名称不能为空：{relative_path}")
    engine = str(data.get("engine") or "").strip()
    if engine not in _VOICE_ENGINES:
        raise ValueError(f"音色预设 engine 不受支持：{relative_path}")
    if not str(data.get("license") or "").strip():
        raise ValueError(f"音色预设必须声明 license：{relative_path}")
    if data.get("consent") is not True:
        raise ValueError(f"音色预设必须声明 consent=true，确认拥有音色与参考音频授权：{relative_path}")
    if engine == "openai-compatible" and not str(data.get("voice_id") or "").strip():
        raise ValueError(f"OpenAI 兼容音色预设必须声明 voice_id：{relative_path}")
    if engine == "gpt-sovits":
        if not str(data.get("reference_audio") or "").strip():
            raise ValueError(f"GPT-SoVITS 音色预设必须声明 reference_audio：{relative_path}")
        if not str(data.get("prompt_text") or "").strip():
            raise ValueError(f"GPT-SoVITS 音色预设必须声明参考音频 transcript（prompt_text）：{relative_path}")


def _validate_voice_references(items: list[PluginContribution], plugin_dir: Path) -> None:
    declared_assets = {
        (item.plugin_id, item.relative_path): item
        for item in items
        if item.kind == "voice_asset"
    }
    for item in items:
        if item.kind != "voice_profile":
            continue
        data = json.loads(item.path.read_text(encoding="utf-8"))
        for field in ("reference_audio", "preview_audio"):
            raw = str(data.get(field) or "").replace("\\", "/").strip("/")
            if not raw:
                continue
            _validate_pattern(raw)
            target = (plugin_dir / raw).resolve()
            _ensure_inside(plugin_dir, target)
            asset = declared_assets.get((item.plugin_id, raw))
            if asset is None or asset.path != target:
                raise ValueError(f"音色预设 {field} 必须在 contributes.voice_assets 中声明：{raw}")
            if field == "reference_audio" and target.suffix.lower() != ".wav":
                raise ValueError(f"GPT-SoVITS 参考音频必须为 WAV：{raw}")
