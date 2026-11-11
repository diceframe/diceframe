"""Repair fills what a character owes; it is not a second upgrade path."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.progression.advancement import (
    ADVANCEMENT_RECORDS_KEY,
    Dnd2024AdvancementEngine,
    ProgressionCatalogError,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.webui.services.ruleset_advancement import (
    CardAdvancementDependencies,
    complete_card_choices,
    pending_card_choices,
)

CHAMPION = "subclass:champion"
DRACONIC = "subclass:draconic_sorcery"
LOCALE = "en"


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _engine(runtime: Dnd2024Runtime) -> Dnd2024AdvancementEngine:
    return Dnd2024AdvancementEngine(runtime.load_bundle(LOCALE))


def _resolver(runtime: Dnd2024Runtime) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))


def _fighter(runtime: Dnd2024Runtime) -> dict:
    choices = runtime.builder_choices(None, {"locale": LOCALE})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    draft = {**preset["draft"], "locale": LOCALE, "name": "Repair Tester"}
    return runtime.finalize_character(None, draft)["ruleset_character"]


def _advance_to(
    engine: Dnd2024AdvancementEngine, character: dict, target: int,
) -> dict:
    while int(character["build"]["level"]) < target:
        requirements = {
            row["id"] for row in engine.preview_next_level(character)["requirements"]
        }
        choices: dict = {}
        if "subclass_ref" in requirements:
            choices["subclass_ref"] = CHAMPION
        if "ability_score_increases" in requirements:
            choices["ability_score_increases"] = {"con": 2}
        character = engine.apply_next_level(character, choices)
    return character


def _without_subclass(character: dict) -> dict:
    damaged = deepcopy(character)
    damaged["features"].pop("subclass_ref", None)
    return damaged


def _missing_ids(engine: Dnd2024AdvancementEngine, character: dict) -> set[str]:
    return {row["id"] for row in engine.missing_choices(character)}


def _sorcerer_draft(runtime: Dnd2024Runtime) -> dict:
    choices = runtime.builder_choices(None, {
        "locale": LOCALE,
        "class_ref": "class:sorcerer",
        "background_ref": "background:acolyte",
        "species_ref": "species:human",
    })
    class_entity = runtime.load_bundle(LOCALE).get("class", "sorcerer")
    draft = {
        "locale": LOCALE,
        "name": "Draconic Hero",
        "level": 1,
        "alignment": "neutral_good",
        "ability_method": "standard_array",
        "base_abilities": {
            "str": 8, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 15,
        },
        "background_ability_bonuses": {"int": 2, "cha": 1},
        "class_ref": "class:sorcerer",
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "class_skill_refs": ["skill:arcana", "skill:deception"],
        "equipment_package_ref": choices["equipment_packages"][0]["ref"],
        "background_equipment_package_ref": choices[
            "background_equipment_packages"
        ][0]["ref"],
        "language_refs": ["language:common", "language:dwarvish", "language:elvish"],
        "class_spell_choices": class_entity["recommended_spell_choices"],
        "feat_choice_answers": {
            "feat:magic_initiate_cleric": {
                "spellcasting_ability": ["wis"],
                "cantrips": ["guidance", "light"],
                "level_1_spell": ["bless"],
            },
        },
    }
    choices = runtime.builder_choices(None, draft)
    if choices["species_sizes"]:
        draft["species_size"] = (
            "medium" if "medium" in choices["species_sizes"]
            else choices["species_sizes"][0]
        )
    if choices["species_skill_count"]:
        draft["species_skill_refs"] = ["skill:perception"]
    if choices["species_feat_count"]:
        draft["species_feat_refs"] = ["feat:alert"]
    if choices["class_tool_count"]:
        draft["class_tool_refs"] = [
            item["ref"]
            for item in choices["class_tools"][: choices["class_tool_count"]]
        ]
    return draft


def test_repairing_the_subclass_clears_the_requirement() -> None:
    """矩阵 27：补完之后，这张卡不再被报告为缺项。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    assert "subclass_ref" in _missing_ids(engine, five)

    repaired = engine.repair_choices(five, {"subclass_ref": CHAMPION})

    assert "subclass_ref" not in _missing_ids(engine, repaired)
    assert repaired["features"]["subclass_ref"] == CHAMPION


def test_repairing_the_subclass_restores_its_features() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    assert _resolver(runtime).has_feature(
        five, "improved_critical", owner=CHAMPION,
    ) is False

    repaired = engine.repair_choices(five, {"subclass_ref": CHAMPION})

    assert _resolver(runtime).has_feature(
        repaired, "improved_critical", owner=CHAMPION,
    ) is True


def test_repair_never_moves_the_level() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))

    repaired = engine.repair_choices(five, {"subclass_ref": CHAMPION})

    assert repaired["build"]["level"] == 5
    assert repaired["build"]["class_levels"][0]["level"] == 5
    assert repaired["progression"]["level"] == 5


def test_repair_refuses_a_subclass_from_another_class() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(five, {"subclass_ref": "subclass:lore"})

    assert "not an SRD subclass for this class" in str(caught.value)


def test_repair_refuses_a_subclass_that_is_not_missing() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(five, {"subclass_ref": CHAMPION})

    assert "is not missing from this character" in str(caught.value)


def test_repairing_an_ability_increase_pays_constitution_back_over_every_level() -> None:
    """矩阵 23：补齐 ASI 不是加一级的量，而是按已有等级回溯。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)

    damaged = deepcopy(five)
    records = damaged["progression"][ADVANCEMENT_RECORDS_KEY]
    damaged["progression"][ADVANCEMENT_RECORDS_KEY] = [
        record for record in records if record["level"] != 4
    ]
    damaged["abilities"]["con"] -= 2
    assert "ability_score_increases" in _missing_ids(engine, damaged)

    hp_before = damaged["resources"]["max_hp"]
    repaired = engine.repair_choices(damaged, {
        "advancements": [
            {"level": 4, "ability_score_increases": {"con": 2}},
        ],
    })

    assert repaired["abilities"]["con"] == five["abilities"]["con"]
    # CON 从 +2 调整值回到 +3，五个已有等级各补 1 点。
    assert repaired["resources"]["max_hp"] - hp_before == 5


def test_repairing_a_subclass_pays_the_declared_maximum_not_one_level() -> None:
    """矩阵 22：子职的 HP 加成是当前等级的绝对值。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    one = runtime.finalize_character(None, _sorcerer_draft(runtime))[
        "ruleset_character"
    ]

    five = deepcopy(one)
    five["build"]["level"] = 5
    five["build"]["class_levels"][0]["level"] = 5
    five["progression"]["level"] = 5
    five["progression"][ADVANCEMENT_RECORDS_KEY] = [
        {"level": 2, "class_ref": "class:sorcerer"},
        {"level": 3, "class_ref": "class:sorcerer"},
        {"level": 4, "class_ref": "class:sorcerer", "ability_score_increase": {"cha": 2}},
        {"level": 5, "class_ref": "class:sorcerer"},
    ]
    assert "subclass_ref" in _missing_ids(engine, five)

    hp_before = five["resources"]["max_hp"]
    repaired = engine.repair_choices(five, {"subclass_ref": DRACONIC})

    declared = _resolver(runtime).feature_max_hp_bonus(repaired)
    assert declared > 0
    assert repaired["resources"]["max_hp"] - hp_before == declared


def test_repair_refuses_an_advancement_the_character_does_not_owe() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(five, {
            "advancements": [
                {"level": 4, "ability_score_increases": {"con": 2}},
            ],
        })

    assert "owes no ability increase" in str(caught.value)


def test_repair_still_requires_a_choice_for_the_level_it_fills() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)
    damaged = deepcopy(five)
    damaged["progression"][ADVANCEMENT_RECORDS_KEY] = [
        record for record in damaged["progression"][ADVANCEMENT_RECORDS_KEY]
        if record["level"] != 4
    ]
    damaged["abilities"]["con"] -= 2

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(damaged, {"advancements": [{"level": 4}]})

    assert "feat_ref is required" in str(caught.value)


def test_repair_writes_the_ledger_entry_it_filled() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))

    repaired = engine.repair_choices(five, {"subclass_ref": CHAMPION})

    records = repaired["progression"][ADVANCEMENT_RECORDS_KEY]
    level_three = next(record for record in records if record["level"] == 3)
    assert level_three["subclass_ref"] == CHAMPION
    assert [record["level"] for record in records] == sorted(
        record["level"] for record in records
    )


# --------------------------------------------------------------------------
# the write contract the HTTP layer sits on
# --------------------------------------------------------------------------

def _card_dependencies(
    cards: list[dict], runtime: Dnd2024Runtime,
) -> CardAdvancementDependencies:
    return CardAdvancementDependencies(
        read_cards=lambda: cards,
        write_cards=lambda updated: cards.__setitem__(slice(None), updated),
        load_rule_by_id=lambda rule_id, language="": SimpleNamespace(
            rule_id=rule_id,
        ),
        runtime_for_card=lambda card: runtime,
    )


def _card(character: dict) -> dict:
    return {
        "id": "card-1",
        "rule_id": "test_dnd2024",
        "language": LOCALE,
        "ruleset_revision": 0,
        "ruleset_character": character,
        "equipment": [],
    }


def test_pending_lists_what_the_card_owes_without_writing() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    cards = [_card(five)]
    dependencies = _card_dependencies(cards, runtime)

    result = pending_card_choices(dependencies, "card-1")

    assert result["ok"] is True
    assert [row["id"] for row in result["missing"]] == ["subclass_ref"]
    assert cards[0]["ruleset_revision"] == 0


def test_completing_a_choice_writes_the_card_and_bumps_the_revision() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    cards = [_card(five)]
    dependencies = _card_dependencies(cards, runtime)

    result = complete_card_choices(dependencies, "card-1", {
        "repairs": {"subclass_ref": CHAMPION},
        "expected_revision": 0,
        "operation_id": "repair-1",
    })

    assert result["ok"] is True, result
    assert result["revision"] == 1
    assert result["duplicate"] is False
    assert cards[0]["ruleset_character"]["features"]["subclass_ref"] == CHAMPION
    assert pending_card_choices(dependencies, "card-1")["missing"] == []


def test_a_retried_repair_does_not_pay_hit_points_twice() -> None:
    """矩阵 25：幂等按 operation_id，不按内容。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    cards = [_card(five)]
    dependencies = _card_dependencies(cards, runtime)

    first = complete_card_choices(dependencies, "card-1", {
        "repairs": {"subclass_ref": CHAMPION},
        "expected_revision": 0,
        "operation_id": "repair-1",
    })
    hp_after_first = first["card"]["ruleset_character"]["resources"]["max_hp"]

    second = complete_card_choices(dependencies, "card-1", {
        "repairs": {"subclass_ref": CHAMPION},
        "expected_revision": 1,
        "operation_id": "repair-1",
    })

    assert second["duplicate"] is True
    assert second["revision"] == 1
    assert second["card"]["ruleset_character"]["resources"]["max_hp"] == hp_after_first


def test_a_stale_revision_is_refused() -> None:
    """矩阵 26：与升级提交共用同一把乐观锁。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    cards = [_card(five)]
    cards[0]["ruleset_revision"] = 3
    dependencies = _card_dependencies(cards, runtime)

    result = complete_card_choices(dependencies, "card-1", {
        "repairs": {"subclass_ref": CHAMPION},
        "expected_revision": 1,
        "operation_id": "repair-1",
    })

    assert result["ok"] is False
    assert result["code"] == "STALE_CHARACTER_REVISION"
    assert cards[0]["ruleset_revision"] == 3
