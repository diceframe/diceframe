"""Compendium facade 测试（MOD-04，母方案 §108）。

只读 facade：模块列表（含传统包与 adventure-module 的区分）、详情（内容
分组 + 冒险清单）、内容库详情（委托 PluginContentCatalog）；provider/tool
等非 content-pack 不进入模块库。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.plugin_host.host import PluginHost
from src.adventures.registry import AdventureSourceRegistry
from src.webui.api import WebAPI
from src.webui.services import modules as modules_service


def _make_module(plugin_dir: Path, *, module_id: str, profile: str | None, plugin_type: str = "content-pack", declared: list[str] | None = None) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "schema_version": 1,
        "id": module_id,
        "name": module_id.replace("-", " ").title(),
        "version": "1.0.0",
        "plugin_type": plugin_type,
        "entrypoint": ["python", "-c", "pass"],
        "contributes": {},
    }
    if profile is not None:
        manifest["content_profile"] = profile
        manifest["content_delivery_mode"] = "catalog"
    if declared is not None:
        manifest["adventure_packages"] = declared
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / "config.schema.json").write_text(
        '{"type": "object", "properties": {}}', encoding="utf-8",
    )


def _make_module_with_adventure(plugin_dir: Path, module_id: str) -> None:
    _make_module(plugin_dir, module_id=module_id, profile="adventure-module", declared=["adventures/castle"])
    package = plugin_dir / "adventures" / "castle"
    shutil.copytree(Path("templates/adventures/lanterns_of_greymoor"), package)
    manifest_path = package / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["adventure_id"] = "plugin:castle-quest"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")


class _Deps:
    def __init__(self, host: PluginHost, registry: AdventureSourceRegistry) -> None:
        self.plugin_host = host
        self.adventure_registry = registry


@pytest.fixture()
def env(tmp_path: Path):
    plugins_root = tmp_path / "plugins"
    _make_module_with_adventure(plugins_root / "castle-module", "castle-module")
    _make_module(plugins_root / "legacy-pack", module_id="legacy-pack", profile=None)
    _make_module(plugins_root / "tool-x", module_id="tool-x", profile=None, plugin_type="tool")
    host = PluginHost(plugins_dir=plugins_root, data_dir=tmp_path / "data")
    for module_id in ("castle-module", "legacy-pack", "tool-x"):
        _, runtime = host._load_runtime(plugins_root / module_id)
        runtime.status = "enabled"
        host.plugins[module_id] = runtime

    registry = AdventureSourceRegistry.from_directories(None, None)
    api = WebAPIStub(host, registry)
    api._sync_plugin_adventure_sources()
    return api, registry


class WebAPIStub:
    """只暴露 sync 方法需要的最小面（完整 WebAPI 构造需要全站依赖）。"""

    _adventure_source_registry: AdventureSourceRegistry
    _plugins: PluginHost

    def __init__(self, plugin_host: PluginHost, registry: AdventureSourceRegistry) -> None:
        self._adventure_source_registry = registry
        self._plugins = plugin_host

    def _sync_plugin_adventure_sources(self) -> None:
        WebAPI._sync_plugin_adventure_sources(self)


def test_list_modules_separates_modules_from_legacy_packs(env) -> None:
    api, _ = env
    result = modules_service.list_modules(_Deps(api._plugins, api._adventure_source_registry))
    by_id = {item["id"]: item for item in result["modules"]}
    # provider/tool 等不进入模块库。
    assert "tool-x" not in by_id
    assert by_id["castle-module"]["is_module"] is True
    assert by_id["castle-module"]["content_profile"] == "adventure-module"
    assert by_id["castle-module"]["adventure_count"] == 1
    # 传统 content-pack 仍在（兼容），但 is_module=False。
    assert by_id["legacy-pack"]["is_module"] is False


def test_module_detail_groups_content_and_adventures(env) -> None:
    api, _ = env
    result = modules_service.module_detail(_Deps(api._plugins, api._adventure_source_registry), "castle-module")
    assert result["ok"] is True
    module = result["module"]
    assert [item["adventure_id"] for item in module["adventures"]] == ["plugin:castle-quest"]
    assert "content_counts" in module


def test_module_detail_unknown_id_fails_closed(env) -> None:
    api, _ = env
    result = modules_service.module_detail(_Deps(api._plugins, api._adventure_source_registry), "nope")
    assert result == {"ok": False, "error_code": "MODULE_NOT_FOUND"}


def test_module_content_delegates_to_catalog(env) -> None:
    api, _ = env
    deps = _Deps(api._plugins, api._adventure_source_registry)
    missing = modules_service.module_content(deps, "castle-module", "npc", "nope")
    assert missing == {"ok": False, "error_code": "CONTENT_NOT_FOUND"}
    # 未知 kind 同样安全返回。
    assert modules_service.module_content(deps, "castle-module", "dragon", "x")["ok"] is False
