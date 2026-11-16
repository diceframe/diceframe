"""插件安装/卸载/市场/自更新/生命周期测试（自 test_plugin_host 拆分）。"""

from __future__ import annotations

import io
import json
import textwrap
import zipfile
import base64

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.plugin_host import PluginHost
from src.plugin_host.requirements import parse_ruleset_requirements
from src.plugin_host.runtime_protocol import PluginInvocationError, PluginProtocolError
from src.rules.rule_system import RuleSystem
from src.webui.services import content_pack_maps as content_pack_map_service
from src.webui.services import maps as map_service
from src.webui.services import plugins as plugin_service
from src.webui.services import rules as rule_service
from src.webui.services import worlds as world_service


from plugin_host_common import write_plugin, write_png, make_plugin_zip


def test_plugin_ruleset_requirement_shape_is_versioned_and_fail_closed():
    assert parse_ruleset_requirements({
        "requires": {"rulesets": [{"id": "core:dnd2024", "minimum_version": 2}]},
    }) == [{"id": "core:dnd2024", "minimum_version": 2}]
    with pytest.raises(ValueError, match="unknown field"):
        parse_ruleset_requirements({"requires": {"rulesets": [{"id": "x", "extra": True}]}})
    with pytest.raises(ValueError, match="duplicate"):
        parse_ruleset_requirements({"requires": {"rulesets": [{"id": "x"}, {"id": "x"}]}})


def test_plugin_host_rejects_malformed_ruleset_requirements_before_activation(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "bad-requirement",
        manifest_extra={"requires": {"rulesets": [{"id": "core:demo", "minimum_version": "1"}]}},
    )
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    detail = host.public_detail("bad-requirement")
    assert detail["status"] == "failed"
    assert "minimum_version" in detail["error"]

class FakeMarketplace:
    def __init__(self, payload, *, plugin_id="demo-plugin", version="1"):
        self.payload = payload
        self.plugin_id = plugin_id
        self.version = version

    async def package_for_plugin(self, plugin_id):
        return {
            "ok": True,
            "payload": self.payload,
            "plugin": {"id": self.plugin_id, "version": self.version},
            "source": {"id": "test"},
        }


@pytest.mark.asyncio
async def test_install_and_uninstall_plugin_zip(tmp_path):
    package = tmp_path / "demo.zip"
    make_plugin_zip(package)
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")

    installed = await host.install_from_zip(package.read_bytes())

    assert installed["id"] == "demo-plugin"
    assert (tmp_path / "plugins" / "demo-plugin" / "plugin.json").exists()
    receipt = json.loads((tmp_path / "data" / "demo-plugin" / "install.json").read_text())
    assert receipt["schema_version"] == 1
    assert receipt["plugin_id"] == "demo-plugin"
    assert len(receipt["manifest_digest"]) == 64
    removed = await host.uninstall("demo-plugin")
    assert removed["uninstalled"] is True
    assert not (tmp_path / "plugins" / "demo-plugin").exists()


@pytest.mark.asyncio
async def test_discover_fails_closed_when_install_manifest_digest_is_tampered(tmp_path):
    package = tmp_path / "demo.zip"
    make_plugin_zip(package)
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    await host.install_from_zip(package.read_bytes())
    manifest_path = tmp_path / "plugins" / "demo-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["description"] = "tampered"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    restarted = PluginHost(tmp_path / "plugins", tmp_path / "data")
    restarted.discover()
    detail = restarted.public_detail("demo-plugin")
    assert detail["status"] == "failed"
    assert "digest mismatch" in detail["error"]
    assert restarted.contributions.list() == []


@pytest.mark.asyncio
async def test_install_rejects_cross_plugin_contribution_conflict_before_publish(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "existing-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {"world_templates": ["worlds/*.json"]}},
    )
    (plugins / "existing-pack" / "worlds").mkdir()
    (plugins / "existing-pack" / "worlds" / "shared.json").write_text(
        json.dumps({"world_id": "shared-world", "world_name": "Shared"}),
        encoding="utf-8",
    )
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.start("existing-pack", require_enabled=False)

    package = tmp_path / "conflict.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("candidate-pack/plugin.json", json.dumps({
            "schema_version": 1,
            "id": "candidate-pack",
            "name": "Candidate",
            "version": "1",
            "description": "candidate",
            "plugin_type": "content-pack",
            "contributes": {"world_templates": ["worlds/*.json"]},
            "config_schema": "config.schema.json",
        }))
        archive.writestr("candidate-pack/config.schema.json", json.dumps({
            "type": "object", "properties": {},
        }))
        archive.writestr(
            "candidate-pack/worlds/shared.json",
            json.dumps({"world_id": "shared-world", "world_name": "Conflict"}),
        )

    with pytest.raises(ValueError, match="plain ID 冲突"):
        await host.install_from_zip(package.read_bytes())
    assert not (plugins / "candidate-pack").exists()
    assert host.public_detail("existing-pack")["status"] == "active"


@pytest.mark.asyncio
async def test_marketplace_install_rejects_package_with_wrong_plugin_id(tmp_path):
    package = tmp_path / "demo.zip"
    make_plugin_zip(package, plugin_id="other-plugin")
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    host.marketplace = FakeMarketplace(package.read_bytes(), plugin_id="demo-plugin")

    with pytest.raises(ValueError, match="ID 与商店索引不一致"):
        await host.install_from_marketplace("demo-plugin")

    assert not (tmp_path / "plugins" / "other-plugin").exists()


@pytest.mark.asyncio
async def test_marketplace_install_rejects_package_with_wrong_version(tmp_path):
    package = tmp_path / "demo.zip"
    make_plugin_zip(package)
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    host.marketplace = FakeMarketplace(package.read_bytes(), version="2")

    with pytest.raises(ValueError, match="版本与商店索引不一致"):
        await host.install_from_marketplace("demo-plugin")

    assert not (tmp_path / "plugins" / "demo-plugin").exists()


@pytest.mark.asyncio
async def test_install_rejects_zip_path_traversal(tmp_path):
    package = tmp_path / "bad.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("../plugin.json", "{}")
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")

    with pytest.raises(ValueError, match="非法路径"):
        await host.install_from_zip(package.read_bytes())


@pytest.mark.asyncio
async def test_install_rejects_package_over_compressed_size_limit(tmp_path, monkeypatch):
    monkeypatch.setitem(PluginHost.install_from_zip.__globals__, "MAX_PLUGIN_PACKAGE_BYTES", 10)
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")

    with pytest.raises(ValueError, match="不能超过 1 MB"):
        await host.install_from_zip(b"x" * 11)


@pytest.mark.asyncio
async def test_overwrite_restarts_plugin_that_was_running(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "demo-plugin", manifest_extra={
        "entrypoint": ["{python}", "-c", "import time; time.sleep(60)"],
    })
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.start("demo-plugin", require_enabled=False)
    assert host.public_detail("demo-plugin")["running"] is True

    package = tmp_path / "demo.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("demo-plugin/plugin.json", json.dumps({
            "schema_version": 1,
            "id": "demo-plugin",
            "name": "Demo",
            "version": "2",
            "description": "updated",
            "plugin_type": "channel-adapter",
            "entrypoint": ["{python}", "-c", "import time; time.sleep(60)"],
            "config_schema": "config.schema.json",
        }))
        archive.writestr("demo-plugin/config.schema.json", json.dumps({
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": False, "ui": {"control": "switch"}},
            },
        }))

    updated = await host.install_from_zip(package.read_bytes(), overwrite=True)

    assert updated["version"] == "2"
    assert updated["enabled"] is True
    assert updated["running"] is True
    await host.stop("demo-plugin")


@pytest.mark.asyncio
async def test_restart_forced_restarts_disabled_plugin(tmp_path):
    # 前端"重启"按钮传 require_enabled=False（强制重启）；control_plugin 曾把该
    # 参数传给不接受它的 restart() 导致 TypeError → HTTP 500。这里验证修复：
    # restart 默认按 enabled（改配置后不启动禁用插件），require_enabled=False 强制重启。
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "demo-plugin", manifest_extra={
        "entrypoint": ["{python}", "-c", "import time; time.sleep(60)"],
    })
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    # disabled 插件默认 restart 不启动进程，enabled 保持 false。
    await host.restart("demo-plugin")
    detail = host.public_detail("demo-plugin")
    assert detail["enabled"] is False
    assert detail["running"] is False

    # 强制 restart（对应前端重启按钮）会启动进程并把 enabled 置 true。
    await host.restart("demo-plugin", require_enabled=False)
    detail = host.public_detail("demo-plugin")
    assert detail["running"] is True
    assert detail["enabled"] is True
    await host.stop("demo-plugin")


@pytest.mark.asyncio
async def test_host_start_writes_generation_file_and_cleanup_removes_it(tmp_path):
    """宿主世代文件：start 时写入插件 runtime 目录，cleanup 时删除。

    插件进程据此感知宿主换代（主程序重启）立即退出，避免孤儿进程残留。
    """
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "demo-plugin", manifest_extra={
        "entrypoint": ["{python}", "-c", "import time; time.sleep(60)"],
    })
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    assert (tmp_path / "data" / "demo-plugin" / "runtime" / ".host-generation").exists() is False

    await host.start("demo-plugin", require_enabled=False)
    gen_path = tmp_path / "data" / "demo-plugin" / "runtime" / ".host-generation"
    assert gen_path.read_text(encoding="ascii").strip()

    await host.cleanup()
    assert gen_path.exists() is False


@pytest.mark.asyncio
async def test_host_generation_is_unique_per_host_instance(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "demo-plugin", manifest_extra={
        "entrypoint": ["{python}", "-c", "import time; time.sleep(60)"],
    })
    first = PluginHost(plugins, tmp_path / "data")
    second = PluginHost(plugins, tmp_path / "data2")
    first.discover()
    second.discover()
    await first.start("demo-plugin", require_enabled=False)
    await second.start("demo-plugin", require_enabled=False)

    first_generation = (
        tmp_path / "data" / "demo-plugin" / "runtime" / ".host-generation"
    ).read_text(encoding="ascii").strip()
    second_generation = (
        tmp_path / "data2" / "demo-plugin" / "runtime" / ".host-generation"
    ).read_text(encoding="ascii").strip()
    assert first_generation
    assert second_generation
    assert first_generation != second_generation
    await first.cleanup()
    await second.cleanup()


@pytest.mark.asyncio
async def test_install_rejects_zip_bomb_by_unpacked_size(tmp_path, monkeypatch):
    monkeypatch.setitem(PluginHost._extract_zip.__globals__, "MAX_PLUGIN_UNPACKED_BYTES", 100)
    package = tmp_path / "large-unpacked.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("demo-plugin/large.txt", "x" * 101)
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")

    with pytest.raises(ValueError, match="解压后"):
        await host.install_from_zip(package.read_bytes())


@pytest.mark.asyncio
async def test_install_rejects_too_many_archive_entries(tmp_path, monkeypatch):
    monkeypatch.setitem(PluginHost._extract_zip.__globals__, "MAX_PLUGIN_ARCHIVE_FILES", 2)
    package = tmp_path / "many-files.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("demo-plugin/a.txt", "a")
        archive.writestr("demo-plugin/b.txt", "b")
        archive.writestr("demo-plugin/c.txt", "c")
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")

    with pytest.raises(ValueError, match="文件数量"):
        await host.install_from_zip(package.read_bytes())


@pytest.mark.asyncio
async def test_auto_update_runs_only_for_plugins_marked_automatic(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "safe-pack", plugin_type="content-pack", entrypoint=False)
    write_plugin(plugins, "process-plugin", plugin_type="channel-adapter", entrypoint=True)
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    host._save_marketplace_metadata("safe-pack", {"update_policy": "automatic"})
    host._save_marketplace_metadata("process-plugin", {"update_policy": "notify"})
    updated = []

    async def fake_install(plugin_id, *, overwrite=False):
        updated.append((plugin_id, overwrite))
        return {"id": plugin_id, "version": "2.0.0"}

    monkeypatch.setattr(host, "install_from_marketplace", fake_install)

    result = await host.auto_update_safe_plugins()

    assert updated == [("safe-pack", True)]
    assert result == [{"id": "safe-pack", "ok": True, "updated": True, "version": "2.0.0"}]


@pytest.mark.asyncio
async def test_rescan_discovers_manually_copied_plugins(tmp_path):
    plugins = tmp_path / "plugins"
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    write_plugin(plugins, "copied-pack", plugin_type="content-pack", entrypoint=False)

    found = await host.rescan()

    assert [item["id"] for item in found] == ["copied-pack"]
    assert host.public_detail("copied-pack")["plugin_type"] == "content-pack"


@pytest.mark.asyncio
async def test_start_enabled_does_not_trigger_auto_update(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "safe-pack", plugin_type="content-pack", entrypoint=False)
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    ran = []

    async def fake_auto_update():
        ran.append(True)
        return []

    monkeypatch.setattr(host, "auto_update_safe_plugins", fake_auto_update)
    await host.start_enabled()
    assert ran == []
    assert host._auto_update_task is None


@pytest.mark.asyncio
async def test_marketplace_listing_does_not_auto_update_by_default(tmp_path, monkeypatch):
    import src.plugin_host.host as host_module

    plugins = tmp_path / "plugins"
    write_plugin(plugins, "safe-pack", plugin_type="content-pack", entrypoint=False)
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    ran = []

    class FakeMarketplace:
        async def list_plugins(self):
            return {"ok": True, "plugins": [], "total": 0, "source": {}}

    host.marketplace = FakeMarketplace()

    async def fake_auto_update():
        ran.append(True)
        return []

    monkeypatch.setattr(host, "auto_update_safe_plugins", fake_auto_update)
    assert host_module._PLUGIN_AUTO_UPDATE_ENABLED is False
    result = await host.marketplace_plugins()
    assert result["ok"] is True
    assert ran == []
    await asyncio.sleep(0)
    assert ran == []
    assert host._auto_update_task is None


@pytest.mark.asyncio
async def test_marketplace_listing_triggers_auto_update_when_enabled(tmp_path, monkeypatch):
    import src.plugin_host.host as host_module

    plugins = tmp_path / "plugins"
    write_plugin(plugins, "safe-pack", plugin_type="content-pack", entrypoint=False)
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    ran = []

    class FakeMarketplace:
        async def list_plugins(self):
            return {"ok": True, "plugins": [], "total": 0, "source": {}}

    host.marketplace = FakeMarketplace()

    async def fake_auto_update():
        ran.append(True)
        return []

    monkeypatch.setattr(host, "auto_update_safe_plugins", fake_auto_update)
    monkeypatch.setattr(host_module, "_PLUGIN_AUTO_UPDATE_ENABLED", True)
    result = await host.marketplace_plugins()
    assert result["ok"] is True
    assert ran == []
    await asyncio.sleep(0)
    assert ran == [True]
    assert host._auto_update_task is not None and host._auto_update_task.done()

@pytest.mark.asyncio
async def test_monitor_backs_off_on_rapid_crash(tmp_path, monkeypatch):
    import src.plugin_host.host as host_module

    plugins = tmp_path / "plugins"
    write_plugin(plugins, "example")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    runtime = host.plugins["example"]
    runtime.config["enabled"] = True
    monkeypatch.setattr(host_module, "_RESTART_BASE_DELAY", 0.01)
    monkeypatch.setattr(host_module, "_RESTART_MAX_DELAY", 0.04)
    monkeypatch.setattr(host_module, "_RESTART_STABLE_SECONDS", 999.0)

    await host.start("example")
    assert runtime.restart_delay_sec == pytest.approx(0.01)
    first_monitor = runtime.monitor_task
    await asyncio.wait_for(first_monitor, timeout=10)
    assert runtime.restart_delay_sec == pytest.approx(0.02)
    second_monitor = runtime.monitor_task
    await asyncio.wait_for(second_monitor, timeout=10)
    assert runtime.restart_delay_sec == pytest.approx(0.04)
    await host.stop("example")


# ---------- 双目录合并模型 ----------

def test_discover_merges_builtin_and_user_dirs(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    write_plugin(builtin, "alpha")
    write_plugin(user, "beta")
    host = PluginHost(user, tmp_path / "data", builtin_dir=builtin)
    found = host.discover()
    assert {p["id"] for p in found} == {"alpha", "beta"}
    assert host.plugins["alpha"].source == "builtin"
    assert host.plugins["beta"].source == "user"


def test_user_dir_overrides_builtin_on_name_conflict(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    write_plugin(builtin, "shared", manifest_extra={"version": "1"})
    write_plugin(user, "shared", manifest_extra={"version": "2"})
    host = PluginHost(user, tmp_path / "data", builtin_dir=builtin)
    host.discover()
    assert host.plugins["shared"].manifest["version"] == "2"
    assert host.plugins["shared"].source == "user"


@pytest.mark.asyncio
async def test_builtin_plugin_cannot_be_uninstalled(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    write_plugin(builtin, "built-in")
    host = PluginHost(user, tmp_path / "data", builtin_dir=builtin)
    host.discover()
    with pytest.raises(ValueError, match="内置插件不可卸载"):
        await host.uninstall("built-in")
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_stop_keep_enabled_false_invokes_on_plugin_stopped(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "tun")
    stopped: list[str] = []

    async def on_stopped(plugin_id: str) -> None:
        stopped.append(plugin_id)

    host = PluginHost(plugins, tmp_path / "data", on_plugin_stopped=on_stopped)
    host.discover()
    # 用户主动停止/卸载（keep_enabled=False）应通知接线层释放隧道发布
    await host.stop("tun", keep_enabled=False)
    assert stopped == ["tun"]


@pytest.mark.asyncio
async def test_stop_keep_enabled_true_skips_on_plugin_stopped(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "tun")
    stopped: list[str] = []

    async def on_stopped(plugin_id: str) -> None:
        stopped.append(plugin_id)

    host = PluginHost(plugins, tmp_path / "data", on_plugin_stopped=on_stopped)
    host.discover()
    # restart/cleanup/更新走 keep_enabled=True，不触发 release（插件会重新拉起并重新发布）
    await host.stop("tun", keep_enabled=True)
    assert stopped == []


