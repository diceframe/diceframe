"""Compatibility preview 测试（LIFE-00，母方案 §33/§57/§123）。

覆盖：requires 解析（缺省/非法/未知字段）、runtime 缺失与版本不足阻断、
format 白名单、adventure-module 无冒险警告、catalog 模式提示、blockers 与
warnings 分离（§57）。
"""

from __future__ import annotations

import pytest

from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services.modules import parse_requires, preview_module_install


class _Deps:
    def __init__(self, registry: RulesetRuntimeRegistry) -> None:
        self.ruleset_registry = registry


def _manifest(**overrides: object) -> dict:
    manifest: dict = {
        "id": "castle-module",
        "plugin_type": "content-pack",
        "content_profile": "adventure-module",
        "content_delivery_mode": "catalog",
        "requires": {"rulesets": [{"id": "core:dnd2024", "minimum_version": 1}]},
    }
    manifest.update(overrides)
    return manifest


def test_parse_requires_defaults_and_validation() -> None:
    assert parse_requires({}) == []
    assert parse_requires({"requires": None}) == []
    assert parse_requires(_manifest()) == [
        {"id": "core:dnd2024", "minimum_version": 1},
    ]
    with pytest.raises(ValueError, match="unknown field"):
        parse_requires({"requires": {"rulesets": [{"id": "x", "extra": 1}]}})
    with pytest.raises(ValueError, match="minimum_version"):
        parse_requires({"requires": {"rulesets": [{"id": "x", "minimum_version": 0}]}})


def test_preview_blocks_when_runtime_missing() -> None:
    registry = RulesetRuntimeRegistry()
    result = preview_module_install(_Deps(registry), _manifest())
    assert result["blockers"] == ["ruleset_runtime_missing:core:dnd2024>=1"]
    assert result["warnings"]


def test_preview_passes_when_runtime_available() -> None:
    from src.rulesets.builtin import build_default_ruleset_registry

    registry = build_default_ruleset_registry()
    result = preview_module_install(_Deps(registry), _manifest())
    assert result["blockers"] == []
    assert result["blockers_count"] == 0
    assert result["content_profile"] == "adventure-module"


def test_preview_blocks_on_version_mismatch() -> None:
    from src.rulesets.builtin import build_default_ruleset_registry

    registry = build_default_ruleset_registry()
    result = preview_module_install(_Deps(registry), _manifest(
        requires={"rulesets": [{"id": "core:dnd2024", "minimum_version": 99}]},
    ))
    assert result["blockers"] == ["ruleset_runtime_missing:core:dnd2024>=99"]


def test_preview_blocks_unsupported_format_and_bad_profile() -> None:
    from src.rulesets.builtin import build_default_ruleset_registry

    registry = build_default_ruleset_registry()
    result = preview_module_install(_Deps(registry), _manifest(
        format="diceframe:adventure-graph-v9",
    ))
    assert any(item.startswith("adventure_format_unsupported") for item in result["blockers"])

    result = preview_module_install(_Deps(registry), _manifest(
        content_profile="adventure-module", content_delivery_mode="legacy_autoimport",
    ))
    assert any("catalog" in item for item in result["blockers"])


def test_preview_warnings_are_soft() -> None:
    from src.rulesets.builtin import build_default_ruleset_registry

    registry = build_default_ruleset_registry()
    result = preview_module_install(_Deps(registry), _manifest())
    assert "adventure_module_without_adventures" in result["warnings"]
    assert "catalog_mode_content_not_autoloaded" in result["warnings"]
    # warnings 不阻断。
    assert result["blockers_count"] == 0
