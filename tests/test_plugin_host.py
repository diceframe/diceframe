from __future__ import annotations

import io
import json
import textwrap
import zipfile
from pathlib import Path

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


def test_import_all_plugin_content_imports_characters_and_entries(tmp_path):
    from src.webui.services.plugins import import_all_plugin_content

    plugins = tmp_path / "plugins"
    write_plugin(plugins, "packs", plugin_type="content-pack", entrypoint=False,
                 manifest_extra={"docs": "README.md", "contributes": {
                     "characters": ["characters/*.json"],
                     "npcs": ["npc/*.json"],
                 }})
    (plugins / "packs" / "characters").mkdir()
    (plugins / "packs" / "npc").mkdir()
    (plugins / "packs" / "characters" / "hero.json").write_text(json.dumps({
        "id": "hero", "character_name": "Hero", "attributes": {}, "skills": []}), encoding="utf-8")
    (plugins / "packs" / "npc" / "mentor.json").write_text(json.dumps({
        "id": "mentor", "name": "Mentor", "description": "old mentor"}), encoding="utf-8")
    data_dir = tmp_path / "data"
    cfg = data_dir / "packs"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    host = PluginHost(plugins, data_dir)
    host.discover()

    class _Lore:
        worlds = {"w1": {"id": "w1", "name": "W"}}
        entries = {}
        def get_world(self, wid): return self.worlds.get(wid)
        def get_entry(self, eid): return self.entries.get(eid)
    class _Api:
        def __init__(self):
            self._plugins = host
            self._lore = _Lore()
            self.cards = []
        def save_character_card(self, card): self.cards.append(card); return {"ok": True}
        def save_entry(self, e): self._lore.entries[e["id"]] = e; return {"ok": True}

    api = _Api()
    result = import_all_plugin_content(api, "packs", "w1")

    assert result["ok"] is True
    assert result["imported_count"] == 2
    assert len(api.cards) == 1
    assert len(api._lore.entries) == 1


def test_sync_plugin_lorebook_and_cleanup(tmp_path):
    from src.webui.services.plugins import cleanup_plugin_lorebook, sync_plugin_lorebooks

    plugins = tmp_path / "plugins"
    write_plugin(plugins, "worlds", plugin_type="content-pack", entrypoint=False,
                 manifest_extra={"contributes": {"world_templates": ["worlds/*.json"]}})
    worlds_dir = plugins / "worlds" / "worlds"
    worlds_dir.mkdir(parents=True)
    (worlds_dir / "w.json").write_text(json.dumps({
        "world_id": "w", "world_name": "W", "default_rule": "none",
        "starter_lorebook": [
            {"id": "e1", "name": "Place", "type": "location", "keywords": ["P"], "content": "a place", "tier": "core"},
        ],
    }), encoding="utf-8")
    data_dir = tmp_path / "data"
    cfg = data_dir / "worlds"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    host = PluginHost(plugins, data_dir)
    host.discover()

    class _Lore:
        def __init__(self): self.entries = {}; self.worlds = {}
        def get_world(self, wid): return self.worlds.get(wid)
        def create_world(self, wid, name, description="", language=""): self.worlds[wid] = {"world_id": wid}
        def update_world_language(self, wid, lang): pass
        def get_entry(self, eid): return self.entries.get(eid)
        def add_entry(self, e): self.entries[e["id"]] = e
        def list_worlds(self): return [{"world_id": w} for w in self.worlds]
        def list_entries(self, wid): return [e for e in self.entries.values() if e["world_id"] == wid]
        def delete_entry(self, eid): self.entries.pop(eid, None)
        def delete_entries_by_plugin(self, pid):
            before = len(self.entries)
            self.entries = {k: v for k, v in self.entries.items() if v.get("source_plugin") != pid}
            return before - len(self.entries)
        def list_plugin_worlds(self, pid):
            wids = {e["world_id"] for e in self.entries.values() if e.get("source_plugin") == pid}
            return [{"id": w, "world_id": w} for w in wids]
        def delete_world_cascade(self, wid):
            self.entries = {k: v for k, v in self.entries.items() if v["world_id"] != wid}
            self.worlds.pop(wid, None)
    class _Api:
        def __init__(self): self._plugins = host; self._lore = _Lore(); self._reg = None
        def list_character_cards(self): return {"cards": []}
        def delete_character_card(self, card_id): return {"ok": True}

    api = _Api()
    synced = sync_plugin_lorebooks(api)
    assert synced["ok"] is True
    assert synced["synced"] == 1
    assert len(api._lore.entries) == 1
    entry_id = next(iter(api._lore.entries))
    assert "_plugin_worlds_" in entry_id
    assert next(iter(api._lore.entries.values())).get("source_plugin") == "worlds"

    removed = cleanup_plugin_lorebook(api, "worlds")
    assert removed["ok"] is True
    assert removed["removed"] == 1
    assert len(api._lore.entries) == 0


def test_cleanup_removes_imported_and_auto_synced_entries(tmp_path):
    """卸载按 source_plugin 清掉一键导入和自动灌入条目；用户自建保留；插件创建的世界有用户内容则保留。"""
    from src.webui.services.plugins import cleanup_plugin_lorebook

    plugins = tmp_path / "plugins"
    host = PluginHost(plugins, tmp_path / "data")

    class _Lore:
        def __init__(self):
            self.entries = {
                # 一键导入到你世界书的条目
                "myworld_plugin_npc_frieren-journey_frieren_himmel_hero": {"id": "myworld_plugin_npc_frieren-journey_frieren_himmel_hero", "world_id": "myworld", "source_plugin": "frieren-journey"},
                # 自动灌入插件世界书的条目
                "frieren_journey_world_plugin_frieren-journey_e1": {"id": "frieren_journey_world_plugin_frieren-journey_e1", "world_id": "frieren_journey_world", "source_plugin": "frieren-journey"},
                # 用户自建条目，不能删
                "myworld_user_note": {"id": "myworld_user_note", "world_id": "myworld", "source_plugin": ""},
            }
        def delete_entries_by_plugin(self, pid):
            before = len(self.entries)
            self.entries = {k: v for k, v in self.entries.items() if v.get("source_plugin") != pid}
            return before - len(self.entries)
        def list_plugin_worlds(self, pid):
            wids = {e["world_id"] for e in self.entries.values() if e.get("source_plugin") == pid}
            return [{"id": w, "world_id": w} for w in wids]
        def list_entries(self, wid): return [e for e in self.entries.values() if e["world_id"] == wid]
        def delete_world_cascade(self, wid):
            self.entries = {k: v for k, v in self.entries.items() if v["world_id"] != wid}

    class _Api:
        _plugins = host
        _lore = _Lore()
        _reg = None
        def list_character_cards(self): return {"cards": []}
        def delete_character_card(self, card_id): return {"ok": True}

    api = _Api()
    result = cleanup_plugin_lorebook(api, "frieren-journey")

    assert result["removed"] == 2
    # 插件创建的两个世界都含有用户自建内容或来源条目 -> 保留（无对局引用但世界仍有条目）
    # myworld 还有用户自建条目 -> 保留；frieren_journey_world 已无条目 -> 可删
    assert "myworld_user_note" in api._lore.entries
    assert result["worlds_removed"] == 1
    assert "myworld" in result["worlds_kept"]


@pytest.mark.asyncio
async def test_update_plugin_config_enables_and_syncs_lorebook(tmp_path):
    """启用内容包时应立即同步世界书。

    回归：update_plugin_config 曾用 result.get('ok') 判断成功，而 public_detail 不含
    ok 字段，导致启用时 sync 永不触发--只能靠后续开世界书页才同步。
    """
    from src.webui.services.plugins import update_plugin_config

    plugins = tmp_path / "plugins"
    write_plugin(plugins, "worlds", plugin_type="content-pack", entrypoint=False,
                 manifest_extra={"contributes": {"world_templates": ["worlds/*.json"]}})
    worlds_dir = plugins / "worlds" / "worlds"
    worlds_dir.mkdir(parents=True)
    (worlds_dir / "w.json").write_text(json.dumps({
        "world_id": "w", "world_name": "W", "default_rule": "none",
        "starter_lorebook": [
            {"id": "e1", "name": "Place", "type": "location", "keywords": ["P"], "content": "a place", "tier": "core"},
        ],
    }), encoding="utf-8")
    data_dir = tmp_path / "data"
    cfg = data_dir / "worlds"
    cfg.mkdir(parents=True)
    # 初始禁用，模拟用户刚装好内容包还没勾选启用
    (cfg / "config.json").write_text(json.dumps({"enabled": False}), encoding="utf-8")
    host = PluginHost(plugins, data_dir)
    host.discover()

    class _Lore:
        def __init__(self): self.entries = {}; self.worlds = {}
        def get_world(self, wid): return self.worlds.get(wid)
        def create_world(self, wid, name, description="", language=""): self.worlds[wid] = {"world_id": wid}
        def get_entry(self, eid): return self.entries.get(eid)
        def add_entry(self, e): self.entries[e["id"]] = e
    class _Api:
        def __init__(self): self._plugins = host; self._lore = _Lore()
        def list_character_cards(self): return {"cards": []}
        def delete_character_card(self, card_id): return {"ok": True}

    api = _Api()
    result = await update_plugin_config(api, "worlds", {"enabled": True})

    assert result["ok"] is True
    assert result["enabled"] is True
    # 启用即同步：无需先开世界书页，条目已带插件标记灌入
    assert len(api._lore.entries) == 1
    assert "_plugin_worlds_" in next(iter(api._lore.entries))


@pytest.mark.asyncio
async def test_export_content_pack_round_trips_through_install(tmp_path):
    """导出 .dfplugin -> 装回 -> 世界/角色卡正确出现，且 starter_lorebook 无损。"""
    import zipfile
    from src.webui.services.plugins import export_content_pack

    plugins_dir = tmp_path / "plugins"
    data_dir = tmp_path / "data"
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "myrule.json").write_text(json.dumps({
        "rule_id": "myrule", "rule_name": "测试规则", "dice_system": "none",
    }), encoding="utf-8")
    host = PluginHost(plugins_dir, data_dir)

    class _Lore:
        world = {"id": "w1", "name": "W", "description": "a world", "language": "zh-CN"}
        entries = [
            {"id": "e1", "world_id": "w1", "name": "Place", "type": "location",
             "keywords": ["P"], "content": "a place", "tier": "core"},
            {"id": "e2", "world_id": "w1", "name": "NPC", "type": "npc",
             "keywords": ["N"], "content": "a npc", "tier": "background",
             "unreliable": True, "order": 50, "group": "boss",
             "match_mode": "all", "sticky": 3, "connected_to": ["e1"]},
        ]
        def get_world(self, wid): return self.world if wid == "w1" else None
        def list_entries(self, wid): return list(self.entries) if wid == "w1" else []

    card = {"id": "card1", "character_name": "Hero", "race": "人类", "class": "战士",
            "attributes": {"str": 16}, "skills": [], "background": "bg", "rule_id": "myrule",
            "source": "插件内容包：old-pack", "plugin_content_id": "hero",
            "source_plugin": "old-pack", "portrait": {"kind": "builtin", "id": "warrior"}}

    class _Api:
        def __init__(self):
            self._plugins = host
            self._lore = _Lore()
            self._rules_dir = rules_dir
        def list_character_cards(self):
            return {"cards": [card], "total": 1}

    api = _Api()
    result = export_content_pack(api, "my-pack", "我的包", "1.0.0", "导出测试",
                                 world_id="w1", card_ids=["card1"], rule_id="myrule")

    assert result["ok"] is True
    assert result["filename"] == "my-pack-1.0.0.dfplugin"
    # 包结构：manifest + schema + readme + 世界 + 角色 + 规则
    archive = zipfile.ZipFile(io.BytesIO(result["payload"]))
    names = set(archive.namelist())
    assert "my-pack/plugin.json" in names
    assert "my-pack/config.schema.json" in names
    assert "my-pack/README.md" in names
    assert "my-pack/content/worlds/w1.json" in names
    assert "my-pack/content/rules/myrule.json" in names
    char_files = [n for n in names if n.startswith("my-pack/content/characters/")]
    assert len(char_files) == 1
    # starter_lorebook 无损：2 条，类型/内容保留
    world_tmpl = json.loads(archive.read("my-pack/content/worlds/w1.json"))
    assert world_tmpl["world_id"] == "w1"
    assert world_tmpl["default_rule"] == "myrule"
    assert len(world_tmpl["starter_lorebook"]) == 2
    assert {e["type"] for e in world_tmpl["starter_lorebook"]} == {"location", "npc"}
    # P2：starter_lorebook 元数据无损往返（unreliable/order/group/match_mode/sticky/connected_to 保留）
    e2 = next(e for e in world_tmpl["starter_lorebook"] if e["id"] == "e2")
    assert e2["unreliable"] is True
    assert e2["order"] == 50
    assert e2["group"] == "boss"
    assert e2["match_mode"] == "all"
    assert e2["sticky"] == 3
    assert e2["connected_to"] == ["e1"]
    assert "world_id" not in e2  # 内部追踪字段不进模板
    # 卡模板：builtin portrait 保留；source/source_plugin/plugin_content_id 不泄露原插件身份
    char_tmpl = json.loads(archive.read(char_files[0]))
    assert char_tmpl.get("portrait") == {"kind": "builtin", "id": "warrior"}
    assert "source_plugin" not in char_tmpl
    assert "source" not in char_tmpl
    assert "plugin_content_id" not in char_tmpl

    # 装回：install_from_zip 能装，启用后世界与角色卡可读
    await host.install_from_zip(result["payload"], overwrite=True)
    assert "my-pack" in host.plugins
    await host.update_config("my-pack", {"enabled": True})
    loaded = host.load_world_template("w1")
    assert loaded and loaded.get("world_id") == "w1"
    assert len(loaded.get("starter_lorebook", [])) == 2
    cards = host.list_content_resources("character_template").get("character_template", [])
    assert len(cards) == 1
    assert cards[0].get("character_name") == "Hero"


def test_export_content_pack_flat_has_plugin_json_at_root(tmp_path):
    """flat=True 导出仓库源码：plugin.json 在根目录，无 <id>/ 前缀，解压即可推到 GitHub。"""
    import zipfile
    from src.webui.services.plugins import export_content_pack

    plugins_dir = tmp_path / "plugins"
    data_dir = tmp_path / "data"
    host = PluginHost(plugins_dir, data_dir)

    class _Lore:
        world = {"id": "w1", "name": "W", "description": "d", "language": "zh-CN"}
        def get_world(self, wid): return self.world if wid == "w1" else None
        def list_entries(self, wid): return [] if wid != "w1" else [
            {"id": "e1", "world_id": "w1", "name": "P", "type": "location", "content": "c", "tier": "core"}]

    class _Api:
        def __init__(self): self._plugins = host; self._lore = _Lore(); self._rules_dir = tmp_path
        def list_character_cards(self): return {"cards": []}

    api = _Api()
    result = export_content_pack(api, "my-pack", "我的包", "1.0.0", "desc", world_id="w1", flat=True)

    assert result["ok"] is True
    assert result["filename"] == "my-pack-1.0.0-src.zip"
    archive = zipfile.ZipFile(io.BytesIO(result["payload"]))
    names = archive.namelist()
    # plugin.json 在根目录，没有 my-pack/ 前缀
    assert "plugin.json" in names
    assert "config.schema.json" in names
    assert "content/worlds/w1.json" in names
    assert not any(n.startswith("my-pack/") for n in names), names


def test_cleanup_plugin_removes_imported_character_cards(tmp_path):
    from src.webui.services.plugins import cleanup_plugin_lorebook

    plugins = tmp_path / "plugins"
    write_plugin(plugins, "packs", plugin_type="content-pack", entrypoint=False)
    data_dir = tmp_path / "data"
    (data_dir / "packs").mkdir(parents=True)
    (data_dir / "packs" / "config.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    host = PluginHost(plugins, data_dir)
    host.discover()

    cards_path = tmp_path / "character_cards.json"
    cards_path.write_text(json.dumps([
        {"id": "plugin_packs_hero", "character_name": "Hero", "schema_version": 2, "source_plugin": "packs"},
        {"id": "plugin_packs_fern", "character_name": "Fern", "schema_version": 2, "source_plugin": "packs"},
        {"id": "user_card", "character_name": "My Card", "schema_version": 2, "source_plugin": ""},
    ]), encoding="utf-8")

    class _Api:
        _plugins = host
        _lore = None
        _reg = None
        _character_cards_path = cards_path
        def __init__(self): self.cards = json.loads(cards_path.read_text(encoding="utf-8"))
        def list_character_cards(self):
            return {"cards": self.cards, "total": len(self.cards)}
        def delete_character_card(self, card_id):
            self.cards = [c for c in self.cards if c.get("id") != card_id]
            cards_path.write_text(json.dumps(self.cards), encoding="utf-8")
            return {"ok": True, "card_id": card_id}

    api = _Api()
    result = cleanup_plugin_lorebook(api, "packs")

    assert result["ok"] is True
    assert result["cards_removed"] == 2
    remaining = [c["character_name"] for c in api.cards]
    assert remaining == ["My Card"]


def test_cleanup_removes_cards_saved_through_real_save_path(tmp_path):
    """回归：插件角色卡经 save_character_card 落盘后 source_plugin 必须保留，
    否则 cleanup_plugin_lorebook 按 source_plugin 过滤会匹配不到。曾经因为
    _to_character_card 重建卡时丢弃该字段，导致卸载清理在生产中空转。"""
    from src.webui.services.plugins import cleanup_plugin_lorebook, _content_to_character_card
    from src.webui.services.character_cards import (
        save_character_card, list_character_cards, delete_character_card,
    )

    plugins = tmp_path / "plugins"
    write_plugin(plugins, "packs", plugin_type="content-pack", entrypoint=False)
    data_dir = tmp_path / "data"
    (data_dir / "packs").mkdir(parents=True)
    (data_dir / "packs" / "config.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    host = PluginHost(plugins, data_dir)
    host.discover()

    cards_path = tmp_path / "character_cards.json"

    class _Api:
        _plugins = host
        _lore = None
        _reg = None
        _character_cards_path = cards_path
        def list_character_cards(self):
            return list_character_cards(self)
        def delete_character_card(self, card_id):
            return delete_character_card(self, card_id)

    api = _Api()
    # 走真实导入链路：_content_to_character_card 打 source_plugin 标 -> save_character_card 落盘
    card = _content_to_character_card({
        "id": "hero",
        "plugin_id": "packs",
        "plugin_name": "packs",
        "character_name": "Hero",
        "race": "人类",
        "class": "冒险者",
    })
    save_character_card(api, card)

    persisted = list_character_cards(api)["cards"]
    assert len(persisted) == 1
    assert persisted[0]["source_plugin"] == "packs"  # Bug 1：曾在此处被 _to_character_card 丢弃

    result = cleanup_plugin_lorebook(api, "packs")
    assert result["cards_removed"] == 1
    assert list_character_cards(api)["cards"] == []


def test_list_plugin_types_drives_frontend_filters():
    """前端筛选/展示由后端类型表驱动：filterable 类型按 filter_order 升序。"""
    from src.plugin_host.support import list_plugin_types
    types = list_plugin_types()
    filterable = [t["id"] for t in types if t["filterable"]]
    assert filterable == ["content-pack", "theme", "tool", "channel-adapter", "map-pack"]
    assert len(types) == 8
    assert {t["id"] for t in types} == {
        "channel-adapter", "content-pack", "theme", "map-pack",
        "import-export", "provider", "tool", "bot-extension",
    }


def test_autoimport_plugin_content_idempotent():
    """启用内容包自动灌注：角色模板->卡库，npc/spell->插件世界；重复调用条目不复制。"""
    from src.webui.services.plugins import _autoimport_plugin_content

    saved_cards: list = []
    saved_entries: list = []

    class _Contrib:
        def __init__(self, plugin_id, key):
            self.plugin_id = plugin_id
            self.key = key

    class _ContribRegistry:
        def __init__(self, items):
            self._items = items
        def list(self, kind):
            return self._items

    class _Plugins:
        def __init__(self, contribs):
            self.contributions = _ContribRegistry(contribs)
        def list_content_resources(self):
            return {
                "character_template": [{"plugin_id": "pack", "id": "hero", "character_name": "Hero", "plugin_name": "pack"}],
                "npc": [{"plugin_id": "pack", "id": "himmel", "name": "Himmel", "description": "hero"}],
                "spell": [{"plugin_id": "pack", "id": "zoltraak", "name": "Zoltraak", "description": "spell"}],
                "item": [], "class": [],
            }

    class _Lore:
        def __init__(self):
            self.entries: dict = {}
        def get_world(self, wid):
            return wid == "w1"
        def get_entry(self, eid):
            return self.entries.get(eid)
        def update_entry(self, eid, entry):
            self.entries[eid] = entry

    lore = _Lore()

    class _Api:
        def __init__(self, plugins):
            self._plugins = plugins
            self._lore = lore
        def save_character_card(self, card):
            saved_cards.append(card)
            return {"ok": True}
        def save_entry(self, entry):
            lore.entries[entry["id"]] = entry
            saved_entries.append(entry)
            return {"ok": True}

    api = _Api(_Plugins([_Contrib("pack", "w1")]))
    _autoimport_plugin_content(api, "pack")

    assert len(saved_cards) == 1
    assert saved_cards[0]["character_name"] == "Hero"
    assert len(saved_entries) == 2
    assert all(e["world_id"] == "w1" for e in saved_entries)
    assert all(e["source_plugin"] == "pack" for e in saved_entries)
    assert any("npc" in e["id"] for e in saved_entries)
    assert any("spell" in e["id"] for e in saved_entries)

    # 幂等：再调一次，已存在的世界书条目跳过（不复制）
    _autoimport_plugin_content(api, "pack")
    assert len(saved_entries) == 2


def test_autoimport_plugin_content_without_world_only_imports_cards():
    """无 world_template 时：只导角色模板，npc/spell 跳过（无自然归属世界）。"""
    from src.webui.services.plugins import _autoimport_plugin_content

    saved_cards: list = []
    saved_entries: list = []

    class _ContribRegistry:
        def list(self, kind):
            return []

    class _Plugins:
        contributions = _ContribRegistry()
        def list_content_resources(self):
            return {
                "character_template": [{"plugin_id": "pack", "id": "hero", "character_name": "Hero", "plugin_name": "pack"}],
                "npc": [{"plugin_id": "pack", "id": "himmel", "name": "Himmel", "description": "hero"}],
                "spell": [], "item": [], "class": [],
            }

    class _Api:
        _plugins = _Plugins()
        _lore = None
        def save_character_card(self, card):
            saved_cards.append(card)
            return {"ok": True}
        def save_entry(self, entry):
            saved_entries.append(entry)
            return {"ok": True}

    _autoimport_plugin_content(_Api(), "pack")
    assert len(saved_cards) == 1
    assert len(saved_entries) == 0  # 无世界，npc 跳过


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
async def test_install_marketplace_plugin_triggers_autoimport_when_enabled():
    """已启用内容包从商店安装/更新后自动同步世界书并灌入内容。"""
    from src.webui.services.plugins import install_marketplace_plugin

    saved_cards: list = []
    saved_entries: list = []

    class _Contrib:
        def __init__(self, key):
            self.plugin_id = "pack"
            self.key = key

    class _ContribRegistry:
        def list(self, kind):
            return [_Contrib("w1")]

    class _Plugins:
        contributions = _ContribRegistry()

        def __init__(self):
            self.synced = False

        async def install_from_marketplace(self, plugin_id, overwrite=False):
            return {"id": plugin_id, "name": "pack"}

        def public_detail(self, plugin_id):
            return {"id": plugin_id, "enabled": True, "status": "active"}

        def sync_lorebooks(self, lore):
            self.synced = True
            return 0

        def list_content_resources(self):
            return {
                "character_template": [{"plugin_id": "pack", "id": "hero", "character_name": "Hero", "plugin_name": "pack"}],
                "npc": [{"plugin_id": "pack", "id": "himmel", "name": "Himmel", "description": "hero"}],
                "spell": [], "item": [], "class": [],
            }

    class _Lore:
        def get_world(self, wid):
            return wid == "w1"

        def get_entry(self, eid):
            return None

        def update_entry(self, eid, entry):
            saved_entries.append(entry)

    class _Api:
        _plugins = _Plugins()
        _lore = _Lore()

        def save_character_card(self, card):
            saved_cards.append(card)
            return {"ok": True}

        def save_entry(self, entry):
            saved_entries.append(entry)
            return {"ok": True}

    api = _Api()
    result = await install_marketplace_plugin(api, "pack")

    assert result["ok"] is True
    assert api._plugins.synced is True
    assert len(saved_cards) == 1
    assert saved_cards[0]["character_name"] == "Hero"
    assert len(saved_entries) == 1
    assert saved_entries[0]["source_plugin"] == "pack"


@pytest.mark.asyncio
async def test_install_marketplace_plugin_skips_autoimport_when_disabled():
    """未启用插件安装后不触发自动导入。"""
    from src.webui.services.plugins import install_marketplace_plugin

    class _Plugins:
        async def install_from_marketplace(self, plugin_id, overwrite=False):
            return {"id": plugin_id, "name": "pack"}

        def public_detail(self, plugin_id):
            return {"id": plugin_id, "enabled": False, "status": "disabled"}

        def sync_lorebooks(self, lore):
            raise AssertionError("should not sync")

        def list_content_resources(self):
            raise AssertionError("should not list")

    class _Api:
        _plugins = _Plugins()
        _lore = None

    result = await install_marketplace_plugin(_Api(), "pack")
    assert result["ok"] is True


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


def test_bot_extension_requires_extend_permission_when_explicit(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "under-declared-bridge",
        plugin_type="bot-extension",
        manifest_extra={"permissions": ["process.spawn", "plugin.data"]},
    )
    host = PluginHost(plugins, tmp_path / "data")

    detail = host.discover()[0]

    assert detail["status"] == "failed"
    assert "bot.extend" in detail["error"]


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
