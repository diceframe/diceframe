"""D&D catalog loader 测试（DNDMOD-01，母方案 §10/§111）。

覆盖：多来源装载（adventure-local / module / core 优先序）、source-aware
解析（显式引用直查、v1 引用按链回溯）、同源 canonical 重复 = 装载错误、
跨来源同名允许、坏记录 fail closed（整源拒绝）、目录装载。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.content_modules.refs import parse_content_ref
from src.rulesets.dnd2024.content.catalog import (
    CatalogLoadError,
    catalog_from_sources,
    load_catalog_dir,
)


CORE_GOBLIN = {
    "profile_id": "goblin", "name": "Goblin", "source_ref": "core:srd",
    "hp": 7, "armor_class": 13, "speed": 30,
    "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
    "attacks": [{"id": "scimitar", "damage": "1d6+2", "attack_bonus": 4}],
}
MODULE_VAMPIRE = {
    "profile_id": "ash_vampire", "name": "Ash Vampire", "source_ref": "module:castle-module",
    "hp": 82, "armor_class": 16, "speed": 30,
    "abilities": {"str": 16, "dex": 18, "con": 16, "int": 12, "wis": 14, "cha": 18},
    "attacks": [{"id": "bite", "damage": "2d8+4", "attack_bonus": 7}],
}
ADVENTURE_GOBLIN = {
    "profile_id": "goblin", "name": "Goblin (greymoor variant)",
    "source_ref": "adventure:core:lanterns", "hp": 9, "armor_class": 13, "speed": 30,
    "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
    "attacks": [{"id": "scimitar", "damage": "1d6+2", "attack_bonus": 4}],
}


def _catalog() -> object:
    return catalog_from_sources([
        ("adventure:core:lanterns", {"monster": {"goblin": ADVENTURE_GOBLIN}}),
        ("module:castle-module", {"monster": {"ash_vampire": MODULE_VAMPIRE}}),
        ("core:srd", {"monster": {"goblin": CORE_GOBLIN}}),
    ])


def test_resolution_prefers_adventure_local_then_module_then_core() -> None:
    catalog = _catalog()
    chain = catalog.lookup_chain(local_label="adventure:core:lanterns")

    local_ref = parse_content_ref("monster:goblin", default_source="adventure:core:lanterns")
    assert chain.resolve(local_ref).source_label == "adventure:core:lanterns"

    module_ref = parse_content_ref("monster:ash_vampire", default_source="adventure:core:lanterns")
    assert chain.resolve(module_ref).source_label == "module:castle-module"

    # adventure 与 module 都没有的 id → core。
    core_ref = parse_content_ref(
        {"source": "core:srd", "kind": "monster", "id": "goblin"},
        default_source="adventure:core:lanterns",
    )
    assert chain.resolve(core_ref).source_label == "core:srd"
    assert chain.resolve(core_ref).value["name"] == "Goblin"


def test_explicit_ref_resolves_only_its_own_source() -> None:
    catalog = _catalog()
    ref = parse_content_ref(
        {"source": "module:castle-module", "kind": "monster", "id": "ash_vampire"},
        default_source="adventure:core:lanterns",
    )
    assert catalog.resolve(ref)["name"] == "Ash Vampire"
    # 未知来源引用 → 无结果（不跨源猜测）。
    missing = parse_content_ref(
        {"source": "module:other", "kind": "monster", "id": "ash_vampire"},
        default_source="adventure:core:lanterns",
    )
    assert catalog.resolve(missing) is None


def test_same_source_duplicate_ref_is_a_load_error() -> None:
    with pytest.raises(CatalogLoadError, match="duplicate ref"):
        catalog_from_sources([
            ("module:castle", {"monster": {"ash_vampire": MODULE_VAMPIRE}}),
        ] + [
            ("module:castle", {"monster": {"ash_vampire": MODULE_VAMPIRE}}),
        ])


def test_cross_source_same_id_is_allowed() -> None:
    catalog = _catalog()
    # adventure 与 core 都有 goblin：合法（引用带 source）。
    assert catalog.count("monster") == 3


def test_invalid_record_fails_closed() -> None:
    bad = {**MODULE_VAMPIRE, "hp": 0}
    with pytest.raises(Exception) as excinfo:
        catalog_from_sources([("module:castle", {"monster": {"ash_vampire": bad}})])
    assert "hp" in str(excinfo.value)


def test_load_catalog_dir_reads_json_and_rejects_duplicates(tmp_path: Path) -> None:
    module_root = tmp_path / "packs" / "dnd2024"
    module_root.mkdir(parents=True)
    (module_root / "monsters.json").write_text(
        json.dumps({"kind": "monster", **MODULE_VAMPIRE}), encoding="utf-8",
    )
    source = load_catalog_dir(module_root, source_label="module:castle-module")
    assert source.lookup("monster", "ash_vampire")["profile_id"] == "ash_vampire"

    (module_root / "monsters.json").write_text(
        json.dumps([
            {"kind": "monster", **MODULE_VAMPIRE},
            {"kind": "monster", **MODULE_VAMPIRE},
        ]), encoding="utf-8",
    )
    with pytest.raises(CatalogLoadError, match="duplicate ref"):
        load_catalog_dir(module_root, source_label="module:castle-module")
