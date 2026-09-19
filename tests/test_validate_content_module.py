"""Content module packaging validation 测试（LIFE-04，母方案 §127/§199）。

端到端跑校验脚本的主函数：合法 homebrew 模组 → ok 报告（含冒险与 catalog
清单）；坏 manifest → errors + 退出码语义。
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_content_module import validate_module


def _make_module(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "homebrew-module"
    (plugin_dir / "packs" / "dnd2024").mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "schema_version": 1, "id": "homebrew-module", "name": "Homebrew",
        "version": "1.0.0", "plugin_type": "content-pack",
        "content_profile": "adventure-module", "content_delivery_mode": "catalog",
        "ruleset_catalogs": ["packs/dnd2024"], "contributes": {},
    }), encoding="utf-8")
    (plugin_dir / "config.schema.json").write_text(
        '{"type": "object", "properties": {}}', encoding="utf-8",
    )
    (plugin_dir / "packs" / "dnd2024" / "monsters.json").write_text(json.dumps({
        "kind": "monster", "profile_id": "moss_golem", "name": "Moss Golem",
        "source_ref": "module:homebrew-module", "hp": 45, "armor_class": 14,
        "speed": 20,
        "abilities": {"str": 16, "dex": 8, "con": 16, "int": 5, "wis": 10, "cha": 5},
        "attacks": [{"id": "slam", "damage": "2d8+3", "attack_bonus": 5}],
    }), encoding="utf-8")
    return plugin_dir


def test_valid_module_report_is_ok(tmp_path: Path) -> None:
    report = validate_module(_make_module(tmp_path))
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["catalogs"] == [{"directory": "dnd2024", "records": 1}]
    assert "no adventure_packages declared" in report["warnings"]


def test_bad_catalog_record_fails_the_report(tmp_path: Path) -> None:
    plugin_dir = _make_module(tmp_path)
    (plugin_dir / "packs" / "dnd2024" / "broken.json").write_text(json.dumps({
        "kind": "monster", "profile_id": "broken", "name": "Broken",
        "source_ref": "module:homebrew-module", "hp": 0, "armor_class": 14,
        "speed": 20,
        "abilities": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "attacks": [{"id": "slam", "damage": "2d8+3", "attack_bonus": 5}],
    }), encoding="utf-8")
    report = validate_module(plugin_dir)
    assert report["ok"] is False
    assert any("catalog dnd2024" in error for error in report["errors"])


def test_missing_plugin_json_fails_closed(tmp_path: Path) -> None:
    empty = tmp_path / "empty-module"
    empty.mkdir()
    report = validate_module(empty)
    assert report["ok"] is False
    assert report["errors"] == ["missing plugin.json"]
