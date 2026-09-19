"""D&D module catalog contracts 测试（DNDMOD-00，母方案 §110/§29）。"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.content import (
    CatalogContractError,
    validate_encounter_profile,
    validate_item_record,
    validate_monster_profile,
    validate_npc_statblock,
)


def _monster(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "profile_id": "ash_vampire",
        "name": "Ash Vampire",
        "source_ref": "module:castle-module",
        "hp": 82,
        "armor_class": 16,
        "speed": 30,
        "abilities": {"str": 16, "dex": 18, "con": 16, "int": 12, "wis": 14, "cha": 18},
        "attacks": [
            {"id": "bite", "name": "Bite", "damage": "2d8+4", "attack_bonus": 7},
        ],
    }
    record.update(overrides)
    return record


def test_monster_profile_round_trips_valid_shape() -> None:
    validated = validate_monster_profile(_monster())
    assert validated["profile_id"] == "ash_vampire"
    assert validated["attacks"][0]["damage"] == "2d8+4"
    assert validated["source_ref"] == "module:castle-module"


def test_monster_mechanics_match_combat_bounds() -> None:
    with pytest.raises(CatalogContractError, match="hp"):
        validate_monster_profile(_monster(hp=10001))
    with pytest.raises(CatalogContractError, match="armor_class"):
        validate_monster_profile(_monster(armor_class=0))
    with pytest.raises(CatalogContractError, match="damage"):
        validate_monster_profile(_monster(attacks=[
            {"id": "bite", "damage": "3d12+8d6", "attack_bonus": 5},
        ]))
    with pytest.raises(CatalogContractError, match="must contain exactly"):
        validate_monster_profile(_monster(abilities={"str": 10}))


def test_monster_rejects_mechanics_choosing_outside_contract() -> None:
    with pytest.raises(CatalogContractError, match="unknown field"):
        validate_monster_profile(_monster(cr="5"))
    with pytest.raises(CatalogContractError, match="duplicates attack id"):
        validate_monster_profile(_monster(attacks=[
            {"id": "bite", "damage": "1d6"},
            {"id": "bite", "damage": "1d8"},
        ]))


def test_npc_statblock_carries_identity_plus_mechanics() -> None:
    validated = validate_npc_statblock({
        "statblock_id": "count_von_castle",
        "name": "Count von Castle",
        "source_ref": "module:castle-module",
        "hp": 75, "armor_class": 17, "speed": 30,
        "abilities": {"str": 18, "dex": 14, "con": 16, "int": 16, "wis": 12, "cha": 18},
        "attacks": [{"id": "rapier", "damage": "1d8+4", "attack_bonus": 7}],
        "description": "城堡主人。",
    })
    assert validated["hp"] == 75
    assert validated["description"] == "城堡主人。"
    monster = _monster()
    monster.pop("profile_id")
    with pytest.raises(CatalogContractError, match="statblock_id"):
        validate_npc_statblock(monster)  # 缺 statblock_id → fail closed


def test_item_record_is_identity_only() -> None:
    validated = validate_item_record({
        "item_id": "castle_sigil", "name": "Castle Sigil",
        "source_ref": "module:castle-module", "category": "key_item",
        "description": "进入内城的信物。",
    })
    assert validated["category"] == "key_item"
    # 母方案 §189：数量/装备不是 catalog 契约的一部分。
    with pytest.raises(CatalogContractError, match="unknown field"):
        validate_item_record({
            "item_id": "x", "name": "X", "category": "tool", "qty": 3,
        })


def test_encounter_profile_references_monsters_by_content_ref() -> None:
    validated = validate_encounter_profile({
        "encounter_id": "gate_ambush",
        "name": "Gate Ambush",
        "source_ref": "module:castle-module",
        "difficulty": "standard",
        "enemies": [
            {"ref": {"source": "module:castle-module", "kind": "monster", "id": "ash_vampire"}},
            {"ref": "monster:goblin", "count": 4},
        ],
    })
    assert validated["enemies"][0]["ref"]["source"] == "module:castle-module"
    assert validated["enemies"][1]["ref"]["id"] == "goblin"
    assert validated["enemies"][1]["count"] == 4


def test_encounter_profile_fail_closed() -> None:
    with pytest.raises(CatalogContractError, match="difficulty"):
        validate_encounter_profile({
            "encounter_id": "x", "name": "X", "difficulty": "epic",
            "enemies": [{"ref": "monster:goblin"}],
        })
    with pytest.raises(CatalogContractError, match="ref"):
        validate_encounter_profile({
            "encounter_id": "x", "name": "X", "difficulty": "standard",
            "enemies": [{"ref": "goblin-only"}],
        })
    with pytest.raises(CatalogContractError, match="count"):
        validate_encounter_profile({
            "encounter_id": "x", "name": "X", "difficulty": "standard",
            "enemies": [{"ref": "monster:goblin", "count": 0}],
        })
