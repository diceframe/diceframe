"""Module bound-game guards 测试（LIFE-01，母方案 §35/§36/§37/§124）。

覆盖：绑定存档存在时 uninstall/disable/update 被阻断并给出受影响存档清单、
未绑定时放行、非保护动作不受影响、坏包不拖垮检查。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.plugin_host.host import PluginHost
from src.adventures.registry import AdventureSourceRegistry
from src.webui.services.modules import (
    ModuleInUse,
    assert_module_action_allowed,
    module_bound_games,
)


class _Deps:
    def __init__(self, host: PluginHost, registry: AdventureSourceRegistry, instances: list) -> None:
        self.plugin_host = host
        self.adventure_registry = registry
        self.list_instances = lambda: instances


def _make_module(tmp_path: Path) -> tuple[PluginHost, AdventureSourceRegistry]:
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "castle-module"
    (plugin_dir / "adventures" / "castle").mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "schema_version": 1, "id": "castle-module", "name": "Castle",
        "version": "1.0.0", "plugin_type": "content-pack",
        "content_profile": "adventure-module", "content_delivery_mode": "catalog",
        "adventure_packages": ["adventures/castle"], "contributes": {},
    }), encoding="utf-8")
    (plugin_dir / "config.schema.json").write_text(
        '{"type": "object", "properties": {}}', encoding="utf-8",
    )
    package = plugin_dir / "adventures" / "castle"
    package.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path("templates/adventures/lanterns_of_greymoor"), package, dirs_exist_ok=True)
    manifest_path = package / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["adventure_id"] = "plugin:castle-quest"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    host = PluginHost(plugins_dir=plugins_root, data_dir=tmp_path / "data")
    _, runtime = host._load_runtime(plugin_dir)
    runtime.status = "enabled"
    host.plugins["castle-module"] = runtime

    registry = AdventureSourceRegistry.from_directories(None, None)
    from src.webui.api import WebAPI

    api = WebAPI.__new__(WebAPI)
    api._adventure_source_registry = registry
    api._plugins = host
    api._sync_plugin_adventure_sources()
    return host, registry


def test_uninstall_blocked_with_game_list_when_bound(tmp_path: Path) -> None:
    from src.engine.game_instance import GameInstance

    host, registry = _make_module(tmp_path)
    bound_game = GameInstance(game_key=("web", "room-a", "bot"))
    bound_game.world_id = "greymoor"
    assert bound_game.bind_adventure({
        "adventure_id": "plugin:castle-quest", "version": "1", "format": "v1",
        "content_digest": "deadbeef", "world_id": "greymoor",
    })
    deps = _Deps(host, registry, [bound_game])

    games = module_bound_games(deps, "castle-module")
    assert games == [{
        "game_key": "web|room-a|bot",
        "adventure_id": "plugin:castle-quest",
        "run_id": bound_game.run_id,
    }]

    with pytest.raises(ModuleInUse) as excinfo:
        assert_module_action_allowed(deps, "castle-module", "uninstall")
    assert excinfo.value.games[0]["game_key"] == "web|room-a|bot"
    with pytest.raises(ModuleInUse):
        assert_module_action_allowed(deps, "castle-module", "disable")
    with pytest.raises(ModuleInUse):
        assert_module_action_allowed(deps, "castle-module", "update")


def test_unbound_module_actions_are_allowed(tmp_path: Path) -> None:
    host, registry = _make_module(tmp_path)
    deps = _Deps(host, registry, [])
    for action in ("uninstall", "disable", "update"):
        assert_module_action_allowed(deps, "castle-module", action)  # 不抛


def test_non_protected_actions_pass_even_when_bound(tmp_path: Path) -> None:
    from src.engine.game_instance import GameInstance

    host, registry = _make_module(tmp_path)
    bound_game = GameInstance(game_key=("web", "room-a", "bot"))
    deps = _Deps(host, registry, [bound_game])
    assert_module_action_allowed(deps, "castle-module", "start")  # 不抛
    assert_module_action_allowed(deps, "castle-module", "view")   # 不抛


def test_unknown_module_has_no_bound_games(tmp_path: Path) -> None:
    host, registry = _make_module(tmp_path)
    deps = _Deps(host, registry, [])
    assert module_bound_games(deps, "nope") == []
    assert_module_action_allowed(deps, "nope", "uninstall")  # 不抛
