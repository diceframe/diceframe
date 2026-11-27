"""ADR-0015 stage 1: verdicts are codes, and the game language never decides.

The checks here protect three contracts:

1. the same dice facts produce the same code and the same state in every game
   language (only display text may differ);
2. a saved check written before codes existed is reconstructed deterministically
   from the facts the core already persists;
3. the narration consistency gate reads codes and attributes sentences
   structurally, so it works outside Chinese and does not cross-attribute in a
   party.
"""

from __future__ import annotations

import pytest

from src.commands.round_helpers import validate_dice_constraint
from src.engine import check_verdict
from src.engine.dice_rules_coc import coc_success_level
from src.engine.dice_rules_d20 import d20_verdict
from src.engine.intent.lexicon import narration_patterns

LANGUAGES = ("zh-CN", "en", "ja", "de", "ru")


# ---------------------------------------------------------------- the ladder


@pytest.mark.parametrize(
    ("roll", "threshold", "expected"),
    [
        (1, 60, check_verdict.CRITICAL_SUCCESS),
        (100, 60, check_verdict.CRITICAL_FAILURE),
        (96, 40, check_verdict.CRITICAL_FAILURE),  # threshold < 50 fumbles on 96+
        (96, 60, check_verdict.FAILURE),  # and merely fails above it
        (61, 60, check_verdict.FAILURE),
        (60, 60, check_verdict.REGULAR_SUCCESS),
        (30, 60, check_verdict.HARD_SUCCESS),
        (12, 60, check_verdict.EXTREME_SUCCESS),
    ],
)
def test_percentile_ladder_is_language_free(roll: int, threshold: int, expected: str) -> None:
    assert coc_success_level(roll, threshold) == expected


def test_d20_verdict_uses_codes() -> None:
    assert d20_verdict(20, 25, 15) == check_verdict.CRITICAL_SUCCESS
    assert d20_verdict(1, 16, 15) == check_verdict.CRITICAL_FAILURE
    assert d20_verdict(12, 16, 15) == check_verdict.SUCCESS
    assert d20_verdict(12, 14, 15) == check_verdict.FAILURE


def test_required_level_downgrades_without_changing_failures() -> None:
    assert check_verdict.apply_required_level(check_verdict.REGULAR_SUCCESS, "hard") == check_verdict.FAILURE
    assert check_verdict.apply_required_level(check_verdict.HARD_SUCCESS, "hard") == check_verdict.HARD_SUCCESS
    assert check_verdict.apply_required_level(check_verdict.CRITICAL_SUCCESS, "extreme") == check_verdict.CRITICAL_SUCCESS
    assert check_verdict.apply_required_level(check_verdict.CRITICAL_FAILURE, "extreme") == check_verdict.CRITICAL_FAILURE


# ----------------------------------------------------------- display text


def test_every_code_renders_in_every_language() -> None:
    for code in check_verdict.CODES:
        labels = {check_verdict.render(code, language) for language in LANGUAGES}
        assert all(labels), code
        assert code not in labels, f"{code} leaked as raw text"


def test_only_exact_legacy_labels_are_read_back() -> None:
    """The compatibility bridge is exact-match: prose is never interpreted.

    Reading a known label is allowed only through ``src.compat.verdict_labels``
    (pre-ADR-0015 writers); anything else stays unknown rather than guessed.
    """

    from src.compat.verdict_labels import legacy_code

    for code in check_verdict.CODES:
        for language in LANGUAGES:
            label = check_verdict.render(code, language)
            assert legacy_code(label) in (code, "")
    for prose in ("Barely", "你差点就成功了", "Success!", "barely success", "大成功！"):
        assert legacy_code(prose) == "", prose
        assert check_verdict.code_of({"verdict": prose}) == "", prose


def test_dice_facts_beat_a_stale_label_and_a_code_beats_both() -> None:
    """Precedence is explicit: declared code, then dice facts, then label."""

    # Roll 70 against 60 is a failure; a wrong label cannot overrule the roll.
    legacy = {"dice": "d100", "roll": 70, "threshold": 60, "verdict": "大成功"}
    assert check_verdict.code_of(legacy) == check_verdict.FAILURE
    assert check_verdict.code_of({**legacy, "verdict_code": "critical_success"}) == check_verdict.CRITICAL_SUCCESS
    # Without dice facts the exact legacy label is the only evidence left.
    assert check_verdict.code_of({"verdict": "大成功"}) == check_verdict.CRITICAL_SUCCESS


# ------------------------------------------- reconstructing pre-code saves


def test_code_is_derived_from_persisted_d100_facts() -> None:
    legacy = {"dice": "d100", "roll": 12, "threshold": 60, "verdict": "极难成功"}
    assert check_verdict.code_of(legacy) == check_verdict.EXTREME_SUCCESS


def test_derivation_applies_the_saved_difficulty() -> None:
    legacy = {
        "dice": "d100", "roll": 45, "threshold": 60, "difficulty": "hard",
        "required_threshold": 30, "verdict": "失败",
    }
    assert check_verdict.code_of(legacy) == check_verdict.FAILURE
    # The roll itself succeeded; only the required level failed it.
    assert check_verdict.percentile_code(45, 60) == check_verdict.REGULAR_SUCCESS


def test_derivation_honours_a_lost_opposed_roll() -> None:
    legacy = {
        "dice": "d100", "roll": 30, "threshold": 60,
        "opposed": {"outcome": "lose"}, "verdict": "失败",
    }
    assert check_verdict.code_of(legacy) == check_verdict.FAILURE


def test_derivation_honours_a_spent_luck_upgrade() -> None:
    legacy = {"dice": "d100", "roll": 70, "threshold": 60, "luck_spent": 10, "verdict": "成功"}
    assert check_verdict.code_of(legacy) == check_verdict.SUCCESS


def test_derivation_uses_d20_facts() -> None:
    assert check_verdict.code_of({"dice": "d20", "roll": 18, "total": 21, "dc": 15}) == check_verdict.SUCCESS
    assert check_verdict.code_of({"dice": "d20", "roll": 18, "total": 21, "dc": 25}) == check_verdict.FAILURE
    assert check_verdict.code_of(
        {"dice": "d20", "roll": 20, "total": 25, "dc": 15, "is_critical": True},
    ) == check_verdict.CRITICAL_SUCCESS


def test_runtime_checks_are_never_guessed() -> None:
    """A ruleset's own wording is not parsed into a code."""

    runtime = {
        "dice": "runtime", "planner_source": "runtime",
        "roll": 3, "threshold": 50, "verdict": "Barely",
    }
    assert check_verdict.code_of(runtime) == ""


def test_declared_code_wins_over_derivation() -> None:
    assert check_verdict.code_of(
        {"dice": "d100", "roll": 70, "threshold": 60, "verdict_code": "success"},
    ) == check_verdict.SUCCESS


# ------------------------------------------------- narration consistency


def _critical_check(**overrides: object) -> dict:
    check = {
        "check_id": "c1",
        "actor_uid": "p1",
        "actor_ref": "player:p1",
        "actor_name": "Aster",
        "dice": "d100",
        "roll": 1,
        "threshold": 60,
        "verdict_code": check_verdict.CRITICAL_SUCCESS,
        "verdict": "大成功",
    }
    check.update(overrides)
    return check


@pytest.mark.parametrize("language", LANGUAGES)
def test_narration_patterns_exist_for_every_supported_language(language: str) -> None:
    patterns = narration_patterns(language)
    assert patterns["second_person"]
    assert patterns["failure_words"]


def test_contradicting_narration_is_rejected_in_every_language() -> None:
    narration = {
        "zh-CN": "你的攻击落空了，剑锋只是擦过甲片。",
        "en": "You miss badly, the blade scraping only armour.",
        "ja": "あなたの攻撃は外れ、剣は甲冑をかすめただけだ。",
        "de": "Du verfehlst dein Ziel, die Klinge streift nur die Rüstung.",
        "ru": "Ты промахнулся, клинок лишь скользнул по броне.",
    }
    for language in LANGUAGES:
        assert validate_dice_constraint(
            [_critical_check()], narration[language], language=language,
        ) is False, language


def test_consistent_narration_passes_in_every_language() -> None:
    narration = {
        "zh-CN": "你的剑精准命中，敌人踉跄后退。",
        "en": "You strike true and the enemy staggers back.",
        "ja": "あなたの剣は正確に命中し、敵はよろめいた。",
        "de": "Du triffst, und der Feind taumelt zurück.",
        "ru": "Ты попадаешь точно, и враг отшатывается.",
    }
    for language in LANGUAGES:
        assert validate_dice_constraint(
            [_critical_check()], narration[language], language=language,
        ) is True, language


def test_parties_are_attributed_by_name_not_by_pronoun() -> None:
    """A pronoun must not attribute a sentence to every seat in a party."""

    checks = [
        _critical_check(actor_uid="p1", actor_ref="player:p1", actor_name="Aster"),
        _critical_check(
            check_id="c2", actor_uid="p2", actor_ref="player:p2", actor_name="Bram",
            verdict_code=check_verdict.CRITICAL_FAILURE, verdict="大失败",
        ),
    ]
    # No name, so nothing is attributed: silence is safer than cross-attributing.
    assert validate_dice_constraint(
        checks, "You stumble and your strike goes wide.", language="en",
    ) is True
    # Named attribution still works, and Bram's fumble cannot claim a hit.
    assert validate_dice_constraint(
        checks, "Aster lands a clean blow. Bram staggers but still hits the mark.", language="en",
    ) is False


def test_unknown_verdicts_are_ignored_rather_than_guessed() -> None:
    runtime = {"actor_uid": "p1", "actor_name": "Aster", "planner_source": "runtime", "verdict": "Barely"}
    assert validate_dice_constraint([runtime], "你落空了。", language="zh-CN") is True


def test_missing_language_resource_skips_the_soft_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.commands.round_helpers.narration_patterns", lambda _language: {})
    assert validate_dice_constraint([_critical_check()], "你落空了。", language="eo") is True

# ------------------------------------------- runtime contract (optional keys)


def test_runtime_may_declare_a_code_or_a_success_flag() -> None:
    from src.rulesets.narrative_checks import _canonical_check

    request = {"check_id": "c1", "actor_uid": "p1"}
    coded = _canonical_check(request, {"roll": 7, "verdict": "Whatever", "verdict_code": "critical_success"})
    assert coded is not None and check_verdict.code_of(coded) == check_verdict.CRITICAL_SUCCESS

    flagged = _canonical_check(request, {"roll": 7, "verdict": "Whatever", "success": False})
    assert flagged is not None and check_verdict.code_of(flagged) == check_verdict.FAILURE

    bare = _canonical_check(request, {"roll": 7, "verdict": "Whatever"})
    assert bare is not None and check_verdict.code_of(bare) == ""


# ------------------------------------------------ rank is not success/failure


def test_a_fumble_never_opens_luck_or_push() -> None:
    """Luck rescues a plain failure only; a fumble stays a fumble."""

    assert check_verdict.is_failure(check_verdict.CRITICAL_FAILURE) is True
    assert check_verdict.is_failure(check_verdict.FAILURE) is True
    assert check_verdict.code_of({"verdict_code": "critical_failure"}) != check_verdict.FAILURE
    assert check_verdict.code_of({"verdict_code": "failure"}) == check_verdict.FAILURE
