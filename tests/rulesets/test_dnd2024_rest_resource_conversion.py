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
