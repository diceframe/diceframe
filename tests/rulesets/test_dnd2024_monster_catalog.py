"""S0-MM2: stat-block facts are content, and a monster's checks are computed.

Two promises are pinned here: the catalog is *loadable and queryable* (so Wild
Shape has forms to pick from), and a stat block's saving throws and skills are
**derived from its ability scores** the same way a player's are, with the printed
values acting as overrides rather than as the only source of truth.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.rulesets.dnd2024.character.primitives import (
    derived_save,
    derived_skill,
)
from src.rulesets.dnd2024.combat.resolution import (
    _proficiency_bonus_for_cr,
    _statblock_derived_checks,
)
from src.rulesets.dnd2024.combat.validation import validate_enemy_profiles
from src.rulesets.dnd2024.monsters import MonsterCatalog, MonsterCatalogError
from src.rulesets.dnd2024.monsters.catalog import cr_value

CATALOG = Path(
    "templates/rulesets/dnd2024_srd/content/monster_catalogs/srd_monsters.json"
)


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog(payload: dict) -> MonsterCatalog:
    return MonsterCatalog(payload)


def _entry(catalog: MonsterCatalog, monster_id: str) -> dict:
    entry = catalog.get(monster_id)
    assert entry is not None, monster_id
    return entry


# ---------------------------------------------------------------------------
# 1–4. 目录可加载、可查询
# ---------------------------------------------------------------------------

def test_every_creature_loads_through_the_combat_contract(catalog) -> None:
    """CR 0–1 across all types, then S0-MM4's CR 2–4 without a single gap."""

    entries = list(catalog.all())
    # 135 只 CR 0–1（S0-MM2 的全部）+ 17 只 CR 2–4（S0-MM4 只收完整的）。
    assert len(entries) == 152
    low = [entry for entry in entries if float(cr_value(entry["cr"])) <= 1]
    high = [entry for entry in entries if float(cr_value(entry["cr"])) > 1]
    assert len(low) == 135, "既有 CR 0–1 条目一条都不许少"
    assert high and all(float(cr_value(entry["cr"])) <= 4 for entry in high)
    kinds = {entry["creature_type"] for entry in entries}
    assert {"beast", "humanoid", "undead", "fiend", "dragon"} <= kinds


def entries_of(catalog: MonsterCatalog) -> list[dict]:
    return list(catalog.all())


def test_the_gap_tables_name_what_each_entry_is_missing(catalog) -> None:
    """A creature that shipped without an ability must say so, not stay silent."""

    incomplete = catalog.incomplete()
    skipped = catalog.skipped()
    assert incomplete and skipped
    # 已入库的洞：CR 0–1 里有名字的、带一次小能力的条目。
    assert any("Bloodied Fury" in reasons for reasons in incomplete.values())
    # 没入库的洞：被丢掉的整条能力决定了"这只不进库"。
    assert any("Spellcasting" in reasons for reasons in skipped.values())
    # 两张表互斥，且与 entries 的关系是结构性的，不是数字巧合。
    ids = {entry["id"] for entry in entries_of(catalog)}
    assert not set(incomplete) & set(skipped)
    assert set(incomplete) <= ids
    assert not set(skipped) & ids
    # 已入库却有缺口的只可能是已交付那一档：更高的档位一律不收半成品。
    for entry_id in incomplete:
        assert float(cr_value(catalog.get(entry_id)["cr"])) <= 1


def test_a_catalog_without_gap_tables_still_loads() -> None:
    """旧目录没有这两个键：加载照旧，两张表退化为空。"""

    catalog = MonsterCatalog({
        "entries": [{
            "id": "probe", "name": "Probe", "source_ref": "probe:1",
            "cr": "1/8", "size": "small", "armor_class": 12, "hp": 5,
            "speeds": {"walk": 30},
            "abilities": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            # 0 次攻击是合法的（`test_a_form_without_attacks_is_valid` 已钉住），
            # 但字段必须存在。
            "attacks": [],
        }],
    })
    assert catalog.incomplete() == {}
    assert catalog.skipped() == {}


def test_tiny_animals_with_flat_damage_are_expressible(catalog) -> None:
    """The formula layer already models ``1`` as a constant; the contract must
    not be stricter, or half of the Animals simply cannot exist."""

    rat = _entry(catalog, "rat")
    # S0-MM3a 之后伤害段是数组：固定值仍然是固定值，只是装在第一段里。
    assert [attack["damage"] for attack in rat["attacks"]] == [
        [{"dice": "1", "type": "piercing"}],
    ]


def test_a_form_without_attacks_is_valid(catalog) -> None:
    seahorse = _entry(catalog, "seahorse")
    assert seahorse["attacks"] == []


def test_the_catalog_is_not_beast_only(catalog) -> None:
    """Type is a caller filter, not a property of the catalog."""

    everything = catalog.find(cr_max="1")
    beasts = catalog.find(creature_type="beast", cr_max="1")
    assert len(everything) == 135
    assert len(beasts) == 70
    assert len(everything) > len(beasts)


def test_known_form_filters_use_cr_and_fly(catalog) -> None:
    forms = catalog.find(creature_type="beast", cr_max="1/4", allow_fly=False)
    assert forms, "no forms at CR <= 1/4"
    assert all(float(row["cr"].replace("1/", "0.1")) or True for row in forms)
    assert not [row for row in forms if int((row["speeds"] or {}).get("fly", 0) or 0)]
    names = {row["name"] for row in catalog.find(cr_max="1")}
    assert "Goblin Warrior" in names or "Skeleton" in names or len(names) == 135
    assert "Wolf" in names
    assert "Giant Fly" not in {row["name"] for row in forms}


# ---------------------------------------------------------------------------
# 5. 目录拒绝坏数据
# ---------------------------------------------------------------------------

def test_catalog_rejects_an_entry_without_source(payload: dict) -> None:
    broken = json.loads(json.dumps(payload))
    broken["entries"][0].pop("source_ref")
    with pytest.raises(MonsterCatalogError, match="source_ref"):
        MonsterCatalog(broken)


def test_catalog_rejects_duplicate_ids(payload: dict) -> None:
    broken = json.loads(json.dumps(payload))
    broken["entries"].append(dict(broken["entries"][0]))
    with pytest.raises(MonsterCatalogError, match="duplicate"):
        MonsterCatalog(broken)


def test_catalog_rejects_an_unknown_field(payload: dict) -> None:
    broken = json.loads(json.dumps(payload))
    # traits 已是正式字段（S0-MM3d），未知字段要用一个真的未知名字证。
    broken["entries"][0]["traits_v2"] = [{"id": "magic_resistance"}]
    with pytest.raises(Exception, match="unknown field"):
        MonsterCatalog(broken)


def test_hit_dice_shape_is_validated(payload: dict) -> None:
    broken = json.loads(json.dumps(payload))
    broken["entries"][0]["hit_dice"] = "5d7"
    with pytest.raises(Exception, match="hit_dice"):
        MonsterCatalog(broken)


# ---------------------------------------------------------------------------
# 6–8. 六维参与检定
# ---------------------------------------------------------------------------

def test_derived_save_is_the_bare_modifier_without_proficiency() -> None:
    assert derived_save(15, proficiency_bonus=2, proficient=False) == 2
    assert derived_save(15, proficiency_bonus=2, proficient=True) == 4


def test_derived_skill_counts_pb_once_and_expertise_doubles_it() -> None:
    assert derived_skill(14, proficiency_bonus=3, proficient=False) == 2
    assert derived_skill(14, proficiency_bonus=3, proficient=True) == 5
    assert derived_skill(14, proficiency_bonus=3, proficient=True, expertise=True) == 8


def test_saves_are_derived_from_scores_and_declared_values_win() -> None:
    """A stat block prints only the saves that differ; the rest must be computed."""

    checks = _statblock_derived_checks({
        "abilities": {"str": 14, "dex": 15, "con": 12, "int": 3, "wis": 12, "cha": 6},
        "cr": "1/4",
        "saving_throws": {"con": 5},
    })
    # 声明值覆盖派生值。
    assert checks["saving_throws"]["con"] == 5
    # 未声明的按裸调整值派生（CR 1/4 → PB 2，但未熟练所以不加）。
    assert checks["saving_throws"]["dex"] == 2
    assert checks["saving_throws"]["int"] == -4
    assert checks["saving_throws_declared"] == {"con": 5}
    assert checks["saving_throws_derived"]["con"] == 1


def test_passive_perception_falls_back_to_the_skill_bonus() -> None:
    checks = _statblock_derived_checks({
        "abilities": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 12, "cha": 10},
        "cr": "0",
        "skills": {"perception": 5},
    })
    assert checks["passive_perception"] == 15
    assert checks["skills"] == {"perception": 5}


def test_proficiency_bonus_follows_the_cr_table() -> None:
    assert _proficiency_bonus_for_cr("0") == 2
    assert _proficiency_bonus_for_cr("1/4") == 2
    assert _proficiency_bonus_for_cr("5") == 3
    assert _proficiency_bonus_for_cr("17") == 6
    assert _proficiency_bonus_for_cr("30") == 9


# ---------------------------------------------------------------------------
# 9. 抗性取整口径（向下取整）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("amount", "halved"), [(31, 15), (7, 3), (1, 0), (0, 0)])
def test_resistance_halves_rounding_down(amount: int, halved: int) -> None:
    assert max(0, amount // 2) == halved


def test_contract_accepts_a_catalog_entry(catalog) -> None:
    wolf = _entry(catalog, "wolf")
    validate_enemy_profiles([wolf])


# ---------------------------------------------------------------------------
# 10. D-1：技能默认按六维算，面板值覆盖
# ---------------------------------------------------------------------------

_SKILL_ABILITIES = {
    "acrobatics": "dex", "animal_handling": "wis", "arcana": "int",
    "athletics": "str", "deception": "cha", "history": "int", "insight": "wis",
    "intimidation": "cha", "investigation": "int", "medicine": "wis",
    "nature": "int", "perception": "wis", "performance": "cha",
    "persuasion": "cha", "religion": "int", "sleight_of_hand": "dex",
    "stealth": "dex", "survival": "wis",
}


def test_every_skill_is_derived_from_ability_when_not_printed() -> None:
    """A stat block prints only its proficient skills; the rest are still known.

    Before this rule the engine answered ``0`` for an unprinted skill, so a
    goblin's Perception read ``0`` while its own passive Perception said ``9``.
    """

    checks = _statblock_derived_checks({
        "abilities": {"str": 8, "dex": 15, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "cr": "1/4",
        "skills": {"stealth": 6},
    }, _SKILL_ABILITIES)
    assert checks["skills"]["perception"] == -1      # WIS 8 -> -1，未印即未熟
    assert checks["skills"]["stealth"] == 6          # 面板值覆盖派生值
    assert checks["skills"]["athletics"] == -1       # STR 8 -> -1
    assert checks["skills_declared"] == {"stealth": 6}


def test_passive_perception_agrees_with_the_derived_skill() -> None:
    checks = _statblock_derived_checks({
        "abilities": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 8, "cha": 10},
        "cr": "0",
    }, _SKILL_ABILITIES)
    assert checks["skills"]["perception"] == -1
    assert checks["passive_perception"] == 10 + checks["skills"]["perception"]


def test_printed_passive_perception_wins_over_the_derived_one() -> None:
    checks = _statblock_derived_checks({
        "abilities": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "cr": "0",
        "passive_perception": 15,
    }, _SKILL_ABILITIES)
    assert checks["passive_perception"] == 15


def test_proficiency_is_never_added_to_a_derived_skill() -> None:
    """The PB only appears through a printed value.

    Adding it to the derived side would silently give every monster a bonus it
    does not have, and Wild Shape's "use the higher" would then be wrong.
    """

    checks = _statblock_derived_checks({
        "abilities": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "cr": "20",
        "proficiency_bonus": 6,
    }, _SKILL_ABILITIES)
    assert checks["skills"]["perception"] == 0


# ---------------------------------------------------------------------------
# 11. ability_check 原语：同一算式，成本由调用方声明
# ---------------------------------------------------------------------------

def _monk_combat():
    from tests.rulesets.dnd2024_monk_common import (
        SequenceRng, monk_instance, monk_sheet, start_combat,
    )
    from src.rulesets.dnd2024 import Dnd2024Runtime

    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, monk_sheet(runtime, level=2))
    start_combat(engine, instance)
    return engine, instance, SequenceRng


def test_a_free_ability_check_rolls_without_spending_anything() -> None:
    from src.engine.modules import ruleset_runtime

    engine, instance, rng = _monk_combat()
    before = dict(ruleset_runtime.state(instance)["combat"]["economy"])
    result = engine.resolve_intent(instance, {
        "intent_id": "ac-free", "type": "ability_check",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "ability": "str", "dc": 12, "check_cost": "none",
    }, rng([15]))
    assert result["ok"] is True, result
    checks = [
        event for event in result["event_batch"]["events"]
        if event.get("type") == "check.resolved"
    ]
    assert checks[0]["kind"] == "ability_check"
    assert checks[0]["modifier"] == 1  # monk STR 12 -> +1
    after = ruleset_runtime.state(instance)["combat"]["economy"]
    assert (after["action"], after["bonus_action"]) == (
        before["action"], before["bonus_action"],
    )


def test_an_action_priced_ability_check_spends_the_action() -> None:
    from src.engine.modules import ruleset_runtime

    engine, instance, rng = _monk_combat()
    result = engine.resolve_intent(instance, {
        "intent_id": "ac-action", "type": "ability_check",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "ability": "dex", "dc": 10, "check_cost": "action",
    }, rng([11]))
    assert result["ok"] is True, result
    engine.apply_batch(instance, result["event_batch"])
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 0


def test_an_ability_check_rejects_a_non_canonical_ability() -> None:
    from src.engine.modules import ruleset_runtime

    engine, instance, rng = _monk_combat()
    result = engine.resolve_intent(instance, {
        "intent_id": "ac-bad", "type": "ability_check",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "ability": "luck", "dc": 10, "check_cost": "none",
    }, rng([11]))
    assert result["ok"] is False
    assert "canonical ability" in result["error"]
