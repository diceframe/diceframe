"""The growth ledger is the card's history, read one level at a time.

There is no second store to compare against: every assertion here asks what the
history says a level chose, because that is now the only thing recorded.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.progression.advancement import (
    Dnd2024AdvancementEngine,
    ProgressionCatalogError,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


def _quick_character(
    preset_id: str = "stalwart_guardian",
) -> tuple[Dnd2024Runtime, dict]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    draft = {**preset["draft"], "locale": "en", "name": "Ledger Tester"}
    return runtime, runtime.finalize_character(None, draft)["ruleset_character"]


def _engine(runtime: Dnd2024Runtime) -> Dnd2024AdvancementEngine:
    return Dnd2024AdvancementEngine(runtime.load_bundle("en"))


def _ability_choice(character: dict, requirement: dict) -> dict[str, int]:
    total = int(requirement.get("total", 1) or 1)
    maximum = int(requirement.get("maximum_score", 20) or 20)
    abilities = character.get("abilities") or {}
    for ability in ("con", "wis", "str", "dex", "int", "cha"):
        if int(abilities.get(ability, 0) or 0) + total <= maximum:
            return {ability: total}
    raise AssertionError("no ability score has room for this increase")


def _advance_to(
    engine: Dnd2024AdvancementEngine, character: dict, target: int,
) -> dict:
    """Answer whatever each level asks, so the chain is complete by `target`."""

    while int(character["build"]["level"]) < target:
        requirements = engine.preview_next_level(character)["requirements"]
        choices: dict = {}
        for requirement in requirements:
            requirement_id = requirement["id"]
            if requirement_id == "subclass_ref":
                choices["subclass_ref"] = "subclass:champion"
            elif requirement.get("kind") == "feature_choice":
                # 某一级开始必答的特性选项（冠军 7 级的额外战斗风格）。这里按
                # 需求本身作答，而不是写死哪一级哪个特性：这条链要走到 19 级，
                # 任何一级新增必答槽都不该让夹具失效。
                choices.setdefault("feature_choices", []).append({
                    "owner": requirement["owner"],
                    "feature_id": requirement["feature_id"],
                    "choice_id": requirement["choice_id"],
                    "selected_refs": [
                        option["value"] for option in requirement["options"]
                        if option["available"]
                    ][: int(requirement.get("count", 1) or 1)],
                })
            elif requirement_id in ("feat_ref", "epic_boon_ref"):
                choices[requirement_id] = requirement["options"][0]["value"]
            elif requirement_id == "ability_score_increases":
                choices["ability_score_increases"] = _ability_choice(
                    character, requirement,
                )
        character = engine.apply_next_level(character, choices)
    return character


def test_creation_has_an_empty_ledger() -> None:
    """矩阵 5：1 级没有任何一级的选择可记，但历史存在且为空。"""

    runtime, character = _quick_character()

    assert _engine(runtime)._ledger_entries(character) == []


def test_a_card_without_history_cannot_be_judged() -> None:
    """矩阵 5：``None`` 与 ``[]`` 不同——没有历史不等于没有欠账。"""

    engine = _engine(_quick_character()[0])

    assert engine._ledger_entries({}) is None
    assert engine._ledger_entries({"progression": {"history": "trimmed"}}) is None


def test_each_level_contributes_exactly_one_entry() -> None:
    """矩阵 1：1→5 逐级升级后，账本恰有 2..5 四条且递增。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    five = _advance_to(engine, character, 5)
    entries = engine._ledger_entries(five)

    assert entries is not None
    assert [entry["level"] for entry in entries] == [2, 3, 4, 5]
    assert [entry["class_ref"] for entry in entries] == ["class:fighter"] * 4


def test_an_empty_level_records_only_its_identity() -> None:
    """2 级没有可选项，条目里不能出现空的选择字段。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    two = engine.apply_next_level(character)

    assert engine._ledger_entries(two) == [
        {"level": 2, "class_ref": "class:fighter"},
    ]


def test_the_subclass_choice_is_recorded_at_the_level_that_granted_it() -> None:
    """子职是 3 级的选择，账本必须把它记在 3 级。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    two = engine.apply_next_level(character)
    three = engine.apply_next_level(two, {"subclass_ref": "subclass:champion"})

    entries = engine._ledger_entries(three)
    assert entries is not None
    assert entries[-1] == {
        "level": 3,
        "class_ref": "class:fighter",
        "subclass_ref": "subclass:champion",
    }


def test_a_half_feat_records_both_its_slot_and_its_ability_point() -> None:
    """矩阵 2：半专长既占专长槽，也给一点属性，两样都要留痕。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    two = engine.apply_next_level(character)
    three = engine.apply_next_level(two, {"subclass_ref": "subclass:champion"})
    four = engine.apply_next_level(
        three, {"feat_ref": "feat:grappler", "ability_score_increases": {"str": 1}},
    )

    entries = engine._ledger_entries(four)
    assert entries is not None
    assert entries[-1]["level"] == 4
    assert entries[-1]["feat_ref"] == "feat:grappler"
    assert entries[-1]["ability_score_increase"] == {"str": 1}
    # 派生值与提交的选择逐字段一致，而不是另一套口径。
    assert four["progression"]["history"][-1]["choices"]["feat_ref"] == "feat:grappler"


def test_a_plain_ability_increase_is_not_recorded_as_a_feat() -> None:
    """矩阵 3：纯 ASI 走伪专长路径，但账本不能把它写成专长。

    ``feat:ability_score_improvement`` 是为了让「占专长槽」只有一条代码路径
    而造的占位引用。把它记成 ``feat_ref`` 会让缺失项检测去要一个玩家从未
    选过的专长。排除规则现在只在派生侧，读的人不会与写的口径分家。
    """

    runtime, character = _quick_character()
    engine = _engine(runtime)

    two = engine.apply_next_level(character)
    three = engine.apply_next_level(two, {"subclass_ref": "subclass:champion"})
    four = engine.apply_next_level(three, {"ability_score_increases": {"con": 2}})

    entries = engine._ledger_entries(four)
    assert entries is not None
    assert entries[-1]["ability_score_increase"] == {"con": 2}
    assert "feat_ref" not in entries[-1]
    assert "epic_boon_ref" not in entries[-1]


def test_an_epic_boon_lands_in_its_own_slot() -> None:
    """矩阵 4：19 级的史诗恩惠记 ``epic_boon_ref``，不是 ``feat_ref``。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    nineteen = _advance_to(engine, character, 19)
    entries = engine._ledger_entries(nineteen)

    assert entries is not None
    assert entries[-1]["level"] == 19
    assert "epic_boon_ref" in entries[-1]
    assert "feat_ref" not in entries[-1]


def test_advancing_writes_no_second_ledger() -> None:
    """矩阵 6：每级只有一个写入位置。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    five = _advance_to(engine, character, 5)

    assert "advancement_records" not in five["progression"]
    assert [entry["to_level"] for entry in five["progression"]["history"]] == [
        2, 3, 4, 5,
    ]


def test_a_frozen_ledger_from_an_older_save_changes_nothing() -> None:
    """矩阵 12：G3 留下的 records 字段已冻结，内容错乱也不该影响检测。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    five = _advance_to(engine, character, 5)
    without = deepcopy(five)
    five["progression"]["advancement_records"] = [
        {"level": 2, "class_ref": "class:fighter", "feat_ref": "feat:grappler"},
    ]

    assert engine.missing_choices(five) == engine.missing_choices(without)


def test_the_history_refuses_a_level_that_does_not_advance() -> None:
    """矩阵 7：重复写同一级说明两个写入方读错了当前等级。

    正常路径上 replay 保证连续性，这里防御的是绕过 replay 的直接调用，所以
    夹具故意把「等级」与「已有历史」调成互相矛盾的状态。
    """

    runtime, character = _quick_character()
    engine = _engine(runtime)

    two = engine.apply_next_level(character)
    forged = deepcopy(two)
    forged["build"]["level"] = 1
    forged["build"]["class_levels"][0]["level"] = 1
    forged["progression"]["level"] = 1

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.apply_next_level(forged)

    assert "one level at a time" in str(caught.value)
    assert [entry["to_level"] for entry in forged["progression"]["history"]] == [2]
