"""Homebrew v1 端到端测试（DNDMOD-04，母方案 §30/§114）。

用户自制怪物 + 物品 = **同一个模块 catalog 路径**（无独立 Homebrew 系统）：

content-pack 模组声明 ruleset_catalogs
    → host 校验并携带（declared-only）
    → WebAPI 同步装载（DNDMOD-01 契约校验）
    → catalog 解析自制怪物（DNDMOD-02 encounter）
    → catalog 解析自制物品（DNDMOD-03 reward intent）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugin_host.host import PluginHost
from src.rulesets.dnd2024.content.encounter import resolve_encounter
from src.rulesets.dnd2024.content.rewards import reward_intent_from_ref


MODULE_MANIFEST = {
    "schema_version": 1,
    "id": "homebrew-module",
    "name": "Homebrew Module",
    "version": "1.0.0",
    "plugin_type": "content-pack",
    "entrypoint": ["python", "-c", "pass"],
    "content_profile": "adventure-module",
    "content_delivery_mode": "catalog",
    "ruleset_catalogs": ["packs/dnd2024"],
    "contributes": {},
}


def _make_homebrew_module(tmp_path: Path) -> PluginHost:
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "homebrew-module"
    (plugin_dir / "packs" / "dnd2024").mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(MODULE_MANIFEST), encoding="utf-8")
    (plugin_dir / "config.schema.json").write_text(
        '{"type": "object", "properties": {}}', encoding="utf-8",
    )
    # 自制怪物（homebrew monster → D&D Content Catalog，母方案 §30）。
    (plugin_dir / "packs" / "dnd2024" / "monsters.json").write_text(json.dumps({
        "kind": "monster", "profile_id": "moss_golem", "name": "Moss Golem",
        "source_ref": "module:homebrew-module", "hp": 45, "armor_class": 14,
        "speed": 20,
        "abilities": {"str": 16, "dex": 8, "con": 16, "int": 5, "wis": 10, "cha": 5},
        "attacks": [{"id": "slam", "damage": "2d8+3", "attack_bonus": 5}],
    }), encoding="utf-8")
    # 自制物品（homebrew item → D&D Content Catalog）。
    (plugin_dir / "packs" / "dnd2024" / "items.json").write_text(json.dumps([
        {"kind": "item", "item_id": "warden_badge", "name": "Warden Badge",
         "source_ref": "module:homebrew-module", "category": "key_item",
         "description": "古树守卫的徽记。"},
    ]), encoding="utf-8")
    return PluginHost(plugins_dir=plugins_root, data_dir=tmp_path / "data")


def test_host_loads_homebrew_module_with_catalog_declaration(tmp_path: Path) -> None:
    host = _make_homebrew_module(tmp_path)
    _, runtime = host._load_runtime(tmp_path / "plugins" / "homebrew-module")
    assert runtime.ruleset_catalogs_root.name == "packs"
    assert runtime.ruleset_catalog_directories == ("dnd2024",)


def test_host_rejects_unsafe_catalog_paths(tmp_path: Path) -> None:
    manifest = dict(MODULE_MANIFEST, ruleset_catalogs=["../outside"])
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "homebrew-module"
    (plugin_dir / "packs").mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / "config.schema.json").write_text(
        '{"type": "object", "properties": {}}', encoding="utf-8",
    )
    host = PluginHost(plugins_dir=plugins_root, data_dir=tmp_path / "data")
    with pytest.raises(ValueError, match="\\.\\.|绝对路径|越界"):
        host._load_runtime(plugin_dir)


def test_homebrew_monster_and_item_flow_through_the_catalog(tmp_path: Path) -> None:
    host = _make_homebrew_module(tmp_path)
    _, runtime = host._load_runtime(tmp_path / "plugins" / "homebrew-module")
    runtime.status = "enabled"
    host.plugins[runtime.manifest["id"]] = runtime

    # 模拟 WebAPI.module_catalog 的装载（不构造全站依赖）。
    from src.rulesets.dnd2024.content.catalog import DndContentCatalog, load_catalog_dir

    source = load_catalog_dir(
        runtime.ruleset_catalogs_root / "dnd2024", source_label="module:homebrew-module",
    )
    catalog = DndContentCatalog([source])

    # 自制怪物可被 encounter 解析（DNDMOD-02 路径）。
    instances = resolve_encounter(catalog, {
        "encounter_id": "grove_guard",
        "name": "Grove Guard",
        "source_ref": "module:homebrew-module",
        "difficulty": "standard",
        "enemies": [{"ref": "monster:moss_golem", "count": 2}],
    }, default_source="module:homebrew-module")
    assert [item["id"] for item in instances] == ["moss_golem_1", "moss_golem_2"]
    assert instances[0]["hp"] == 45

    # 自制物品可产生 reward intent（DNDMOD-03 路径）。
    intent = reward_intent_from_ref(
        catalog, "item:warden_badge",
        default_source="module:homebrew-module", recipient_uid="p1",
    )
    assert intent["name"] == "Warden Badge"
    assert intent["category"] == "key_item"
