"""The growth ledger records what each level chose, not what it added up to."""

from __future__ import annotations

from src.rulesets.dnd2024.progression.advancement import (
    ADVANCEMENT_RECORDS_KEY,
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


def test_creation_writes_no_ledger() -> None:
    """矩阵 5：1 级的来源已经是 base + background，账本不该重复记一遍。"""

    _runtime, character = _quick_character()

    assert ADVANCEMENT_RECORDS_KEY not in (character.get("progression") or {})


def test_an_empty_level_records_only_its_identity() -> None:
    """矩阵 3：2 级没有可选项，记录里不能出现空的选择字段。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    level_two = engine.apply_next_level(character)

    records = level_two["progression"][ADVANCEMENT_RECORDS_KEY]
    assert records == [{"level": 2, "class_ref": "class:fighter"}]


def test_the_subclass_choice_is_recorded_at_the_level_that_granted_it() -> None:
    """矩阵 1：子职是 3 级的选择，账本必须把它记在 3 级。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    level_two = engine.apply_next_level(character)
    level_three = engine.apply_next_level(
        level_two, {"subclass_ref": "subclass:champion"}
    )

    record = level_three["progression"][ADVANCEMENT_RECORDS_KEY][-1]
    assert record == {
        "level": 3,
        "class_ref": "class:fighter",
        "subclass_ref": "subclass:champion",
    }


def test_a_plain_ability_increase_is_not_recorded_as_a_feat() -> None:
    """矩阵 2 的反面：纯 ASI 走伪专长路径，但账本不能把它写成专长。

    ``feat:ability_score_improvement`` 是为了让「占专长槽」只有一条代码路径
    而造的占位引用。把它记成 ``feat_ref`` 会让缺失项检测去要一个玩家从未
    选过的专长。
    """

    runtime, character = _quick_character()
    engine = _engine(runtime)

    level_two = engine.apply_next_level(character)
    level_three = engine.apply_next_level(
        level_two, {"subclass_ref": "subclass:champion"}
    )
    level_four = engine.apply_next_level(
        level_three, {"ability_score_increases": {"con": 2}}
    )

    record = level_four["progression"][ADVANCEMENT_RECORDS_KEY][-1]
    assert record["level"] == 4
    assert record["ability_score_increase"] == {"con": 2}
    assert "feat_ref" not in record
    assert "epic_boon_ref" not in record


def test_a_half_feat_records_both_its_slot_and_its_ability_point() -> None:
    """矩阵 2：半专长既占专长槽，也给一点属性，两样都要留痕。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    level_two = engine.apply_next_level(character)
    level_three = engine.apply_next_level(
        level_two, {"subclass_ref": "subclass:champion"}
    )
    level_four = engine.apply_next_level(
        level_three,
        {"feat_ref": "feat:grappler", "ability_score_increases": {"str": 1}},
    )

    record = level_four["progression"][ADVANCEMENT_RECORDS_KEY][-1]
    assert record["feat_ref"] == "feat:grappler"
    assert record["ability_score_increase"] == {"str": 1}


def test_the_ledger_agrees_with_the_audit_history() -> None:
    """矩阵 6：账本是权威事实，history 是审计日志，两者不允许漂移。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    level_two = engine.apply_next_level(character)
    level_three = engine.apply_next_level(
        level_two, {"subclass_ref": "subclass:champion"}
    )

    records = level_three["progression"][ADVANCEMENT_RECORDS_KEY]
    history = level_three["progression"]["history"]
    assert [record["level"] for record in records] == [
        entry["to_level"] for entry in history
    ]
    assert records[-1]["subclass_ref"] == history[-1]["choices"]["subclass_ref"]


def test_the_ledger_rejects_a_level_that_does_not_advance() -> None:
    """矩阵 4：账本按等级递增，重复写同一级说明两个写入方读错了当前等级。"""

    runtime, character = _quick_character()
    engine = _engine(runtime)

    level_two = engine.apply_next_level(character)
    progression = level_two["progression"]

    try:
        engine._record_advancement(
            progression,
            level=2,
            class_ref="class:fighter",
            choices={},
            diff={},
        )
    except ProgressionCatalogError as exc:
        assert "one level at a time" in str(exc)
    else:  # pragma: no cover - the assertion above is the contract
        raise AssertionError("a non-advancing ledger append must be refused")

    assert len(progression[ADVANCEMENT_RECORDS_KEY]) == 1
