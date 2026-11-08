"""S0-D3: Cutting Words on the attack-check face.

Every assertion goes through the authoritative chain the web layer uses:
``available_intents`` -> ``validate_intent`` -> ``resolve_intent`` ->
``apply_batch``.  The window's own die is rolled at ``decision.resolve`` time,
never folded into the attack's dice, so the straight-through sequence stays
byte identical to the pre-window engine.

Scope: the attack-check face only.  The damage-roll face exists in the shared
window module and is covered by ``test_dnd2024_check_windows.py``; this file
does not claim the ability-check face, which SRD 5.2.1 allows but the engine
does not automate.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.check_windows import CHECK_WINDOW_DECISION
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.models import ClassFeatureError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
    monk_sheet,
)

LOCALE = "en"
LORE_OWNER = "subclass:college_of_lore"
BARD_ABILITIES = {"str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15}
MONK_ABILITIES = {"str": 12, "dex": 14, "con": 13, "int": 8, "wis": 15, "cha": 10}


# --------------------------------------------------------------------------
# sheets: real builder + real advancement path
# --------------------------------------------------------------------------


def _spell_choices(runtime: Dnd2024Runtime, class_ref: str, level: int) -> dict:
    options = Dnd2024SpellSelection(runtime.load_bundle(LOCALE)).options(
        class_ref, level
    )
    requirements = options["requirements"]
    cantrips = [row["ref"] for row in options["cantrips"]]
    leveled = [row["ref"] for row in options["leveled_spells"]]
    prepared = leveled[: int(requirements["prepared_spell_count"])]
    spellbook: list[str] = []
    minimum = int(requirements.get("spellbook_minimum") or 0)
    if minimum:
        spellbook = leveled[:minimum]
        prepared = spellbook[: int(requirements["prepared_spell_count"])]
    return {
        "cantrip_refs": cantrips[: int(requirements["cantrip_count"])],
        "prepared_spell_refs": prepared,
        "spellbook_refs": spellbook,
    }


def _class_sheet(
    runtime: Dnd2024Runtime,
    class_ref: str,
    skills: list[str],
    package: str,
    abilities: dict[str, int],
    level: int,
) -> dict:
    draft = monk_draft(runtime, LOCALE)
    draft.update(
        class_ref=class_ref,
        class_skill_refs=skills,
        class_tool_refs=[],
        equipment_package_ref=package,
        base_abilities=abilities,
    )
    choices = runtime.builder_choices(None, draft)
    draft["class_tool_refs"] = [item["ref"] for item in choices["class_tools"]][
        : choices["class_tool_count"]
    ]
    draft["class_spell_choices"] = _spell_choices(runtime, class_ref, 1)
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        picks["class_spell_choices"] = _spell_choices(runtime, class_ref, next_level)
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def sheets(runtime: Dnd2024Runtime) -> dict[str, dict]:
    return {
        "monk": monk_sheet(runtime),
        "bard": _class_sheet(
            runtime,
            "class:bard",
            ["skill:performance", "skill:persuasion", "skill:stealth"],
            "equipment_package:bard_a",
            BARD_ABILITIES,
            3,
        ),
        "bard2": _class_sheet(
            runtime,
            "class:bard",
            ["skill:performance", "skill:persuasion", "skill:stealth"],
            "equipment_package:bard_a",
            BARD_ABILITIES,
            2,
        ),
    }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _state(instance) -> dict:
    from src.engine.modules import ruleset_runtime

    return ruleset_runtime.state(instance)


def _combat(
    runtime: Dnd2024Runtime,
    players: dict[str, dict],
    *,
    rolls: list[int],
    positions: dict[str, int] | None = None,
    enemy_position: int = 5,
    enemy_hp: int = 60,
) -> tuple[Dnd2024CombatEngine, object]:
    """Start combat with the real ordering and an arbitrary player roster."""

    first_uid = next(iter(players))
    engine, instance = monk_instance(
        runtime, deepcopy(players[first_uid]), uid=first_uid
    )
    for uid, sheet in list(players.items())[1:]:
        instance.players[uid] = {
            "character_name": str(sheet.get("character_name") or "Hero"),
            "character_sheet": deepcopy(sheet),
        }
    intent = {
        "intent_id": "start",
        "type": "combat.start",
        "expected_version": 0,
        "submitted_by": first_uid,
        "enemies": [goblin(position=enemy_position, hp=enemy_hp)],
    }
    if positions is not None:
        intent["player_positions"] = positions
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    _to_enemy_turn(engine, instance)
    return engine, instance


def _to_enemy_turn(engine: Dnd2024CombatEngine, instance, tag: str = "") -> None:
    """Hand the turn to the enemy so an enemy attack is legal."""

    for step in range(12):
        state = _state(instance)
        actor_id = str(state["combat"]["initiative"][state["combat"]["turn_index"]])
        if actor_id.startswith("enemy:"):
            return
        _apply(
            engine,
            instance,
            {
                "intent_id": f"end-{tag}{step}",
                "type": "end_turn",
                "expected_version": state["version"],
                "submitted_by": actor_id.split(":", 1)[1],
                "actor_id": actor_id,
            },
            SequenceRng([]),
        )
    raise AssertionError("the enemy never got a turn")


def _apply(engine: Dnd2024CombatEngine, instance, intent: dict, rng) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _pending(instance) -> list[dict]:
    return list(_state(instance)["combat"].get("pending_decisions") or [])


def _canonical(instance, uid: str) -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _pool(instance, resource_id: str, uid: str) -> dict:
    return (
        _canonical(instance, uid).get("resources", {}).get("class", {}).get(resource_id)
        or {}
    )


def _reaction(instance, uid: str) -> int:
    return int(_state(instance)["combat"]["reactions"].get(f"player:{uid}", 0))


def _next_enemy_turn(engine: Dnd2024CombatEngine, instance, tag: str = "r") -> None:
    """Finish the enemy's current turn and come back around to a fresh one."""

    for step in range(12):
        state = _state(instance)
        actor_id = str(state["combat"]["initiative"][state["combat"]["turn_index"]])
        if step and actor_id.startswith("enemy:"):
            return
        _apply(
            engine,
            instance,
            {
                "intent_id": f"round-{tag}{step}",
                "type": "end_turn",
                "expected_version": state["version"],
                "submitted_by": (
                    str(instance.gm_uid)
                    if actor_id.startswith("enemy:")
                    else actor_id.split(":", 1)[1]
                ),
                "actor_id": actor_id,
            },
            SequenceRng([]),
        )
    raise AssertionError("never reached a fresh enemy turn")


def _enemy_attack(
    engine,
    instance,
    rolls: list[int],
    *,
    intent_id: str = "g1",
    target_id: str = "player:p3",
) -> list[dict]:
    intent = {
        "intent_id": intent_id,
        "type": "attack",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm",
        "actor_id": "enemy:goblin-1",
        "target_id": target_id,
        "attack_id": "scimitar",
    }
    return _apply(engine, instance, intent, SequenceRng(list(rolls)))


def _resolve(
    engine,
    instance,
    decision: dict,
    option: str,
    *,
    by: str | None = None,
    rng=None,
    intent_id: str = "decide-1",
) -> list[dict]:
    return _apply(
        engine,
        instance,
        {
            "intent_id": intent_id,
            "type": "decision.resolve",
            "expected_version": _state(instance)["version"],
            "submitted_by": by or decision["assigned_to"],
            "decision_id": decision["decision_id"],
            "option": option,
        },
        rng or SequenceRng([]),
    )


def _settle_all(engine, instance, *, tag: str = "settle") -> list[dict]:
    """Decline every pending decision until the attack has fully landed."""

    collected: list[dict] = []
    for step in range(10):
        pending = _pending(instance)
        if not pending:
            return collected
        decision = pending[0]
        collected.extend(
            _resolve(
                engine,
                instance,
                decision,
                "decline",
                by=decision["assigned_to"] or "gm",
                intent_id=f"{tag}-{step}",
            )
        )
    raise AssertionError("the attack never settled")


def _checked(events: list[dict], kind: str = "attack") -> dict:
    return next(
        event
        for event in events
        if event.get("type") == "check.resolved" and event.get("kind") == kind
    )


def _resolver(runtime: Dnd2024Runtime) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))


def _three(runtime: Dnd2024Runtime, sheets: dict) -> tuple:
    """Two lore bards and a monk target, so distance is not the bard's own."""

    return _combat(
        runtime,
        {"gm": sheets["bard"], "p2": sheets["bard"], "p3": sheets["monk"]},
        rolls=[20, 1, 2],
        positions={"gm": 5, "p2": 15, "p3": 5},
    )


# --------------------------------------------------------------------------
# 1. declaration and ownership
# --------------------------------------------------------------------------


def test_a_level_three_lore_bard_owns_cutting_words(runtime, sheets) -> None:
    resolver = _resolver(runtime)
    character = sheets["bard"]["ruleset_character"]

    owned = [
        item for item in resolver.features_for(character) if item.id == "cutting_words"
    ]
    assert len(owned) == 1
    assert owned[0].owner == LORE_OWNER
    assert owned[0].minimum_level == 3
    assert resolver.has_feature(character, "cutting_words", owner=LORE_OWNER)
    assert not resolver.has_feature(
        sheets["bard2"]["ruleset_character"],
        "cutting_words",
    )


def test_a_bard_without_a_subclass_ref_does_not_own_cutting_words(
    runtime, sheets
) -> None:
    resolver = _resolver(runtime)
    character = deepcopy(sheets["bard"]["ruleset_character"])
    character["features"].pop("subclass_ref", None)

    assert resolver.has_feature(character, "cutting_words") is False

    engine, instance = _combat(
        runtime,
        {"gm": sheets["monk"], "p2": sheets["bard"]},
        rolls=[20, 1, 2],
        positions={"gm": 0, "p2": 10},
    )
    instance.players["p2"]["character_sheet"]["ruleset_character"]["features"].pop(
        "subclass_ref",
        None,
    )
    events = _enemy_attack(engine, instance, [11], target_id="player:gm")
    assert _pending(instance) == []
    assert [event["type"] for event in events].count("dnd2024.decision.pending") == 0


def test_a_foreign_or_invented_subclass_ref_does_not_grant_cutting_words(
    runtime,
    sheets,
) -> None:
    resolver = _resolver(runtime)
    for fake in ("subclass:college_of_valor", "subclass:cutting_words"):
        character = deepcopy(sheets["bard"]["ruleset_character"])
        character["features"]["subclass_ref"] = fake
        assert resolver.has_feature(character, "cutting_words") is False, fake


def test_a_non_bard_cannot_claim_the_lore_owner(runtime, sheets) -> None:
    resolver = _resolver(runtime)
    character = deepcopy(sheets["monk"]["ruleset_character"])
    character["features"]["subclass_ref"] = LORE_OWNER

    assert resolver.has_feature(character, "cutting_words", owner=LORE_OWNER) is False


def test_the_two_locales_name_the_feature_but_keep_the_identity() -> None:
    for locale, expected in (("en", "Cutting Words"), ("zh-CN", "语出惊人")):
        resolver = Dnd2024ClassFeatureResolver(Dnd2024Runtime().load_bundle(locale))
        assert resolver.catalog.label("cutting_words", "cutting_words") == expected
        definition = next(
            item
            for item in resolver.catalog.class_features("bard")
            if item.id == "cutting_words"
        )
        assert definition.owner == LORE_OWNER
        assert definition.resource_id == "bardic_inspiration"
        assert definition.parameters["trigger"] == "attack_roll_success"
        assert definition.parameters["range_ft"] == 60


def test_the_inspiration_die_track_reads_d6_d8_d10_d12(runtime, sheets) -> None:
    resolver = _resolver(runtime)
    cases = {4: 6, 5: 8, 10: 10, 15: 12}
    for level, sides in cases.items():
        sheet = _class_sheet(
            runtime,
            "class:bard",
            ["skill:performance", "skill:persuasion", "skill:stealth"],
            "equipment_package:bard_a",
            BARD_ABILITIES,
            level,
        )
        character = sheet["ruleset_character"]
        assert resolver.has_feature(character, "cutting_words"), level
        assert (
            resolver.feature_value(
                character,
                "cutting_words",
                "die_sides",
                0,
            )
            == sides
        ), level


@pytest.mark.parametrize(
    "parameters",
    [
        {"range_ft": 0},
        {"range_ft": -5},
        {"range_ft": True},
        {"range_ft": 60.5},
        {"trigger": "damage_roll_success"},
        {"trigger": ""},
    ],
)
def test_a_malformed_declaration_fails_closed_at_load(parameters) -> None:
    bundle = Dnd2024Runtime().load_bundle(LOCALE)
    entities = deepcopy(bundle.entities)
    feature = next(
        item
        for item in entities["class_feature_catalog"]["srd_class_features"]["classes"][
            "bard"
        ]["features"]
        if item["id"] == "cutting_words"
    )
    feature["parameters"].update(parameters)

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(replace(bundle, entities=entities))


def test_the_existing_catalogs_still_load(runtime) -> None:
    resolver = _resolver(runtime)
    assert resolver.available is True
    assert resolver.has_feature(
        monk_sheet(runtime)["ruleset_character"],
        "martial_arts",
    )


# --------------------------------------------------------------------------
# 2. opening, authority, exclusions
# --------------------------------------------------------------------------


def test_a_hit_opens_one_check_window_for_the_bard(runtime, sheets) -> None:
    engine, instance = _three(runtime, sheets)
    events = _enemy_attack(engine, instance, [11])

    decision = _pending(instance)[0]
    assert decision["kind"] == CHECK_WINDOW_DECISION
    assert decision["window_code"] == "cutting_words_check"
    assert decision["window_owner_id"] == "player:gm"
    assert decision["actor_id"] == "enemy:goblin-1"
    assert decision["target_id"] == "player:p3"
    assert decision["options"] == ["spend", "decline"]
    assert decision["resource_id"] == "bardic_inspiration"
    assert decision["die_sides"] == 6
    assert decision["check"]["total"] == 15
    assert decision["check"]["success"] is True
    assert _pending(instance) == [decision]


def test_the_window_precedes_damage_and_keeps_the_original_dice(
    runtime, sheets
) -> None:
    engine, instance = _three(runtime, sheets)
    before_hp = int(_canonical(instance, "p3")["resources"]["hp"])
    events = _enemy_attack(engine, instance, [11])

    types = [event["type"] for event in events]
    assert "dnd2024.decision.pending" in types
    assert "resource.changed" not in types
    assert "dnd2024.victory" not in types
    assert int(_canonical(instance, "p3")["resources"]["hp"]) == before_hp

    # The original check has not been committed yet: its dice live only in the
    # pending decision, and they are the attacker's own rolls.
    decision = _pending(instance)[0]
    assert decision["check"]["natural"] == 11
    assert decision["check"]["modifier"] == 4
    assert decision["check"]["total"] == 15
    assert decision["check"]["target"] == 15
    assert decision["check"]["success"] is True
    assert decision["check"]["critical"] is False


def test_a_miss_or_a_natural_one_opens_no_window(runtime, sheets) -> None:
    for roll in (3, 1):
        engine, instance = _three(runtime, sheets)
        events = _enemy_attack(engine, instance, [roll])
        assert _pending(instance) == [], roll
        resolved = [event for event in events if event.get("type") == "check.resolved"]
        assert resolved and resolved[-1]["success"] is False, roll


def test_no_window_without_a_pool_reaction_hp_or_awareness(runtime, sheets) -> None:
    def _solo(runtime, sheets):
        """One bard as the only candidate: a second bard would mask a failure."""

        return _combat(
            runtime,
            {"gm": sheets["monk"], "p2": sheets["bard"]},
            rolls=[20, 1, 2],
            positions={"gm": 0, "p2": 10},
        )

    # empty pool
    engine, instance = _solo(runtime, sheets)
    _pool(instance, "bardic_inspiration", "p2")["current"] = 0
    _enemy_attack(engine, instance, [11], target_id="player:gm")
    assert _pending(instance) == []

    # no reaction left
    engine, instance = _solo(runtime, sheets)
    _state(instance)["combat"]["reactions"]["player:p2"] = 0
    _enemy_attack(engine, instance, [11], target_id="player:gm")
    assert _pending(instance) == []

    # incapacitated
    engine, instance = _solo(runtime, sheets)
    _canonical(instance, "p2")["conditions"] = {"incapacitated": True}
    _enemy_attack(engine, instance, [11], target_id="player:gm")
    assert _pending(instance) == []

    # blinded bard
    engine, instance = _solo(runtime, sheets)
    _canonical(instance, "p2")["conditions"] = {"blinded": True}
    _enemy_attack(engine, instance, [11], target_id="player:gm")
    assert _pending(instance) == []

    # zero HP
    engine, instance = _solo(runtime, sheets)
    _canonical(instance, "p2")["resources"]["hp"] = 0
    _enemy_attack(engine, instance, [11], target_id="player:gm")
    assert _pending(instance) == []


def test_an_invisible_attacker_cannot_be_seen(runtime, sheets) -> None:
    engine, instance = _three(runtime, sheets)
    _state(instance)["combat"]["enemies"]["goblin-1"]["conditions"] = {
        "invisible": True
    }
    _enemy_attack(engine, instance, [11])
    assert _pending(instance) == []


def test_the_range_is_measured_to_the_attacker_as_declared(runtime, sheets) -> None:
    engine, instance = _combat(
        runtime,
        {"gm": sheets["monk"], "p2": sheets["bard"]},
        rolls=[20, 1, 2],
        positions={"gm": 5, "p2": 65},
    )
    _enemy_attack(engine, instance, [11], target_id="player:gm")
    assert _pending(instance)[0]["window_owner_id"] == "player:p2"

    engine, instance = _combat(
        runtime,
        {"gm": sheets["monk"], "p2": sheets["bard"]},
        rolls=[20, 1, 2],
        positions={"gm": 5, "p2": 66},
    )
    _enemy_attack(engine, instance, [11], target_id="player:gm")
    assert _pending(instance) == []


def test_the_trigger_distance_ignores_who_takes_the_hit(runtime, sheets) -> None:
    engine, instance = _combat(
        runtime,
        {"gm": sheets["monk"], "p2": sheets["bard"]},
        rolls=[20, 1, 2],
        positions={"gm": 5, "p2": 55},
    )
    _enemy_attack(engine, instance, [11], target_id="player:gm")
    decision = _pending(instance)[0]
    assert decision["window_owner_id"] == "player:p2"
    assert decision["target_id"] == "player:gm"


def test_only_the_assigned_actor_resolves_and_other_intents_wait(
    runtime, sheets
) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [11])
    decision = _pending(instance)[0]

    rejected = engine.resolve_intent(
        instance,
        {
            "intent_id": "wrong-owner",
            "type": "decision.resolve",
            "expected_version": _state(instance)["version"],
            "submitted_by": "p2",
            "decision_id": decision["decision_id"],
            "option": "spend",
        },
        SequenceRng([]),
    )
    assert rejected["ok"] is False

    for intent in (
        {"type": "attack", "actor_id": "player:gm", "target_id": "enemy:goblin-1"},
        {"type": "end_turn", "actor_id": "player:gm"},
    ):
        blocked = engine.resolve_intent(
            instance,
            {
                "intent_id": f"blocked-{intent['type']}",
                "expected_version": _state(instance)["version"],
                "submitted_by": "gm",
                **intent,
            },
            SequenceRng([]),
        )
        assert blocked["ok"] is False, intent["type"]

    _apply(
        engine,
        instance,
        {
            "intent_id": "chat",
            "type": "combat.message",
            "expected_version": _state(instance)["version"],
            "submitted_by": "gm",
            "text": "why me",
        },
        SequenceRng([]),
    )


def test_client_supplied_numbers_cannot_change_the_result(runtime, sheets) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [11])
    decision = _pending(instance)[0]

    events = _apply(
        engine,
        instance,
        {
            "intent_id": "forged",
            "type": "decision.resolve",
            "expected_version": _state(instance)["version"],
            "submitted_by": "gm",
            "decision_id": decision["decision_id"],
            "option": "spend",
            "total": 99,
            "damage": 999,
            "rolls": [20],
            "die_sides": 12,
            "attack_context": {"weapon": {"damage": "10d10"}},
        },
        SequenceRng([4]),
    )
    check = _checked(events)
    assert check["total"] == 11
    assert check["check_penalty"] == 4
    assert _pending(instance) == []


def test_a_spend_that_became_illegal_is_rejected_not_treated_as_decline(
    runtime,
    sheets,
) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [11])
    decision = _pending(instance)[0]
    _pool(instance, "bardic_inspiration", "gm")["current"] = 0

    rejected = engine.resolve_intent(
        instance,
        {
            "intent_id": "stale-spend",
            "type": "decision.resolve",
            "expected_version": _state(instance)["version"],
            "submitted_by": "gm",
            "decision_id": decision["decision_id"],
            "option": "spend",
        },
        SequenceRng([]),
    )
    assert rejected["ok"] is False
    assert _pending(instance) == [decision]
    assert _reaction(instance, "gm") == 1


# --------------------------------------------------------------------------
# 3. spend, decline, and the flip
# --------------------------------------------------------------------------


def test_decline_costs_nothing_and_reproduces_the_straight_through_damage(
    runtime,
    sheets,
) -> None:
    def _settle(engine, instance) -> int:
        """Decline every pending decision and return the target's final HP."""

        step = 0
        while _pending(instance):
            step += 1
            pending = _pending(instance)[0]
            _resolve(
                engine,
                instance,
                pending,
                "decline",
                by=pending["assigned_to"] or "gm",
                intent_id=f"settle-{step}",
            )
            assert step < 10
        return int(_canonical(instance, "gm")["resources"]["hp"])

    straight_engine, straight = _combat(
        runtime,
        {"gm": sheets["monk"]},
        rolls=[20, 1],
        positions={"gm": 0},
    )
    straight_hp = int(_canonical(straight, "gm")["resources"]["hp"])
    _enemy_attack(straight_engine, straight, [11], target_id="player:gm")
    straight_after = _settle(straight_engine, straight)

    engine, instance = _combat(
        runtime,
        {"gm": sheets["monk"], "p2": sheets["bard"]},
        rolls=[20, 1, 2],
        positions={"gm": 0, "p2": 10},
    )
    _enemy_attack(engine, instance, [11], target_id="player:gm")
    decision = _pending(instance)[0]
    assert decision["window_code"] == "cutting_words_check"
    events = _resolve(engine, instance, decision, "decline", by="p2")
    assert "check_penalty" not in _checked(events)
    windowed_after = _settle(engine, instance)

    assert straight_after == windowed_after < straight_hp
    assert _pool(instance, "bardic_inspiration", "p2")["current"] == 2
    assert _reaction(instance, "p2") == 1


def test_spend_turns_a_hit_into_a_miss_and_stops_before_damage(runtime, sheets) -> None:
    engine, instance = _three(runtime, sheets)
    before_hp = int(_canonical(instance, "p3")["resources"]["hp"])
    _enemy_attack(engine, instance, [11])
    decision = _pending(instance)[0]
    assert decision["check"]["total"] == 15

    events = _resolve(
        engine, instance, decision, "spend", by="gm", rng=SequenceRng([4])
    )
    types = [event["type"] for event in events]
    assert types == [
        "intent.submitted",
        "dnd2024.decision.cleared",
        "dnd2024.class_resource.spent",
        "dnd2024.reaction.spent",
        "check.resolved",
    ]
    check = _checked(events)
    assert check["kind"] == "attack"
    assert check["total"] == 11
    assert check["check_penalty"] == 4
    assert check["check_penalty_rolls"] == [4]
    assert check["success"] is False
    assert check["critical"] is False
    assert check["natural"] == 11
    assert _pending(instance) == []
    assert int(_canonical(instance, "p3")["resources"]["hp"]) == before_hp
    assert _pool(instance, "bardic_inspiration", "gm")["current"] == 1
    assert _reaction(instance, "gm") == 0


def test_a_cut_that_still_hits_rolls_damage_on_the_reduced_total(
    runtime, sheets
) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [15])
    decision = _pending(instance)[0]
    assert decision["check"]["total"] == 19

    events = _resolve(
        engine, instance, decision, "spend", by="gm", rng=SequenceRng([2])
    )
    check = _checked(events)
    assert check["total"] == 17
    assert check["success"] is True
    assert any(event["type"] == "resource.changed" for event in events) or _pending(
        instance
    )


def test_the_cut_leaves_natural_dice_modifier_and_bless_untouched(
    runtime, sheets
) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [11])
    decision = _pending(instance)[0]
    original = deepcopy(decision["check"])

    events = _resolve(
        engine, instance, decision, "spend", by="gm", rng=SequenceRng([5])
    )
    check = _checked(events)
    assert check["natural"] == original["natural"]
    assert check["rolls"] == original["rolls"]
    assert check["modifier"] == original["modifier"]
    assert check.get("check_bonus", 0) == original.get("check_bonus", 0)
    assert check["target"] == original["target"]
    assert check["total"] == original["total"] - 5


def test_a_natural_twenty_stays_critical_and_hits_after_the_cut(
    runtime, sheets
) -> None:
    engine, instance = _three(runtime, sheets)
    events = _enemy_attack(engine, instance, [20])
    decision = _pending(instance)[0]
    assert decision["check"]["critical"] is True

    events = _resolve(
        engine, instance, decision, "spend", by="gm", rng=SequenceRng([6])
    )
    check = _checked(events)
    assert check["natural"] == 20
    assert check["critical"] is True
    assert check["success"] is True
    assert check["total"] == 18


def test_automation_declines_a_window_it_cannot_improve(runtime, sheets) -> None:
    """A critical is not worth a Bardic Inspiration die, and neither is a hit
    the largest die cannot flip."""

    from src.engine.player_control import set_control

    # A natural twenty: the cut cannot take the critical away.
    engine, instance = _three(runtime, sheets)
    set_control(instance, "gm", "ai")
    _enemy_attack(engine, instance, [20])
    decision = _pending(instance)[0]
    assert decision["check"]["critical"] is True
    assert engine.next_automatic_intent(instance)["option"] == "decline"

    # A hit the largest die cannot flip: 22 - 6 still reaches AC 15.
    engine, instance = _three(runtime, sheets)
    set_control(instance, "gm", "ai")
    _enemy_attack(engine, instance, [18])
    assert engine.next_automatic_intent(instance)["option"] == "decline"


def test_a_five_foot_crit_upgrade_needs_the_attack_to_still_hit(
    runtime, sheets
) -> None:
    """The 5-foot upgrade is a hit rider, not a d20 fact.

    ``paralyzed`` grants the attacker advantage and upgrades any hit within 5
    feet to a critical.  Cutting Words must still be able to remove that hit,
    and removing it must remove the upgraded critical with it.
    """

    engine, instance = _three(runtime, sheets)
    _canonical(instance, "p3")["conditions"] = {"paralyzed": True}
    _enemy_attack(engine, instance, [11, 3])
    decision = _pending(instance)[0]
    assert decision["check"]["critical"] is True
    assert decision["check"]["critical_upgrade"] is True
    assert decision["settlement"]["crit_on_hit"] is True
    before_hp = int(_canonical(instance, "p3")["resources"]["hp"])

    events = _resolve(
        engine, instance, decision, "spend", by="gm", rng=SequenceRng([6])
    )
    check = _checked(events)
    assert check["success"] is False
    assert check["critical"] is False
    assert not any(event["type"] == "resource.changed" for event in events)
    _settle_all(engine, instance, tag="after-miss")
    assert int(_canonical(instance, "p3")["resources"]["hp"]) == before_hp

    # Declining keeps both the hit and the upgrade, and a critical doubles the
    # damage dice compared with the same attack against a standing target.
    engine, instance = _three(runtime, sheets)
    _canonical(instance, "p3")["conditions"] = {"paralyzed": True}
    _enemy_attack(engine, instance, [11, 3])
    before_hp = int(_canonical(instance, "p3")["resources"]["hp"])
    events = _resolve(engine, instance, _pending(instance)[0], "decline", by="gm")
    events += _settle_all(engine, instance, tag="crit")
    critical_hit = next(
        event for event in events if event["type"] == "resource.changed"
    )
    assert critical_hit["critical"] is True

    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [11])
    events = _resolve(engine, instance, _pending(instance)[0], "decline", by="gm")
    events += _settle_all(engine, instance, tag="plain")
    plain_hit = next(event for event in events if event["type"] == "resource.changed")
    assert plain_hit["critical"] is False

    # A critical doubles the damage dice of the same weapon.
    assert len(critical_hit["rolls"]) == 2 * len(plain_hit["rolls"])


def test_one_bard_gets_one_face_and_the_reaction_comes_back_next_round(
    runtime,
    sheets,
) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [15])
    decision = _pending(instance)[0]
    _resolve(engine, instance, decision, "spend", by="gm", rng=SequenceRng([1]))
    step = 0
    while _pending(instance):
        step += 1
        pending = _pending(instance)[0]
        _resolve(
            engine,
            instance,
            pending,
            "decline",
            by=pending["assigned_to"] or "gm",
            intent_id=f"drain-{step}",
        )
        assert step < 8
    assert _reaction(instance, "gm") == 0
    assert _pool(instance, "bardic_inspiration", "gm")["current"] == 1

    # A Reaction does not come back inside the same round; a new round does
    # restore it, so the same bard is a candidate again on the next attack.
    _next_enemy_turn(engine, instance)
    assert _reaction(instance, "gm") == 1
    _enemy_attack(engine, instance, [11], intent_id="g2")
    assert _pending(instance)[0]["window_owner_id"] == "player:gm"


# --------------------------------------------------------------------------
# 4. several reactors, automation, replay
# --------------------------------------------------------------------------


def test_two_bards_are_offered_in_order_and_the_decline_passes_on(
    runtime, sheets
) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [15])

    first = _pending(instance)[0]
    assert first["window_owner_id"] == "player:gm"

    events = _resolve(engine, instance, first, "decline", by="gm")
    second = _pending(instance)[0]
    assert second["window_owner_id"] == "player:p2"
    assert second["window_code"] == "cutting_words_check"
    assert second["decision_id"] != first["decision_id"]
    assert _checked(events)["success"] is True
    assert _reaction(instance, "gm") == 1

    _resolve(engine, instance, second, "decline", by="p2", intent_id="decide-2")
    assert _reaction(instance, "p2") == 1


def test_a_second_bard_can_still_cut_a_hit_the_first_left_alone(
    runtime, sheets
) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [14])

    first = _pending(instance)[0]
    assert first["check"]["total"] == 18
    _resolve(engine, instance, first, "spend", by="gm", rng=SequenceRng([1]))
    second = _pending(instance)[0]
    assert second["window_owner_id"] == "player:p2"
    assert second["check"]["total"] == 17

    events = _resolve(
        engine,
        instance,
        second,
        "spend",
        by="p2",
        rng=SequenceRng([4]),
        intent_id="decide-2",
    )
    check = _checked(events)
    assert check["total"] == 13
    assert check["success"] is False
    assert _pending(instance) == []


def test_a_cut_that_becomes_a_miss_cancels_every_remaining_bard(
    runtime, sheets
) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [11])

    first = _pending(instance)[0]
    events = _resolve(engine, instance, first, "spend", by="gm", rng=SequenceRng([6]))
    assert _checked(events)["success"] is False
    assert _pending(instance) == []


def test_automation_spends_only_when_the_largest_die_can_flip(runtime, sheets) -> None:
    from src.engine.player_control import set_control

    engine, instance = _three(runtime, sheets)
    set_control(instance, "gm", "ai")
    _enemy_attack(engine, instance, [11])

    decision = _pending(instance)[0]
    assert decision["assigned_to"] in {"", instance.gm_uid}
    intent = engine.next_automatic_intent(instance)
    assert intent is not None
    assert intent["option"] == "spend"


def test_a_human_bard_never_gets_an_automatic_resolve(runtime, sheets) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [11])
    assert _pending(instance)[0]["assigned_to"] == "gm"

    automatic = engine.next_automatic_intent(instance)
    assert automatic is None or automatic.get("submitted_by") != "gm"


def test_a_replayed_window_intent_does_not_spend_twice(runtime, sheets) -> None:
    engine, instance = _three(runtime, sheets)
    _enemy_attack(engine, instance, [11])
    decision = _pending(instance)[0]
    intent = {
        "intent_id": "once",
        "type": "decision.resolve",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm",
        "decision_id": decision["decision_id"],
        "option": "spend",
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng([4]))
    assert resolved["ok"] is True
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert _pool(instance, "bardic_inspiration", "gm")["current"] == 1

    replay = engine.resolve_intent(instance, intent, SequenceRng([4]))
    if replay["ok"]:
        # A replayed batch either applies nothing or is refused; either way it
        # must not charge the bard twice.
        try:
            assert (
                engine.apply_batch(instance, replay["event_batch"])["applied"] is False
            )
        except Exception:  # noqa: BLE001 - a refused replay is also acceptable
            pass
    assert _pool(instance, "bardic_inspiration", "gm")["current"] == 1
    assert _reaction(instance, "gm") == 0


def test_a_windowless_attack_keeps_the_baseline_dice(runtime, sheets) -> None:
    engine, instance = _combat(
        runtime,
        {"gm": sheets["monk"]},
        rolls=[20, 1],
        positions={"gm": 0},
    )
    events = _enemy_attack(engine, instance, [11, 3], target_id="player:gm")
    types = [event["type"] for event in events]
    assert "dnd2024.decision.pending" not in types
    assert "resource.changed" in types
