"""Plugin adventure contribution 测试（MOD-03，母方案 §107）。

覆盖：manifest adventure_packages 校验（路径安全/declared-only/公共父目录/
仅 content-pack）、PluginRuntime 携带解析结果、WebAPI 把启用的模组注册为
plugin 来源（禁用后消失）、不复制目录（loader 直接指向 plugin 目录）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.adventures.bundle import AdventureBundleLoader
from src.plugin_host.host import PluginHost
from src.webui.api import WebAPI


def _make_module(plugin_dir: Path, *, declared: list[str] | None, plugin_type: str = "content-pack") -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "schema_version": 1,
        "id": "castle-module",
        "name": "Castle Module",
        "version": "1.0.0",
        "plugin_type": plugin_type,
        "entrypoint": ["python", "-c", "pass"],
        "content_profile": "adventure-module",
        "content_delivery_mode": "catalog",
        "contributes": {},
    }
    if declared is not None:
        manifest["adventure_packages"] = declared
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / "config.schema.json").write_text(
        '{"type": "object", "properties": {}}', encoding="utf-8",
    )
    # 一个真实的冒险包（从内置复制并改名），声明路径指向它。
    package = plugin_dir / "adventures" / "castle"
    source = Path("templates/adventures/lanterns_of_greymoor")
    shutil.copytree(source, package)
    manifest_path = package / "manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["adventure_id"] = "plugin:castle-quest"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")


def test_host_accepts_declared_packages_and_carries_them(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    _make_module(plugins_root / "castle-module", declared=["adventures/castle"])
    host = PluginHost(plugins_dir=plugins_root, data_dir=tmp_path / "data")

    plugin_id, runtime = host._load_runtime(plugins_root / "castle-module")
    assert plugin_id == "castle-module"
    assert runtime.adventure_packages_root == (plugins_root / "castle-module" / "adventures").resolve()
    assert runtime.adventure_package_directories == ("castle",)


def test_host_rejects_bad_adventure_packages(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    _make_module(plugins_root / "castle-module", declared=["../outside"])
    host = PluginHost(plugins_dir=plugins_root, data_dir=tmp_path / "data")
    with pytest.raises(ValueError, match="\\.\\.|绝对路径|越界"):
        host._load_runtime(plugins_root / "castle-module")

    _make_module(plugins_root / "m2", declared=["adventures/missing"])
    with pytest.raises(ValueError, match="目录|manifest"):
        host._load_runtime(plugins_root / "m2")


def test_declared_only_loader_hides_undeclared_siblings(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    _make_module(plugins_root / "castle-module", declared=["adventures/castle"])
    host = PluginHost(plugins_dir=plugins_root, data_dir=tmp_path / "data")
    _, runtime = host._load_runtime(plugins_root / "castle-module")

    loader = AdventureBundleLoader(
        runtime.adventure_packages_root,
        allowed_directory_ids=runtime.adventure_package_directories,
    )
    # 声明内的包可见。
    assert [b.manifest.adventure_id for b in loader.list("zh-CN")] == ["plugin:castle-quest"]
    with pytest.raises(Exception):
        loader.load("undeclared-sibling", "zh-CN")


def test_webapi_syncs_enabled_module_into_registry(tmp_path: Path) -> None:
    """启用的模组 → plugin 来源进入 registry；disabled → 消失。"""

    plugins_root = tmp_path / "plugins"
    _make_module(plugins_root / "castle-module", declared=["adventures/castle"])
    host = PluginHost(plugins_dir=plugins_root, data_dir=tmp_path / "data")
    plugin_id, runtime = host._load_runtime(plugins_root / "castle-module")
    runtime.status = "enabled"
    host.plugins[plugin_id] = runtime

    registry = host._adventure_registry_stub = None  # 占位避免误用
    from src.adventures.registry import AdventureSourceRegistry

    registry = AdventureSourceRegistry.from_directories(None, None)
    api = WebAPI.__new__(WebAPI)
    api._adventure_source_registry = registry
    api._plugins = host
    api._sync_plugin_adventure_sources()

    sources = registry.source_for("plugin", "castle-module")
    assert sources is not None
    bundle, source = registry.resolve("plugin:castle-quest", "zh-CN")
    assert source.source_id == "castle-module"
    # 不复制目录：loader 直接读 plugin 目录下的包。
    assert "castle" in str(bundle.root)

    runtime.status = "disabled"
    api._sync_plugin_adventure_sources()
    assert registry.source_for("plugin", "castle-module") is None
