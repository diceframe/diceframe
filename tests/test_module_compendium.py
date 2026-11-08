"""Compendium facade 测试（MOD-04，母方案 §108）。

只读 facade：模块列表（含传统包与 adventure-module 的区分）、详情（内容
分组 + 冒险清单）、内容库详情（委托 PluginContentCatalog）；provider/tool
等非 content-pack 不进入模块库。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.plugin_host.host import PluginHost
from src.adventures.registry import AdventureSourceRegistry
from src.webui.api import WebAPI
from src.webui.services import modules as modules_service


def _make_module(plugin_dir: Path, *, module_id: str, profile: str | None, plugin_type: str = "content-pack", declared: list[str] | None = None, contributes: dict | None = None) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "schema_version": 1,
        "id": module_id,
        "name": module_id.replace("-", " ").title(),
        "version": "1.0.0",
        "plugin_type": plugin_type,
        "contributes": contributes or {},
    }
    if plugin_type != "content-pack" or profile is None:
        manifest["entrypoint"] = ["python", "-c", "pass"]
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
    _make_module(plugin_dir, module_id=module_id, profile="adventure-module", declared=["adventures/castle"], contributes={"npcs": ["content/npc/*.json"]})
    npc = plugin_dir / "content" / "npc"
    npc.mkdir(parents=True)
    (npc / "guide.json").write_text(json.dumps({"id": "guide", "name": "Guide", "description": "A guide"}), encoding="utf-8")
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


def test_disabled_module_summary_and_detail_use_declared_inventory(env) -> None:
    api, registry = env
    api._plugins.plugins["castle-module"].status = "disabled"
    api._sync_plugin_adventure_sources()
    deps = _Deps(api._plugins, registry)

    summary = next(row for row in modules_service.list_modules(deps)["modules"] if row["id"] == "castle-module")
    detail = modules_service.module_detail(deps, "castle-module")["module"]

    assert summary["adventure_count"] == 1
    assert summary["content_counts"] == {"npc": 1}
    assert summary["inventory_status"] == "disabled"
    assert detail["adventure_count"] == 1
    assert detail["content_counts"] == {"npc": 1}
    assert detail["inventory_status"] == "disabled"
    assert len(detail["content"]["npc"]) == 1
    assert len(detail["adventures"]) == 1


def test_unreadable_declared_inventory_is_unknown_not_zero(env, tmp_path: Path) -> None:
    api, registry = env
    runtime = api._plugins.plugins["castle-module"]
    runtime.adventure_packages_root = tmp_path / "missing-adventures"
    runtime.adventure_package_directories = ("missing",)
    deps = _Deps(api._plugins, registry)

    summary = next(row for row in modules_service.list_modules(deps)["modules"] if row["id"] == "castle-module")
    detail = modules_service.module_detail(deps, "castle-module")["module"]

    assert summary["adventure_count"] is None
    assert summary["inventory_status"] == "unknown"
    assert detail["adventure_count"] is None
    assert detail["inventory_status"] == "unknown"


def test_malformed_declared_adventure_is_unknown_not_zero(env) -> None:
    api, registry = env
    runtime = api._plugins.plugins["castle-module"]
    manifest = runtime.adventure_packages_root / "castle" / "manifest.json"
    manifest.write_text("{ malformed", encoding="utf-8")
    deps = _Deps(api._plugins, registry)

    summary = next(row for row in modules_service.list_modules(deps)["modules"] if row["id"] == "castle-module")

    assert summary["adventure_count"] is None
    assert summary["inventory_status"] == "unknown"


def test_missing_declared_adventure_manifest_is_unknown_not_zero(env) -> None:
    api, registry = env
    runtime = api._plugins.plugins["castle-module"]
    manifest = runtime.adventure_packages_root / "castle" / "manifest.json"
    manifest.unlink()
    deps = _Deps(api._plugins, registry)

    summary = next(row for row in modules_service.list_modules(deps)["modules"] if row["id"] == "castle-module")

    assert summary["adventure_count"] is None
    assert summary["inventory_status"] == "unknown"


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


# issue #708：包内容读取必须按请求者视角过滤（fail-closed）。

_VISIBILITY_ENTRIES = [
    {"id": "pub", "name": "公开", "visible_to": ["*"]},
    {"id": "secret", "name": "守秘", "visible_to": []},
    {"id": "alice", "name": "指定角色", "visible_to": ["alice"]},
]


class _StubCatalog:
    def get_content_resource(self, kind: str, key: str, *, plugin_id: str = "", language: str = ""):
        return {"id": key, "entries": [dict(entry) for entry in _VISIBILITY_ENTRIES]}


class _StubHost:
    content = _StubCatalog()


def test_module_content_filters_entries_for_player_view() -> None:
    deps = SimpleNamespace(plugin_host=_StubHost())
    player = modules_service.module_content(deps, "m", "lorebook", "guide", viewer_is_gm=False)
    assert [entry["id"] for entry in player["content"]["entries"]] == ["pub"]
    assert player["content"]["total_entries"] == 3
    assert player["content"]["hidden_entries"] == 2

    gm = modules_service.module_content(deps, "m", "lorebook", "guide")
    assert len(gm["content"]["entries"]) == 3
    assert "hidden_entries" not in gm["content"]

    # 非 lorebook kind 的资源不做条目裁剪（形状不同）。
    npc = modules_service.module_content(deps, "m", "npc", "x", viewer_is_gm=False)
    assert len(npc["content"]["entries"]) == 3


class _StubLorebookStore:
    def list_lorebooks(self) -> list[dict]:
        return []

    def list_book_entries(self, book_id: str) -> list[dict]:
        return [dict(entry) for entry in _VISIBILITY_ENTRIES]


class _StubResourcesHost:
    def list_content_resources(self, kind: str) -> dict[str, list[dict]]:
        if kind == "lorebook":
            return {"lorebook": [{
                "plugin_id": "castle-module", "id": "guide", "entries": _VISIBILITY_ENTRIES,
            }]}
        return {}


def test_module_lorebook_entry_counts_follow_viewer() -> None:
    deps = SimpleNamespace(lorebook_store=_StubLorebookStore(), plugin_host=_StubResourcesHost())

    gm_rows = modules_service._module_lorebooks(deps, "castle-module")
    assert gm_rows[0]["entry_count"] == 3

    player_rows = modules_service._module_lorebooks(deps, "castle-module", viewer_is_gm=False)
    assert player_rows[0]["entry_count"] == 1

    # 已物化的书走 store 计数，同一视角谓词。
    assert modules_service._book_entry_count(_StubLorebookStore(), "b") == 3
    assert modules_service._book_entry_count(_StubLorebookStore(), "b", viewer_is_gm=False) == 1
