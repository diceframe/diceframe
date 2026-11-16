"""A starting level only counts once the server can replay it.

The panel's chain is an editing convenience: it produces a level-N card with
N-1 `history` entries.  What makes that card legal is `normalize_character_submission`,
which throws all of it away, rebuilds a level-1 character from the same
submission and applies the recorded choices level by level.  If the chain ever
drifted from what the engine writes, this replay is where it would surface --
so the test drives the chain the way the panel does and then submits it.
"""

from __future__ import annotations

import copy
import json

import pytest

from src.rules.rule_system import RuleSystem
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_http_common import quick_character

RULE_ID = "test_dnd2024"


def _rule() -> RuleSystem:
    return RuleSystem({
        "rule_id": RULE_ID,
        "runtime": {"id": "core:dnd2024", "minimum_version": 1},
    })


def _chained(runtime: Dnd2024Runtime, rule: RuleSystem, target_level: int) -> dict:
    """Mirror the panel: preview, fill what the level asks, apply, repeat."""
    character = quick_character(runtime, "stalwart_guardian", "Starting Level")
    for _ in range(target_level - 1):
        preview = runtime.preview_advancement(rule, character, {"hp_method": "fixed"})
        choices: dict[str, str] = {"hp_method": "fixed"}
        for requirement in preview["requirements"]:
            if requirement["id"] == "subclass_ref":
                choices["subclass_ref"] = requirement["options"][0]["value"]
        character = runtime.apply_advancement(rule, character, choices)
    return character


def test_a_chained_starting_level_replays_and_owes_nothing() -> None:
    runtime = Dnd2024Runtime()
    rule = _rule()
    submitted = _chained(runtime, rule, 3)
    assert submitted["level"] == 3

    canonical = runtime.normalize_character_submission(rule, submitted, "en")

    assert canonical["level"] == 3
    inner = canonical["ruleset_character"]
    assert inner["build"]["level"] == 3
    assert inner["progression"]["level"] == 3
    # The chain wrote exactly the entries replay expects: one per level, in
    # order, starting at 2.
    assert [entry["to_level"] for entry in inner["progression"]["history"]] == [2, 3]
    # Every level's choices were made in the panel, so the audit after a submit
    # must be silent.  A non-empty list here would be the G3 mismatch the task
    # spec calls a stop condition.
    assert runtime.missing_advancement_choices(rule, canonical) == []


def test_the_submission_path_is_stateless_and_leaves_the_input_alone() -> None:
    runtime = Dnd2024Runtime()
    rule = _rule()
    submitted = _chained(runtime, rule, 3)
    before = json.dumps(submitted, sort_keys=True, default=str)

    first = runtime.normalize_character_submission(rule, submitted, "en")
    second = runtime.normalize_character_submission(rule, submitted, "en")

    # Replay is a pure function of the submission: no revision, no idempotency
    # key, nothing to echo back -- which is why the local chain needs neither.
    assert json.dumps(first, sort_keys=True, default=str) == json.dumps(
        second, sort_keys=True, default=str,
    )
    assert "ruleset_revision" not in first
    assert "operation_id" not in first
    assert json.dumps(submitted, sort_keys=True, default=str) == before


def test_an_imported_three_level_card_without_history_is_still_rejected() -> None:
    runtime = Dnd2024Runtime()
    rule = _rule()
    imported = copy.deepcopy(_chained(runtime, rule, 3))
    imported["ruleset_character"]["progression"].pop("history", None)

    with pytest.raises(ValueError, match="advancement history is incomplete"):
        runtime.normalize_character_submission(rule, imported, "en")
