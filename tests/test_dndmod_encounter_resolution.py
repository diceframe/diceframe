"""Encounter resolution 测试（DNDMOD-02，母方案 §112 / E2E 场景 C）。

覆盖：core / module / adventure-local 三种来源的怪物引用解析、count 展开
的唯一 id、输出形状满足 combat enemy 契约（id 词法/hp/ac/attacks 骰式）、
引用缺失或指向非 monster 时 fail closed。
"""

from __future__ import annotations

import re

import pytest

from src.content_modules.refs import ContentRefError
from src.rulesets.dnd2024.content.catalog import catalog_from_sources
from src.rulesets.dnd2024.content.encounter import resolve_encounter


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
    "attacks": [{"id": "bite", "name": "Bite", "damage": "2d8+4", "attack_bonus": 7}],
}
ADVENTURE_HOUND = {
    "profile_id": "grave_hound", "name": "Grave Hound", "source_ref": "adventure:core:lanterns",
    "hp": 12, "armor_class": 12, "speed": 40,
    "abilities": {"str": 12, "dex": 16, "con": 12, "int": 3, "wis": 10, "cha": 6},
    "attacks": [{"id": "bite", "damage": "1d6+1", "attack_bonus": 4}],
}


def _catalog() -> object:
    return catalog_from_sources([
        ("adventure:core:lanterns", {"monster": {"grave_hound": ADVENTURE_HOUND}}),
        ("module:castle-module", {"monster": {"ash_vampire": MODULE_VAMPIRE}}),
        ("core:srd", {"monster": {"goblin": CORE_GOBLIN}}),
    ])


def _encounter(enemies: list[dict]) -> dict:
    return {
        "encounter_id": "gate_ambush",
        "name": "Gate Ambush",
        "source_ref": "module:castle-module",
        "difficulty": "standard",
        "enemies": enemies,
    }


def test_resolve_supports_core_module_and_adventure_sources() -> None:
    catalog = _catalog()
    instances = resolve_encounter(catalog, _encounter([
        {"ref": {"source": "core:srd", "kind": "monster", "id": "goblin"}},
        {"ref": "monster:ash_vampire"},  # 裸 ref = 本模块（module:castle-module）
        {"ref": {"source": "adventure:core:lanterns", "kind": "monster", "id": "grave_hound"}},
    ]), default_source="module:castle-module")

    assert [item["profile_id"] for item in instances] == [
        "goblin", "ash_vampire", "grave_hound",
    ]
    vampire = instances[1]
    assert vampire["hp"] == 82 and vampire["armor_class"] == 16
    assert vampire["attacks"][0]["damage"] == "2d8+4"


def test_count_expands_unique_combat_ids() -> None:
    catalog = _catalog()
    instances = resolve_encounter(catalog, _encounter([
        {"ref": {"source": "core:srd", "kind": "monster", "id": "goblin"}, "count": 3},
    ]), default_source="module:castle-module")
    ids = [item["id"] for item in instances]
    assert ids == ["goblin_1", "goblin_2", "goblin_3"]
    assert len(set(ids)) == len(ids)
    for item in instances:
        # combat enemy id 词法（combat.validation）。
        assert re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", item["id"])
        assert re.fullmatch(r"[1-9]\d*d(?:4|6|8|10|12|20)(?:\+[1-9]\d*)?", item["attacks"][0]["damage"])


def test_unresolved_ref_fails_closed_without_substitution() -> None:
    catalog = _catalog()
    with pytest.raises(ContentRefError, match="unresolved"):
        resolve_encounter(catalog, _encounter([
            {"ref": "monster:does_not_exist"},
        ]), default_source="module:castle-module")
    # 裸 ref 只命中本模块来源：跨源引用必须显式（母方案 §9，不猜）。
    with pytest.raises(ContentRefError, match="unresolved"):
        resolve_encounter(catalog, _encounter([
            {"ref": "monster:grave_hound"},  # grave_hound 在 adventure 来源
        ]), default_source="module:castle-module")


def test_ref_pointing_at_non_monster_is_rejected() -> None:
    catalog = catalog_from_sources([
        ("adventure:core:lanterns", {"item": {"castle_sigil": {
            "item_id": "castle_sigil", "name": "Sigil",
            "source_ref": "adventure:core:lanterns", "category": "key_item",
        }}}),
    ])
    with pytest.raises(ContentRefError, match="must point at a monster"):
        resolve_encounter(catalog, _encounter([
            {"ref": "item:castle_sigil"},
        ]), default_source="adventure:core:lanterns")
