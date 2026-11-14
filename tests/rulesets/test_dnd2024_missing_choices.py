"""A character that never chose must be told so, and must be able to fix it."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.progression.advancement import (
    Dnd2024AdvancementEngine,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

CHAMPION = "subclass:champion"


def _quick_character(
    preset_id: str = "stalwart_guardian", *, locale: str = "en",
) -> tuple[Dnd2024Runtime, dict]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": locale})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    draft = {**preset["draft"], "locale": locale, "name": "Audit Tester"}
    return runtime, runtime.finalize_character(None, draft)["ruleset_character"]


def _engine(runtime: Dnd2024Runtime) -> Dnd2024AdvancementEngine:
    return Dnd2024AdvancementEngine(runtime.load_bundle("en"))


def _advance_to(
    engine: Dnd2024AdvancementEngine,
    character: dict,
    target: int,
) -> dict:
    """Advance level by level, answering whatever the level asks for.

    A subclass is always answered here: the upgrade transaction refuses to
    skip it, so a card without one cannot be produced this way.  Tests that
    need the gap remove the field afterwards, which is exactly how such a card
    arrives from outside.
    """

    while int(character["build"]["level"]) < target:
        next_level = int(character["build"]["level"]) + 1
        choices: dict = {}
        requirements = {
            row["id"] for row in engine.preview_next_level(character)["requirements"]
        }
        if "subclass_ref" in requirements:
            choices["subclass_ref"] = CHAMPION
        if "ability_score_increases" in requirements:
            choices["ability_score_increases"] = {"con": 2}
        character = engine.apply_next_level(character, choices)
        assert int(character["build"]["level"]) == next_level
    return character


def _without_subclass(character: dict) -> dict:
    damaged = deepcopy(character)
    damaged["features"].pop("subclass_ref", None)
    return damaged


def _ids(missing: list[dict]) -> set[str]:
    return {row["id"] for row in missing}


def _drop_increase(character: dict, level: int) -> dict:
    """Erase one level's ability answer, as a skipped level actually looks."""

    damaged = deepcopy(character)
    for entry in damaged["progression"]["history"]:
        if entry["to_level"] == level:
            entry["choices"].pop("ability_score_increases", None)
            entry["diff"].pop("advancement_feat_ref", None)
    return damaged


def _forged_history(target: int, increases_at: set[int]) -> list[dict]:
    """A contiguous history for 2..target, with the listed levels answered."""

    return [
        {
            "from_level": level - 1,
            "to_level": level,
            "choices": (
                {"ability_score_increases": {"con": 2}}
                if level in increases_at else {}
            ),
            "diff": {},
            "source_ref": "test:fixture",
        }
        for level in range(2, target + 1)
    ]


def test_a_fighter_past_level_three_without_a_subclass_is_reported() -> None:
    """矩阵 7/8：缺口不会因为「这一级没授予子职」而消失。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, character, 5))

    missing = engine.missing_choices(five)
    assert "subclass_ref" in _ids(missing)
    row = next(item for item in missing if item["id"] == "subclass_ref")
    assert row["level"] == 3
    assert row["options"][0]["value"] == CHAMPION

    six = _without_subclass(_advance_to(engine, five, 6))
    assert "subclass_ref" in _ids(engine.missing_choices(six))


def test_a_character_that_already_chose_owes_nothing_for_the_subclass() -> None:
    """矩阵 9：已选过的子职不再出现在缺项里。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)
    five = _advance_to(engine, character, 5)

    assert "subclass_ref" not in _ids(engine.missing_choices(five))


def test_the_upgrade_is_refused_while_the_subclass_is_missing() -> None:
    """矩阵 10：补齐不再是可选项，升级事务本身就不放行。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, character, 5))

    preview = engine.preview_next_level(
        five, {"ability_score_increases": {"con": 2}}
    )

    assert preview["ok"] is False
    assert "subclass_ref is required at this level" in preview["errors"]
    assert "subclass_ref" in {
        row["id"] for row in preview["requirements"]
    }


def test_catching_up_lists_every_feature_the_subclass_already_owed() -> None:
    """矩阵 11：补选时 3 级的子职特性对角色是新的，必须一起登记。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, character, 5))

    preview = engine.preview_next_level(
        five,
        {"subclass_ref": CHAMPION, "ability_score_increases": {"con": 2}},
    )

    assert preview["ok"] is True, preview["errors"]
    gained = set(preview["diff"]["gained_feature_ids"])
    assert {"improved_critical", "remarkable_athlete"}.issubset(gained)


def test_a_chosen_subclass_cannot_be_swapped() -> None:
    """矩阵 12：能补，不能改。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)
    four = _advance_to(engine, character, 4)

    preview = engine.preview_next_level(four, {"subclass_ref": "subclass:lore"})

    assert preview["ok"] is False
    assert "subclass_ref cannot be changed once selected" in preview["errors"]


def test_a_skipped_ability_increase_is_reported_from_the_ledger() -> None:
    """矩阵 13：哪一级没作答，就报哪一级。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)
    five = _advance_to(engine, character, 5)

    damaged = _drop_increase(five, 4)

    missing = engine.missing_choices(damaged)
    asi = next(item for item in missing if item["id"] == "ability_score_increases")
    assert asi["level"] == 4
    assert asi["total"] == 2


def test_the_fighter_extra_ability_levels_are_not_assumed_to_be_four_eight_twelve() -> None:
    """矩阵 14：Fighter 的 6 级 ASI 由目录决定，不写死通用等级表。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)
    six = _advance_to(engine, character, 6)

    damaged = _drop_increase(six, 6)

    asi_levels = {
        item["level"]
        for item in engine.missing_choices(damaged)
        if item["id"] == "ability_score_increases"
    }
    assert asi_levels == {6}


def test_an_old_save_without_a_ledger_is_not_declared_incomplete() -> None:
    """矩阵 17：没有历史就无法区分「漏选」与「字段还不存在」，不能误报。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)
    five = _advance_to(engine, character, 5)

    old = deepcopy(five)
    (old.get("progression") or {}).pop("history", None)

    assert "ability_score_increases" not in _ids(engine.missing_choices(old))


def test_an_old_save_still_owes_its_subclass() -> None:
    """矩阵 18：子职不依赖账本，旧存档一样要补。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, character, 5))

    old = deepcopy(five)
    (old.get("progression") or {}).pop("history", None)

    assert "subclass_ref" in _ids(engine.missing_choices(old))


def test_unfilled_prepared_slots_are_reported() -> None:
    """矩阵 19/20：法术只比对数量，不做法术合法性审计。"""

    runtime, wizard = _quick_character("curious_arcanist")
    engine = _engine(runtime)

    spell_class = wizard["spellcasting"]["class"]
    assert spell_class["prepared_spell_refs"], "fixture must start with prepared spells"

    trimmed = deepcopy(wizard)
    trimmed["spellcasting"]["class"]["prepared_spell_refs"] = (
        trimmed["spellcasting"]["class"]["prepared_spell_refs"][:1]
    )

    missing = engine.missing_choices(trimmed)
    row = next(
        item for item in missing if item["id"] == "prepared_spell_count"
    )
    assert row["actual"] == 1
    assert row["expected"] > row["actual"]

    assert "prepared_spell_count" not in _ids(engine.missing_choices(wizard))


def test_cantrips_are_counted_separately_from_prepared_spells() -> None:
    """矩阵 21：戏法、准备法术、法术书是三个独立容量。"""

    runtime, character = _quick_character("curious_arcanist")
    engine = _engine(runtime)

    damaged = deepcopy(character)
    damaged["spellcasting"]["class"]["cantrip_refs"] = []

    missing = engine.missing_choices(damaged)
    assert "cantrip_count" in _ids(missing)
    assert "prepared_spell_count" not in _ids(missing)


def test_epic_boons_are_audited_at_nineteen() -> None:
    """矩阵 16：19 级的史诗恩惠也是必选项，不是只有 ASI 要查。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)
    eighteen = _advance_to(engine, character, 4)

    forged = deepcopy(eighteen)
    forged["build"]["level"] = 19
    forged["build"]["class_levels"][0]["level"] = 19
    forged["progression"]["level"] = 19
    forged["progression"]["history"] = _forged_history(19, {4, 6, 8, 12, 14, 16})

    missing = engine.missing_choices(forged)
    assert "epic_boon_ref" in _ids(missing)
    assert any(
        item["id"] == "epic_boon_ref" and item["level"] == 19 for item in missing
    )
