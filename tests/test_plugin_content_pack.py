"""插件 content pack 域测试（自 test_plugin_host 拆分）。"""

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

class ContentMapApiFacade:
    """Minimal WebAPI map-packaging facade used by service unit tests."""

    @staticmethod
    def resolve_map_background_file(selection):
        del selection
        return None

    def package_content_map(self, *args, **kwargs):
        dependencies = content_pack_map_service.ContentMapDependencies(
            resolve_background_file=self.resolve_map_background_file,
        )
        return content_pack_map_service.package_content_map(
            dependencies, *args, **kwargs
        )


def _plugin_content_dependencies(api) -> plugin_service.PluginContentDependencies:
    host = getattr(api, "_plugins", None)
    lorebook = getattr(api, "_lore", None)
    registry = getattr(api, "_reg", None)
    list_games = getattr(registry, "list_all", lambda: []) if registry else lambda: []
    plugin_asset = getattr(
        api,
        "plugin_asset_path",
        lambda plugin_id, relative_path: host.public_asset_path(
            plugin_id, relative_path,
        ),
    )
    return plugin_service.PluginContentDependencies(
        plugin_host=host,
        store=plugin_service.PluginContentStoreDependencies(
            lorebook=lorebook,
            list_games=list_games,
            list_character_cards=getattr(api, "list_character_cards", lambda: {"cards": []}),
            save_character_card=getattr(
                api, "save_character_card", lambda _card: {"ok": False}
            ),
            delete_character_card=getattr(
                api, "delete_character_card", lambda _card_id: {"ok": True}
            ),
            save_entry=getattr(api, "save_entry", lambda _entry: {"ok": False}),
        ),
        portraits=plugin_service.PluginPortraitDependencies(
            plugin_asset_path=plugin_asset,
            avatar_file=getattr(api, "avatar_file", lambda _asset_id: None),
            generated_image_file=getattr(
                api, "generated_image_file", lambda _asset_id: None
            ),
            save_avatar_upload=getattr(
                api, "save_avatar_upload", lambda _data, _name: {"ok": False}
            ),
        ),
    )


def _plugin_lifecycle_dependencies(api) -> plugin_service.PluginLifecycleDependencies:
    content = _plugin_content_dependencies(api)
    return plugin_service.PluginLifecycleDependencies(
        plugin_host=content.plugin_host,
        content=content,
    )


def _plugin_export_dependencies(api) -> plugin_service.PluginExportDependencies:
    host = getattr(api, "_plugins", None)
    return plugin_service.PluginExportDependencies(
        plugin_host=host,
        lorebook=getattr(api, "_lore", None),
        rules_dir=getattr(api, "_rules_dir"),
        list_character_cards=getattr(api, "list_character_cards", lambda: {"cards": []}),
        media=plugin_service.PluginExportMediaDependencies(
            package_scene_image=getattr(
                api, "package_scene_image", lambda _reference, _files: None
            ),
            package_content_map=getattr(
                api, "package_content_map", lambda *_args, **_kwargs: None
            ),
            avatar_file=getattr(api, "avatar_file", lambda _asset_id: None),
            generated_image_file=getattr(
                api, "generated_image_file", lambda _asset_id: None
            ),
            plugin_asset_path=getattr(
                api,
                "plugin_asset_path",
                lambda plugin_id, relative_path: host.public_asset_path(
                    plugin_id, relative_path,
                ),
            ),
        ),
    )


async def _unused_map_save(_instance):
    return None


def _map_dependencies(api) -> map_service.MapDependencies:
    plugin_host = getattr(api, "_plugins", None)
    load_world_template = getattr(api, "_load_world_template", None)
    return map_service.MapDependencies(
        get_instance=api._reg.get,
        parse_game_key=api._parse_key,
        list_lore_entries=api._lore.list_entries,
        list_map_assets=(
            plugin_host.list_map_assets
            if plugin_host is not None
            else lambda _world_id: {
                "maps": [], "locations": [], "icons": [], "scenes": [],
            }
        ),
        validate_background_selection=lambda _selection: {"kind": "auto"},
        save_instance=_unused_map_save,
        load_world_template=(
            load_world_template
            if callable(load_world_template)
            else lambda _world_id: None
        ),
        map_background_file=lambda _asset_id: None,
        generated_image_file=lambda _asset_id: None,
    )


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
    result = import_all_plugin_content(_plugin_content_dependencies(api), "packs", "w1")

    assert result["ok"] is True
    assert result["imported_count"] == 2
    assert len(api.cards) == 1
    assert len(api._lore.entries) == 1


def test_content_pack_portraits_preview_and_import_as_local_uploads(tmp_path):
    from src.webui.services.plugins import import_all_plugin_content

    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "portrait-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={
            "contributes": {
                "characters": ["characters/*.json"],
                "npcs": ["npc/*.json"],
                "portraits": ["assets/portraits/*"],
            }
        },
    )
    pack = plugins / "portrait-pack"
    (pack / "characters").mkdir()
    (pack / "npc").mkdir()
    portrait_dir = pack / "assets" / "portraits"
    portrait_dir.mkdir(parents=True)
    portrait_path = portrait_dir / "mira.png"
    portrait_path.write_bytes(b"\x89PNG\r\n\x1a\nportrait-test")
    portable_portrait = {"kind": "asset", "path": "assets/portraits/mira.png"}
    (pack / "characters" / "hero.json").write_text(
        json.dumps({
            "id": "hero",
            "character_name": "Hero",
            "attributes": {},
            "skills": [],
            "portrait": portable_portrait,
        }),
        encoding="utf-8",
    )
    (pack / "npc" / "mira.json").write_text(
        json.dumps({
            "id": "mira",
            "name": "Mira",
            "description": "guide",
            "portrait": portable_portrait,
        }),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    config_dir = data_dir / "portrait-pack"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    host = PluginHost(plugins, data_dir)
    host.discover()

    resources = host.list_content_resources()
    expected_runtime_portrait = {
        "kind": "plugin",
        "plugin_id": "portrait-pack",
        "path": "assets/portraits/mira.png",
    }
    assert resources["character_template"][0]["portrait"] == expected_runtime_portrait
    assert resources["npc"][0]["portrait"] == expected_runtime_portrait

    class _Lore:
        worlds = {"w1": {"id": "w1", "name": "World"}}

        def __init__(self):
            self.entries = {}

        def get_world(self, world_id):
            return self.worlds.get(world_id)

        def get_entry(self, entry_id):
            return self.entries.get(entry_id)

        def update_entry(self, entry_id, entry):
            self.entries[entry_id] = entry

    class _Api:
        def __init__(self):
            self._plugins = host
            self._lore = _Lore()
            self.cards = []
            self.saved_portraits = []

        def plugin_asset_path(self, plugin_id, relative_path):
            return host.public_asset_path(plugin_id, relative_path)

        def save_avatar_upload(self, file_data, file_name=""):
            self.saved_portraits.append((file_data, file_name))
            return {"ok": True, "portrait": {"kind": "upload", "asset_id": f"local-{len(self.saved_portraits)}"}}

        def avatar_file(self, asset_id):
            return None

        def save_character_card(self, card):
            self.cards.append(card)
            return {"ok": True}

        def save_entry(self, entry):
            self._lore.entries[entry["id"]] = entry
            return {"ok": True}

    api = _Api()
    result = import_all_plugin_content(
        _plugin_content_dependencies(api), "portrait-pack", "w1"
    )

    assert result["ok"] is True
    assert result["imported_count"] == 2
    assert result["error_count"] == 0
    assert api.cards[0]["portrait"] == {"kind": "upload", "asset_id": "local-1"}
    assert next(iter(api._lore.entries.values()))["portrait"] == {
        "kind": "upload",
        "asset_id": "local-2",
    }
    assert [name for _, name in api.saved_portraits] == ["mira.png", "mira.png"]


def test_content_pack_scene_image_assets_are_exposed_as_plugin_references(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "scene-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {
            "world_templates": ["content/worlds/*.json"],
            "scene_images": ["assets/scenes/*"],
        }},
    )
    pack = plugins / "scene-pack"
    (pack / "content" / "worlds").mkdir(parents=True)
    (pack / "assets" / "scenes").mkdir(parents=True)
    (pack / "assets" / "scenes" / "valley.webp").write_bytes(b"RIFF-scene-test")
    (pack / "content" / "worlds" / "valley.json").write_text(json.dumps({
        "world_id": "valley",
        "world_name": "Valley",
        "default_rule": "freeform_fantasy",
        "scene_image": {"kind": "asset", "path": "assets/scenes/valley.webp"},
    }), encoding="utf-8")
    config_dir = tmp_path / "data" / "scene-pack"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    world = host.load_world_template("valley")

    assert world["scene_image"] == {
        "kind": "plugin",
        "plugin_id": "scene-pack",
        "path": "assets/scenes/valley.webp",
    }


def test_content_pack_hash_named_scene_assets_are_normalized_on_install(tmp_path):
    """作者用哈希/原名/中文命名图片也能安装：资产身份自动规范化，路径引用不变。"""
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "hash-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {
            "world_templates": ["content/worlds/*.json"],
            "scene_images": ["assets/scenes/*"],
        }},
    )
    pack = plugins / "hash-pack"
    (pack / "content" / "worlds").mkdir(parents=True)
    scenes = pack / "assets" / "scenes"
    scenes.mkdir(parents=True)
    world_hash = "311efe3bc91c167568bacb6bd7cc0e7e78c016a7763e4502f7ce4e67de4d6d4c"
    (scenes / f"{world_hash}.webp").write_bytes(b"RIFF-hash-world")
    (scenes / "valley.webp").write_bytes(b"RIFF-plain")
    (scenes / "背景 一.png").write_bytes(b"PNG-cn-1")
    (scenes / "背景 二.png").write_bytes(b"PNG-cn-2")
    (pack / "content" / "worlds" / "valley.json").write_text(json.dumps({
        "world_id": "valley",
        "world_name": "Valley",
        "default_rule": "freeform_fantasy",
        "scene_image": {"kind": "asset", "path": f"assets/scenes/{world_hash}.webp"},
    }), encoding="utf-8")
    config_dir = tmp_path / "data" / "hash-pack"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")

    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    assets = {item.relative_path: item for item in host.contributions.list("scene_image_asset")}
    assert f"assets/scenes/{world_hash}.webp" in assets
    # 数字开头的哈希名：规范化为字母开头 + 原名短哈希后缀
    assert assets[f"assets/scenes/{world_hash}.webp"].ref.local_id.startswith("n311efe")
    # 已经合法的名字原样保留（存量身份不漂移）
    assert assets["assets/scenes/valley.webp"].ref.local_id == "valley"
    # 中文/空格名各自得到唯一身份，不互相覆盖
    cn_ids = {item.ref.local_id for path, item in assets.items() if path.endswith(".png")}
    assert len(cn_ids) == 2
    # 路径引用解析不受身份规范化影响
    world = host.load_world_template("valley")
    assert world["scene_image"] == {
        "kind": "plugin",
        "plugin_id": "hash-pack",
        "path": f"assets/scenes/{world_hash}.webp",
    }


def test_content_pack_manifest_declares_scene_image_assets():
    manifest = plugin_service.build_content_pack_manifest(
        "scene-pack", "Scene Pack", "1.0.0", "", True, True, False,
        has_scene_images=True,
    )

    assert manifest["contributes"]["scene_images"] == ["assets/scenes/*"]
    assert "content.scene-image" in manifest["capabilities"]
    assert manifest["content_schema_version"] == 2
    assert manifest["locale_schema_version"] == 1
    assert manifest["default_locale"] == "zh-CN"


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
    synced = sync_plugin_lorebooks(_plugin_content_dependencies(api))
    assert synced["ok"] is True
    assert synced["synced"] == 1
    assert len(api._lore.entries) == 1
    entry_id = next(iter(api._lore.entries))
    assert "_plugin_worlds_" in entry_id
    assert next(iter(api._lore.entries.values())).get("source_plugin") == "worlds"

    removed = cleanup_plugin_lorebook(_plugin_content_dependencies(api), "worlds")
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
    result = cleanup_plugin_lorebook(
        _plugin_content_dependencies(api), "frieren-journey"
    )

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
    result = await update_plugin_config(
        _plugin_lifecycle_dependencies(api), "worlds", {"enabled": True}
    )

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

    class _Api(ContentMapApiFacade):
        def __init__(self):
            self._plugins = host
            self._lore = _Lore()
            self._rules_dir = rules_dir
        def list_character_cards(self):
            return {"cards": [card], "total": 1}

    api = _Api()
    result = export_content_pack(_plugin_export_dependencies(api), "my-pack", "我的包", "1.0.0", "导出测试",
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
    assert not any(name.endswith("myrule_en.json") for name in names)
    manifest = json.loads(archive.read("my-pack/plugin.json"))
    assert manifest["content_schema_version"] == 2
    assert manifest["locale_schema_version"] == 1
    exported_rule = json.loads(archive.read("my-pack/content/rules/myrule.json"))
    assert exported_rule["rule_schema_version"] == 2
    char_files = [n for n in names if n.startswith("my-pack/content/characters/")]
    assert len(char_files) == 1
    # starter_lorebook 无损：2 条，类型/内容保留
    world_tmpl = json.loads(archive.read("my-pack/content/worlds/w1.json"))
    assert world_tmpl["world_id"] == "w1"
    assert world_tmpl["world_schema_version"] == 2
    assert world_tmpl["default_locale"] == "zh-CN"
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


@pytest.mark.asyncio
async def test_export_content_pack_bundles_world_map_background_locations_and_icons(tmp_path):
    from src.webui.services.plugins import export_content_pack

    plugins_dir = tmp_path / "plugins"
    host = PluginHost(plugins_dir, tmp_path / "data")
    background = tmp_path / "background.webp"
    Image.new("RGB", (640, 360), (38, 54, 72)).save(background, format="WEBP")
    icon_buffer = io.BytesIO()
    Image.new("RGBA", (64, 64), (74, 116, 142, 200)).save(icon_buffer, format="PNG")

    class _Lore:
        world = {"id": "w1", "name": "旧城区", "description": "", "language": "zh-CN"}
        entries = [{
            "id": "old-town",
            "world_id": "w1",
            "name": "Old Town",
            "type": "location",
            "keywords": ["old town"],
            "content": "Rainy streets.",
            "tier": "core",
            "connected_to": [],
        }]

        def get_world(self, world_id):
            return self.world if world_id == "w1" else None

        def list_entries(self, world_id):
            return list(self.entries) if world_id == "w1" else []

    class _Api(ContentMapApiFacade):
        def __init__(self):
            self._plugins = host
            self._lore = _Lore()
            self._rules_dir = tmp_path

        def list_character_cards(self):
            return {"cards": []}

        @staticmethod
        def resolve_map_background_file(selection):
            return background if selection == {"kind": "upload", "asset_id": "test"} else None

    api = _Api()
    result = export_content_pack(
        _plugin_export_dependencies(api),
        "map-content",
        "地图内容包",
        "1.0.0",
        "map export",
        world_id="w1",
        include_map=True,
        map_background={"kind": "upload", "asset_id": "test"},
        map_icons=[{
            "id": "Old Town",
            "file_name": "Old Town.png",
            "file_data": base64.b64encode(icon_buffer.getvalue()).decode("ascii"),
        }],
    )

    assert result["ok"] is True
    archive = zipfile.ZipFile(io.BytesIO(result["payload"]))
    names = set(archive.namelist())
    assert "map-content/maps/definitions/w1-map.json" in names
    assert "map-content/maps/locations/old-town.json" in names
    assert "map-content/maps/icons/old_town.webp" in names
    assert "map-content/maps/backgrounds/w1-map.webp" in names

    manifest = json.loads(archive.read("map-content/plugin.json"))
    assert manifest["contributes"] | {
        "map_definitions": ["maps/definitions/*.json"],
        "map_locations": ["maps/locations/*.json"],
        "map_icons": ["maps/icons/*.webp"],
        "map_backgrounds": ["maps/backgrounds/*.webp"],
    } == manifest["contributes"]
    assert "content.map" in manifest["capabilities"]

    world = json.loads(archive.read("map-content/content/worlds/w1.json"))
    assert world["default_map"] == "plugin:map-content:map:w1-map"
    location = json.loads(archive.read("map-content/maps/locations/old-town.json"))
    assert location["icon"] == "old_town"

    await host.install_from_zip(result["payload"], overwrite=True)
    await host.update_config("map-content", {"enabled": True})
    assets = host.list_map_assets("w1")
    assert assets["maps"][0]["background"] == "w1-map"
    assert assets["locations"][0]["id"] == "old-town"
    assert assets["icons"][0]["id"] == "old_town"


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

    class _Api(ContentMapApiFacade):
        def __init__(self): self._plugins = host; self._lore = _Lore(); self._rules_dir = tmp_path
        def list_character_cards(self): return {"cards": []}

    api = _Api()
    result = export_content_pack(
        _plugin_export_dependencies(api),
        "my-pack",
        "我的包",
        "1.0.0",
        "desc",
        world_id="w1",
        flat=True,
    )

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
    result = cleanup_plugin_lorebook(_plugin_content_dependencies(api), "packs")

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
        CharacterCardDependencies,
        delete_character_card,
        list_character_cards,
        save_character_card,
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
        _character_card_dependencies = CharacterCardDependencies(
            cards_path=cards_path,
        )
        def list_character_cards(self):
            return list_character_cards(self._character_card_dependencies)
        def delete_character_card(self, card_id):
            return delete_character_card(
                self._character_card_dependencies, card_id,
            )

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
    save_character_card(api._character_card_dependencies, card)

    persisted = list_character_cards(api._character_card_dependencies)["cards"]
    assert len(persisted) == 1
    assert persisted[0]["source_plugin"] == "packs"  # Bug 1：曾在此处被 _to_character_card 丢弃

    result = cleanup_plugin_lorebook(_plugin_content_dependencies(api), "packs")
    assert result["cards_removed"] == 1
    assert list_character_cards(api._character_card_dependencies)["cards"] == []


@pytest.mark.asyncio
async def test_voice_pack_registers_authorized_reference_audio(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "narrator-voice",
        plugin_type="voice-pack",
        entrypoint=False,
        manifest_extra={
            "contributes": {
                "voices": ["voices/*.json"],
                "voice_assets": ["voices/*.wav"],
            },
        },
    )
    voice_dir = plugins / "narrator-voice" / "voices"
    voice_dir.mkdir(parents=True)
    (voice_dir / "narrator.wav").write_bytes(b"RIFF-test-wave")
    (voice_dir / "narrator.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "narrator",
        "name": "Narrator",
        "engine": "gpt-sovits",
        "language": "zh-CN",
        "reference_audio": "voices/narrator.wav",
        "prompt_text": "欢迎来到冒险。",
        "prompt_language": "zh-CN",
        "license": "CC-BY-4.0",
        "consent": True,
    }), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    await host.update_config("narrator-voice", {"enabled": True})
    profiles = host.list_voice_profiles()

    assert profiles[0]["id"] == "plugin:narrator-voice:voice:narrator"
    assert profiles[0]["engine"] == "gpt-sovits"
    assert Path(profiles[0]["_reference_audio_path"]).name == "narrator.wav"
    assert profiles[0]["preview_url"].endswith("/voices/narrator.wav")


def test_voice_pack_rejects_missing_consent(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "unsafe-voice",
        plugin_type="voice-pack",
        entrypoint=False,
        manifest_extra={"contributes": {"voices": ["voices/*.json"]}},
    )
    voice_dir = plugins / "unsafe-voice" / "voices"
    voice_dir.mkdir(parents=True)
    (voice_dir / "unsafe.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "unsafe",
        "name": "Unsafe",
        "engine": "openai-compatible",
        "voice_id": "unsafe",
        "license": "unknown",
    }), encoding="utf-8")

    detail = PluginHost(plugins, tmp_path / "data").discover()[0]

    assert detail["status"] == "failed"
    assert "consent=true" in detail["error"]


def test_content_pack_map_contributions_infer_map_asset_permission(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "map-content",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {"map_icons": ["maps/icons/*.png"]}},
    )
    icon_dir = plugins / "map-content" / "maps" / "icons"
    icon_dir.mkdir(parents=True)
    write_png(icon_dir / "town.png")

    detail = PluginHost(plugins, tmp_path / "data").discover()[0]

    assert detail["status"] == "disabled"
    assert "content.read" in detail["permissions"]
    assert "map.assets" in detail["permissions"]


def test_removed_map_pack_type_is_rejected(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "legacy-map", plugin_type="map-pack", entrypoint=False)

    detail = PluginHost(plugins, tmp_path / "data").discover()[0]

    assert detail["status"] == "failed"
    assert "不支持的 plugin_type" in detail["error"]


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
    _autoimport_plugin_content(_plugin_content_dependencies(api), "pack")

    assert len(saved_cards) == 1
    assert saved_cards[0]["character_name"] == "Hero"
    assert len(saved_entries) == 2
    assert all(e["world_id"] == "w1" for e in saved_entries)
    assert all(e["source_plugin"] == "pack" for e in saved_entries)
    assert any("npc" in e["id"] for e in saved_entries)
    assert any("spell" in e["id"] for e in saved_entries)

    # 幂等：再调一次，已存在的世界书条目跳过（不复制）
    _autoimport_plugin_content(_plugin_content_dependencies(api), "pack")
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

    api = _Api()
    _autoimport_plugin_content(_plugin_content_dependencies(api), "pack")
    assert len(saved_cards) == 1
    assert len(saved_entries) == 0  # 无世界，npc 跳过


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

        async def install_from_marketplace(self, plugin_id, overwrite=False, **_kwargs):
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
    result = await install_marketplace_plugin(
        _plugin_lifecycle_dependencies(api), "pack"
    )

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
        async def install_from_marketplace(self, plugin_id, overwrite=False, **_kwargs):
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

    api = _Api()
    result = await install_marketplace_plugin(
        _plugin_lifecycle_dependencies(api), "pack"
    )
    assert result["ok"] is True


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
        "schema_version": 2,
        "id": "paper-theme",
        "name": "Paper Theme",
        "description": "Soft paper colors",
        "tokens": {
            "base": {
                "--df-accent": "#ccaa66",
                "--gold": "#ffaa00",
                "--df-shadow": "url(http://bad)",
                "--df-info": "not-a-color",
                "--df-radius-md": "calc(100vw)",
            },
            "dark": {"--df-surface-1": "#111111"},
        },
    }), encoding="utf-8")
    write_plugin(
        plugins,
        "legacy-theme",
        plugin_type="theme",
        entrypoint=False,
        manifest_extra={"contributes": {"theme": "theme/theme.json"}},
    )
    legacy_theme_dir = plugins / "legacy-theme" / "theme"
    legacy_theme_dir.mkdir(parents=True)
    (legacy_theme_dir / "theme.json").write_text(json.dumps({
        "id": "legacy-theme",
        "name": "Legacy Theme",
        "tokens": {"base": {"--gold": "#ffcc00"}},
    }), encoding="utf-8")
    write_plugin(
        plugins,
        "map-assets",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {"map_locations": ["maps/locations/*.json"]}},
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
    await host.update_config("legacy-theme", {"enabled": True})
    await host.update_config("map-assets", {"enabled": True})

    assert {item["key"] for item in host.list_contributions("theme")} == {
        "legacy-theme",
        "paper-theme",
    }
    assert host.list_contributions("map_location")[0]["key"] == "town"
    themes = host.list_themes()
    assert [theme["id"] for theme in themes] == ["paper-theme"]
    theme = themes[0]
    assert theme["schema_version"] == 2
    assert theme["tokens"]["base"] == {"--df-accent": "#ccaa66"}
    assert theme["tokens"]["dark"] == {"--df-surface-1": "#111111"}


@pytest.mark.asyncio
async def test_content_pack_maps_are_consumed_by_map_service(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "map-assets",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {
            "map_locations": ["maps/locations/*.json"],
            "map_icons": ["maps/icons/*.png"],
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
    write_png(icon_dir / "town.png")
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

    result = map_service.get_map_locations(_map_dependencies(Api), "demo")

    assert result["locations"][0]["id"] == "town"
    assert result["assets"]["icons"][0]["url"] == "/api/plugins/assets/map-assets/maps/icons/town.png"
    assert host.public_asset_path("map-assets", "maps/icons/town.png").exists()
    with pytest.raises(KeyError):
        host.public_asset_path("map-assets", "plugin.json")


def test_fantasy_world_uses_builtin_map_background_without_plugin(tmp_path):
    class Registry:
        @staticmethod
        def get(_key):
            return SimpleNamespace(world_id="default_fantasy", scene="")

    class Lore:
        @staticmethod
        def list_entries(_world_id, _entry_type):
            return []

    class Api:
        _plugins = None
        _reg = Registry()
        _lore = Lore()

        @staticmethod
        def _parse_key(game_key):
            return ("web", game_key, "web_bot")

    result = map_service.get_map_locations(_map_dependencies(Api), "demo")

    assert result["active_map"]["id"] == "builtin:map:fantasy-region-v1"
    assert result["active_map"]["background"]["url"] == "/v2-assets/ui/maps/fantasy-region-v1.webp"
    assert result["capabilities"]["has_background"] is True
    assert result["capabilities"]["has_plugin_assets"] is False


@pytest.mark.parametrize(("rule_id", "asset_id"), [
    ("freeform_coc", "occult-town-v1"),
    ("freeform_cyberpunk", "cyber-city-v1"),
])
def test_copied_world_uses_builtin_background_recommended_by_rule(rule_id, asset_id):
    class Registry:
        @staticmethod
        def get(_key):
            return SimpleNamespace(world_id="custom_copy_123", rule_id=rule_id, scene="")

    class Lore:
        @staticmethod
        def list_entries(_world_id, _entry_type):
            return []

    class Api:
        _plugins = None
        _reg = Registry()
        _lore = Lore()

        @staticmethod
        def _parse_key(game_key):
            return ("web", game_key, "web_bot")

    result = map_service.get_map_locations(_map_dependencies(Api), "demo")

    assert result["active_map"]["id"] == f"builtin:map:{asset_id}"
    assert result["active_map"]["background"]["url"] == f"/v2-assets/ui/maps/{asset_id}.webp"


def test_old_save_without_rule_uses_world_template_rule_for_builtin_background():
    class Registry:
        @staticmethod
        def get(_key):
            return SimpleNamespace(world_id="legacy_copy_123", rule_id="", scene="")

    class Lore:
        @staticmethod
        def list_entries(_world_id, _entry_type):
            return []

    class Api:
        _plugins = None
        _reg = Registry()
        _lore = Lore()

        @staticmethod
        def _parse_key(game_key):
            return ("web", game_key, "web_bot")

        @staticmethod
        def _load_world_template(_world_id):
            return {"default_rule": "freeform_coc"}

    result = map_service.get_map_locations(_map_dependencies(Api), "demo")

    assert result["active_map"]["id"] == "builtin:map:occult-town-v1"


@pytest.mark.asyncio
async def test_content_pack_map_definition_applies_background_icons_and_stable_coordinates(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "map-assets",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {
            "map_definitions": ["maps/maps/*.json"],
            "map_locations": ["maps/locations/*.json"],
            "map_icons": ["maps/icons/*.png"],
            "map_backgrounds": ["maps/scenes/*.png"],
        }},
    )
    plugin_dir = plugins / "map-assets" / "maps"
    (plugin_dir / "maps").mkdir(parents=True)
    (plugin_dir / "locations").mkdir(parents=True)
    (plugin_dir / "icons").mkdir(parents=True)
    (plugin_dir / "scenes").mkdir(parents=True)
    (plugin_dir / "maps" / "arkham.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "arkham",
        "name": "Arkham",
        "worlds": ["coc_horror"],
        "mode": "graph",
        "background": "arkham",
        "nodes": [{"location_ref": "station", "x": -18, "y": 12, "icon": "station"}],
        "default_view": {"x": 0, "y": 0, "zoom": 1.4},
    }), encoding="utf-8")
    (plugin_dir / "locations" / "station.json").write_text(json.dumps({
        "id": "station", "name": "Station", "worlds": ["coc_horror"],
    }), encoding="utf-8")
    write_png(plugin_dir / "icons" / "station.png")
    write_png(plugin_dir / "scenes" / "arkham.png", size=(640, 360))

    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.update_config("map-assets", {"enabled": True})

    class Registry:
        @staticmethod
        def get(_key):
            return SimpleNamespace(world_id="coc_horror", scene="Station")

    class Lore:
        @staticmethod
        def list_entries(_world_id, _entry_type):
            return []

    class Api:
        _plugins = host
        _reg = Registry()
        _lore = Lore()

        @staticmethod
        def _parse_key(game_key):
            return ("web", game_key, "web_bot")

        @staticmethod
        def _load_world_template(_world_id):
            return {"world_id": "coc_horror", "default_map": "plugin:map-assets:map:arkham"}

    result = map_service.get_map_locations(_map_dependencies(Api), "demo")

    assert result["current_location_id"] == "station"
    assert result["active_map"]["id"] == "plugin:map-assets:map:arkham"
    assert result["active_map"]["background"]["url"].endswith("maps/scenes/arkham.png")
    assert result["locations"][0]["x"] == -18
    assert result["locations"][0]["y"] == 12
    assert result["locations"][0]["icon_url"].endswith("maps/icons/station.png")
    assert result["capabilities"] == {
        "can_expand": True,
        "can_edit": False,
        "has_background": True,
        "has_plugin_assets": True,
    }


@pytest.mark.asyncio
async def test_map_asset_ids_are_namespaced_by_plugin(tmp_path):
    plugins = tmp_path / "plugins"
    for plugin_id in ("map-east", "map-west"):
        write_plugin(
            plugins,
            plugin_id,
            plugin_type="content-pack",
            entrypoint=False,
            manifest_extra={"contributes": {"map_icons": ["icons/*.png"]}},
        )
        icon_dir = plugins / plugin_id / "icons"
        icon_dir.mkdir()
        write_png(icon_dir / "station.png")

    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.update_config("map-east", {"enabled": True})
    await host.update_config("map-west", {"enabled": True})

    icons = host.list_map_assets()["icons"]
    assert len(icons) == 2
    assert {item["ref"] for item in icons} == {
        "plugin:map-east:icon:station",
        "plugin:map-west:icon:station",
    }


def test_content_pack_rejects_invalid_declared_map_image(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "broken-map",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {"map_icons": ["icons/*.png"]}},
    )
    icon_dir = plugins / "broken-map" / "icons"
    icon_dir.mkdir()
    (icon_dir / "bad.png").write_bytes(b"not-an-image")

    found = PluginHost(plugins, tmp_path / "data").discover()

    assert found[0]["status"] == "failed"
    assert "无法读取地图图标" in found[0]["error"]


def test_content_pack_rejects_missing_map_asset_reference(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "broken-reference-map",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {"map_definitions": ["maps/*.json"]}},
    )
    maps_dir = plugins / "broken-reference-map" / "maps"
    maps_dir.mkdir()
    (maps_dir / "overview.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "overview",
        "mode": "graph",
        "background": "missing-scene",
        "nodes": [],
    }), encoding="utf-8")

    host = PluginHost(plugins, tmp_path / "data")
    found = host.discover()

    assert found[0]["status"] == "failed"
    assert "background=missing-scene" in found[0]["error"]
    assert host.list_contributions("map_definition") == []


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

    rule_dependencies = rule_service.RuleDependencies(
        rules_dir=Api._rules_dir,
        plugin_host=host,
    )
    world_dependencies = world_service.WorldDependencies(
        lorebook=None,
        worlds_dir=Api._worlds_dir,
        plugin_host=host,
        llm_client=None,
        character_gen_max_tokens=2048,
        invalidate_lorebook_index=None,
        list_instances=lambda: [],
    )
    rule_items = rule_service.list_rules(rule_dependencies)["rules"]
    world_items = world_service.list_world_templates(world_dependencies)["templates"]
    detail = rule_service.get_rule_template(rule_dependencies, "pack_rule")

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

    dependencies = _plugin_content_dependencies(api)
    card_result = plugin_service.import_plugin_content(
        dependencies, "character_template", "hero", "library-pack"
    )
    entry_result = plugin_service.import_plugin_content(
        dependencies, "npc", "elder", "library-pack", "pack_world"
    )

    assert card_result["ok"] is True
    assert api.cards[0]["character_name"] == "Hero"
    assert api.cards[0]["source"] == "插件内容包：Example"
    assert entry_result["ok"] is True
    assert api.entries[0]["world_id"] == "pack_world"
    assert api.entries[0]["type"] == "npc"
    assert "Village elder." in api.entries[0]["content"]


@pytest.mark.asyncio
async def test_v2_plugin_rule_catalog_metadata_and_locale_fallback(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "starter-content-v2", plugin_type="content-pack", entrypoint=False,
                 manifest_extra={"content_schema_version": 2, "locale_schema_version": 1,
                                 "default_locale": "en", "contributes": {
                                     "rules": ["content/rules/*.json"],
                                     "items": ["content/items/*.json"],
                                 }})
    root = plugins / "starter-content-v2"
    (root / "content" / "rules").mkdir(parents=True)
    (root / "content" / "items").mkdir(parents=True)
    (root / "locales" / "en" / "rules").mkdir(parents=True)
    (root / "locales" / "en" / "items").mkdir(parents=True)
    (root / "content" / "rules" / "moonlight.json").write_text(json.dumps({
        "rule_schema_version": 2, "rule_id": "moonlight", "extends": "base_d20", "rule_name": "Core Moonlight",
        "description": "Core description", "dice_system": "d20", "attributes": [],
    }), encoding="utf-8")
    (root / "content" / "items" / "shared.json").write_text(json.dumps({
        "id": "shared", "name": "Core Shared", "damage_dice": "1d8",
    }), encoding="utf-8")
    (root / "locales" / "en" / "rules" / "moonlight.json").write_text(json.dumps({
        "locale_schema_version": 1, "locale": "en", "target": {"kind": "rule", "id": "moonlight"},
        "fields": {"rule_name": "Moonlight Rule", "description": "Localized description"},
    }), encoding="utf-8")
    item_locale_path = root / "locales" / "en" / "items" / "shared.json"
    item_locale_path.write_text(json.dumps({
        "locale_schema_version": 1, "locale": "en", "target": {"kind": "item", "id": "shared"},
        "fields": {"name": "Localized Shared"},
    }), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.update_config("starter-content-v2", {"enabled": True})
    resource = host.get_content_resource("rule", "moonlight", plugin_id="starter-content-v2", language="en")
    assert resource is not None
    assert resource["id"] == "moonlight"
    assert resource["plugin_id"] == "starter-content-v2"
    assert resource["source"] == "plugin" and resource["readonly"] is True
    assert resource["ref"] == "plugin:starter-content-v2:rule:moonlight"
    assert resource["rule_name"] == "Moonlight Rule"
    assert resource["intents"]
    item = host.get_content_resource("item", "shared", plugin_id="starter-content-v2", language="en-US")
    assert item is not None and item["name"] == "Localized Shared"
    assert item["damage_dice"] == "1d8"
    invalid_overlays = [
        {"locale_schema_version": 1, "locale": "en", "target": {"kind": "item", "id": "shared"}, "fields": {"damage_dice": "9d99"}},
        {"locale_schema_version": 1, "locale": "en", "target": {"kind": "item", "id": "shared"}, "fields": {"name": "x"}, "unexpected": True},
        {"locale_schema_version": 1, "locale": "en", "target": {"kind": "item", "id": "wrong"}, "fields": {"name": "x"}},
        {"locale_schema_version": 999, "locale": "en", "target": {"kind": "item", "id": "shared"}, "fields": {"name": "x"}},
        {"locale_schema_version": 1, "locale": "en", "target": {"kind": "item", "id": "shared"}, "fields": "bad"},
    ]
    for invalid in invalid_overlays:
        item_locale_path.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(ValueError):
            host.get_content_resource("item", "shared", plugin_id="starter-content-v2", language="en")
    rule_locale_path = root / "locales" / "en" / "rules" / "moonlight.json"
    for invalid in (
        {"locale_schema_version": 1, "locale": "en", "target": {"kind": "rule", "id": "moonlight"}, "fields": "bad"},
        {"locale_schema_version": 1, "locale": "en", "target": {"kind": "rule", "id": "moonlight"}, "items": []},
    ):
        rule_locale_path.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(ValueError):
            host.get_content_resource("rule", "moonlight", plugin_id="starter-content-v2", language="en")


@pytest.mark.asyncio
async def test_v1_plugin_rule_non_content_layout_ignores_locales(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "legacy-rule-pack", plugin_type="content-pack", entrypoint=False,
                 manifest_extra={"contributes": {"rules": ["rules/*.json"]}})
    root = plugins / "legacy-rule-pack"
    (root / "rules").mkdir(parents=True)
    (root / "locales" / "en" / "rules").mkdir(parents=True)
    rule_path = root / "rules" / "legacy.json"
    rule_path.write_text(json.dumps({
        "rule_id": "legacy", "rule_name": "Legacy Rule", "dice_system": "d20", "attributes": [],
    }), encoding="utf-8")
    (root / "locales" / "en" / "rules" / "legacy.json").write_text(json.dumps({"fields": "bad"}), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.update_config("legacy-rule-pack", {"enabled": True})
    assert host.load_rule_template("legacy") ["rule_name"] == "Legacy Rule"


@pytest.mark.asyncio
async def test_v2_plugin_namespace_and_plain_id_collisions_are_explicit(tmp_path):
    plugins = tmp_path / "plugins"
    for plugin_id in ("pack-a", "pack-b"):
        write_plugin(plugins, plugin_id, plugin_type="content-pack", entrypoint=False,
                     manifest_extra={"content_schema_version": 2, "contributes": {"items": ["content/items/*.json"]}})
        item_dir = plugins / plugin_id / "content" / "items"
        item_dir.mkdir(parents=True)
        (item_dir / "shared.json").write_text(json.dumps({"id": "shared", "name": plugin_id}), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.update_config("pack-a", {"enabled": True})
    await host.update_config("pack-b", {"enabled": True})
    assert host.get_content_resource("item", "shared", plugin_id="pack-a")["name"] == "pack-a"
    assert host.get_content_resource("item", "shared", plugin_id="pack-b")["name"] == "pack-b"
    assert host.get_content_resource("item", "shared") is None


@pytest.mark.asyncio
async def test_same_plugin_duplicate_v2_item_id_is_rejected(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "duplicate", plugin_type="content-pack", entrypoint=False,
                 manifest_extra={"content_schema_version": 2, "contributes": {"items": ["content/items/*.json"]}})
    item_dir = plugins / "duplicate" / "content" / "items"
    item_dir.mkdir(parents=True)
    for filename in ("one.json", "two.json"):
        (item_dir / filename).write_text(json.dumps({"id": "same", "name": filename}), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    details = host.discover()
    assert details[0]["status"] == "failed"


@pytest.mark.parametrize("resource_id", ["Moon Blade", "Moon_Blade", "moon blade", "月刃"])
def test_v2_plugin_rejects_noncanonical_resource_ids(tmp_path, resource_id):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "canonical-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={
            "content_schema_version": 2,
            "locale_schema_version": 1,
            "default_locale": "en",
            "contributes": {"items": ["content/items/*.json"]},
        },
    )
    item_dir = plugins / "canonical-pack" / "content" / "items"
    item_dir.mkdir(parents=True)
    (item_dir / "item.json").write_text(
        json.dumps({"id": resource_id, "name": "Moon Blade"}),
        encoding="utf-8",
    )

    details = PluginHost(plugins, tmp_path / "data").discover()

    assert details[0]["status"] == "failed"
    assert "规范形式" in details[0]["error"] or "ID 非法" in details[0]["error"]


@pytest.mark.asyncio
async def test_v2_rule_and_world_plain_id_collisions_are_rejected(tmp_path):
    plugins = tmp_path / "plugins"
    for plugin_id in ("rule-a", "rule-b"):
        write_plugin(plugins, plugin_id, plugin_type="content-pack", entrypoint=False,
                     manifest_extra={"content_schema_version": 2, "contributes": {"rules": ["content/rules/*.json"]}})
        rule_dir = plugins / plugin_id / "content" / "rules"
        rule_dir.mkdir(parents=True)
        (rule_dir / "shared.json").write_text(json.dumps({
            "rule_id": "shared", "rule_schema_version": 2, "rule_name": plugin_id,
            "dice_system": "d20", "attributes": [],
        }), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.update_config("rule-a", {"enabled": True})
    with pytest.raises(ValueError, match="plain ID 冲突"):
        await host.update_config("rule-b", {"enabled": True})

    world_plugins = tmp_path / "world-plugins"
    for plugin_id in ("world-a", "world-b"):
        write_plugin(world_plugins, plugin_id, plugin_type="content-pack", entrypoint=False,
                     manifest_extra={"content_schema_version": 2, "contributes": {"world_templates": ["content/worlds/*.json"]}})
        world_dir = world_plugins / plugin_id / "content" / "worlds"
        world_dir.mkdir(parents=True)
        (world_dir / "shared.json").write_text(json.dumps({
            "world_id": "shared", "world_name": plugin_id, "starter_lorebook": [],
        }), encoding="utf-8")
    world_host = PluginHost(world_plugins, tmp_path / "world-data")
    world_host.discover()
    await world_host.update_config("world-a", {"enabled": True})
    with pytest.raises(ValueError, match="plain ID 冲突"):
        await world_host.update_config("world-b", {"enabled": True})


@pytest.mark.parametrize("field,value", [
    ("content_schema_version", 999), ("locale_schema_version", 999),
    ("content_schema_version", "two"), ("locale_schema_version", "one"),
])
def test_manifest_future_and_non_numeric_versions_fail_closed(tmp_path, field, value):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "invalid-version", manifest_extra={field: value})
    host = PluginHost(plugins, tmp_path / "data")
    details = host.discover()
    assert details[0]["status"] == "failed"


