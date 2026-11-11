"""Plugin contribution registry for static DiceFrame plugin resources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from src.content.contracts import ResourceRef, asset_local_id, canonical_id

from .contracts import PluginContributionView, PluginManifest
from .map_validation import (
    MAP_IMAGE_KINDS,
    MAP_KINDS,
    validate_map_definition,
    validate_map_image,
    validate_map_references,
)
from .support import plugin_type_descriptor
from src.localization.messages import t

_IMAGE_KINDS = frozenset({"portrait_asset", "scene_image_asset", "standing_portrait"}) | MAP_IMAGE_KINDS
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".ogg", ".opus", ".flac", ".m4a", ".aac"})
_VOICE_ENGINES = frozenset({"openai-compatible", "gpt-sovits", "edge-tts"})
_NAMESPACED_KINDS = MAP_KINDS | frozenset({"voice_profile", "voice_asset"})
_V2_CANONICAL_KINDS = frozenset({
    "character_template", "npc", "item", "spell", "class", "rule", "world_template",
    "world", "lorebook", "adventure",
})


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
    content_schema_version: int = 1
    default_locale: str = ""

    @property
    def ref(self) -> ResourceRef:
        # Asset paths are legacy local keys; retain the original key while
        # exposing a canonical identity for the V2 registry. Asset file names
        # are free-form author input (assets are referenced by path, not by
        # id), so their identity is auto-normalized instead of enforced.
        if self.kind.endswith("_asset"):
            local_id = asset_local_id(self.key)
        elif self.content_schema_version >= 2 and self.kind in _V2_CANONICAL_KINDS:
            local_id = canonical_id(self.key)
        else:
            local_id = re.sub(r"[^a-zA-Z0-9_-]", "_", self.key).lower().strip("_") or "asset"
        return ResourceRef(f"plugin:{self.plugin_id}", self.kind, local_id)

    def to_dict(self) -> PluginContributionView:
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
        self._by_ref: dict[ResourceRef, PluginContribution] = {}
        self._ambiguous: set[tuple[str, str]] = set()

    def clear(self) -> None:
        self._items.clear()
        self._by_kind_key.clear()
        self._by_ref.clear()
        self._ambiguous.clear()

    def clear_plugin(self, plugin_id: str) -> None:
        kept = [item for item in self._items if item.plugin_id != plugin_id]
        self._items = []
        self._by_kind_key = {}
        self._by_ref = {}
        self._ambiguous = set()
        for item in kept:
            self._add(item)

    def register_static_plugin(self, manifest: PluginManifest, plugin_dir: Path) -> list[PluginContribution]:
        plugin_type = str(manifest.get("plugin_type") or "")
        mapping = plugin_type_descriptor(plugin_type).get("contributes")
        if not mapping:
            return []
        contributes = manifest.get("contributes")
        if contributes is None:
            return []
        if not isinstance(contributes, dict):
            raise ValueError(t("plugin_host_registry.contributes_not_object"))

        plugin_id = str(manifest.get("id") or "")
        self.clear_plugin(plugin_id)
        registered: list[PluginContribution] = []
        try:
            for field, value in contributes.items():
                kind = mapping.get(str(field))
                if not kind:
                    raise ValueError(t("plugin_host_registry.contribution_unsupported", plugin_type=plugin_type, field=field))
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
        if plugin_id:
            return self._by_ref.get(ResourceRef(f"plugin:{plugin_id}", kind, key))
        if kind not in _NAMESPACED_KINDS:
            if (kind, key) in self._ambiguous:
                return None
            return self._by_kind_key.get((kind, key))
        matches = [item for item in self._items if item.kind == kind and item.key == key]
        return matches[0] if len(matches) == 1 else None

    def _add(self, item: PluginContribution) -> None:
        ref = item.ref
        if ref in self._by_ref:
            raise ValueError(t("plugin_host_registry.resource_ref_duplicate", ref=ref))
        self._by_ref[ref] = item
        lookup_key = f"{item.plugin_id}:{item.key}" if item.kind in _NAMESPACED_KINDS else item.key
        existing = self._by_kind_key.get((item.kind, lookup_key))
        if existing:
            if item.kind in {"rule", "world_template"} and existing.plugin_id != item.plugin_id:
                raise ValueError(t("plugin_host_registry.plain_id_conflict", kind=item.kind, resource_id=item.key))
            if item.kind in _NAMESPACED_KINDS:
                raise ValueError(t("plugin_host_registry.resource_id_duplicate_in_plugin", kind=item.kind, resource_id=item.key))
            if existing.plugin_id != item.plugin_id and item.content_schema_version < 2:
                raise ValueError(
                    t("plugin_host_registry.resource_conflict", kind=item.kind, resource_id=item.key, plugin_id=existing.plugin_id)
                )
            if existing.plugin_id != item.plugin_id:
                self._ambiguous.add((item.kind, item.key))
        self._items.append(item)
        self._by_kind_key[(item.kind, lookup_key)] = item


def validate_contributes(manifest: PluginManifest, plugin_dir: Path) -> None:
    registry = ContributionRegistry()
    registry.register_static_plugin(manifest, plugin_dir)


def _expand_contribution_paths(plugin_dir: Path, value: Any) -> list[Path]:
    patterns = value if isinstance(value, list) else [value]
    if not all(isinstance(pattern, str) and pattern.strip() for pattern in patterns):
        raise ValueError(t("plugin_host_registry.contributes_path_type"))
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
        raise ValueError(t("plugin_host_registry.contributes_path_unsafe"))


def _contribution_from_path(
    manifest: PluginManifest,
    plugin_dir: Path,
    kind: str,
    path: Path,
) -> PluginContribution:
    if kind in _IMAGE_KINDS and path.suffix.lower() not in _IMAGE_SUFFIXES:
        labels = {
            "portrait_asset": t("plugin_host_registry.label_portrait_asset"),
            "scene_image_asset": t("plugin_host_registry.label_scene_image_asset"),
            "standing_portrait": t("plugin_host_registry.label_standing_portrait"),
            "map_icon": t("plugin_host_registry.label_map_icon"),
            "map_scene": t("plugin_host_registry.label_map_scene"),
        }
        raise ValueError(t("plugin_host_registry.image_format_unsupported", label=labels[kind], path=path.relative_to(plugin_dir)))
    if kind == "voice_asset" and path.suffix.lower() not in _AUDIO_SUFFIXES:
        raise ValueError(t("plugin_host_registry.voice_asset_format_unsupported", path=path.relative_to(plugin_dir)))
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
            raise ValueError(t("plugin_host_registry.resource_json_invalid", path=path.relative_to(plugin_dir))) from exc
        if not isinstance(data, dict):
            raise ValueError(t("plugin_host_registry.resource_not_object", path=path.relative_to(plugin_dir)))
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
        raise ValueError(t("plugin_host_registry.resource_id_empty", path=path.relative_to(plugin_dir)))
    content_schema_version = int(manifest.get("content_schema_version", 1) or 1)
    normalized_key = key.strip()
    if content_schema_version >= 2 and kind in _V2_CANONICAL_KINDS:
        try:
            canonical = canonical_id(normalized_key)
        except ValueError as exc:
            raise ValueError(
                t("plugin_host_registry.resource_id_invalid", normalized_key=repr(normalized_key), path=path.relative_to(plugin_dir))
            ) from exc
        if canonical != normalized_key:
            raise ValueError(
                t("plugin_host_registry.resource_id_not_canonical", canonical=repr(canonical), normalized_key=repr(normalized_key))
            )
    return PluginContribution(
        plugin_id=str(manifest.get("id") or ""),
        plugin_name=str(manifest.get("name") or manifest.get("id") or ""),
        plugin_type=str(manifest.get("plugin_type") or ""),
        kind=kind,
        key=normalized_key,
        path=path,
        relative_path=path.relative_to(plugin_dir).as_posix(),
        title=title.strip(),
        description=description.strip(),
        content_schema_version=content_schema_version,
        default_locale=str(manifest.get("default_locale") or ""),
    )


def _ensure_inside(root: Path, target: Path) -> None:
    root = root.resolve()
    target = target.resolve()
    if target != root and root not in target.parents:
        raise ValueError(t("plugin_host_registry.contributes_path_outside"))


def _validate_voice_profile(data: dict[str, Any], relative_path: Path) -> None:
    if data.get("schema_version") != 1:
        raise ValueError(t("plugin_host_registry.voice_schema_version_invalid", relative_path=relative_path))
    voice_id = str(data.get("id") or "").strip()
    if not voice_id or len(voice_id) > 96:
        raise ValueError(t("plugin_host_registry.voice_id_invalid", relative_path=relative_path))
    if not str(data.get("name") or "").strip():
        raise ValueError(t("plugin_host_registry.voice_name_empty", relative_path=relative_path))
    engine = str(data.get("engine") or "").strip()
    if engine not in _VOICE_ENGINES:
        raise ValueError(t("plugin_host_registry.voice_engine_unsupported", relative_path=relative_path))
    if not str(data.get("license") or "").strip():
        raise ValueError(t("plugin_host_registry.voice_license_required", relative_path=relative_path))
    if data.get("consent") is not True:
        raise ValueError(t("plugin_host_registry.voice_consent_required", relative_path=relative_path))
    if engine in {"openai-compatible", "edge-tts"} and not str(data.get("voice_id") or "").strip():
        raise ValueError(t("plugin_host_registry.voice_voice_id_required", relative_path=relative_path))
    if engine == "gpt-sovits":
        if not str(data.get("reference_audio") or "").strip():
            raise ValueError(t("plugin_host_registry.voice_reference_audio_required", relative_path=relative_path))
        if not str(data.get("prompt_text") or "").strip():
            raise ValueError(t("plugin_host_registry.voice_transcript_required", relative_path=relative_path))


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
                raise ValueError(t("plugin_host_registry.voice_reference_undeclared", field=field, raw=raw))
            if field == "reference_audio" and target.suffix.lower() != ".wav":
                raise ValueError(t("plugin_host_registry.reference_audio_not_wav", raw=raw))
