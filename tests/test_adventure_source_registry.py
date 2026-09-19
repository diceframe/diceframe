"""Adventure Source Registry 测试（MOD-02，母方案 §31/§72/§106）。

覆盖：builtin/user 双目录聚合、按 adventure_id 解析、跨来源同 id 的显式
冲突（resolve 拒绝 + list 标记）、binding 指定来源时定向解析、plugin 来源
注册（MOD-03 预留）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.adventures.bundle import AdventureBundleError, AdventureBundleLoader
from src.adventures.registry import (
    AdventureSource,
    AdventureSourceConflict,
    AdventureSourceRegistry,
)
from src.engine.game_instance import GameInstance


BUILTIN = Path("templates/adventures")


def _make_user_package(tmp_path: Path, adventure_id: str = "user:my-user-quest") -> Path:
    """复制内置包为用户目录包并整体改名（manifest/entity/locale target 三处）。"""

    user_root = tmp_path / "user-adventures"
    user_root.mkdir(parents=True, exist_ok=True)
    package = user_root / "my-user-quest"
    shutil.copytree(BUILTIN / "lanterns_of_greymoor", package)
    short_id = adventure_id.split(":", 1)[-1]

    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["adventure_id"] = adventure_id
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    adventure_path = package / "adventure.json"
    adventure = json.loads(adventure_path.read_text(encoding="utf-8"))
    adventure["id"] = short_id
    adventure_path.write_text(json.dumps(adventure), encoding="utf-8")

    for locale_file in package.glob("locales/*/adventure.json"):
        localized = json.loads(locale_file.read_text(encoding="utf-8"))
        if isinstance(localized.get("target"), dict):
            localized["target"]["id"] = short_id
        locale_file.write_text(json.dumps(localized), encoding="utf-8")
    return user_root


def test_registry_aggregates_builtin_and_user_sources(tmp_path: Path) -> None:
    user_root = _make_user_package(tmp_path)
    registry = AdventureSourceRegistry.from_directories(BUILTIN, user_root)

    aggregated = registry.list("zh-CN")
    ids = {bundle.manifest.adventure_id for bundle, _ in aggregated}
    assert "core:lanterns_of_greymoor" in ids  # builtin
    assert "user:my-user-quest" in ids          # user
    sources = {bundle.manifest.adventure_id: source.kind for bundle, source in aggregated}
    assert sources["core:lanterns_of_greymoor"] == "builtin"
    assert sources["user:my-user-quest"] == "user"


def test_resolve_across_sources_and_conflict_is_explicit(tmp_path: Path) -> None:
    user_root = _make_user_package(tmp_path)
    registry = AdventureSourceRegistry.from_directories(BUILTIN, user_root)

    # 独占 id：正常解析并带来源。
    bundle, source = registry.resolve("user:my-user-quest", "zh-CN")
    assert source.kind == "user"
    assert bundle.manifest.adventure_id == "user:my-user-quest"

    # 跨来源同 id：复制内置包到 user 目录 → 显式冲突，不静默挑选（母方案 §31）。
    shutil.copytree(BUILTIN / "lanterns_of_greymoor", user_root / "duplicate-quest")
    with pytest.raises(AdventureSourceConflict) as excinfo:
        registry.resolve("core:lanterns_of_greymoor", "zh-CN")
    assert {s.kind for s in excinfo.value.sources} == {"builtin", "user"}
    assert "core:lanterns_of_greymoor" in registry.conflicts("zh-CN")


def test_resolve_with_explicit_source_defeats_conflict(tmp_path: Path) -> None:
    user_root = _make_user_package(tmp_path)
    shutil.copytree(BUILTIN / "lanterns_of_greymoor", user_root / "duplicate-quest")
    registry = AdventureSourceRegistry.from_directories(BUILTIN, user_root)

    bundle, source = registry.resolve(
        "core:lanterns_of_greymoor", "zh-CN", source_kind="user",
    )
    assert source.kind == "user"
    bundle, source = registry.resolve(
        "core:lanterns_of_greymoor", "zh-CN", source_kind="builtin",
    )
    assert source.kind == "builtin"


def test_resolve_unknown_source_or_id_fails_closed(tmp_path: Path) -> None:
    registry = AdventureSourceRegistry.from_directories(BUILTIN, None)
    with pytest.raises(AdventureBundleError):
        registry.resolve("does-not-exist", "zh-CN")
    with pytest.raises(AdventureBundleError, match="not available"):
        registry.resolve("core:lanterns_of_greymoor", "zh-CN", source_kind="plugin")


def test_plugin_source_registration_is_ready_for_mod03(tmp_path: Path) -> None:
    user_root = _make_user_package(tmp_path)
    plugin_root = tmp_path / "plugin-adventures"
    plugin_root.mkdir(parents=True, exist_ok=True)
    package = plugin_root / "castle-module"
    shutil.copytree(BUILTIN / "lanterns_of_greymoor", package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["adventure_id"] = "plugin:castle-quest"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    adventure_path = package / "adventure.json"
    adventure = json.loads(adventure_path.read_text(encoding="utf-8"))
    adventure["id"] = "castle-quest"
    adventure_path.write_text(json.dumps(adventure), encoding="utf-8")
    for locale_file in package.glob("locales/*/adventure.json"):
        localized = json.loads(locale_file.read_text(encoding="utf-8"))
        if isinstance(localized.get("target"), dict):
            localized["target"]["id"] = "castle-quest"
        locale_file.write_text(json.dumps(localized), encoding="utf-8")

    registry = AdventureSourceRegistry.from_directories(BUILTIN, user_root)
    registry.register(AdventureSource("plugin", "castle-module", AdventureBundleLoader(plugin_root)))

    bundle, source = registry.resolve("plugin:castle-quest", "zh-CN")
    assert source.kind == "plugin" and source.source_id == "castle-module"

    with pytest.raises(ValueError, match="already registered"):
        registry.register(AdventureSource("plugin", "castle-module", AdventureBundleLoader(plugin_root)))


def test_game_binding_keeps_working_with_registry_sources(tmp_path: Path) -> None:
    """存档 binding 语义不受影响：adventure_binding 仍按 id 记录（v1 兼容）。"""
    instance = GameInstance(game_key=("web", "wr2-registry", "bot"))
    instance.world_id = "world-1"
    assert instance.bind_adventure({
        "adventure_id": "user:my-user-quest", "version": "1", "format": "v1",
        "content_digest": "deadbeef", "world_id": "world-1",
    })
    assert instance.adventure_binding["adventure_id"] == "user:my-user-quest"
