"""Long-rest option projection: what the panel draws is what the engine commits.

The rest panel asks the server what a long rest requires (Circle of the Land
terrain) and what it may change (Spell Mastery study).  These tests pin that
projection to the same tables the commit path validates from, and pin the
transport that carries the answer back:

* the read is a pure projection -- no rest session, no revision bump;
* only slots that declare a rest replacement appear (Signature Spells does not);
* the runtime forwards ``free_cast`` to the engine on a long rest and refuses it
  on a short one;
* an illegal study fails when the rest is opened, not halfway through.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.engine.game_instance import GameInstance, GameState
from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine, RestError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.webui.services import ruleset_rest
from src.webui.services.ruleset_rest import LiveRulesetRestDependencies

from test_dnd2024_free_casting import (
    LOCALE,
    MASTERY,
    _canonical,
    _sheet,
    _wizard,
)
from test_dnd2024_live_equipment_reconciliation import _draft as _fighter_draft
from test_dnd2024_spell_grants import _druid as _land_druid

TERRAIN_FEATURE = "circle_of_the_land_spells"
TERRAINS = ["arid", "polar", "temperate", "tropical"]


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def engine(runtime: Dnd2024Runtime) -> Dnd2024RestEngine:
    return Dnd2024RestEngine(runtime.load_bundle(LOCALE))


def _slots(options: dict) -> list[str]:
    return [row["slot_id"] for row in options["free_cast"]]


# --------------------------------------------------------------------------
# the projection itself
# --------------------------------------------------------------------------


def test_a_wizard_at_eighteen_sees_both_study_slots(engine: Dnd2024RestEngine, runtime) -> None:
    options = engine.rest_options(_wizard(runtime, 18))
    assert _slots(options) == ["spell_mastery_1", "spell_mastery_2"]
    assert [row["spell_level"] for row in options["free_cast"]] == [1, 2]
    for row in options["free_cast"]:
        assert row["rest_replace"] == 1
        assert row["valid"] is True
        assert row["spell_name"], row
        assert row["options"], row
        assert all(option["level"] == row["spell_level"] for option in row["options"])


def test_every_candidate_is_reachable_at_the_slot_level(engine: Dnd2024RestEngine, runtime) -> None:
    """A candidate the panel can draw must also survive the commit check."""

    character = _wizard(runtime, 18)
    options = engine.rest_options(character)
    for row in options["free_cast"]:
        other = next(
            option["ref"] for option in row["options"] if option["ref"] != row["spell_ref"]
        )
        # Would raise if the projection offered a spell the planner rejects.
        engine.validate_free_cast(character, {row["slot_id"]: other})


def test_signature_spells_are_not_a_rest_choice(engine: Dnd2024RestEngine, runtime) -> None:
    """Spell Mastery prints a study rule; Signature Spells does not."""

    options = engine.rest_options(_wizard(runtime, 20))
    assert _slots(options) == ["spell_mastery_1", "spell_mastery_2"]
    assert all(row["rest_replace"] == 1 for row in options["free_cast"])


def test_arcana_are_not_rest_choices(engine: Dnd2024RestEngine, runtime) -> None:
    options = engine.rest_options(_canonical(_sheet(runtime, "class:warlock", 17)))
    assert options["free_cast"] == []


def test_a_character_without_rest_choices_gets_empty_fields(
    engine: Dnd2024RestEngine, runtime,
) -> None:
    sheet = runtime.finalize_character(None, _fighter_draft(runtime))
    assert engine.rest_options(sheet["ruleset_character"]) == {
        "land_terrain": None, "free_cast": [], "short_recovery": [],
    }


def test_the_land_druid_sees_every_terrain_and_no_choice_yet(
    engine: Dnd2024RestEngine, runtime,
) -> None:
    terrain = engine.rest_options(_land_druid(runtime))["land_terrain"]
    assert terrain is not None
    assert terrain["feature_id"] == TERRAIN_FEATURE
    assert terrain["owner"] == "subclass:circle_of_the_land"
    assert terrain["options"] == TERRAINS
    assert terrain["current"] == ""
    assert terrain["required"] is True


def test_the_stored_terrain_is_reported_back(engine: Dnd2024RestEngine, runtime) -> None:
    terrain = engine.rest_options(_land_druid(runtime, 5, "polar"))["land_terrain"]
    assert terrain["current"] == "polar"


def test_reading_the_options_never_touches_the_sheet(
    engine: Dnd2024RestEngine, runtime,
) -> None:
    character = _land_druid(runtime, 5, "polar")
    before = deepcopy(character)
    engine.rest_options(character)
    assert character == before


# --------------------------------------------------------------------------
# the transport
# --------------------------------------------------------------------------


def test_the_runtime_advertises_both_long_rest_declarations() -> None:
    assert {"land_terrain", "free_cast"} <= set(Dnd2024Runtime.rest_option_names)


def test_the_long_rest_commits_the_study(runtime: Dnd2024Runtime) -> None:
    sheet = _sheet(runtime, "class:wizard", 18, free_cast=MASTERY)
    canonical = _canonical(sheet)
    row = Dnd2024RestEngine(runtime.load_bundle(LOCALE)).rest_options(canonical)["free_cast"][0]
    replacement = next(
        option["ref"] for option in row["options"] if option["ref"] != row["spell_ref"]
    )
    result = runtime.complete_rest(
        None, sheet, "long", free_cast={row["slot_id"]: replacement},
    )
    events = [event for event in result["events"] if event["type"] == "rebind_free_cast"]
    assert events == [{
        **events[0],
        "slot_id": row["slot_id"],
        "before": row["spell_ref"],
        "after": replacement,
    }]
    stored = result["character"]["ruleset_character"]["spellcasting"]["class"]["free_cast_slots"]
    assert stored[row["slot_id"]]["spell_ref"] == replacement


def test_a_short_rest_refuses_the_study(runtime: Dnd2024Runtime) -> None:
    sheet = _sheet(runtime, "class:wizard", 18, free_cast=MASTERY)
    with pytest.raises(ValueError, match="free_cast is only valid for a long rest"):
        runtime.complete_rest(None, sheet, "short", free_cast={"spell_mastery_1": "spell:shield"})


def test_an_illegal_study_fails_before_the_rest_is_opened(
    engine: Dnd2024RestEngine, runtime,
) -> None:
    character = _wizard(runtime, 18)
    with pytest.raises(RestError, match="level"):
        engine.validate_free_cast(character, {"spell_mastery_1": "spell:fireball"})


def test_the_study_budget_is_one_slot_per_rest(engine: Dnd2024RestEngine, runtime) -> None:
    character = _wizard(runtime, 18)
    options = {row["slot_id"]: row for row in engine.rest_options(character)["free_cast"]}
    picks = {
        slot_id: next(
            option["ref"] for option in row["options"] if option["ref"] != row["spell_ref"]
        )
        for slot_id, row in options.items()
    }
    with pytest.raises(RestError, match="at most 1"):
        engine.validate_free_cast(character, picks)


# --------------------------------------------------------------------------
# the read service
# --------------------------------------------------------------------------


class _Registry:
    def __init__(self, runtime: Dnd2024Runtime) -> None:
        self._runtime = runtime

    def resolve(self, template: str) -> Dnd2024Runtime:
        return self._runtime


async def _never_saved(instance: object) -> None:  # pragma: no cover - must not run
    raise AssertionError("reading rest options must not persist anything")


def _live_game(runtime: Dnd2024Runtime, sheet: dict) -> GameInstance:
    instance = GameInstance(("web", "restopts", "bot"))
    instance.state = GameState.ACTIVE_ACTION
    instance.players["p1"] = {"character_name": "Hero", "character_sheet": sheet}
    return instance


def _deps(runtime: Dnd2024Runtime, instance: GameInstance) -> LiveRulesetRestDependencies:
    return LiveRulesetRestDependencies(
        get_instance=lambda key: instance,
        parse_game_key=lambda gk: ("web", gk, "bot"),
        save_instance=_never_saved,
        load_rule_for_game=lambda inst: SimpleNamespace(template="dnd2024"),
        ruleset_registry=_Registry(runtime),
    )


def test_the_read_service_returns_options_without_starting_a_rest(
    runtime: Dnd2024Runtime,
) -> None:
    sheet = _sheet(runtime, "class:wizard", 18, free_cast=MASTERY)
    instance = _live_game(runtime, sheet)
    result = ruleset_rest.read_options(_deps(runtime, instance), "restopts", "p1")
    assert result["ok"] is True
    assert _slots(result["rest_options"]) == ["spell_mastery_1", "spell_mastery_2"]
    assert result["rest_session"]["status"] == "idle"
    assert instance.players["p1"]["character_sheet"] is sheet


def test_the_read_service_rejects_an_unknown_character(runtime: Dnd2024Runtime) -> None:
    instance = _live_game(runtime, _sheet(runtime, "class:wizard", 18, free_cast=MASTERY))
    result = ruleset_rest.read_options(_deps(runtime, instance), "restopts", "nobody")
    assert result == {
        "ok": False, "code": "CHARACTER_NOT_FOUND", "error": "角色不存在",
    }
