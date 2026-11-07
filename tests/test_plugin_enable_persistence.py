"""启用/禁用语义：声明型插件（内容包/模块）的"启用"必须跨重启持久。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.plugin_host import PluginHost
from src.webui.services import plugins as plugin_service

from plugin_host_common import write_plugin


def _content_pack(tmp_path: Path) -> tuple[Path, Path]:
    plugins = tmp_path / "plugins"
    data = tmp_path / "data"
    write_plugin(plugins, "safe-pack", plugin_type="content-pack", entrypoint=False)
    return plugins, data


@pytest.mark.asyncio
async def test_enabling_a_declaration_only_pack_persists_enabled(tmp_path):
    plugins, data = _content_pack(tmp_path)
    host = PluginHost(plugins, data)
    host.discover()
    assert host.public_detail("safe-pack")["enabled"] is False

    await host.start("safe-pack", require_enabled=False)

    detail = host.public_detail("safe-pack")
    assert detail["status"] == "active"
    assert detail["enabled"] is True
    saved = json.loads((data / "safe-pack" / "config.json").read_text(encoding="utf-8"))
    assert saved["enabled"] is True


@pytest.mark.asyncio
async def test_enabled_content_pack_is_still_active_after_a_restart(tmp_path):
    plugins, data = _content_pack(tmp_path)
    host = PluginHost(plugins, data)
    host.discover()
    await host.start("safe-pack", require_enabled=False)

    restarted = PluginHost(plugins, data)
    restarted.discover()

    assert restarted.public_detail("safe-pack")["enabled"] is True
    assert restarted.public_detail("safe-pack")["status"] == "active"


@pytest.mark.asyncio
async def test_disabling_a_content_pack_persists_too(tmp_path):
    plugins, data = _content_pack(tmp_path)
    host = PluginHost(plugins, data)
    host.discover()
    await host.start("safe-pack", require_enabled=False)
    await host.stop("safe-pack")

    restarted = PluginHost(plugins, data)
    restarted.discover()

    assert restarted.public_detail("safe-pack")["enabled"] is False
    assert restarted.public_detail("safe-pack")["status"] == "disabled"


@pytest.mark.asyncio
async def test_control_plugin_start_enables_a_disabled_content_pack(tmp_path):
    """安装后的默认禁用状态必须能被 /plugins/{id}/start（前端"启用"）翻转并持久。"""

    plugins, data = _content_pack(tmp_path)
    host = PluginHost(plugins, data)
    host.discover()
    # 该包没有 world_template 贡献，同步世界书是空操作，故只给最小 content 依赖。
    dependencies = plugin_service.PluginLifecycleDependencies(
        plugin_host=host,
        content=SimpleNamespace(plugin_host=host, store=SimpleNamespace(lorebook=None)),
    )

    result = await plugin_service.control_plugin(dependencies, "safe-pack", "start")

    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["status"] == "active"
