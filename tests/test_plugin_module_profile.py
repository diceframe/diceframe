"""Content Module profile 契约测试（MOD-00，母方案 §6/§34/§73/§104）。

- 缺省字段 → content-pack / legacy_autoimport（旧包零行为变化）；
- adventure-module MUST catalog（安装=注册内容库，不启用即灌注）；
- 未知 profile / mode fail closed；
- host manifest 校验真实接入。
"""

from __future__ import annotations

import pytest

from src.plugin_host.support import (
    CONTENT_DELIVERY_MODES,
    CONTENT_PROFILES,
    content_delivery_mode,
    content_profile,
    is_adventure_module,
    validate_content_module_profile,
)


def test_defaults_preserve_legacy_content_pack_behaviour() -> None:
    manifest = {"id": "legacy-pack", "plugin_type": "content-pack"}
    assert content_profile(manifest) == "content-pack"
    assert content_delivery_mode(manifest) == "legacy_autoimport"
    assert is_adventure_module(manifest) is False
    validate_content_module_profile(manifest)  # 不抛


def test_adventure_module_requires_catalog_mode() -> None:
    manifest = {
        "id": "castle-module", "plugin_type": "content-pack",
        "content_profile": "adventure-module", "content_delivery_mode": "catalog",
    }
    assert is_adventure_module(manifest) is True
    validate_content_module_profile(manifest)  # 不抛

    with pytest.raises(ValueError, match="catalog"):
        validate_content_module_profile({
            "id": "castle-module", "content_profile": "adventure-module",
            "content_delivery_mode": "legacy_autoimport",
        })


def test_unknown_profile_or_mode_fail_closed() -> None:
    with pytest.raises(ValueError, match="content_profile"):
        validate_content_module_profile({"content_profile": "theme-module"})
    with pytest.raises(ValueError, match="content_delivery_mode"):
        validate_content_module_profile({"content_delivery_mode": "auto"})


def test_content_pack_may_opt_into_catalog_mode() -> None:
    manifest = {
        "id": "modern-pack", "plugin_type": "content-pack",
        "content_delivery_mode": "catalog",
    }
    validate_content_module_profile(manifest)  # 传统包也可声明 catalog
    assert content_delivery_mode(manifest) == "catalog"
    assert is_adventure_module(manifest) is False


def test_vocabularies_are_the_declared_ones() -> None:
    assert set(CONTENT_PROFILES) == {"content-pack", "adventure-module"}
    assert set(CONTENT_DELIVERY_MODES) == {"legacy_autoimport", "catalog"}


def test_host_manifest_validation_rejects_bad_module_profile(tmp_path) -> None:
    """真实 manifest 校验链路：非法 profile 组合在安装时被拒。"""

    from src.plugin_host.host import PluginHost

    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    plugin_dir = plugins_root / "bad-module"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        """
        {
          "schema_version": 1,
          "id": "bad-module",
          "name": "Bad Module",
          "version": "1.0.0",
          "plugin_type": "content-pack",
          "entrypoint": ["python", "-c", "pass"],
          "content_profile": "adventure-module",
          "content_delivery_mode": "legacy_autoimport",
          "contributes": {}
        }
        """,
        encoding="utf-8",
    )
    (plugin_dir / "config.schema.json").write_text(
        '{"type": "object", "properties": {}}', encoding="utf-8",
    )
    host = PluginHost(plugins_dir=plugins_root, data_dir=tmp_path / "data")
    with pytest.raises(ValueError, match="catalog"):
        host._load_runtime(plugin_dir)


# ---- LIFE-03：marketplace 模组元数据 ----


def test_marketplace_item_carries_module_metadata() -> None:
    """Hub 清单中的模组字段透传到规范化条目（content_profile/规则目标/冒险数/语言）。"""
    from src.plugin_host.marketplace import _normalize_market_item

    item = {
        "id": "castle-module",
        "manifest": {
            "id": "castle-module", "name": "Castle Module", "version": "1.0.0",
            "plugin_type": "content-pack",
            "content_profile": "adventure-module",
            "content_delivery_mode": "catalog",
            "automation_level": "guided",
            "supported_locales": ["zh-CN", "en"],
            "adventure_packages": ["adventures/castle", "adventures/crypt"],
            "requires": {"rulesets": [{"id": "core:dnd2024", "minimum_version": 1}]},
            "license": "CC-BY-4.0",
        },
    }
    normalized = _normalize_market_item(item)
    assert normalized["content_profile"] == "adventure-module"
    assert normalized["content_delivery_mode"] == "catalog"
    assert normalized["ruleset_targets"] == ["core:dnd2024"]
    assert normalized["adventure_count"] == 2
    assert normalized["automation_level"] == "guided"
    assert normalized["languages"] == ["zh-CN", "en"]
    assert normalized["license"] == "CC-BY-4.0"


def test_marketplace_item_defaults_for_legacy_plugins() -> None:
    from src.plugin_host.marketplace import _normalize_market_item

    normalized = _normalize_market_item({
        "id": "legacy-pack",
        "manifest": {"id": "legacy-pack", "name": "Legacy", "plugin_type": "content-pack"},
    })
    assert normalized["content_profile"] == "content-pack"
    assert normalized["content_delivery_mode"] == "legacy_autoimport"
    assert normalized["ruleset_targets"] == []
    assert normalized["adventure_count"] == 0
