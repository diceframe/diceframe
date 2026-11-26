"""S0-F6a: the Short Rest recovery budgets.

Three features share one shape -- "when you finish a Short Rest, choose what to
take back" -- and none of them is a free refill:

* Arcane Recovery (Wizard 1) spends a combined-level budget of
  ``ceil(level / 2)`` across expended slots of level 1-5;
* Natural Recovery (Circle of the Land 6) does the same with the Druid level,
  and exists only for that subclass;
* Sorcerous Restoration (Sorcerer 5) restores up to ``floor(level / 2)``
  Sorcery Points, never more than are actually spent.

All three are once per Long Rest, and a submission that recovers nothing must
not burn the day's use.  The budgets come from the rest catalog, so the numbers
asserted here are the content's, not a second table in this file.

The item numbers in the docstrings are the task spec's matrix.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine, RestError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.casting import (
    apply_free_cast_cost,
    free_cast_resource,
    spell_cost_events,
)
from src.rulesets.dnd2024.spells.font_of_magic import (
    FontOfMagicError,
    apply_create_slot,
    apply_slot_to_points,
    create_slot_plan,
    parse_created_slot_costs,
    slot_to_points_plan,
)

from test_dnd2024_free_casting import _sheet as _wizard_sheet
from test_dnd2024_spell_grants import _sheet as _class_sheet

LOCALE = "en"


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def engine(runtime: Dnd2024Runtime) -> Dnd2024RestEngine:
    return Dnd2024RestEngine(runtime.load_bundle(LOCALE))


@pytest.fixture(scope="module")
def resolver(runtime: Dnd2024Runtime) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))


def _wizard(runtime: Dnd2024Runtime, level: int) -> dict:
    return _wizard_sheet(runtime, "class:wizard", level)


def _druid(runtime: Dnd2024Runtime, level: int) -> dict:
    return _class_sheet(runtime, "class:druid", level)


def _sorcerer(runtime: Dnd2024Runtime, level: int) -> dict:
    return _class_sheet(runtime, "class:sorcerer", level)


def _canonical(payload: dict) -> dict:
    nested = payload.get("ruleset_character")
    return nested if isinstance(nested, dict) else payload


def _spend_slots(sheet: dict, spent: dict[int, int]) -> dict:
    """Mark ``spent`` slots expended; keys are slot levels."""

    out = deepcopy(sheet)
    current = _canonical(out)["spellcasting"]["class"]["slots_current"]
    for level, count in spent.items():
        current[str(level)] = int(current[str(level)]) - count
    return out


def _spend_points(sheet: dict, points: int) -> dict:
    out = deepcopy(sheet)
    pool = _canonical(out)["resources"]["class"]["sorcery_points"]
    pool["current"] = int(pool["current"]) - points
    return out


def _slots(payload: dict) -> dict[str, int]:
    return dict(_canonical(payload)["spellcasting"]["class"]["slots_current"])


def _uses(payload: dict, resource_id: str) -> int:
    return int(_canonical(payload)["resources"]["class"][resource_id]["current"])


def _points(payload: dict) -> int:
    return int(_canonical(payload)["resources"]["class"]["sorcery_points"]["current"])


def _recovered(events: list[dict]) -> list[dict]:
    return [event for event in events if event["type"] == "recover_spell_slots"]


# --------------------------------------------------------------------------
# matrix 1: the features exist where the class tables say they do
# --------------------------------------------------------------------------


def test_the_recovery_features_are_granted_at_their_level(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    assert resolver.has_feature(_canonical(_wizard(runtime, 1)), "arcane_recovery")
    assert not resolver.has_feature(_canonical(_druid(runtime, 5)), "natural_recovery")
    assert resolver.has_feature(
        _canonical(_druid(runtime, 6)), "natural_recovery",
        owner="subclass:circle_of_the_land",
    )
    assert not resolver.has_feature(
        _canonical(_sorcerer(runtime, 4)), "sorcerous_restoration",
    )
    assert resolver.has_feature(
        _canonical(_sorcerer(runtime, 5)), "sorcerous_restoration",
    )


# --------------------------------------------------------------------------
# matrix 2-3: the Arcane budget
# --------------------------------------------------------------------------


def test_the_arcane_budget_is_half_the_wizard_level_rounded_up(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine,
) -> None:
    budgets = {
        level: engine.rest_recovery_options(_wizard(runtime, level))[0]["budget"]
        for level in (1, 2, 3, 4, 5)
    }
    assert budgets == {1: 1, 2: 1, 3: 2, 4: 2, 5: 3}


def test_arcane_recovery_takes_one_high_slot_or_several_low_ones(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    spent = {1: 2, 2: 2, 3: 1}
    before = _slots(_spend_slots(_wizard(runtime, 5), spent))

    one_high = engine.complete_short_rest(
        _spend_slots(_wizard(runtime, 5), spent),
        recovery={"arcane_recovery": {"slots": {"3": 1}}},
    )
    assert _slots(one_high["character"])["3"] == before["3"] + 1
    assert [event["slots"] for event in _recovered(one_high["events"])] == [{"3": 1}]

    many_low = engine.complete_short_rest(
        _spend_slots(_wizard(runtime, 5), spent),
        recovery={"arcane_recovery": {"slots": {"1": 1, "2": 1}}},
    )
    after = _slots(many_low["character"])
    assert after["1"] == before["1"] + 1
    assert after["2"] == before["2"] + 1
    assert after["3"] == before["3"]


def test_an_overspent_arcane_budget_is_refused(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    sheet = _spend_slots(_wizard(runtime, 5), {3: 2, 2: 1})
    with pytest.raises(RestError, match="budget is 3 combined levels, 6 requested"):
        engine.complete_short_rest(sheet, recovery={"arcane_recovery": {"slots": {"3": 2}}})


def test_arcane_recovery_refuses_level_six_and_unexpended_slots(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    # 11 级法师有 6 环槽：SRD 把 6 环以上明确排除在恢复预算之外。
    six = _spend_slots(_wizard(runtime, 11), {6: 1})
    with pytest.raises(RestError, match="level 1 to 5"):
        engine.complete_short_rest(six, recovery={"arcane_recovery": {"slots": {"6": 1}}})

    with pytest.raises(RestError, match="more level 1 slots than are expended"):
        engine.complete_short_rest(
            _wizard(runtime, 5), recovery={"arcane_recovery": {"slots": {"1": 1}}},
        )


def test_an_unknown_recovery_key_is_refused(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    sheet = _spend_slots(_wizard(runtime, 5), {1: 2})
    with pytest.raises(RestError, match="does not accept luck"):
        engine.complete_short_rest(
            sheet, recovery={"arcane_recovery": {"slots": {"1": 1}, "luck": 1}},
        )
    with pytest.raises(RestError, match="not a short-rest recovery option"):
        engine.complete_short_rest(
            _wizard(runtime, 5), recovery={"wild_shape": {"slots": {"1": 1}}},
        )


# --------------------------------------------------------------------------
# matrix 4: once per long rest, and a no-op is not a use
# --------------------------------------------------------------------------


def test_arcane_recovery_is_once_per_long_rest(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    used = engine.complete_short_rest(
        _spend_slots(_wizard(runtime, 5), {1: 3, 2: 2, 3: 1}),
        recovery={"arcane_recovery": {"slots": {"1": 1}}},
    )["character"]
    assert _uses(used, "arcane_recovery") == 0

    with pytest.raises(RestError, match="cannot be used again until the next long rest"):
        engine.complete_short_rest(used, recovery={"arcane_recovery": {"slots": {"1": 1}}})

    slept = engine.complete_long_rest(used)["character"]
    assert _uses(slept, "arcane_recovery") == 1
    # 长休把槽全部补满了，先真花掉一格再试。
    again = engine.complete_short_rest(
        _spend_slots(slept, {1: 1}), recovery={"arcane_recovery": {"slots": {"1": 1}}},
    )
    assert _uses(again["character"], "arcane_recovery") == 0


def test_a_zero_choice_does_not_burn_the_daily_use(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    result = engine.complete_short_rest(
        _spend_slots(_wizard(runtime, 5), {1: 2}),
        recovery={"arcane_recovery": {"slots": {}}},
    )

    assert _recovered(result["events"]) == []
    assert _uses(result["character"], "arcane_recovery") == 1


# --------------------------------------------------------------------------
# matrix 5: Natural Recovery and its subclass gate
# --------------------------------------------------------------------------


def test_natural_recovery_uses_the_druid_budget(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    # ``ceil(level / 2)``：6 级 3、7 级 4。任务书矩阵 5 写"6/7 均预算 3"，
    # 与它自己引用的原文（round up）矛盾，按原文实现（见该文件 §10 偏差）。
    for level, budget in ((6, 3), (7, 4)):
        rows = engine.rest_recovery_options(_druid(runtime, level))
        assert [row["resource_id"] for row in rows] == ["natural_recovery"]
        assert rows[0]["budget"] == budget

    sheet = _spend_slots(_druid(runtime, 6), {1: 2, 3: 1})
    before = _slots(sheet)
    result = engine.complete_short_rest(
        sheet, recovery={"natural_recovery": {"slots": {"3": 1}}},
    )
    assert _slots(result["character"])["3"] == before["3"] + 1

    with pytest.raises(RestError, match="budget is 3 combined levels"):
        engine.complete_short_rest(
            _spend_slots(_druid(runtime, 6), {3: 2, 2: 1}),
            recovery={"natural_recovery": {"slots": {"3": 2}}},
        )


def test_a_druid_outside_the_circle_has_no_natural_recovery(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    moon = _druid(runtime, 6)
    _canonical(moon)["features"]["subclass_ref"] = "subclass:circle_of_the_moon"

    pool = engine.sync_resources(moon)["resources"]["class"]
    assert "natural_recovery" not in pool
    assert engine.rest_recovery_options(moon) == []
    with pytest.raises(RestError, match="not a short-rest recovery option"):
        engine.complete_short_rest(
            moon, recovery={"natural_recovery": {"slots": {"1": 1}}},
        )


# --------------------------------------------------------------------------
# matrix 8: Sorcerous Restoration
# --------------------------------------------------------------------------


def test_sorcerous_restoration_restores_half_the_level_rounded_down(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    assert [
        engine.rest_recovery_options(_sorcerer(runtime, level))[0]["budget"]
        for level in (5, 6)
    ] == [2, 3]

    result = engine.complete_short_rest(
        _spend_points(_sorcerer(runtime, 5), 4),
        recovery={"sorcerous_restoration": {"points": 2}},
    )
    assert _points(result["character"]) == 3
    assert [event["type"] for event in result["events"] if "recover" in event["type"]] == [
        "recover_class_points",
    ]


def test_sorcerous_restoration_respects_the_budget_and_the_expended_points(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    with pytest.raises(RestError, match="can regain at most 2 points"):
        engine.complete_short_rest(
            _spend_points(_sorcerer(runtime, 5), 4),
            recovery={"sorcerous_restoration": {"points": 3}},
        )
    with pytest.raises(RestError, match="cannot regain more points than are expended"):
        engine.complete_short_rest(
            _spend_points(_sorcerer(runtime, 5), 1),
            recovery={"sorcerous_restoration": {"points": 2}},
        )


# --------------------------------------------------------------------------
# matrix 22-23: nothing half-written, and the budget follows the level
# --------------------------------------------------------------------------


def test_a_refused_recovery_leaves_the_sheet_untouched(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    sheet = _spend_slots(_wizard(runtime, 5), {1: 2})
    before = deepcopy(sheet)

    with pytest.raises(RestError):
        engine.complete_short_rest(
            sheet, recovery={"arcane_recovery": {"slots": {"1": 3}}},
        )

    assert sheet == before
    assert _uses(sheet, "arcane_recovery") == 1


def test_the_budget_and_the_pool_follow_the_new_level(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine,
) -> None:
    low = engine.rest_recovery_options(_wizard(runtime, 4))[0]
    high = engine.rest_recovery_options(_wizard(runtime, 5))[0]

    assert (low["budget"], high["budget"]) == (2, 3)
    # 5 级多出一个 3 环槽：预算涨了，可恢复的槽也跟着涨。
    assert [row["level"] for row in high["slots"]] == [1, 2, 3]
    assert high["uses_left"] == 1


# --------------------------------------------------------------------------
# the projection and the transport
# --------------------------------------------------------------------------


def test_every_projected_choice_survives_the_commit_check(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    """A row the panel can draw must also pass the planner."""

    sheet = _spend_slots(_wizard(runtime, 5), {1: 2, 2: 2, 3: 1})
    for slot in engine.rest_recovery_options(sheet)[0]["slots"]:
        if not slot["expended"]:
            continue
        engine.validate_short_recovery(sheet, {
            "arcane_recovery": {"slots": {str(slot["level"]): 1}},
        })

    engine.validate_short_recovery(
        _spend_points(_sorcerer(runtime, 5), 3),
        {"sorcerous_restoration": {"points": 2}},
    )


def test_the_runtime_carries_recovery_on_a_short_rest_only(
    runtime: Dnd2024Runtime,
) -> None:
    assert "recovery" in runtime.rest_option_names

    result = runtime.complete_rest(
        None, _spend_slots(_wizard(runtime, 5), {1: 2}), "short",
        recovery={"arcane_recovery": {"slots": {"1": 1}}},
    )
    assert _slots(result["character"])["1"] == 3

    with pytest.raises(ValueError, match="recovery is only valid for a short rest"):
        runtime.complete_rest(None, _wizard(runtime, 5), "long", recovery={})
    with pytest.raises(ValueError, match="recovery must be an object"):
        runtime.complete_rest(None, _wizard(runtime, 5), "short", recovery=[("a", 1)])


# --------------------------------------------------------------------------
# matrix 10-13: Magical Cunning, the 1-minute rite
# --------------------------------------------------------------------------


def _warlock(runtime: Dnd2024Runtime, level: int) -> dict:
    return _class_sheet(runtime, "class:warlock", level)


def _pact(payload: dict) -> tuple[str, int, int]:
    """``(slot level key, maximum, current)`` for a Pact Magic caster."""

    class_magic = _canonical(payload)["spellcasting"]["class"]
    assert class_magic["slot_profile"] == "pact"
    (key, maximum), = class_magic["slots_max"].items()
    return str(key), int(maximum), int(class_magic["slots_current"][str(key)])


def _spend_pact(payload: dict, count: int) -> dict:
    out = deepcopy(payload)
    key, _maximum, current = _pact(out)
    _canonical(out)["spellcasting"]["class"]["slots_current"][key] = current - count
    return out


def test_matrix_10_the_rite_reports_the_minute_it_spends(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    sheet = _spend_pact(_warlock(runtime, 5), 2)

    result = engine.complete_ritual(sheet)

    assert result["minutes"] == 1
    assert result["ritual"] == "magical_cunning"
    # 恢复与扣次数是这一笔的两半，顺序固定：先给槽，再花掉今天的一次。
    assert [event["type"] for event in result["events"]] == [
        "recover_pact_slots", "spend_resource_use",
    ]
    assert result["events"][0]["minutes"] == 1


def test_matrix_10_a_rite_with_nothing_to_regain_is_refused(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    full = _warlock(runtime, 5)

    with pytest.raises(RestError, match="no expended Pact Magic slots"):
        engine.complete_ritual(full)
    # 被拒的仪式不花掉次数：每日一次的机会不能这样被烧掉。
    assert _uses(full, "magical_cunning") == 1


def test_matrix_11_the_ceiling_is_half_the_maximum_slots_rounded_up(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    expected = {2: (2, 1), 11: (3, 2), 17: (4, 2)}
    for level, (maximum, regain) in expected.items():
        sheet = _spend_pact(_warlock(runtime, level), maximum)
        row = engine.ritual_options(sheet)[0]
        assert (row["maximum"], row["regain"]) == (maximum, regain), level
        result = engine.complete_ritual(sheet)
        key, _maximum, current = _pact(result["character"])
        assert current == regain
        assert result["events"][0]["slots"] == {key: regain}

    # 一槽的情形只在公式里出现（Magical Cunning 从 2 级起，2 级已有 2 槽）。
    assert Dnd2024RestEngine._ritual_regain(
        {"budget": "half_max_round_up"}, 1, 0, False,
    ) == 1


def test_matrix_12_the_rite_is_once_per_long_rest(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    sheet = _spend_pact(_warlock(runtime, 11), 3)

    once = engine.complete_ritual(sheet)
    assert _uses(once["character"], "magical_cunning") == 0

    with pytest.raises(RestError, match="until the next long rest"):
        engine.complete_ritual(_spend_pact(once["character"], 1))

    # 长休把这一天的一次还回来，仪式因此可以再来一遍（长休也已补满槽位）。
    slept = engine.complete_long_rest(_spend_pact(once["character"], 1))
    assert _uses(slept["character"], "magical_cunning") == 1
    assert engine.complete_ritual(_spend_pact(slept["character"], 2))["minutes"] == 1


def test_matrix_12_a_sheet_with_a_second_slot_row_is_refused(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    """Pact Magic keeps exactly one slot row; anything else is not guessed at."""

    sheet = _spend_pact(_warlock(runtime, 5), 2)
    sheet["ruleset_character"]["spellcasting"]["class"]["slots_max"]["1"] = 1

    assert engine.ritual_options(sheet) == []
    with pytest.raises(RestError, match="requires Pact Magic spell slots"):
        engine.complete_ritual(sheet)


def test_matrix_13_eldritch_master_replaces_the_ceiling(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    assert resolver.has_feature(_canonical(_warlock(runtime, 20)), "eldritch_master")
    assert not resolver.has_feature(_canonical(_warlock(runtime, 19)), "eldritch_master")

    sheet = _spend_pact(_warlock(runtime, 20), 4)
    row = engine.ritual_options(sheet)[0]
    assert row["full_budget"] is True and row["regain"] == 4

    result = engine.complete_ritual(sheet)
    assert _pact(result["character"])[2] == 4
    # 满额恢复仍然是同一次使用，不是额外再来一次。
    assert _uses(result["character"], "magical_cunning") == 0
    assert [event["type"] for event in result["events"]].count("spend_resource_use") == 1


def test_the_runtime_carries_the_rite_on_the_same_sheet(
    runtime: Dnd2024Runtime,
) -> None:
    sheet = _spend_pact(_warlock(runtime, 5), 2)

    rows = runtime.ritual_options(None, sheet)
    assert [row["resource_id"] for row in rows] == ["magical_cunning"]

    result = runtime.complete_ritual(None, sheet)
    assert _pact(result["character"])[2] == 1
    assert result["minutes"] == 1
    # 面板拿到的仍是整张卡，和休息的提交结果同形。
    assert "ruleset_character" in result["character"]


# --------------------------------------------------------------------------
# matrix 14-17: Font of Magic, both directions
# --------------------------------------------------------------------------


def _set_points(sheet: dict, points: int) -> dict:
    out = deepcopy(sheet)
    _canonical(out)["resources"]["class"]["sorcery_points"]["current"] = points
    return out


def _font_slots(payload: dict) -> dict[str, int]:
    return dict(_canonical(payload)["spellcasting"]["class"].get("font_slots") or {})


def _costs(resolver: Dnd2024ClassFeatureResolver) -> tuple[tuple[int, int, int], ...]:
    return parse_created_slot_costs(
        resolver.feature_definition("font_of_magic").parameters["created_slot_costs"]
    )


def test_matrix_14_a_slot_becomes_points_equal_to_its_level(
    runtime: Dnd2024Runtime,
) -> None:
    sheet = _sorcerer(runtime, 11)
    # 1、5、6 环各换一次，点数按环阶而不是按槽的"价值"给。
    for level, gained in ((1, 1), (5, 5), (6, 6)):
        base = _set_points(sheet, 0)
        # 只有一个该环槽时不必先花掉它——把它花掉就没得换了。
        spent = base if _slots(base)[str(level)] == 1 else _spend_slots(base, {level: 1})
        plan = slot_to_points_plan(spent, level)
        assert plan["points"] == gained
        character = _canonical(deepcopy(spent))
        events = apply_slot_to_points(character, plan)
        assert _slots(character)[str(level)] == _slots(spent)[str(level)] - 1
        assert _points(character) == gained
        assert [event["type"] for event in events] == [
            "dnd2024.spell.slot_spent", "dnd2024.class_resource.refunded",
        ]
        # 槽换点不用动作，也不占用本回合的施法位。
        assert events[0]["exchange"] is True
        assert events[1]["amount"] == gained


def test_matrix_15_a_conversion_that_overflows_the_pool_is_refused_whole(
    runtime: Dnd2024Runtime,
) -> None:
    sheet = _sorcerer(runtime, 11)  # 11 点上限，此刻是满的

    with pytest.raises(FontOfMagicError, match="exceed"):
        slot_to_points_plan(sheet, 1)
    # 拒绝发生在计划阶段：源槽与点数都没有动。
    assert _slots(sheet)["1"] == 4
    assert _points(sheet) == 11


def test_matrix_15_a_legal_conversion_moves_both_halves_together(
    runtime: Dnd2024Runtime,
) -> None:
    sheet = _set_points(_spend_slots(_sorcerer(runtime, 9), {2: 1}), 4)

    plan = slot_to_points_plan(sheet, 2)
    character = _canonical(deepcopy(sheet))
    apply_slot_to_points(character, plan)

    assert _slots(character)["2"] == _slots(sheet)["2"] - 1
    assert _points(character) == 6


def test_matrix_16_the_five_rows_and_their_minimum_levels(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    assert _costs(resolver) == (
        (1, 2, 2), (2, 3, 3), (3, 5, 5), (4, 6, 7), (5, 7, 9),
    )
    sheet = _sorcerer(runtime, 9)
    for level, (points, minimum) in {
        1: (2, 2), 2: (3, 3), 3: (5, 5), 4: (6, 7), 5: (7, 9),
    }.items():
        plan = create_slot_plan(_set_points(sheet, 9), level, _costs(resolver), 9)
        assert (plan["points"], plan["before"], plan["after"]) == (points, 9, 9 - points)


def test_matrix_16_an_illegal_level_or_a_short_purse_creates_nothing(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    sheet = _sorcerer(runtime, 9)
    costs = _costs(resolver)

    # 6 环以上造不出来：表里根本没有这一行。
    with pytest.raises(FontOfMagicError, match="cannot create"):
        create_slot_plan(_set_points(sheet, 9), 6, costs, 9)
    # 等级下限是"授予该特性的职业等级"，不是总等级。
    with pytest.raises(FontOfMagicError, match="requires level 7"):
        create_slot_plan(_set_points(sheet, 9), 4, costs, 5)
    # 点不够就是不够，不做部分扣款。
    with pytest.raises(FontOfMagicError, match="not enough"):
        create_slot_plan(_set_points(sheet, 4), 3, costs, 9)


def test_matrix_16_creating_a_slot_spends_points_and_records_its_source(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    sheet = _set_points(_sorcerer(runtime, 9), 9)

    plan = create_slot_plan(sheet, 5, _costs(resolver), 9)
    character = _canonical(deepcopy(sheet))
    events = apply_create_slot(character, plan)

    assert _points(character) == 2
    assert _font_slots(character) == {"5": 1}
    # 临时槽不在普通槽表里：普通槽一个都没多。
    assert _slots(character) == _slots(sheet)
    assert events[0]["type"] == "dnd2024.class_resource.spent"
    assert events[0]["amount"] == 7
    assert events[1] == {
        "type": "dnd2024.spell.font_slot_created",
        "slot_level": 5,
        "source": "font_of_magic",
        "points": 7,
    }


def test_matrix_17_the_bonus_action_belongs_to_the_capability_not_to_the_rules(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    """``apply_create_slot`` must not spend an action a second time."""

    sheet = _set_points(_sorcerer(runtime, 9), 9)
    character = _canonical(deepcopy(sheet))

    events = apply_create_slot(
        character, create_slot_plan(sheet, 1, _costs(resolver), 9),
    )

    assert not [event for event in events if event["type"] == "dnd2024.action.spent"]


def test_matrix_20_a_long_rest_discards_the_created_slots(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    sheet = _spend_points(_spend_slots(_sorcerer(runtime, 9), {1: 2}), 4)
    created = _canonical(deepcopy(sheet))
    apply_create_slot(created, create_slot_plan(sheet, 1, _costs(resolver), 9))
    assert _font_slots(created) == {"1": 1}

    result = engine.complete_long_rest(created)

    # 临时槽消失，普通槽与点数照原规则回满。
    assert _font_slots(result["character"]) == {}
    assert _slots(result["character"]) == _slots(_sorcerer(runtime, 9))
    assert _points(result["character"]) == 9
    discarded = [
        event for event in result["events"] if event["type"] == "discard_font_slots"
    ]
    assert discarded and discarded[0]["slots"] == {"1": 1}


def test_matrix_21_a_second_long_rest_is_idempotent(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    once = engine.complete_long_rest(_sorcerer(runtime, 9))
    twice = engine.complete_long_rest(once["character"])

    assert not [
        event for event in twice["events"] if event["type"] == "discard_font_slots"
    ]
    assert _font_slots(twice["character"]) == {}
    assert _slots(twice["character"]) == _slots(once["character"])


# --------------------------------------------------------------------------
# F6d (matrix 6/7): Natural Recovery's free-cast face
# --------------------------------------------------------------------------


def _land_druid(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    level: int = 6, terrain: str = "arid",
) -> dict:
    """A Circle of the Land druid after a Long Rest on ``terrain``."""

    sheet = _druid(runtime, level)
    result = engine.complete_long_rest(deepcopy(_canonical(sheet)), land_terrain=terrain)
    return result["character"]


def _free_cast_pools(actor: dict, spell_ref: str) -> list[str]:
    return [
        str(row["resource_id"])
        for row in actor["spell_grants"]
        if row.get("spell_ref") == spell_ref and row.get("resource_id")
    ]


def _cast(actor: dict, spell_id: str, level: int, slot_level: int) -> list[dict]:
    return spell_cost_events(actor, {"id": spell_id, "level": level}, slot_level)


def _acting(resolver: Dnd2024ClassFeatureResolver, character: dict) -> dict:
    actor = resolver.actor_projection(character)
    actor["actor_id"] = "druid-1"
    return actor


def _spend_free_cast(character: dict) -> None:
    apply_free_cast_cost(character, {
        "type": "dnd2024.class_resource.spent",
        "resource_id": "natural_recovery_cast",
        "amount": 1,
    })


def test_6a_only_the_land_druid_has_the_free_cast_pool(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    moon = _druid(runtime, 6)
    _canonical(moon)["features"]["subclass_ref"] = "subclass:circle_of_the_moon"
    synced = engine.sync_resources(moon)["resources"]["class"]
    assert "natural_recovery" not in synced
    assert "natural_recovery_cast" not in synced

    # 投影层也要挡：`_resource_definitions` 不看 requires_subclass，月德鲁伊
    # 在 actor 里会拿到 0/0 的幽灵资源。那不是"没显示"，而是"存在但永远为 0"。
    actor = resolver.actor_projection(_canonical(moon))
    ids = {str(row["id"]) for row in actor["class_resources"]}
    assert "natural_recovery" not in ids
    assert "natural_recovery_cast" not in ids
    assert all(int(row["maximum"]) > 0 for row in actor["class_resources"])


def test_the_pool_only_appears_at_the_level_its_feature_grants_it(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    # land 3 级就有地形授予，但免费面的池是 6 级才建的；5 级的授予行不能标池，
    # 否则投影在说"这个法术能被它付账"，而付账时池根本不存在。
    five = _acting(resolver, _land_druid(engine, runtime, level=5))
    six = _acting(resolver, _land_druid(engine, runtime, level=6))

    assert _free_cast_pools(five, "spell:fireball") == []
    assert _free_cast_pools(six, "spell:fireball") == ["natural_recovery_cast"]


def test_6b_a_current_terrain_spell_is_paid_with_the_pool(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    actor = _acting(resolver, _land_druid(engine, runtime, terrain="arid"))

    # fireball 是 arid 5 环授予、德鲁伊 5 级起在表内。同一法术只该有一行授予
    # （池标在原行上），另起一行会让按 spell_ref 读发布的消费者撞上重复。
    assert len([row for row in actor["spell_grants"]
                if row["spell_ref"] == "spell:fireball"]) == 1

    events = _cast(actor, "fireball", 3, 0)

    assert events == [{
        "type": "dnd2024.class_resource.spent",
        "actor_id": "druid-1",
        "resource_id": "natural_recovery_cast",
        "amount": 1,
    }]


def test_6b_a_slotted_cast_still_pays_a_slot(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    actor = _acting(resolver, _land_druid(engine, runtime, terrain="arid"))

    # 免费面只在 `slot_level == 0`（明确"不用槽"）时才是候选；带槽施法
    # 不该顺手把每日一次花掉。
    events = _cast(actor, "fireball", 3, 3)

    assert events[0]["type"] == "dnd2024.spell.slot_spent"
    assert all(event["type"] != "dnd2024.class_resource.spent" for event in events)


def test_a_cantrip_is_never_a_free_cast(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    actor = _acting(resolver, _land_druid(engine, runtime, terrain="arid"))

    # fire_bolt 也在 arid 表里（0 环）。戏法不花槽，"省一个槽"无从谈起，
    # 所以 helper 自己就得拒绝，而不是等调用方先判 level > 0。
    assert free_cast_resource(actor, "spell:fire_bolt", 0) is None


def test_6c_6d_the_pool_runs_out_and_only_covers_grants(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    character = _land_druid(engine, runtime, terrain="arid")

    # 前置：池满时确实可用。没有这一步，"花掉后不可用"在特性压根没实现时也会绿。
    assert free_cast_resource(_acting(resolver, character), "spell:fireball", 3) is not None

    _spend_free_cast(character)
    actor = _acting(resolver, character)

    assert _uses(character, "natural_recovery_cast") == 0
    assert free_cast_resource(actor, "spell:fireball", 3) is None
    with pytest.raises(ValueError, match="free spell cast is not available"):
        _cast(actor, "fireball", 3, 0)

    # 不是本子职授予的法术，即便池子是满的也不能免费。
    full = _acting(resolver, _land_druid(engine, runtime, terrain="arid"))
    assert free_cast_resource(full, "spell:cure_wounds", 1) is None


def test_6e_the_pool_refills_on_a_long_rest_only(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    character = _land_druid(engine, runtime)
    _spend_free_cast(character)

    short = engine.complete_short_rest(deepcopy(character))["character"]
    long = engine.complete_long_rest(deepcopy(character))["character"]

    assert _uses(short, "natural_recovery_cast") == 0
    assert _uses(long, "natural_recovery_cast") == 1
    assert free_cast_resource(
        _acting(resolver, short), "spell:fireball", 3,
    ) is None
    assert free_cast_resource(
        _acting(resolver, long), "spell:fireball", 3,
    ) is not None


def test_6f_spending_the_free_cast_leaves_the_slot_face_alone(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    character = _land_druid(engine, runtime)
    _spend_free_cast(character)
    spent = _spend_slots(character, {1: 2})
    before = _slots(spent)

    # 花掉免费面之后，短休的槽恢复照样可用（两个计数互不影响）。
    result = engine.complete_short_rest(
        spent, recovery={"natural_recovery": {"slots": {"1": 1}}},
    )

    assert _slots(result["character"])["1"] == before["1"] + 1
    assert _uses(result["character"], "natural_recovery") == 0
    assert _uses(result["character"], "natural_recovery_cast") == 0


def test_6f_spending_the_slot_face_leaves_the_free_cast_alone(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    character = _land_druid(engine, runtime)
    rested = engine.complete_short_rest(
        _spend_slots(character, {1: 2}),
        recovery={"natural_recovery": {"slots": {"1": 1}}},
    )["character"]

    # 反序：短休面用掉之后，免费面仍然是满的、也仍然可用。
    assert _uses(rested, "natural_recovery") == 0
    assert _uses(rested, "natural_recovery_cast") == 1
    assert free_cast_resource(
        _acting(resolver, rested), "spell:fireball", 3,
    ) is not None


def test_7_switching_terrain_switches_what_can_be_cast_for_free(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    arid = _land_druid(engine, runtime, terrain="arid")
    polar = engine.complete_long_rest(deepcopy(arid), land_terrain="polar")["character"]

    arid_actor = resolver.actor_projection(arid)
    polar_actor = resolver.actor_projection(polar)

    assert _free_cast_pools(arid_actor, "spell:fireball") == ["natural_recovery_cast"]
    assert _free_cast_pools(polar_actor, "spell:fireball") == []
    assert _free_cast_pools(polar_actor, "spell:fog_cloud") == ["natural_recovery_cast"]
    assert free_cast_resource(arid_actor, "spell:fog_cloud", 1) is None


def test_every_free_cast_pool_is_declared_where_the_feature_says(
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    """内容漂移守卫：`free_cast_resource_id` 是个裸引用，写错就静默失效。

    它跨两张内容表（职业特性 → 休息表），加载期没有外键可查。写成
    `natural_recovery` 更糟：那会把短休恢复面和免费面重新合并，直接毁掉
    "两个计数独立"的契约。所以在这里逐条对一遍，让 CI 先红。
    """

    checked = 0
    for class_id in resolver.resources.by_class:
        declarations = resolver.resources.by_class[class_id]
        for definition in resolver.catalog.class_features(class_id):
            pool_id = str(definition.parameters.get("free_cast_resource_id") or "")
            if not pool_id:
                continue
            checked += 1
            pool = declarations.get(pool_id)
            assert pool is not None, f"{definition.id} names unknown pool {pool_id}"
            # 池的等级**不必**≤声明它的特性：Circle of the Land Spells 是 3 级，
            # 而它指向的免费面是 6 级的 Natural Recovery。等级门禁在投影层
            # （`class_resource_definition` 按 minimum_level 拒绝），由
            # `test_the_pool_only_appears_at_the_level_its_feature_grants_it` 钉住。
            if pool.requires_subclass:
                assert pool.requires_subclass == definition.owner, (
                    f"{definition.id} is owned by {definition.owner} "
                    f"but its pool needs {pool.requires_subclass}"
                )
    assert checked, "no feature declares a free-cast pool"
