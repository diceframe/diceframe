"""S0-F19 (part 2): the Hunter swaps its chosen option when it rests.

SRD p61 gives both option groups the same clause -- "Whenever you finish a
Short or Long Rest, you can replace the chosen option with the other one" --
so this is one rest capability, not two features' private paths.  The answer
lives in the ``feature_choices`` slots the sheet already keeps for Expertise,
which is why nothing new is stored here.

The shape follows the Fiend's rest-time resistance choice: plan first, then
commit, so an illegal swap is rejected before HP or slots are restored.
"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine, RestError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from test_dnd2024_live_equipment_reconciliation import _draft

LOCALE = "en"
HUNTER = "subclass:hunter"


def _hunter(runtime: Dnd2024Runtime, level: int = 11) -> dict:
    """A real ranger sheet, levelled by hand.

    The resolver reads ``build.class_levels``, not ``build.level``, so both are
    set: a subclass feature nobody owns is exactly the failure this test file
    is meant to catch, and half a patch would hide it.
    """

    sheet = runtime.finalize_character(None, _draft(runtime, "class:ranger"))
    canonical = sheet["ruleset_character"]
    canonical["build"]["level"] = level
    canonical["build"]["class_levels"] = [{"class_ref": "class:ranger", "level": level}]
    canonical.setdefault("features", {})["subclass_ref"] = HUNTER
    canonical["features"].pop("feature_choices", None)
    return canonical


def _choices(character: dict) -> list[dict]:
    return [dict(row) for row in (character.get("features") or {}).get("feature_choices") or []]


@pytest.fixture()
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture()
def engine(runtime: Dnd2024Runtime) -> Dnd2024RestEngine:
    return Dnd2024RestEngine(runtime.load_bundle(LOCALE))


# ---------------------------------------------------------------------------
# planning: what the commit path will accept
# ---------------------------------------------------------------------------

def test_the_panel_offers_both_groups_with_their_alternatives(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    rows = engine.rest_options(_hunter(runtime))["hunter_options"]
    by_id = {row["feature_id"]: row for row in rows}
    assert set(by_id) == {"hunters_prey", "defensive_tactics"}
    assert by_id["hunters_prey"]["options"] == ["colossus_slayer", "horde_breaker"]
    assert by_id["defensive_tactics"]["options"] == [
        "escape_the_horde", "multiattack_defense",
    ]
    assert by_id["hunters_prey"]["owner"] == HUNTER


def test_a_ranger_without_the_subclass_sees_no_hunter_options(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    canonical = _hunter(runtime, level=5)
    canonical["features"]["subclass_ref"] = "subclass:beast_master"
    assert engine.rest_options(canonical)["hunter_options"] == []


def test_an_absent_payload_keeps_the_current_option(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    assert engine._plan_hunter_options(_hunter(runtime), None) == {}


def test_a_legal_swap_rewrites_the_choice_slot(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    character = _hunter(runtime)
    plan = engine._plan_hunter_options(
        character, {"hunters_prey": "feature:horde_breaker"},
    )
    assert plan == {"hunters_prey": "horde_breaker"}
    events = engine._apply_hunter_options(character, plan)
    assert events == [{
        "type": "rebind_feature_choice", "owner": HUNTER,
        "feature_id": "hunters_prey", "choice_id": "hunters_prey_option",
        "before": None, "after": "feature:horde_breaker",
    }]
    assert _choices(character) == [{
        "owner": HUNTER, "feature_id": "hunters_prey",
        "choice_id": "hunters_prey_option", "selected_refs": ["feature:horde_breaker"],
    }]


def test_swapping_twice_keeps_one_slot_per_group(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    character = _hunter(runtime)
    for choice in ("horde_breaker", "colossus_slayer"):
        plan = engine._plan_hunter_options(character, {"hunters_prey": f"feature:{choice}"})
        engine._apply_hunter_options(character, plan)
    rows = _choices(character)
    assert len(rows) == 1
    assert rows[0]["selected_refs"] == ["feature:colossus_slayer"]


# ---------------------------------------------------------------------------
# rejections
# ---------------------------------------------------------------------------

def test_an_option_from_the_other_group_is_rejected(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    with pytest.raises(RestError):
        engine._plan_hunter_options(
            _hunter(runtime), {"hunters_prey": "feature:escape_the_horde"},
        )


def test_an_unknown_group_is_rejected(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    with pytest.raises(RestError):
        engine._plan_hunter_options(_hunter(runtime), {"sneak_attack": "feature:horde_breaker"})


def test_a_character_without_the_feature_is_rejected(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    canonical = _hunter(runtime, level=5)
    canonical["features"]["subclass_ref"] = "subclass:beast_master"
    with pytest.raises(RestError):
        engine._plan_hunter_options(canonical, {"hunters_prey": "feature:horde_breaker"})


def test_a_non_string_choice_is_rejected(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    with pytest.raises(RestError):
        engine._plan_hunter_options(_hunter(runtime), {"hunters_prey": 3})


# ---------------------------------------------------------------------------
# the rest itself
# ---------------------------------------------------------------------------

def test_a_short_rest_commits_the_swap(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    character = _hunter(runtime)
    result = engine.complete_short_rest(
        character, {}, hunter_options={"hunters_prey": "feature:horde_breaker"},
    )
    committed = result["character"]
    assert _choices(committed) == [{
        "owner": HUNTER, "feature_id": "hunters_prey",
        "choice_id": "hunters_prey_option", "selected_refs": ["feature:horde_breaker"],
    }]
    assert "rebind_feature_choice" in {event["type"] for event in result["events"]}


def test_a_long_rest_can_swap_the_defensive_option_too(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    character = _hunter(runtime)
    result = engine.complete_long_rest(
        character, hunter_options={"defensive_tactics": "feature:multiattack_defense"},
    )
    assert _choices(result["character"]) == [{
        "owner": HUNTER, "feature_id": "defensive_tactics",
        "choice_id": "defensive_tactics_option",
        "selected_refs": ["feature:multiattack_defense"],
    }]


def test_a_rejected_swap_leaves_the_rest_uncommitted(
    engine: Dnd2024RestEngine, runtime: Dnd2024Runtime,
) -> None:
    character = _hunter(runtime)
    with pytest.raises(RestError):
        engine.complete_short_rest(
            character, {}, hunter_options={"hunters_prey": "feature:multiattack_defense"},
        )
