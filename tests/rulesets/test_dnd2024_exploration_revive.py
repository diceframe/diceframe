"""R1: exploration-level revival for the revive spell family.

Revivify is settled end-to-end out of combat (slot + cast event + P9 revival
semantics). The rest of the family must be rejected whole when any declared
semantic is not implemented yet — never partially applied, never narrated.
"""

from __future__ import annotations

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.exploration import Dnd2024ExplorationEngine
from src.rulesets.dnd2024.exploration.revive_effects import (
    REVIVE_SEMANTICS,
    UNSUPPORTED_MESSAGE,
    is_fully_implemented,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from tests.rulesets.test_dnd2024_combat import _character


DEATH_STATE = {"dead", "unconscious", "stable", "death_saves"}
FAMILY = (
    "spell:revivify",
    "spell:raise_dead",
    "spell:reincarnate",
    "spell:resurrection",
    "spell:true_resurrection",
)


class NoRollRng:
    def randint(self, minimum: int, maximum: int) -> int:
        raise AssertionError(f"no dice expected, got randint({minimum}, {maximum})")


def _instance(
    *, slots: dict | None = None, alive_ally: bool = False,
) -> tuple[Dnd2024Runtime, GameInstance, Dnd2024ExplorationEngine]:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-explore-revive", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    caster = _character(runtime, "kindly_bulwark", "Caster")
    instance.players["gm"] = {"character_name": "Caster", "character_sheet": caster}
    class_magic = caster["ruleset_character"]["spellcasting"]["class"]
    prepared = class_magic.setdefault("prepared_spell_refs", [])
    for spell_ref in FAMILY:
        if spell_ref not in prepared:
            prepared.append(spell_ref)
    if slots is not None:
        class_magic["slots_current"] = dict(slots)

    ally = _character(runtime, "stalwart_guardian", "Fallen Ally")
    ally_canonical = ally["ruleset_character"]
    if alive_ally:
        ally_canonical["conditions"] = {}
    else:
        ally_canonical["resources"]["hp"] = 0
        ally_canonical["conditions"] = {
            "dead": {"source": "death_saves"},
            "death_saves": {"successes": 0, "failures": 3},
        }
    instance.players["ally"] = {"character_name": "Fallen Ally", "character_sheet": ally}
    assert instance.bind_ruleset_runtime(caster["rule_binding"])
    return runtime, instance, Dnd2024ExplorationEngine(runtime.load_bundle("en"), locale="en")


def version(instance) -> int:
    return int(ruleset_runtime.state(instance).get("version", 0) or 0)


def cast(engine, instance, spell_ref: str, slot: int, targets) -> dict:
    return engine.resolve_intent(instance, {
        "intent_id": f"exp:{spell_ref}:{slot}",
        "type": "exploration.cast_spell",
        "expected_version": version(instance),
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": spell_ref, "slot_level": slot,
        "target_ids": list(targets),
    }, NoRollRng())


def slots_of(instance) -> dict:
    return (
        instance.players["gm"]["character_sheet"]["ruleset_character"]
        ["spellcasting"]["class"]["slots_current"]
    )


def ally_of(instance) -> dict:
    return instance.players["ally"]["character_sheet"]["ruleset_character"]


def caster_of(instance) -> dict:
    return instance.players["gm"]["character_sheet"]["ruleset_character"]


def spell_intents(engine, instance) -> list[dict]:
    return engine.available_intents(instance, "player:gm")


def event_types(resolved: dict) -> list[str]:
    return [
        event["type"] for event in resolved["events"]
        if event["type"] != "intent.submitted"
    ]


def assert_no_slot_change(instance, before: dict, level: int) -> None:
    assert slots_of(instance).get(str(level)) == before.get(str(level))


# --- #1 revivify out of combat -------------------------------------------


def test_revivify_revives_a_dead_player_out_of_combat() -> None:
    _, instance, engine = _instance(slots={"3": 1})
    resolved = cast(engine, instance, "spell:revivify", 3, ["player:ally"])
    assert resolved["ok"] is True, resolved
    assert event_types(resolved) == [
        "dnd2024.spell.slot_spent",
        "dnd2024.spell.cast",
        "dnd2024.character.revived",
    ]
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True

    ally = ally_of(instance)
    assert ally["resources"]["hp"] == 1
    assert set(ally["conditions"]) & DEATH_STATE == set()
    assert slots_of(instance)["3"] == 0
    assert ruleset_runtime.state(instance)["combat"]["status"] == "none"


# --- #2 dying / living / hostile targets ---------------------------------


def test_dying_ally_is_not_dead_enough() -> None:
    _, instance, engine = _instance(slots={"3": 1})
    ally = ally_of(instance)
    ally["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 2},
    }
    resolved = cast(engine, instance, "spell:revivify", 3, ["player:ally"])
    assert resolved["ok"] is False
    assert resolved["error"] == "revivify requires a dead ally"
    assert_no_slot_change(instance, {"3": 1}, 3)


def test_living_ally_is_rejected() -> None:
    _, instance, engine = _instance(slots={"3": 1}, alive_ally=True)
    resolved = cast(engine, instance, "spell:revivify", 3, ["player:ally"])
    assert resolved["ok"] is False
    assert resolved["error"] == "revivify requires a dead ally"
    assert_no_slot_change(instance, {"3": 1}, 3)


def test_hostile_target_is_rejected() -> None:
    _, instance, engine = _instance(slots={"3": 1})
    resolved = cast(engine, instance, "spell:revivify", 3, ["enemy:goblin-1"])
    assert resolved["ok"] is False
    assert resolved["error"] == "revivify requires a dead ally"
    assert_no_slot_change(instance, {"3": 1}, 3)


# --- #2b exploration casting eligibility -----------------------------------


def test_dead_caster_cannot_revive_itself() -> None:
    _, instance, engine = _instance(slots={"3": 1})
    caster = caster_of(instance)
    caster["resources"]["hp"] = 0
    caster["conditions"] = {
        "dead": {"source": "death_saves"},
        "death_saves": {"successes": 0, "failures": 3},
    }
    resolved = cast(engine, instance, "spell:revivify", 3, ["player:gm"])
    assert resolved["ok"] is False
    assert "cannot cast" in resolved["error"]
    assert "events" not in resolved
    assert_no_slot_change(instance, {"3": 1}, 3)


def test_dead_caster_cannot_revive_another_ally() -> None:
    _, instance, engine = _instance(slots={"3": 1})
    caster = caster_of(instance)
    caster["resources"]["hp"] = 0
    caster["conditions"] = {
        "dead": {"source": "death_saves"},
        "death_saves": {"successes": 0, "failures": 3},
    }
    resolved = cast(engine, instance, "spell:revivify", 3, ["player:ally"])
    assert resolved["ok"] is False
    assert "cannot cast" in resolved["error"]
    assert_no_slot_change(instance, {"3": 1}, 3)


@pytest.mark.parametrize(
    ("condition", "conditions"),
    [
        ("zero_hp_unconscious", {
            "unconscious": {"source": "zero_hp"},
            "death_saves": {"successes": 0, "failures": 2},
        }),
        ("paralyzed", {"paralyzed": {"source": "test"}}),
        ("unconscious", {"unconscious": {"source": "test"}}),
    ],
)
def test_incapacitated_casters_cannot_cast(condition: str, conditions: dict) -> None:
    _, instance, engine = _instance(slots={"3": 1})
    caster = caster_of(instance)
    caster["conditions"] = dict(conditions)
    if condition == "zero_hp_unconscious":
        caster["resources"]["hp"] = 0
    resolved = cast(engine, instance, "spell:revivify", 3, ["player:ally"])
    assert resolved["ok"] is False
    assert "cannot cast" in resolved["error"]
    assert "events" not in resolved
    assert_no_slot_change(instance, {"3": 1}, 3)


@pytest.mark.parametrize(
    ("condition", "conditions"),
    [
        ("dead", {
            "dead": {"source": "death_saves"},
            "death_saves": {"successes": 0, "failures": 3},
        }),
        ("zero_hp_unconscious", {
            "unconscious": {"source": "zero_hp"},
            "death_saves": {"successes": 0, "failures": 2},
        }),
        ("paralyzed", {"paralyzed": {"source": "test"}}),
    ],
)
def test_ineligible_casters_have_no_exploration_spell_intent(
    condition: str, conditions: dict,
) -> None:
    _, instance, engine = _instance(slots={"3": 1})
    caster = caster_of(instance)
    caster["conditions"] = dict(conditions)
    if condition != "paralyzed":
        caster["resources"]["hp"] = 0
    assert spell_intents(engine, instance) == []


def test_normal_and_reaction_blocked_casters_can_still_cast() -> None:
    _, instance, engine = _instance(slots={"3": 1})
    assert spell_intents(engine, instance)
    caster_of(instance)["conditions"] = {"reaction_blocked": {"source": "test"}}
    assert spell_intents(engine, instance)
    resolved = cast(engine, instance, "spell:revivify", 3, ["player:ally"])
    assert resolved["ok"] is True, resolved


# --- #3 no slot -----------------------------------------------------------


def test_missing_slot_is_rejected_without_consuming_one() -> None:
    _, instance, engine = _instance(slots={"3": 0})
    resolved = cast(engine, instance, "spell:revivify", 3, ["player:ally"])
    assert resolved["ok"] is False
    assert resolved["error"] == "the selected spell slot is not available"
    assert_no_slot_change(instance, {"3": 0}, 3)


# --- #4 unimplemented spell forms are rejected whole ---------------------


@pytest.mark.parametrize(
    ("spell_ref", "slot", "slots"),
    [
        ("spell:reincarnate", 5, {"5": 1}),
        ("spell:true_resurrection", 9, {"9": 1}),
    ],
)
def test_reincarnate_and_true_resurrection_are_rejected(
    spell_ref: str, slot: int, slots: dict,
) -> None:
    _, instance, engine = _instance(slots=slots)
    before = dict(slots_of(instance))
    resolved = cast(engine, instance, spell_ref, slot, ["player:ally"])
    assert resolved["ok"] is False
    assert resolved["error"] == UNSUPPORTED_MESSAGE
    assert "events" not in resolved
    assert_no_slot_change(instance, before, slot)


# --- #5 penalty-dependent forms stay rejected until the hooks exist -----


@pytest.mark.parametrize(
    ("spell_ref", "slot", "slots"),
    [
        ("spell:raise_dead", 5, {"5": 1}),
        ("spell:resurrection", 7, {"7": 1}),
    ],
)
def test_penalty_forms_are_rejected_until_hooks_land(
    spell_ref: str, slot: int, slots: dict,
) -> None:
    assert is_fully_implemented(REVIVE_SEMANTICS[spell_ref]) is False
    _, instance, engine = _instance(slots=slots)
    before = dict(slots_of(instance))
    resolved = cast(engine, instance, spell_ref, slot, ["player:ally"])
    assert resolved["ok"] is False
    assert resolved["error"] == UNSUPPORTED_MESSAGE
    assert "events" not in resolved
    assert_no_slot_change(instance, before, slot)


def test_only_revivify_is_fully_implemented() -> None:
    implemented = [
        spell_ref for spell_ref, definition in REVIVE_SEMANTICS.items()
        if is_fully_implemented(definition)
    ]
    assert implemented == ["spell:revivify"]
    assert REVIVE_SEMANTICS["spell:revivify"] == {"hp": 1}
