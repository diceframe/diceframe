"""插件静态贡献目录：主题、地图、内容包与公开资源。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .registry import ContributionRegistry


THEME_SCHEMA_VERSION = 2
THEME_TOKEN_NAMES = frozenset({
    "--df-font-title",
    "--df-font-body",
    "--df-font-mono",
    "--df-canvas",
    "--df-canvas-glow",
    "--df-surface-1",
    "--df-surface-2",
    "--df-surface-3",
    "--df-surface-raised",
    "--df-control-bg",
    "--df-border",
    "--df-border-soft",
    "--df-focus",
    "--df-accent",
    "--df-accent-strong",
    "--df-interactive",
    "--df-interactive-strong",
    "--df-success",
    "--df-success-strong",
    "--df-warning",
    "--df-danger",
    "--df-danger-strong",
    "--df-info",
    "--df-text",
    "--df-text-secondary",
    "--df-text-muted",
    "--df-on-accent",
    "--df-hover",
    "--df-shadow",
    "--df-shadow-strong",
    "--df-radius-sm",
    "--df-radius-md",
    "--df-radius-lg",
})
THEME_VALUE_PATTERN = re.compile(r"^[\w\s#.,()%/+\-'\"]+$", re.UNICODE)
THEME_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{3,8}$")
THEME_COLOR_FUNCTION_PATTERN = re.compile(
    r"^(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\([0-9a-zA-Z\s.,%/+*\-]+\)$",
)
THEME_RADIUS_PATTERN = re.compile(r"^(?:0|\d+(?:\.\d+)?(?:px|rem|em|%))$")
THEME_COLOR_TOKENS = frozenset({
    name for name in THEME_TOKEN_NAMES
    if name not in {
        "--df-font-title",
        "--df-font-body",
        "--df-font-mono",
        "--df-shadow",
        "--df-shadow-strong",
        "--df-radius-sm",
        "--df-radius-md",
        "--df-radius-lg",
    }
})
THEME_RADIUS_TOKENS = frozenset({"--df-radius-sm", "--df-radius-md", "--df-radius-lg"})


def safe_id_part(value: Any) -> str:
    """归一化 id 片段：小写，非字母数字/下划线/连字符/中文替换为 _，截断 48 字符。

    sync 灌入的条目 id 与卸载清理的标记匹配必须用同一实现，故集中在 content 层。
    """
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_\-一-鿿]+", "_", text)
    return text.strip("_")[:48] or "content"


class PluginContentCatalog:
    """读取已注册静态贡献；不参与插件进程生命周期。"""

    CONTENT_KINDS = frozenset({"character_template", "npc", "item", "spell", "class"})

    def __init__(
        self,
        registry: ContributionRegistry,
        logger: logging.Logger,
    ) -> None:
        self.registry = registry
        self.logger = logger

    def contribution_path(self, kind: str, key: str) -> Path | None:
        item = self.registry.find(kind, key)
        return item.path if item else None

    def load_world_template(self, world_id: str) -> dict[str, Any] | None:
        path = self.contribution_path("world_template", world_id)
        if not path or not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        rule_id = str(data.get("default_rule") or "")
        rule_path = self.contribution_path("rule", rule_id) if rule_id else None
        if rule_path:
            data = dict(data)
            data["_diceframe_rule_path"] = str(rule_path)
        item = self.registry.find("world_template", world_id)
        if item:
            data = self.expose_scene_image(data, item.plugin_id)
        return data

    def expose_scene_image(self, data: dict[str, Any], plugin_id: str) -> dict[str, Any]:
        """Convert a packaged scene image into a browser-safe plugin reference."""
        result = dict(data)
        reference = result.get("scene_image")
        if not isinstance(reference, dict):
            return result
        kind = str(reference.get("kind") or "")
        if kind == "builtin":
            return result
        if kind != "asset":
            result.pop("scene_image", None)
            return result
        relative_path = str(reference.get("path") or "").replace("\\", "/").strip("/")
        declared = any(
            item.plugin_id == plugin_id
            and item.kind == "scene_image_asset"
            and item.relative_path == relative_path
            for item in self.registry.list("scene_image_asset")
        )
        if declared:
            result["scene_image"] = {
                "kind": "plugin",
                "plugin_id": plugin_id,
                "path": relative_path,
            }
        else:
            result.pop("scene_image", None)
        return result

    def list_themes(self) -> list[dict[str, Any]]:
        themes = []
        for item in self.registry.list("theme"):
            try:
                data = json.loads(item.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                if data.get("schema_version") != THEME_SCHEMA_VERSION:
                    self.logger.warning(
                        "Ignoring unsupported plugin theme schema version: %s",
                        item.path,
                    )
                    continue
                theme_id = str(data.get("id") or item.key).strip()
                themes.append({
                    "id": theme_id,
                    "name": str(data.get("name") or item.title or theme_id),
                    "description": str(data.get("description") or item.description or ""),
                    "schema_version": THEME_SCHEMA_VERSION,
                    "plugin_id": item.plugin_id,
                    "plugin_name": item.plugin_name,
                    "tokens": self._sanitize_theme_tokens(data),
                })
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self.logger.warning("插件主题读取失败: %s", item.path, exc_info=True)
        return themes

    def list_map_assets(self, world_id: str = "") -> dict[str, list[dict[str, Any]]]:
        return {
            "maps": self._map_json_items("map_definition", world_id),
            "locations": self._map_json_items("map_location", world_id),
            "icons": [self._asset_item(item) for item in self.registry.list("map_icon")],
            "scenes": [self._asset_item(item) for item in self.registry.list("map_scene")],
        }

    def list_voice_profiles(self) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        assets = {
            (item.plugin_id, item.relative_path): item
            for item in self.registry.list("voice_asset")
        }
        for item in self.registry.list("voice_profile"):
            try:
                data = json.loads(item.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("schema_version") != 1:
                    continue
                local_id = str(data.get("id") or item.key)
                profile = {
                    "id": f"plugin:{item.plugin_id}:voice:{local_id}",
                    "local_id": local_id,
                    "name": str(data.get("name") or item.title or item.key),
                    "engine": str(data.get("engine") or ""),
                    "voice_id": str(data.get("voice_id") or ""),
                    "language": str(data.get("language") or ""),
                    "description": str(data.get("description") or item.description or ""),
                    "prompt_text": str(data.get("prompt_text") or ""),
                    "prompt_language": str(data.get("prompt_language") or data.get("language") or ""),
                    "license": str(data.get("license") or ""),
                    "plugin_id": item.plugin_id,
                    "plugin_name": item.plugin_name,
                    "source": "plugin",
                }
                reference = str(data.get("reference_audio") or "").replace("\\", "/").strip("/")
                preview = str(data.get("preview_audio") or reference).replace("\\", "/").strip("/")
                reference_item = assets.get((item.plugin_id, reference)) if reference else None
                preview_item = assets.get((item.plugin_id, preview)) if preview else None
                if reference_item:
                    profile["_reference_audio_path"] = str(reference_item.path)
                if preview_item:
                    profile["preview_url"] = (
                        f"/api/plugins/assets/{quote(item.plugin_id)}/"
                        f"{quote(preview_item.relative_path, safe='/')}"
                    )
                profiles.append(profile)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self.logger.warning("插件语音资源读取失败: %s", item.path, exc_info=True)
        return profiles

    def list_content_resources(
        self,
        kind: str = "",
        *,
        world_id: str = "",
        rule_id: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        kinds = [kind] if kind in self.CONTENT_KINDS else sorted(self.CONTENT_KINDS)
        return {
            name: self._content_json_items(name, world_id=world_id, rule_id=rule_id)
            for name in kinds
        }

    def get_content_resource(
        self,
        kind: str,
        key: str,
        *,
        plugin_id: str = "",
    ) -> dict[str, Any] | None:
        kind = (kind or "").strip()
        key = (key or "").strip()
        plugin_id = (plugin_id or "").strip()
        if kind not in self.CONTENT_KINDS or not key:
            return None
        item = self.registry.find(kind, key)
        if not item or (plugin_id and item.plugin_id != plugin_id):
            return None
        return next(
            (
                resource
                for resource in self._content_json_items(kind)
                if str(resource.get("id") or "") == key
                and (not plugin_id or str(resource.get("plugin_id") or "") == plugin_id)
            ),
            None,
        )

    def public_asset_path(self, plugin_id: str, relative_path: str, plugin_directory: Path) -> Path:
        normalized = relative_path.replace("\\", "/").strip("/")
        root = plugin_directory.resolve()
        target = (root / normalized).resolve()
        self._ensure_inside(root, target)
        if not target.exists() or not target.is_file() or target.is_symlink():
            raise KeyError("插件资源不存在")
        for item in self.registry.list():
            if item.plugin_id == plugin_id and item.path == target:
                return target
        raise KeyError("插件资源未声明为可访问贡献")

    @staticmethod
    def _sanitize_theme_tokens(data: dict[str, Any]) -> dict[str, dict[str, str]]:
        raw = data.get("tokens")
        if not isinstance(raw, dict):
            raw = {}
        result: dict[str, dict[str, str]] = {"base": {}, "dark": {}, "light": {}}
        for mode in result:
            values = raw.get(mode)
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                name = str(key).strip()
                text = str(value).strip()
                lowered = text.lower()
                if name not in THEME_TOKEN_NAMES:
                    continue
                if (
                    not text
                    or len(text) > 160
                    or not THEME_VALUE_PATTERN.fullmatch(text)
                    or "url(" in lowered
                    or "expression(" in lowered
                    or "javascript" in lowered
                    or not PluginContentCatalog._is_theme_value_valid(name, text)
                ):
                    continue
                result[mode][name] = text
        return result

    @staticmethod
    def _is_theme_value_valid(name: str, value: str) -> bool:
        if name in THEME_COLOR_TOKENS:
            return (
                value in {"transparent", "currentColor"}
                or bool(THEME_HEX_COLOR_PATTERN.fullmatch(value))
                or bool(THEME_COLOR_FUNCTION_PATTERN.fullmatch(value))
            )
        if name in THEME_RADIUS_TOKENS:
            return bool(THEME_RADIUS_PATTERN.fullmatch(value))
        return True

    def _map_json_items(self, kind: str, world_id: str) -> list[dict[str, Any]]:
        result = []
        for item in self.registry.list(kind):
            try:
                data = json.loads(item.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not self._matches_world(data, world_id):
                    continue
                data = dict(data)
                data.setdefault("id", item.key)
                data.setdefault("name", item.title or item.key)
                data.update({
                    "plugin_id": item.plugin_id,
                    "plugin_name": item.plugin_name,
                    "source": "plugin",
                })
                result.append(data)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self.logger.warning("插件地图资源读取失败: %s", item.path, exc_info=True)
        return result

    def _content_json_items(
        self,
        kind: str,
        *,
        world_id: str = "",
        rule_id: str = "",
    ) -> list[dict[str, Any]]:
        result = []
        for item in self.registry.list(kind):
            try:
                data = json.loads(item.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                if not self._matches_world(data, world_id) or not self._matches_rule(data, rule_id):
                    continue
                data = dict(data)
                data.setdefault("id", item.key)
                if kind == "character_template":
                    data.setdefault("character_name", item.title or item.key)
                else:
                    data.setdefault("name", item.title or item.key)
                data.update({
                    "plugin_id": item.plugin_id,
                    "plugin_name": item.plugin_name,
                    "source": "plugin",
                    "readonly": True,
                })
                self._expose_packaged_portrait(data, item.plugin_id)
                result.append(data)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self.logger.warning("插件内容资源读取失败: %s", item.path, exc_info=True)
        return result

    def _expose_packaged_portrait(self, data: dict[str, Any], plugin_id: str) -> None:
        """Convert the portable package contract into a browser-safe runtime reference."""
        portrait = data.get("portrait")
        if not isinstance(portrait, dict) or portrait.get("kind") != "asset":
            return
        relative_path = str(portrait.get("path") or "").replace("\\", "/").strip("/")
        declared = any(
            item.plugin_id == plugin_id
            and item.kind == "portrait_asset"
            and item.relative_path == relative_path
            for item in self.registry.list("portrait_asset")
        )
        if declared:
            data["portrait"] = {
                "kind": "plugin",
                "plugin_id": plugin_id,
                "path": relative_path,
            }
        else:
            data.pop("portrait", None)

    @staticmethod
    def _matches_world(data: dict[str, Any], world_id: str) -> bool:
        target = str(world_id or "")
        if not target:
            return True
        declared = data.get("world_id")
        worlds = data.get("worlds")
        if declared:
            return str(declared) == target
        if isinstance(worlds, list) and worlds:
            return target in {str(item) for item in worlds}
        return True

    @staticmethod
    def _matches_rule(data: dict[str, Any], rule_id: str) -> bool:
        target = str(rule_id or "")
        if not target:
            return True
        declared = data.get("rule_id")
        rules = data.get("rules")
        if declared:
            return str(declared) == target
        if isinstance(rules, list) and rules:
            return target in {str(item) for item in rules}
        return True

    @staticmethod
    def _asset_item(item) -> dict[str, Any]:
        relative_path = item.relative_path
        asset_kind = {
            "map_icon": "icon",
            "map_scene": "scene",
        }.get(item.kind, item.kind)
        return {
            "id": item.key,
            "ref": f"plugin:{item.plugin_id}:{asset_kind}:{item.key}",
            "name": item.title or item.key,
            "description": item.description,
            "plugin_id": item.plugin_id,
            "plugin_name": item.plugin_name,
            "path": relative_path,
            "url": f"/api/plugins/assets/{quote(item.plugin_id)}/{quote(relative_path, safe='/')}",
        }

    @staticmethod
    def _ensure_inside(root: Path, target: Path) -> None:
        root = root.resolve()
        target = target.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("插件资源路径越界") from exc
