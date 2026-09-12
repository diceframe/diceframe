"""插件宿主核心能力测试（拆分后保留发现/配置/工具/机器人运行时）。"""

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
from src.plugin_host.runtime_protocol import PluginInvocationError, PluginProtocolError
from src.rules.rule_system import RuleSystem
from src.webui.services import content_pack_maps as content_pack_map_service
from src.webui.services import maps as map_service
from src.webui.services import plugins as plugin_service
from src.webui.services import rules as rule_service
from src.webui.services import worlds as world_service


from plugin_host_common import write_plugin, write_png, make_plugin_zip

def test_discovery_and_schema_config_need_no_host_code_change(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "first")
    write_plugin(plugins, "second")
    host = PluginHost(plugins, tmp_path / "data")

    found = host.discover()

    assert [item["id"] for item in found] == ["first", "second"]
    assert found[0]["config"]["names"] == []
    assert found[0]["plugin_type"] == "channel-adapter"
    assert found[0]["has_entrypoint"] is True
    assert "process.spawn" in found[0]["permissions"]
    assert "network.client" in found[0]["permissions"]


@pytest.mark.asyncio
async def test_config_normalizes_lists_and_masks_secrets(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins)
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    result = await host.update_config("example", {"names": [" 1 ", "1", "2"], "token": "secret-value"})

    assert result["config"]["names"] == ["1", "2"]
    assert result["config"]["token"] == {"configured": True, "masked": "***alue"}
    assert "secret-value" not in (tmp_path / "data" / "example" / "config.json").read_text(encoding="utf-8")


def test_read_docs_returns_markdown_content(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, manifest_extra={"docs": "README.md"})
    (plugins / "example" / "README.md").write_text("# 说明\n\n使用指南", encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    result = host.read_docs("example")

    assert result["ok"] is True
    assert result["found"] is True
    assert "# 说明" in result["content"]
    assert result["name"] == "README.md"


def test_read_docs_missing_returns_not_found(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins)
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    result = host.read_docs("example")

    assert result["ok"] is False
    assert result["found"] is False


def test_read_docs_rejects_path_traversal(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, manifest_extra={"docs": "../../secret.md"})
    (tmp_path / "secret.md").write_text("secret", encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    result = host.read_docs("example")

    assert result["ok"] is False
    assert result["found"] is False


def test_list_plugin_types_drives_frontend_filters():
    """前端筛选/展示由后端类型表驱动：filterable 类型按 filter_order 升序。"""
    from src.plugin_host.support import list_plugin_types
    types = list_plugin_types()
    filterable = [t["id"] for t in types if t["filterable"]]
    assert filterable == ["content-pack", "theme", "voice-pack", "tool", "channel-adapter", "provider"]
    assert len(types) == 8
    assert {t["id"] for t in types} == {
        "channel-adapter", "content-pack", "theme",
        "import-export", "provider", "tool", "bot-extension", "voice-pack",
    }


def test_rename_dir_with_retry_retries_transient_permission_error(tmp_path, monkeypatch):
    """Windows 下目录被短暂锁定时自动重试，最终成功。"""
    import asyncio

    import src.plugin_host.host as host_module

    calls = {"n": 0}

    def fake_rename(self, target):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return None

    monkeypatch.setattr(Path, "rename", fake_rename)
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    async def run():
        await host_module._rename_dir_with_retry(src_dir, dst_dir)

    asyncio.run(run())
    assert calls["n"] == 3


def test_rename_dir_with_retry_gives_up_after_attempts(tmp_path, monkeypatch):
    """超过重试次数后继续抛错。"""
    import asyncio

    import src.plugin_host.host as host_module

    calls = {"n": 0}

    def fake_rename(self, target):
        calls["n"] += 1
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(Path, "rename", fake_rename)
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    async def run():
        await host_module._rename_dir_with_retry(src_dir, dst_dir, attempts=3, delay=0)

    with pytest.raises(PermissionError):
        asyncio.run(run())
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_static_plugin_type_needs_no_entrypoint(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "paper-theme", plugin_type="theme", entrypoint=False)
    host = PluginHost(plugins, tmp_path / "data")

    found = host.discover()
    before = found[0]
    assert before["plugin_type"] == "theme"
    assert before["has_entrypoint"] is False
    assert before["status"] == "disabled"

    updated = await host.update_config("paper-theme", {"enabled": True})

    assert updated["enabled"] is True
    assert updated["running"] is False
    assert updated["status"] == "active"


def test_public_plugin_detail_reports_real_support_level(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "map-assets", plugin_type="content-pack", entrypoint=False)
    write_plugin(plugins, "future-tool", plugin_type="tool", entrypoint=True)
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    assert host.public_detail("map-assets")["support"]["level"] == "supported"
    assert host.public_detail("future-tool")["support"]["level"] == "supported"


@pytest.mark.asyncio
async def test_tool_plugin_registers_and_executes_over_stdio_rpc(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "echo-tool",
        plugin_type="tool",
        manifest_extra={
            "entrypoint": ["{python}", "{plugin_dir}/main.py"],
            "permissions": ["process.spawn", "plugin.config", "plugin.data", "tool.execute"],
        },
    )
    (plugins / "echo-tool" / "main.py").write_text(textwrap.dedent('''
        import json
        import sys
        for line in sys.stdin:
            request = json.loads(line)
            method = request["method"]
            if method == "initialize":
                result = {
                    "protocol_version": 1,
                    "tools": [{
                        "name": "echo",
                        "title": "Echo",
                        "description": "Return the supplied text.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    }],
                }
            elif method == "tool.call":
                if request["params"]["arguments"]["text"] == "fail":
                    response = {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32000, "message": "expected failure"}}
                    print(json.dumps(response), flush=True)
                    continue
                result = {"content": [{"type": "text", "text": request["params"]["arguments"]["text"]}]}
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            print(json.dumps(response), flush=True)
    '''), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    detail = await host.update_config("echo-tool", {"enabled": True})
    tools = host.list_tools()
    result = await host.call_tool("echo-tool", "echo", {"text": "hello"})

    assert detail["status"] == "running"
    assert detail["tools"][0]["name"] == "echo"
    assert tools[0]["plugin_id"] == "echo-tool"
    assert result["content"][0]["text"] == "hello"
    with pytest.raises(PluginProtocolError, match="缺少必填字段"):
        await host.call_tool("echo-tool", "echo", {})
    with pytest.raises(PluginInvocationError, match="expected failure"):
        await host.call_tool("echo-tool", "echo", {"text": "fail"})
    assert host.public_detail("echo-tool")["status"] == "running"
    await host.cleanup()


@pytest.mark.asyncio
async def test_bot_extension_runs_hooks_and_exposes_validated_images(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "pretty-bridge",
        plugin_type="bot-extension",
        manifest_extra={
            "entrypoint": ["{python}", "{plugin_dir}/main.py"],
            "permissions": ["process.spawn", "plugin.config", "plugin.data", "bot.extend"],
        },
    )
    (plugins / "pretty-bridge" / "main.py").write_text(textwrap.dedent('''
        import os
        from pathlib import Path
        from src.plugin_sdk import BridgeExtensionRuntime

        runtime = BridgeExtensionRuntime()
        data_dir = Path(os.environ["DICEFRAME_PLUGIN_DATA_DIR"])

        @runtime.extension(
            name="demo",
            title="Demo",
            description="Test command and renderer.",
            stages=["before_message", "render"],
            priority=50,
            platforms=["qq"],
        )
        def demo(stage, payload):
            if stage == "before_message":
                if payload.get("text") == "/broken":
                    raise RuntimeError("expected extension failure")
                return {
                    "handled": payload.get("text") == "/plugin",
                    "outputs": [{"type": "text", "text": "plugin handled"}],
                }
            image = data_dir / "demo.png"
            image.write_bytes(b"not-a-real-png-but-safe-test-data")
            return {
                "handled": True,
                "outputs": [{
                    "type": "image",
                    "path": "demo.png",
                    "fallback_text": payload.get("text", ""),
                }],
            }

        runtime.run()
    '''), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    try:
        detail = await host.update_config("pretty-bridge", {"enabled": True})
        command = await host.apply_bridge_extensions(
            "before_message",
            {"platform": "qq", "kind": "command", "text": "/plugin"},
        )
        failed_command = await host.apply_bridge_extensions(
            "before_message",
            {"platform": "qq", "kind": "command", "text": "/broken"},
        )
        rendered = await host.apply_bridge_extensions(
            "render",
            {"platform": "qq", "kind": "status", "text": "fallback"},
        )

        assert detail["status"] == "running"
        assert detail["bridge_extensions"][0]["name"] == "demo"
        assert command["handled"] is True
        assert command["outputs"] == [{"type": "text", "text": "plugin handled"}]
        assert failed_command["handled"] is False
        assert host.public_detail("pretty-bridge")["status"] == "running"
        assert rendered["handled"] is True
        assert rendered["outputs"][0]["asset_url"].endswith("/pretty-bridge/demo.png")
        assert host.bridge_asset_path("pretty-bridge", "demo.png").is_file()
        with pytest.raises(ValueError, match="路径越界"):
            host.bridge_asset_path("pretty-bridge", "../outside.png")
    finally:
        await host.cleanup()


@pytest.mark.asyncio
async def test_repository_bot_extension_example_runs_end_to_end(tmp_path):
    plugins = Path(__file__).resolve().parents[1] / "plugins" / "examples"
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    try:
        detail = await host.update_config(
            "bridge-customizer",
            {
                "enabled": True,
                "reply_footer": "— test footer",
                "image_cards": True,
            },
        )
        command = await host.apply_bridge_extensions(
            "before_message",
            {"platform": "maibot", "kind": "command", "text": "plugin test"},
        )
        changed = await host.apply_bridge_extensions(
            "after_result",
            {"platform": "maibot", "kind": "text", "text": "original"},
        )
        rendered = await host.apply_bridge_extensions(
            "render",
            {
                "platform": "qq",
                "kind": "card",
                "title": "Status",
                "fallback_text": "Status",
            },
        )

        assert detail["status"] == "running"
        assert command["handled"] is True
        assert command["outputs"][0]["type"] == "card"
        assert changed["payload"]["text"] == "original\n— test footer"
        assert rendered["handled"] is True
        assert rendered["outputs"][0]["type"] == "image"
        assert host.bridge_asset_path("bridge-customizer", "example-card.png").is_file()
    finally:
        await host.cleanup()


def test_public_detail_exposes_min_app_version_and_needs_core_update(tmp_path):
    """方案A：public_detail 透传 min_app_version + needs_core_update（展示用，不构成门控）。"""
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "tunnel", manifest_extra={"min_app_version": "99.0.0"})
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    detail = host.public_detail("tunnel")
    assert detail["min_app_version"] == "99.0.0"
    assert detail["needs_core_update"] is True


def test_public_detail_no_min_app_version_is_fine(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "plain")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    detail = host.public_detail("plain")
    assert detail["min_app_version"] == ""
    assert detail["needs_core_update"] is False


def test_version_below_semantics():
    from src.version import version_below
    assert version_below("1.9.13", "1.9.12") is True
    assert version_below("1.9.12", "1.9.12-beta.1") is False  # beta 视为满足同主版本
    assert version_below("1.9.12", "1.9.12") is False
    assert version_below("1.9.12", "1.10.0") is False
    assert version_below("", "1.9.12") is False
    assert version_below("2.0.0", "1.9.12") is True


@pytest.mark.asyncio
async def test_restart_api_consumers_only_restarts_running_http_plugins(tmp_path, monkeypatch):
    """内部 API 基址被修正后，只有"在运行 + 依赖 diceframe.http"的插件需要重启。

    插件进程在 spawn 时快照 TRPG_API_BASE；基址回退到实际成功的监听器后，
    未启动的插件下次 start 自然读到新值，只有运行中的必须重启进程。
    """

    host = PluginHost(tmp_path / "plugins", tmp_path / "data")

    def runtime_with(permissions, process):
        return SimpleNamespace(
            manifest={"permissions": permissions},
            schema={},
            config={},
            process=process,
        )

    host.plugins = {
        "http-running": runtime_with(["diceframe.http"], SimpleNamespace(returncode=None)),
        "http-exited": runtime_with(["diceframe.http"], SimpleNamespace(returncode=0)),
        "http-no-process": runtime_with(["diceframe.http"], None),
        "other-running": runtime_with(["plugin.config"], SimpleNamespace(returncode=None)),
    }
    restarted: list[str] = []

    async def fake_restart(plugin_id, *, require_enabled=True):
        restarted.append(plugin_id)

    monkeypatch.setattr(host, "restart", fake_restart)
    count = await host.restart_api_consumers()

    assert restarted == ["http-running"]
    assert count == 1


