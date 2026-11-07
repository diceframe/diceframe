"""#694：compatible_rulesets 是市场标签，不是 runtime 依赖。"""

from __future__ import annotations

import pytest

from src.plugin_host.marketplace import _marketplace_ruleset_targets
from src.plugin_host.support import (
    MAX_COMPATIBLE_RULESETS,
    parse_compatible_rulesets,
    validate_content_module_profile,
)
from src.rulesets.builtin import build_default_ruleset_registry
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services.modules import preview_module_install


class _Deps:
    def __init__(self, registry: RulesetRuntimeRegistry) -> None:
        self.ruleset_registry = registry


def _pack(**overrides: object) -> dict:
    manifest: dict = {
        "id": "coc-pack",
        "plugin_type": "content-pack",
        "content_profile": "content-pack",
    }
    manifest.update(overrides)
    return manifest


# ---- 字段解析与归一化 --------------------------------------------------------


def test_labels_are_normalized_and_deduped() -> None:
    assert parse_compatible_rulesets(_pack(compatible_rulesets=["coc", " core:coc7 ", "CoC"])) == ["coc7"]
    assert parse_compatible_rulesets(_pack(compatible_rulesets=["dnd", "dnd2024"])) == ["dnd2024"]


def test_unknown_custom_system_labels_are_kept() -> None:
    # 自定义规则没有注册 runtime，也不该被拒绝：标签只是元数据
    assert parse_compatible_rulesets(_pack(compatible_rulesets=["my-homebrew"])) == ["my-homebrew"]


def test_invalid_label_payloads_fail_closed() -> None:
    with pytest.raises(ValueError, match="必须是数组"):
        parse_compatible_rulesets(_pack(compatible_rulesets="coc7"))
    with pytest.raises(ValueError, match="必须是字符串"):
        parse_compatible_rulesets(_pack(compatible_rulesets=["coc7", 7]))
    with pytest.raises(ValueError, match="不允许空字符串"):
        parse_compatible_rulesets(_pack(compatible_rulesets=["  "]))
    with pytest.raises(ValueError, match="最多"):
        parse_compatible_rulesets(_pack(compatible_rulesets=[f"r{index}" for index in range(MAX_COMPATIBLE_RULESETS + 1)]))


def test_agnostic_and_specific_labels_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="互斥"):
        parse_compatible_rulesets(_pack(system_agnostic=True, compatible_rulesets=["coc7"]))


def test_profile_validation_rejects_the_same_conflict() -> None:
    with pytest.raises(ValueError, match="互斥"):
        validate_content_module_profile(_pack(system_agnostic=True, compatible_rulesets=["coc7"]))
    # 只有标签的包通过 profile 校验
    validate_content_module_profile(_pack(compatible_rulesets=["coc7"]))


# ---- 安装校验：硬依赖语义不变 ------------------------------------------------


def test_compatible_label_does_not_block_install() -> None:
    result = preview_module_install(_Deps(RulesetRuntimeRegistry()), _pack(compatible_rulesets=["coc7"]))
    assert result["blockers"] == []
    assert result["ok"] is True


def test_unregistered_runtime_still_blocks_when_declared_as_requires() -> None:
    # 标签不能替代硬依赖：写 requires 的包仍按 runtime 可用性判定
    manifest = _pack(**{"requires": {"rulesets": [{"id": "core:coc7", "minimum_version": 1}]}})
    result = preview_module_install(_Deps(RulesetRuntimeRegistry()), manifest)
    assert result["blockers"] == ["ruleset_runtime_missing:core:coc7>=1"]


def test_registered_runtime_keeps_working() -> None:
    manifest = _pack(**{"requires": {"rulesets": [{"id": "core:dnd2024", "minimum_version": 1}]}})
    result = preview_module_install(_Deps(build_default_ruleset_registry()), manifest)
    assert result["blockers"] == []


# ---- 市场投影 ----------------------------------------------------------------


def test_marketplace_targets_union_requires_and_labels() -> None:
    manifest = {
        "requires": {"rulesets": [{"id": "core:dnd2024"}]},
        "compatible_rulesets": ["coc7", "dnd2024"],
    }
    assert _marketplace_ruleset_targets(manifest) == ["dnd2024", "coc7"]


def test_label_only_pack_counts_as_declared() -> None:
    assert _marketplace_ruleset_targets({"compatible_rulesets": ["coc7"]}) == ["coc7"]
    assert _marketplace_ruleset_targets({}) == []
