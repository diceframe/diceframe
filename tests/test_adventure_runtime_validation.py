"""Runtime validator registry 测试（MOD-01，母方案 §8/§185/§186）。

覆盖：注册/查询/未注册 = unresolved、generic loader 不再拥有 D&D mechanics
校验（不再拒绝 mechanics 缺失的 encounter 结构）、dnd2024 validator 接管
mechanics 边界（hp/ac/attack_bonus/damage/attacks）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.adventures import AdventureBundleError, AdventureBundleLoader
from src.adventures.runtime_validation import (
    adventure_validator_for,
    register_adventure_validator,
)
from src.rulesets.dnd2024.adventure_validation import (
    validate_dnd2024_adventure_content,
)

# 与 test_adventure_bundles 相同的做法：测试期间显式注册 dnd2024 校验器
# （生产组合必然导入 dnd2024 runtime）。
register_adventure_validator("core:dnd2024", validate_dnd2024_adventure_content)


def _entity(entities: dict, kind: str, entity_id: str) -> dict:
    return entities[kind][entity_id]


def test_registry_lookup_is_exact_and_missing_is_unresolved() -> None:
    assert adventure_validator_for("core:dnd2024") is validate_dnd2024_adventure_content
    assert adventure_validator_for("core:coc7") is None
    assert adventure_validator_for("") is None


def test_registry_rejects_empty_runtime_id() -> None:
    with pytest.raises(ValueError):
        register_adventure_validator("", validate_dnd2024_adventure_content)


def _copied_package(tmp_path: Path) -> tuple[AdventureBundleLoader, Path]:
    source = Path("templates/adventures/lanterns_of_greymoor")
    package = tmp_path / "lanterns_of_greymoor"
    shutil.copytree(source, package)
    loader = AdventureBundleLoader(tmp_path)
    return loader, package


def _write_encounter(package: Path, payload: dict) -> None:
    path = package / "content" / "encounters" / "greymoor_encounters.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_encounter(package: Path) -> dict:
    path = package / "content" / "encounters" / "greymoor_encounters.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("mutation,expected", [
    ({"hp": 0}, "encounter enemy hp"),
    ({"hp": 100001}, "encounter enemy hp"),
    ({"armor_class": 0}, "encounter enemy armor_class"),
    ({"armor_class": 41}, "encounter enemy armor_class"),
    ({"attacks": []}, "must contain attacks"),
    ({"attacks": [{"id": "bite", "damage": "1d6", "attack_bonus": 99}]}, "attack bonus"),
    ({"attacks": [{"id": "bite", "attack_bonus": 1}]}, "attack damage"),
])
def test_dnd2024_validator_owns_mechanics_bounds(
    tmp_path: Path, mutation: dict, expected: str,
) -> None:
    loader, package = _copied_package(tmp_path)
    encounter = _read_encounter(package)
    encounter["presets"][0]["enemies"][0].update(mutation)
    _write_encounter(package, encounter)

    with pytest.raises(AdventureBundleError, match=expected):
        loader.load("lanterns_of_greymoor", "zh-CN")


def test_generic_loader_still_validates_encounter_structure(tmp_path: Path) -> None:
    """结构校验留在 generic loader：enemies 非空 / id / difficulty 词表。"""
    loader, package = _copied_package(tmp_path)
    encounter = _read_encounter(package)
    encounter["presets"][0]["difficulty"] = "impossible"
    _write_encounter(package, encounter)
    with pytest.raises(AdventureBundleError, match="invalid encounter difficulty"):
        loader.load("lanterns_of_greymoor", "zh-CN")


def test_unknown_runtime_leaves_mechanics_unresolved(tmp_path: Path) -> None:
    """未注册 runtime：generic 结构可读，mechanics 不由 loader 猜（§185）。"""
    loader, package = _copied_package(tmp_path)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["required_runtime"] = {"id": "core:unknown-rpg", "minimum_version": 1}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    encounter = _read_encounter(package)
    encounter["presets"][0]["enemies"][0]["hp"] = 0  # 机制非法，但无人校验
    _write_encounter(package, encounter)

    loaded = loader.load("lanterns_of_greymoor", "zh-CN")
    assert loaded.manifest.required_runtime_id == "core:unknown-rpg"
