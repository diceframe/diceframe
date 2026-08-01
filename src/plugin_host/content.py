"""插件静态贡献目录：主题、地图、内容包与公开资源。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .registry import ContributionRegistry


class PluginContentCatalog:
    """读取已注册静态贡献；不参与插件进程生命周期。"""

    CONTENT_KINDS = frozenset({"character_template", "npc", "item", "spell", "class"})

    def __init__(
        self,
        registry: ContributionRegistry,
        plugins_dir: Path,
        logger: logging.Logger,
    ) -> None:
        self.registry = registry
        self.plugins_dir = plugins_dir
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
        return data

    def list_themes(self) -> list[dict[str, Any]]:
        themes = []
        for item in self.registry.list("theme"):
            try:
                data = json.loads(item.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                theme_id = str(data.get("id") or item.key).strip()
                themes.append({
                    "id": theme_id,
                    "name": str(data.get("name") or item.title or theme_id),
                    "description": str(data.get("description") or item.description or ""),
                    "plugin_id": item.plugin_id,
                    "plugin_name": item.plugin_name,
                    "tokens": self._sanitize_theme_tokens(data),
                })
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self.logger.warning("插件主题读取失败: %s", item.path, exc_info=True)
        return themes

    def list_map_assets(self, world_id: str = "") -> dict[str, list[dict[str, Any]]]:
        return {
            "locations": self._map_json_items("map_location", world_id),
            "icons": [self._asset_item(item) for item in self.registry.list("map_icon")],
            "scenes": [self._asset_item(item) for item in self.registry.list("map_scene")],
            "grids": [self._asset_item(item) for item in self.registry.list("map_grid")],
        }

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

    def public_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/").strip("/")
        root = (self.plugins_dir / plugin_id).resolve()
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
        raw = data.get("tokens") if isinstance(data.get("tokens"), dict) else data.get("variables")
        if not isinstance(raw, dict):
            raw = {}
        if any(key.startswith("--") for key in raw):
            raw = {"base": raw}
        result: dict[str, dict[str, str]] = {"base": {}, "dark": {}, "light": {}}
        for mode in result:
            values = raw.get(mode)
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                name = str(key).strip()
                text = str(value).strip()
                lowered = text.lower()
                if not name.startswith("--"):
                    continue
                if (
                    len(text) > 160
                    or any(character in text for character in "{};")
                    or "url(" in lowered
                    or "expression(" in lowered
                ):
                    continue
                result[mode][name] = text
        return result

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
                result.append(data)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self.logger.warning("插件内容资源读取失败: %s", item.path, exc_info=True)
        return result

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
        return {
            "id": item.key,
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
