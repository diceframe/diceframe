from __future__ import annotations

import json
import textwrap
import zipfile

import pytest

from src.plugin_host import PluginHost
from src.plugin_host.runtime_protocol import PluginInvocationError, PluginProtocolError
from src.rules.rule_system import RuleSystem
from src.webui.services import maps as map_service
from src.webui.services import plugins as plugin_service
from src.webui.services import rules as rule_service
from src.webui.services import worlds as world_service


def write_plugin(root, plugin_id="example", *, plugin_type="channel-adapter", entrypoint=True, manifest_extra=None):
    folder = root / plugin_id
    folder.mkdir(parents=True)
    manifest = {
        "schema_version": 1, "id": plugin_id, "name": "Example", "version": "1",
        "description": "test",
        "config_schema": "config.schema.json",
    }
    if plugin_type is not None:
        manifest["plugin_type"] = plugin_type
    if entrypoint:
        manifest["entrypoint"] = ["{python}", "-c", "pass"]
    if manifest_extra:
        manifest.update(manifest_extra)
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "config.schema.json").write_text(json.dumps({
        "type": "object", "properties": {
            "enabled": {"type": "boolean", "default": False, "ui": {"control": "switch"}},
            "names": {"type": "array", "default": [], "ui": {"control": "string-list"}},
            "token": {"type": "string", "ui": {"control": "secret", "sensitive": True}},
        },
    }), encoding="utf-8")


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


def test_invalid_manifest_isolated_from_other_plugins(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "good")
    bad = plugins / "bad"
    bad.mkdir(parents=True)
    (bad / "plugin.json").write_text("{}", encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")

    found = host.discover()

    assert next(item for item in found if item["id"] == "good")["status"] == "disabled"
    assert next(item for item in found if item["id"] == "bad")["status"] == "failed"


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


@pytest.mark.asyncio
async def test_static_plugin_registers_contributions_only_when_enabled(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "starter-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {"rules": ["content/rules/*.json"]}},
    )
    rule_dir = plugins / "starter-pack" / "content" / "rules"
    rule_dir.mkdir(parents=True)
    (rule_dir / "pack_rule.json").write_text(json.dumps({
        "rule_id": "pack_rule",
        "rule_name": "Pack Rule",
        "attributes": [],
    }), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")

    host.discover()

    assert host.list_contributions("rule") == []
    await host.update_config("starter-pack", {"enabled": True})
    contributions = host.list_contributions("rule")
    assert [item["key"] for item in contributions] == ["pack_rule"]
    assert contributions[0]["path"] == "content/rules/pack_rule.json"

    await host.update_config("starter-pack", {"enabled": False})

    assert host.list_contributions("rule") == []


@pytest.mark.asyncio
async def test_theme_and_map_pack_contributions_are_queryable(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "paper-theme",
        plugin_type="theme",
        entrypoint=False,
        manifest_extra={"contributes": {"theme": "theme/theme.json"}},
    )
    theme_dir = plugins / "paper-theme" / "theme"
    theme_dir.mkdir(parents=True)
    (theme_dir / "theme.json").write_text(json.dumps({
        "id": "paper-theme",
        "name": "Paper Theme",
        "description": "Soft paper colors",
        "tokens": {
            "base": {"--gold": "#ccaa66", "color": "red", "--bad": "url(http://bad)"},
            "dark": {"--panel": "#111111"},
        },
    }), encoding="utf-8")
    write_plugin(
        plugins,
        "map-assets",
        plugin_type="map-pack",
        entrypoint=False,
        manifest_extra={"contributes": {"locations": ["maps/locations/*.json"]}},
    )
    location_dir = plugins / "map-assets" / "maps" / "locations"
    location_dir.mkdir(parents=True)
    (location_dir / "town.json").write_text(json.dumps({
        "id": "town",
        "name": "Town",
    }), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    await host.update_config("paper-theme", {"enabled": True})
    await host.update_config("map-assets", {"enabled": True})

    assert host.list_contributions("theme")[0]["key"] == "paper-theme"
    assert host.list_contributions("map_location")[0]["key"] == "town"
    theme = host.list_themes()[0]
    assert theme["tokens"]["base"] == {"--gold": "#ccaa66"}
    assert theme["tokens"]["dark"] == {"--panel": "#111111"}


@pytest.mark.asyncio
async def test_map_pack_locations_and_assets_are_consumed_by_map_service(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "map-assets",
        plugin_type="map-pack",
        entrypoint=False,
        manifest_extra={"contributes": {
            "locations": ["maps/locations/*.json"],
            "icons": ["maps/icons/*.png"],
        }},
    )
    location_dir = plugins / "map-assets" / "maps" / "locations"
    icon_dir = plugins / "map-assets" / "maps" / "icons"
    location_dir.mkdir(parents=True)
    icon_dir.mkdir(parents=True)
    (location_dir / "town.json").write_text(json.dumps({
        "id": "town",
        "name": "Town",
        "world_id": "pack_world",
        "connected_to": [],
        "content": "A plugin location.",
    }), encoding="utf-8")
    (icon_dir / "town.png").write_bytes(b"fake-png")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.update_config("map-assets", {"enabled": True})

    class Registry:
        def get(self, key):
            class Instance:
                world_id = "pack_world"
                scene = ""
            return Instance()

    class Lore:
        def list_entries(self, world_id, entry_type):
            return []

    class Api:
        _plugins = host
        _reg = Registry()
        _lore = Lore()

        @staticmethod
        def _parse_key(game_key):
            return ("web", game_key, "web_bot")

    result = map_service.get_map_locations(Api, "demo")

    assert result["locations"][0]["id"] == "town"
    assert result["assets"]["icons"][0]["url"] == "/api/plugins/assets/map-assets/maps/icons/town.png"
    assert host.public_asset_path("map-assets", "maps/icons/town.png").exists()
    with pytest.raises(KeyError):
        host.public_asset_path("map-assets", "plugin.json")


def test_unknown_plugin_type_is_rejected_but_isolated(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "good")
    write_plugin(plugins, "weird", plugin_type="unknown-kind")
    host = PluginHost(plugins, tmp_path / "data")

    found = host.discover()

    assert next(item for item in found if item["id"] == "good")["status"] == "disabled"
    bad = next(item for item in found if item["id"] == "weird")
    assert bad["status"] == "failed"
    assert "不支持的 plugin_type" in bad["error"]


def test_missing_plugin_type_is_rejected(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "missing-type", plugin_type=None)
    host = PluginHost(plugins, tmp_path / "data")

    found = host.discover()

    bad = found[0]
    assert bad["status"] == "failed"
    assert "不支持的 plugin_type" in bad["error"]


def test_public_plugin_detail_reports_real_support_level(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "map-assets", plugin_type="map-pack", entrypoint=False)
    write_plugin(plugins, "future-tool", plugin_type="tool", entrypoint=True)
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    assert host.public_detail("map-assets")["support"]["level"] == "partial"
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
async def test_tool_plugin_with_invalid_handshake_fails_closed(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "bad-tool",
        plugin_type="tool",
        manifest_extra={"entrypoint": ["{python}", "{plugin_dir}/main.py"]},
    )
    (plugins / "bad-tool" / "main.py").write_text(
        "import sys\nfor line in sys.stdin:\n print('not-json', flush=True)\n",
        encoding="utf-8",
    )
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    detail = await host.update_config("bad-tool", {"enabled": True})

    assert detail["status"] == "failed"
    assert "stdout 只能输出协议消息" in detail["error"]
    assert detail["running"] is False
    assert host.list_tools() == []


def test_tool_plugin_requires_execute_permission_when_permissions_are_explicit(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "under-declared-tool",
        plugin_type="tool",
        manifest_extra={"permissions": ["process.spawn", "plugin.data"]},
    )
    host = PluginHost(plugins, tmp_path / "data")

    detail = host.discover()[0]

    assert detail["status"] == "failed"
    assert "tool.execute" in detail["error"]


def test_process_environment_does_not_inherit_unrelated_host_secrets(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "future-tool",
        plugin_type="tool",
        entrypoint=True,
        manifest_extra={"permissions": ["process.spawn", "plugin.data"]},
    )
    monkeypatch.setenv("DICEFRAME_TEST_HOST_SECRET", "must-not-leak")
    host = PluginHost(
        plugins,
        tmp_path / "data",
        base_env={"TRPG_API_BASE": "http://127.0.0.1:18000", "TRPG_BOT_TOKEN": "bot-secret"},
    )
    host.discover()

    env = host._build_process_env("future-tool", host.plugins["future-tool"])

    assert "DICEFRAME_TEST_HOST_SECRET" not in env
    assert "TRPG_BOT_TOKEN" not in env
    assert "TRPG_API_BASE" not in env
    assert env["DICEFRAME_PLUGIN_ID"] == "future-tool"
    assert env["DICEFRAME_PLUGIN_DATA_DIR"].endswith("future-tool\\runtime") or env["DICEFRAME_PLUGIN_DATA_DIR"].endswith("future-tool/runtime")


def test_http_capability_receives_only_diceframe_connection_credentials(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "chat-adapter", plugin_type="channel-adapter", entrypoint=True)
    host = PluginHost(
        plugins,
        tmp_path / "data",
        base_env={"TRPG_API_BASE": "http://127.0.0.1:18000", "TRPG_BOT_TOKEN": "bot-secret", "UNRELATED": "no"},
    )
    host.discover()

    env = host._build_process_env("chat-adapter", host.plugins["chat-adapter"])

    assert env["TRPG_API_BASE"] == "http://127.0.0.1:18000"
    assert env["TRPG_BOT_TOKEN"]
    assert env["TRPG_BOT_TOKEN"] != "bot-secret"
    assert host.authenticate_api_token(env["TRPG_BOT_TOKEN"])["plugin_id"] == "chat-adapter"
    assert (tmp_path / "data" / "chat-adapter" / "auth.json").exists()
    assert "UNRELATED" not in env


def test_unknown_plugin_permission_is_rejected(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "bad-permission", manifest_extra={"permissions": ["network.client", "system.full"]})
    host = PluginHost(plugins, tmp_path / "data")

    found = host.discover()

    bad = found[0]
    assert bad["status"] == "failed"
    assert "未知插件权限" in bad["error"]


def test_channel_adapter_still_requires_entrypoint(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "bad-adapter", plugin_type="channel-adapter", entrypoint=False)
    host = PluginHost(plugins, tmp_path / "data")

    found = host.discover()

    bad = found[0]
    assert bad["status"] == "failed"
    assert "entrypoint" in bad["error"]


@pytest.mark.asyncio
async def test_content_pack_rules_and_worlds_are_visible_to_services(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "starter-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {
            "rules": ["content/rules/*.json"],
            "world_templates": ["content/worlds/*.json"],
        }},
    )
    rule_dir = plugins / "starter-pack" / "content" / "rules"
    world_dir = plugins / "starter-pack" / "content" / "worlds"
    rule_dir.mkdir(parents=True)
    world_dir.mkdir(parents=True)
    (rule_dir / "pack_rule.json").write_text(json.dumps({
        "rule_id": "pack_rule",
        "rule_name": "Pack Rule",
        "description": "From plugin",
        "dice_system": "d20",
        "combat_model": "hp_based",
        "attributes": [],
    }), encoding="utf-8")
    (world_dir / "pack_world.json").write_text(json.dumps({
        "world_id": "pack_world",
        "world_name": "Pack World",
        "description": "Plugin world",
        "default_rule": "pack_rule",
        "starter_lorebook": [{"name": "Town", "content": "A small town."}],
    }), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.update_config("starter-pack", {"enabled": True})

    class Api:
        _plugins = host
        _rules_dir = tmp_path / "rules"
        _worlds_dir = tmp_path / "worlds"

    Api._rules_dir.mkdir()
    Api._worlds_dir.mkdir()

    rule_items = rule_service.list_rules(Api)["rules"]
    world_items = world_service.list_world_templates(Api)["templates"]
    detail = rule_service.get_rule_template(Api, "pack_rule")

    assert next(item for item in rule_items if item["rule_id"] == "pack_rule")["plugin_id"] == "starter-pack"
    assert next(item for item in world_items if item["world_id"] == "pack_world")["plugin_id"] == "starter-pack"
    assert detail["ok"] is True
    assert detail["rule"]["readonly"] is True

    world_data = host.load_world_template("pack_world")
    loaded_rule = RuleSystem.load_for_world(world_data, Api._rules_dir)

    assert loaded_rule is not None
    assert loaded_rule.rule_id == "pack_rule"


@pytest.mark.asyncio
async def test_content_pack_catalog_lists_static_resources(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "library-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {
            "character_templates": ["content/characters/*.json"],
            "npcs": ["content/npc/*.json"],
            "items": ["content/items/*.json"],
            "spells": ["content/spells/*.json"],
            "classes": ["content/classes/*.json"],
        }},
    )
    for folder in ("characters", "npc", "items", "spells", "classes"):
        (plugins / "library-pack" / "content" / folder).mkdir(parents=True)
    (plugins / "library-pack" / "content" / "characters" / "hero.json").write_text(json.dumps({
        "id": "hero",
        "character_name": "Hero",
        "rule_id": "pack_rule",
    }), encoding="utf-8")
    (plugins / "library-pack" / "content" / "npc" / "elder.json").write_text(json.dumps({
        "id": "elder",
        "name": "Elder",
        "world_id": "pack_world",
    }), encoding="utf-8")
    (plugins / "library-pack" / "content" / "items" / "key.json").write_text(json.dumps({
        "id": "key",
        "name": "Silver Key",
    }), encoding="utf-8")
    (plugins / "library-pack" / "content" / "spells" / "spark.json").write_text(json.dumps({
        "id": "spark",
        "name": "Spark",
    }), encoding="utf-8")
    (plugins / "library-pack" / "content" / "classes" / "mage.json").write_text(json.dumps({
        "id": "mage",
        "name": "Mage",
    }), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    assert host.list_content_resources()["character_template"] == []
    await host.update_config("library-pack", {"enabled": True})
    all_resources = host.list_content_resources()
    filtered = host.list_content_resources("npc", world_id="other_world")

    assert all_resources["character_template"][0]["character_name"] == "Hero"
    assert all_resources["npc"][0]["name"] == "Elder"
    assert all_resources["item"][0]["name"] == "Silver Key"
    assert all_resources["spell"][0]["name"] == "Spark"
    assert all_resources["class"][0]["name"] == "Mage"
    assert filtered["npc"] == []


@pytest.mark.asyncio
async def test_plugin_content_can_import_character_template_and_lore_entries(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "library-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {
            "character_templates": ["content/characters/*.json"],
            "npcs": ["content/npc/*.json"],
        }},
    )
    character_dir = plugins / "library-pack" / "content" / "characters"
    npc_dir = plugins / "library-pack" / "content" / "npc"
    character_dir.mkdir(parents=True)
    npc_dir.mkdir(parents=True)
    (character_dir / "hero.json").write_text(json.dumps({
        "id": "hero",
        "character_name": "Hero",
        "race": "Human",
        "class": "Fighter",
    }), encoding="utf-8")
    (npc_dir / "elder.json").write_text(json.dumps({
        "id": "elder",
        "name": "Elder",
        "description": "Village elder.",
    }), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.update_config("library-pack", {"enabled": True})

    class Lore:
        def __init__(self):
            self.entries = {}

        def get_world(self, world_id):
            return {"id": world_id, "name": "World"} if world_id == "pack_world" else None

        def get_entry(self, entry_id):
            return self.entries.get(entry_id)

    class Api:
        def __init__(self):
            self._plugins = host
            self._lore = Lore()
            self.cards = []
            self.entries = []

        def save_character_card(self, card):
            self.cards.append(card)
            return {"ok": True, "card": card}

        def save_entry(self, entry):
            self.entries.append(entry)
            self._lore.entries[entry["id"]] = entry
            return {"ok": True, "entry_id": entry["id"]}

    api = Api()

    card_result = plugin_service.import_plugin_content(api, "character_template", "hero", "library-pack")
    entry_result = plugin_service.import_plugin_content(api, "npc", "elder", "library-pack", "pack_world")

    assert card_result["ok"] is True
    assert api.cards[0]["character_name"] == "Hero"
    assert api.cards[0]["source"] == "插件内容包：Example"
    assert entry_result["ok"] is True
    assert api.entries[0]["world_id"] == "pack_world"
    assert api.entries[0]["type"] == "npc"
    assert "Village elder." in api.entries[0]["content"]


def make_plugin_zip(path, plugin_id="demo-plugin"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{plugin_id}/plugin.json", json.dumps({
            "schema_version": 1,
            "id": plugin_id,
            "name": "Demo",
            "version": "1",
            "description": "demo",
            "plugin_type": "channel-adapter",
            "entrypoint": ["{python}", "-c", "pass"],
            "config_schema": "config.schema.json",
        }))
        archive.writestr(f"{plugin_id}/config.schema.json", json.dumps({
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": False, "ui": {"control": "switch"}},
            },
        }))


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
    removed = await host.uninstall("demo-plugin")
    assert removed["uninstalled"] is True
    assert not (tmp_path / "plugins" / "demo-plugin").exists()


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

    with pytest.raises(ValueError, match="不能超过 20 MB"):
        await host.install_from_zip(b"x" * 11)


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
async def test_rescan_discovers_manually_copied_plugins(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    write_plugin(plugins, "copied-pack", plugin_type="content-pack", entrypoint=False)
    monkeypatch.setattr(host, "auto_update_safe_plugins", lambda: _async_value([]))

    found = await host.rescan()

    assert [item["id"] for item in found] == ["copied-pack"]
    assert host.public_detail("copied-pack")["plugin_type"] == "content-pack"


async def _async_value(value):
    return value
