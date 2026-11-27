from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.commands.dice_resolver import DiceResolver
from src.engine import check_verdict
from src.engine.checks import roll_check_request
from src.engine.dice import check_d100_bonus, coc_success_level, d20_critical_thresholds, d20_verdict, roll
from src.engine.game_instance import GameInstance
from src.rules.rule_system import RuleSystem
from src.engine.modules import checks


def _expected_coc(roll_value: int, threshold: int) -> str:
    if roll_value == 1:
        return check_verdict.CRITICAL_SUCCESS
    if roll_value == 100 or (threshold < 50 and roll_value >= 96):
        return check_verdict.CRITICAL_FAILURE
    if roll_value > threshold:
        return check_verdict.FAILURE
    if roll_value <= threshold // 5:
        return check_verdict.EXTREME_SUCCESS
    if roll_value <= threshold // 2:
        return check_verdict.HARD_SUCCESS
    return check_verdict.REGULAR_SUCCESS


def _expected_d20(
    natural: int,
    total: int,
    dc: int,
    crit_on: int | None,
    fumble_on: int | None,
) -> str:
    if crit_on is not None and natural >= crit_on:
        return check_verdict.CRITICAL_SUCCESS
    if fumble_on is not None and natural <= fumble_on:
        return check_verdict.CRITICAL_FAILURE
    return check_verdict.SUCCESS if total >= dc else check_verdict.FAILURE


def test_coc_success_table_matches_independent_oracle_exhaustively() -> None:
    for threshold in range(1, 100):
        for roll_value in range(1, 101):
            assert coc_success_level(roll_value, threshold) == _expected_coc(roll_value, threshold)


@pytest.mark.parametrize("crit_on,fumble_on", [(20, 1), (19, 2), (None, None)])
def test_d20_verdict_matches_independent_oracle_exhaustively(
    crit_on: int | None,
    fumble_on: int | None,
) -> None:
    for natural in range(1, 21):
        for modifier in range(-5, 11):
            total = natural + modifier
            for dc in range(1, 31):
                assert d20_verdict(
                    natural,
                    total,
                    dc,
                    crit_on=crit_on,
                    fumble_on=fumble_on,
                ) == _expected_d20(natural, total, dc, crit_on, fumble_on)


@pytest.mark.parametrize("penalty", [False, True])
def test_coc_bonus_penalty_selection_exhausts_all_two_tens_combinations(
    monkeypatch: pytest.MonkeyPatch,
    penalty: bool,
) -> None:
    current_values: Iterator[int] = iter(())

    def next_roll(_minimum: int, _maximum: int) -> int:
        return next(current_values)

    monkeypatch.setattr("src.engine.dice_rng.random.randint", next_roll)
    for units in range(10):
        for first_tens in range(10):
            for second_tens in range(10):
                current_values = iter((units, first_tens, second_tens))
                result, _verdict = check_d100_bonus(
                    threshold=50,
                    bonus_dice=0 if penalty else 1,
                    penalty_dice=1 if penalty else 0,
                )
                candidates = [
                    100 if tens == 0 and units == 0 else tens * 10 + units
                    for tens in (first_tens, second_tens)
                ]
                expected = max(candidates) if penalty else min(candidates)
                assert result.total == expected


def test_raw_roll_has_no_ruleset_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.engine.dice_rng.random.randint", lambda _a, _b: 20)

    result = roll("d20")

    assert result.natural == 20
    assert result.is_critical is False
    assert result.is_fumble is False


def test_preroll_metadata_uses_custom_d20_critical_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.engine.dice_rng.random.randint", lambda _a, _b: 20)
    strict_rule = RuleSystem({
        "rule_id": "strict_d20",
        "dice_system": "d20",
        "check_mechanic": {
            "dice": "d20",
            "comparison": "roll_plus_modifier_gte_target",
            "critical": {},
        },
    })

    result = roll_check_request({"dice_system": "d20"}, strict_rule)

    assert result["value"] == 20
    assert result["critical"] is False
    assert result["fumble"] is False


@pytest.mark.parametrize("rule_file", ["dnd5e.json", "tests/fixtures/legacy_rules/dnd5e_en.json", "tests/fixtures/legacy_rules/dnd5e_ja.json"])
def test_dnd5e_ability_checks_do_not_auto_succeed_or_fail(
    monkeypatch: pytest.MonkeyPatch,
    rule_file: str,
) -> None:
    rule = RuleSystem.load(Path(rule_file) if "/" in rule_file else Path("templates/rules") / rule_file)
    rolls: Iterator[int] = iter((20, 1))
    monkeypatch.setattr("src.engine.dice_rng.random.randint", lambda _a, _b: next(rolls))

    natural_twenty = roll("d20")
    crit_on, fumble_on = d20_critical_thresholds(rule)
    high_dc_verdict = d20_verdict(
        natural_twenty.natural,
        natural_twenty.natural,
        25,
        crit_on=crit_on,
        fumble_on=fumble_on,
    )
    natural_one = roll("d20")
    low_dc_verdict = d20_verdict(
        natural_one.natural,
        natural_one.natural + 10,
        10,
        crit_on=crit_on,
        fumble_on=fumble_on,
    )

    assert high_dc_verdict == check_verdict.FAILURE
    assert low_dc_verdict == check_verdict.SUCCESS
    assert (crit_on, fumble_on) == (None, None)


@pytest.mark.parametrize(
    "threshold,expected_fumble",
    [(40, True), (80, False)],
)
def test_preroll_coc_fumble_uses_character_threshold(
    monkeypatch: pytest.MonkeyPatch,
    threshold: int,
    expected_fumble: bool,
) -> None:
    monkeypatch.setattr("src.engine.dice_rng.random.randint", lambda _a, _b: 96)

    result = roll_check_request({"dice_system": "d100", "target": threshold})

    assert result["value"] == 96
    assert result["fumble"] is expected_fumble


@pytest.mark.parametrize("rule_file", ["freeform_cyberpunk.json", "freeform_wuxia.json"])
def test_builtin_custom_d20_genres_cap_runaway_dc_and_resolve_normally(rule_file: str) -> None:
    rule = RuleSystem.load(Path("templates/rules") / rule_file)
    attribute = rule.attribute_keys[0]
    instance = GameInstance(("web", "room", "bot"))
    instance.players["p1"] = {
        "character_name": "Tester",
        "character_sheet": {"attributes": {attribute: 16}, "skills": []},
    }
    action = {
        "user_id": "p1",
        "text": "执行高难度行动",
        "check_request": {
            "actor_uid": "p1",
            "dice_system": "d20",
            "attribute": attribute,
            "target": 99,
        },
        "dice_value": 17,
        "dice_rolls": [17],
    }

    DiceResolver().resolve_action_check(instance, action, rule)

    assert checks.last_check(instance)["dc"] == 20
    assert checks.last_check(instance)["total"] == 20
    assert checks.last_check(instance)["verdict_code"] == check_verdict.SUCCESS


@pytest.mark.parametrize(
    "skill_value,expected_verdict",
    [(40, check_verdict.CRITICAL_FAILURE), (80, check_verdict.FAILURE)],
)
def test_custom_d100_resolver_uses_coc7e_fumble_threshold(
    skill_value: int,
    expected_verdict: str,
) -> None:
    rule = RuleSystem({
        "rule_id": "custom_percentile",
        "dice_system": "d100",
        "attributes": [{"key": "focus", "name": "专注"}],
    })
    instance = GameInstance(("web", "room", "bot"))
    instance.players["p1"] = {
        "character_name": "Tester",
        "character_sheet": {
            "attributes": {"focus": 60},
            "skills": [{"name": "调查", "value": skill_value}],
        },
    }
    action = {
        "user_id": "p1",
        "text": "调查现场",
        "check_request": {
            "actor_uid": "p1",
            "dice_system": "d100",
            "attribute": "focus",
            "skill": "调查",
            "target": 99,
        },
        "dice_value": 96,
        "dice_rolls": [96],
    }

    DiceResolver().resolve_action_check(instance, action, rule)

    assert checks.last_check(instance)["threshold"] == skill_value
    assert checks.last_check(instance)["verdict_code"] == expected_verdict


def test_unsupported_custom_dice_system_is_rejected_explicitly() -> None:
    with pytest.raises(ValueError, match="不支持的检定骰制"):
        RuleSystem({"rule_id": "unsupported_pool", "dice_system": "2d6"})
