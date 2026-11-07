from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.rulesets.bundle import RulesetBundleError, RulesetBundleLoader


ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, value: dict | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value), encoding="utf-8")


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundles" / "test_bundle"
    _write(root / "legal" / "ATTRIBUTION.md", "Attribution")
    _write(root / "manifest.json", {
        "schema_version": 1,
        "bundle_id": "test:bundle",
        "runtime_id": "test:runtime",
        "ruleset_version": "0.1.0",
        "content_version": "test+r1",
        "default_locale": "zh-CN",
        "supported_locales": ["zh-CN", "en"],
        "license": {"id": "CC-BY-4.0", "attribution": "legal/ATTRIBUTION.md"},
    })
    _write(root / "content" / "abilities" / "str.json", {
        "schema_version": 1,
        "kind": "ability",
        "id": "str",
        "source_ref": "test:abilities/str",
        "automation_level": "deterministic",
        "score": 10,
    })
    _write(root / "content" / "classes" / "fighter.json", {
        "schema_version": 1,
        "kind": "class",
        "id": "fighter",
        "source_ref": "test:classes/fighter",
        "automation_level": "guided",
        "primary_ability_ref": "ability:str",
        "effects": [{"op": "grant_proficiency", "target": "save:str"}],
    })
    _write(root / "locales" / "zh-CN" / "abilities" / "str.json", {
        "locale_schema_version": 1,
        "locale": "zh-CN",
        "target": {"kind": "ability", "id": "str"},
        "fields": {"name": "力量", "description": "衡量身体力量。"},
    })
    _write(root / "locales" / "en" / "abilities" / "str.json", {
        "locale_schema_version": 1,
        "locale": "en",
        "target": {"kind": "ability", "id": "str"},
        "fields": {"name": "Strength"},
    })
    return root


def test_loads_canonical_entities_and_materializes_locale(tmp_path: Path) -> None:
    _bundle(tmp_path)
    loader = RulesetBundleLoader(tmp_path / "bundles")

    zh = loader.load("test_bundle", "zh-CN")
    en = loader.load("test_bundle", "en")

    assert zh.manifest.runtime_id == "test:runtime"
    assert zh.get("ability", "str")["name"] == "力量"
    assert en.get("ability", "str")["name"] == "Strength"
    assert en.get("ability", "str")["description"] == "衡量身体力量。"
    assert zh.get("ability", "str")["score"] == en.get("ability", "str")["score"] == 10
    assert zh.get("class", "fighter")["primary_ability_ref"] == "ability:str"


def test_returns_copies_instead_of_mutable_authority(tmp_path: Path) -> None:
    _bundle(tmp_path)
    bundle = RulesetBundleLoader(tmp_path / "bundles").load("test_bundle")

    first = bundle.get("ability", "str")
    first["score"] = 99

    assert bundle.get("ability", "str")["score"] == 10


def test_loads_presets_with_the_same_validation_and_reference_rules(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _write(root / "presets" / "characters" / "fighter.json", {
        "schema_version": 1,
        "kind": "quick_character_preset",
        "id": "fighter",
        "source_ref": "test:original-preset",
        "automation_level": "deterministic",
        "draft": {"class_ref": "class:fighter"},
    })

    bundle = RulesetBundleLoader(tmp_path / "bundles").load("test_bundle")

    assert bundle.get("quick_character_preset", "fighter")["draft"]["class_ref"] == (
        "class:fighter"
    )

    preset_path = root / "presets" / "characters" / "fighter.json"
    preset = json.loads(preset_path.read_text(encoding="utf-8"))
    preset["draft"]["class_ref"] = "class:missing"
    _write(preset_path, preset)
    with pytest.raises(RulesetBundleError, match="unresolved reference"):
        RulesetBundleLoader(tmp_path / "bundles").load("test_bundle")


def test_rejects_locale_mechanics_override(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    overlay = json.loads(
        (root / "locales" / "en" / "abilities" / "str.json").read_text(encoding="utf-8")
    )
    overlay["fields"]["score"] = 20
    _write(root / "locales" / "en" / "abilities" / "str.json", overlay)

    with pytest.raises(RulesetBundleError, match="cannot override mechanics"):
        RulesetBundleLoader(tmp_path / "bundles").load("test_bundle", "en")


def test_rejects_unknown_effect_operation(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    fighter_path = root / "content" / "classes" / "fighter.json"
    fighter = json.loads(fighter_path.read_text(encoding="utf-8"))
    fighter["effects"] = [{"op": "teleport_world"}]
    _write(fighter_path, fighter)

    with pytest.raises(RulesetBundleError, match="unknown effect operation"):
        RulesetBundleLoader(tmp_path / "bundles").load("test_bundle")


def test_rejects_executable_content_keys_at_any_depth(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    fighter_path = root / "content" / "classes" / "fighter.json"
    fighter = json.loads(fighter_path.read_text(encoding="utf-8"))
    fighter["choices"] = [{"script": "danger"}]
    _write(fighter_path, fighter)

    with pytest.raises(RulesetBundleError, match="executable content key is forbidden"):
        RulesetBundleLoader(tmp_path / "bundles").load("test_bundle")


def test_rejects_duplicate_entity_and_unresolved_reference(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    duplicate = json.loads(
        (root / "content" / "abilities" / "str.json").read_text(encoding="utf-8")
    )
    _write(root / "content" / "duplicates" / "str.json", duplicate)
    with pytest.raises(RulesetBundleError, match="duplicate entity"):
        RulesetBundleLoader(tmp_path / "bundles").load("test_bundle")

    (root / "content" / "duplicates" / "str.json").unlink()
    fighter_path = root / "content" / "classes" / "fighter.json"
    fighter = json.loads(fighter_path.read_text(encoding="utf-8"))
    fighter["primary_ability_ref"] = "ability:missing"
    _write(fighter_path, fighter)
    with pytest.raises(RulesetBundleError, match="unresolved reference"):
        RulesetBundleLoader(tmp_path / "bundles").load("test_bundle")


def test_rejects_attribution_path_traversal(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["license"]["attribution"] = "../outside.md"
    _write(root / "manifest.json", manifest)
    _write(root.parent / "outside.md", "outside")

    with pytest.raises(RulesetBundleError, match="inside the bundle"):
        RulesetBundleLoader(tmp_path / "bundles").load("test_bundle")


def test_builtin_dnd2024_bundle_skeleton_is_valid_and_localized() -> None:
    loader = RulesetBundleLoader(ROOT / "templates" / "rulesets")

    zh = loader.load("dnd2024_srd", "zh-CN")
    en = loader.load("dnd2024_srd", "en")

    assert zh.manifest.bundle_id == "core:dnd2024-srd"
    assert zh.manifest.runtime_id == "core:dnd2024"
    assert len(zh.list("ability")) == 6
    assert zh.get("ability", "str")["name"] == "力量"
    assert en.get("ability", "str")["name"] == "Strength"


def test_builtin_dnd2024_inventory_and_locale_presentation_are_complete() -> None:
    root = ROOT / "templates" / "rulesets" / "dnd2024_srd"
    bundle = RulesetBundleLoader(ROOT / "templates" / "rulesets").load(
        "dnd2024_srd", "en",
    )

    assert {kind: len(bundle.list(kind)) for kind in (
        "ability", "skill", "language", "class", "species", "background",
        "feat", "equipment_package", "item", "tool", "quick_character_preset",
    )} == {
        "ability": 6,
        "skill": 18,
        "language": 10,
        "class": 12,
        "species": 9,
        "background": 4,
        "feat": 7,
        "equipment_package": 33,
        "item": 82,
        "tool": 32,
        "quick_character_preset": 6,
    }

    zh_root = root / "locales" / "zh-CN"
    en_root = root / "locales" / "en"
    for zh_path in zh_root.rglob("*.json"):
        relative = zh_path.relative_to(zh_root)
        en_path = en_root / relative
        assert en_path.is_file(), relative
        zh_fields = json.loads(zh_path.read_text(encoding="utf-8"))["fields"]
        en_fields = json.loads(en_path.read_text(encoding="utf-8"))["fields"]
        assert set(zh_fields).issubset(en_fields), relative
