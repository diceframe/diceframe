"""Counterspell (P7): the reaction window that interrupts a spell's cast."""

from __future__ import annotations

from copy import deepcopy

from src.engine.modules import ruleset_runtime
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


CANTRIPS = ["spell:fire_bolt", "spell:light", "spell:mage_hand", "spell:ray_of_frost"]
# Ordered by spell level so every level's spellbook prefix stays inside that
# level's maximum spell level. blur / misty_step join at level 2 so they are
# inside the level 5 spellbook (14) and prepared (9) prefixes.
SPELLBOOK = [
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:shield",
    "spell:thunderwave",
    "spell:charm_person",
    "spell:sleep",
    "spell:burning_hands",
    "spell:blur",
    "spell:misty_step",
    "spell:invisibility",
    "spell:mirror_image",
    "spell:fireball",
    "spell:fly",
]
PREPARED = [
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:shield",
    "spell:thunderwave",
    "spell:blur",
    "spell:misty_step",
    "spell:invisibility",
    "spell:mirror_image",
]


def wizard_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        "class:wizard", level
    )
    prepared = PREPARED[: requirements["prepared_spell_count"]]
    spellbook = SPELLBOOK[: requirements["spellbook_minimum"]]
    assert set(prepared).issubset(spellbook)
    return {
        "cantrip_refs": CANTRIPS[: requirements["cantrip_count"]],
        "prepared_spell_refs": prepared,
        "spellbook_refs": spellbook,
    }


def wizard_sheet(runtime: Dnd2024Runtime, *, level: int = 5) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref="class:wizard",
        class_skill_refs=["skill:arcana", "skill:history"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:wizard_a",
        base_abilities={
            "str": 8, "dex": 12, "con": 13, "int": 15, "wis": 14, "cha": 10,
        },
        class_spell_choices=wizard_choices(runtime, 1),
    )
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        choices["class_spell_choices"] = wizard_choices(runtime, next_level)
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def paladin_sheet(runtime: Dnd2024Runtime, *, level: int = 2) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref="class:paladin",
        class_skill_refs=["skill:athletics", "skill:intimidation"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:paladin_a",
        class_spell_choices={
            "cantrip_refs": [],
            "spellbook_refs": [],
            "prepared_spell_refs": ["spell:heroism", "spell:cure_wounds"],
        },
    )
    sheet = runtime.finalize_character(None, draft)
    if level >= 2:
        sheet = runtime.apply_advancement(None, sheet, {
            "class_spell_choices": {
                "cantrip_refs": [],
                "spellbook_refs": [],
                "prepared_spell_refs": [
                    "spell:heroism", "spell:cure_wounds", "spell:bless",
                ],
            }
        })
    return sheet


def enemy(
    enemy_id: str = "goblin-1", *, position: int = 5, hp: int = 100, dc: int = 15,
) -> dict:
    profile = goblin(position=position, hp=hp)
    profile["id"] = enemy_id
    profile["counterspell_dc"] = dc
    return profile


def new_combat(
    *, level: int = 5, enemies=None, sheet: dict | None = None,
) -> tuple[Dnd2024Runtime, object, object]:
    runtime = Dnd2024Runtime()
    if sheet is None:
        sheet = wizard_sheet(runtime, level=level)
    engine, instance = monk_instance(runtime, sheet, uid="gm")
    enemy_list = list(enemies) if enemies is not None else [enemy()]
    rolls = [20, *[1 for _ in enemy_list]]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemy_list,
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"
    return runtime, engine, instance


def state(instance) -> dict:
    return ruleset_runtime.state(instance)


def combat_of(instance) -> dict:
    return state(instance)["combat"]


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def slots(instance, uid: str = "gm") -> dict:
    return canonical(instance, uid)["spellcasting"]["class"]["slots_current"]


def set_turn(engine, instance, actor_id: str = "player:gm") -> None:
    combat = combat_of(instance)
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def request(instance, intent: dict) -> dict:
    intent.setdefault("expected_version", int(state(instance).get("version", 0) or 0))
    return intent


def resolve(engine, instance, intent: dict, rolls=()):
    result = engine.resolve_intent(instance, request(instance, intent), SequenceRng(list(rolls)))
    assert result["ok"], result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"], applied
    return result


def validate(engine, instance, **payload) -> dict:
    payload.setdefault("intent_id", f"validate:{payload['type']}")
    payload.setdefault("submitted_by", "gm")
    return engine.validate_intent(instance, request(instance, payload))


def cast(
    engine, instance, spell_ref: str, slot: int, targets: list[str], rolls=(),
    *, uid: str = "gm", tag: str = "", **extra,
):
    set_turn(engine, instance, f"player:{uid}")
    return resolve(engine, instance, {
        "intent_id": f"cast:{spell_ref}:{slot}:{','.join(targets)}:{tag}",
        "type": "cast_spell", "submitted_by": uid, "actor_id": f"player:{uid}",
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": list(targets),
        **extra,
    }, rolls)


def resolve_decision(engine, instance, decision: dict, option: str, rolls=()):
    return resolve(engine, instance, {
        "intent_id": f"decision:{decision['decision_id']}:{option}",
        "type": "decision.resolve", "submitted_by": "gm",
        "decision_id": decision["decision_id"], "option": option,
    }, rolls)


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def without_submitted(result: dict) -> list[dict]:
    """Drop the fixed ``intent.submitted`` head event from a batch."""

    return [event for event in events_of(result) if event["type"] != "intent.submitted"]


def event_types(result: dict) -> list[str]:
    return [event["type"] for event in without_submitted(result)]


def event_of(result: dict, event_type: str, **match) -> dict:
    return next(
        event for event in without_submitted(result)
        if event["type"] == event_type
        and all(event.get(key) == value for key, value in match.items())
    )


def pending_of(result: dict) -> list[dict]:
    return [event for event in without_submitted(result)
            if event["type"] == "dnd2024.decision.pending"]


# --- #1 enemy profile validation of counterspell_dc ----------------------

def test_counterspell_dc_profile_validation() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, monk_sheet(runtime))

    for bad in (True, -1, 31):
        profile = goblin()
        profile["counterspell_dc"] = bad
        result = validate(engine, instance, type="combat.start", enemies=[profile])
        assert result["ok"] is False, result
        assert "counterspell_dc" in result["error"]

    # Absent is legal and means "this enemy cannot counterspell".
    assert validate(engine, instance, type="combat.start", enemies=[goblin()])["ok"] is True


# --- #2 window creation ------------------------------------------------

def test_eligible_cast_opens_a_counter_window() -> None:
    _runtime, engine, instance = new_combat()
    before_slots = deepcopy(slots(instance))
    before_economy = deepcopy(combat_of(instance)["economy"])
    version = state(instance)["version"]

    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])

    pending = pending_of(result)
    assert len(pending) == 1
    decision = pending[0]
    assert decision["type"] == "dnd2024.decision.pending"
    assert decision["kind"] == "counter_spell"
    assert decision["assigned_to"] == "gm"
    assert decision["options"] == ["counter", "decline"]
    assert decision["threat_actor_ids"] == ["enemy:goblin-1"]
    assert decision["cast_intent"] == {
        "intent_id": "cast:spell:fire_bolt:0:enemy:goblin-1:",
        "type": "cast_spell", "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:fire_bolt", "slot_level": 0,
        "target_ids": ["enemy:goblin-1"], "expected_version": version,
    }
    # Nothing is spent or resolved before the decision is answered.
    assert "dnd2024.action.spent" not in event_types(result)
    assert "dnd2024.spell.slot_spent" not in event_types(result)
    assert "dnd2024.spell.cast" not in event_types(result)
    assert slots(instance) == before_slots
    assert combat_of(instance)["economy"] == before_economy
    assert combat_of(instance)["pending_decisions"][0]["kind"] == "counter_spell"


# --- #3 the pending decision blocks every other intent ------------------

def test_pending_counter_blocks_other_intents() -> None:
    _runtime, engine, instance = new_combat()
    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])
    decision = pending_of(result)[0]

    for payload in (
        {"type": "move", "actor_id": "player:gm", "distance": 10},
        {"type": "attack", "actor_id": "player:gm", "target_id": "enemy:goblin-1"},
        {"type": "cast_spell", "actor_id": "player:gm",
         "spell_ref": "spell:fire_bolt", "slot_level": 0,
         "target_ids": ["enemy:goblin-1"]},
    ):
        rejected = validate(engine, instance, **payload)
        assert rejected["ok"] is False, rejected
        assert rejected["error"] == "resolve the counter spell decision first"

    assert validate(engine, instance, type="decision.resolve",
                    decision_id=decision["decision_id"], option="decline")["ok"] is True
    assert validate(engine, instance, type="combat.message",
                    actor_id="player:gm", text="wait")["ok"] is True


# --- #4 decline: the original spell resolves untouched ------------------

def test_decline_replays_the_original_cast() -> None:
    _runtime, engine, instance = new_combat()
    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])
    decision = pending_of(result)[0]

    resolved = resolve_decision(engine, instance, decision, "decline", rolls=[15, 5])

    assert event_types(resolved) == [
        "dnd2024.decision.cleared",
        "dnd2024.action.spent",
        "dnd2024.spell.cast",
        "check.resolved",
        "resource.changed",
    ]
    assert event_of(resolved, "dnd2024.action.spent", actor_id="player:gm",
                    resource="action")
    spell_attack = event_of(resolved, "check.resolved", kind="spell_attack")
    assert spell_attack["success"] is True
    damage = event_of(resolved, "resource.changed", target_id="enemy:goblin-1")
    assert damage["amount"] > 0
    assert damage["rolls"][0] == 5
    assert combat_of(instance)["pending_decisions"] == []


# --- #5 counter + failed save: the spell is wasted -----------------------

def test_failed_counter_save_wastes_the_action_only() -> None:
    _runtime, engine, instance = new_combat()
    before_slots = deepcopy(slots(instance))
    before_hp = combat_of(instance)["enemies"]["goblin-1"]["hp"]
    before_reactions = deepcopy(combat_of(instance)["reactions"])
    con_modifier = int(
        engine._actor_view(instance, combat_of(instance), "player:gm")["saving_throws"]["con"]
    )

    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])
    decision = pending_of(result)[0]
    resolved = resolve_decision(engine, instance, decision, "counter", rolls=[3])

    assert event_types(resolved) == [
        "dnd2024.decision.cleared",
        "dnd2024.reaction.spent",
        "check.resolved",
        "dnd2024.action.spent",
        "dnd2024.spell.countered",
    ]
    check = event_of(resolved, "check.resolved")
    assert check["kind"] == "saving_throw"
    assert check["ability"] == "con"
    assert check["actor_id"] == "player:gm"
    assert check["source_actor_id"] == "enemy:goblin-1"
    assert check["modifier"] == con_modifier
    assert check["rolls"] == [3]
    assert check["target"] == 15
    assert check["success"] is False
    assert check["counterspell"] is True

    reaction = event_of(resolved, "dnd2024.reaction.spent", actor_id="enemy:goblin-1")
    assert reaction["reason"] == "counterspell"
    assert event_of(resolved, "dnd2024.action.spent", actor_id="player:gm",
                    resource="action")
    countered = event_of(resolved, "dnd2024.spell.countered")
    assert countered["actor_id"] == "player:gm"
    assert countered["spell_ref"] == "spell:fire_bolt"
    assert countered["countered_by"] == "enemy:goblin-1"

    # No slot, no spell.cast, no damage, and no reaction left for the threat.
    assert "dnd2024.spell.slot_spent" not in event_types(resolved)
    assert "dnd2024.spell.cast" not in event_types(resolved)
    assert not any(
        event["type"] == "resource.changed" and event.get("target_id") == "enemy:goblin-1"
        for event in without_submitted(resolved)
    )
    assert slots(instance) == before_slots
    assert combat_of(instance)["enemies"]["goblin-1"]["hp"] == before_hp
    assert combat_of(instance)["reactions"]["enemy:goblin-1"] == 0
    assert combat_of(instance)["reactions"]["player:gm"] == before_reactions["player:gm"]
    assert combat_of(instance)["pending_decisions"] == []


# --- #6 counter + successful save: the spell still resolves -------------

def test_successful_counter_save_lets_the_spell_resolve() -> None:
    _runtime, engine, instance = new_combat()
    before_slots = deepcopy(slots(instance))
    before_hp = combat_of(instance)["enemies"]["goblin-1"]["hp"]

    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])
    decision = pending_of(result)[0]
    resolved = resolve_decision(engine, instance, decision, "counter", rolls=[20, 15, 5])

    assert event_types(resolved)[:4] == [
        "dnd2024.decision.cleared",
        "dnd2024.reaction.spent",
        "check.resolved",
        "dnd2024.action.spent",
    ]
    first_check = event_of(resolved, "check.resolved")
    assert first_check["kind"] == "saving_throw"
    assert first_check["success"] is True
    assert first_check["counterspell"] is True
    assert "dnd2024.spell.cast" in event_types(resolved)
    assert event_of(resolved, "check.resolved", kind="spell_attack")["success"] is True
    damage = event_of(resolved, "resource.changed", target_id="enemy:goblin-1")
    assert damage["rolls"][0] == 5
    assert combat_of(instance)["enemies"]["goblin-1"]["hp"] == before_hp - damage["amount"]
    assert slots(instance) == before_slots
    assert combat_of(instance)["pending_decisions"] == []


# --- #7 bless adds its d4 to the counter save ---------------------------

def test_bless_bonus_applies_to_the_counter_save() -> None:
    runtime = Dnd2024Runtime()
    _runtime, engine, instance = new_combat(sheet=paladin_sheet(runtime))

    blessed = cast(engine, instance, "spell:bless", 1, ["player:gm"], tag="bless")
    resolve_decision(engine, instance, pending_of(blessed)[0], "decline")
    assert "blessed" in engine._actor_view(
        instance, combat_of(instance), "player:gm",
    )["conditions"]

    result = cast(engine, instance, "spell:cure_wounds", 1, ["player:gm"], tag="cure")
    decision = pending_of(result)[0]
    resolved = resolve_decision(engine, instance, decision, "counter", rolls=[3, 2])

    check = event_of(resolved, "check.resolved")
    assert check["kind"] == "saving_throw"
    assert check["rolls"] == [3]
    assert check["bless_bonus"] == 2
    assert check["success"] is False
    assert "dnd2024.spell.countered" in event_types(resolved)
    # The earlier bless keeps its concentration: the countered spell never cast.
    assert "dnd2024.concentration.ended" not in event_types(resolved)
    assert "blessed" in engine._actor_view(
        instance, combat_of(instance), "player:gm",
    )["conditions"]


# --- #8 bonus-action spells spend the bonus action when countered -------

def test_countered_bonus_action_spell_spends_the_bonus_action() -> None:
    _runtime, engine, instance = new_combat()
    before_slots = deepcopy(slots(instance))

    result = cast(
        engine, instance, "spell:misty_step", 2, ["player:gm"],
        teleport_distance=10, tag="step",
    )
    decision = pending_of(result)[0]
    resolved = resolve_decision(engine, instance, decision, "counter", rolls=[3])

    spent = event_of(resolved, "dnd2024.action.spent", actor_id="player:gm")
    assert spent["resource"] == "bonus_action"
    assert "dnd2024.spell.slot_spent" not in event_types(resolved)
    assert "dnd2024.position.changed" not in event_types(resolved)
    assert slots(instance) == before_slots
    assert combat_of(instance)["pending_decisions"] == []


# --- #9 the window stays shut when a condition is not met ---------------

def test_window_requires_range_reaction_and_dc() -> None:
    _runtime, engine, instance = new_combat(enemies=[enemy(position=100)])
    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])
    assert pending_of(result) == []
    assert "dnd2024.spell.cast" in event_types(result)

    _runtime, engine, instance = new_combat()
    combat_of(instance)["reactions"]["enemy:goblin-1"] = 0
    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])
    assert pending_of(result) == []
    assert "dnd2024.spell.cast" in event_types(result)

    _runtime, engine, instance = new_combat(enemies=[enemy(dc=0)])
    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])
    assert pending_of(result) == []
    assert "dnd2024.spell.cast" in event_types(result)


# --- #10 a countered spell never disturbs existing concentration ---------

def test_countered_cast_keeps_the_existing_concentration_spell() -> None:
    _runtime, engine, instance = new_combat()

    blurred = cast(engine, instance, "spell:blur", 2, ["player:gm"], tag="blur")
    resolve_decision(engine, instance, pending_of(blurred)[0], "decline")
    assert "blurred" in engine._actor_view(
        instance, combat_of(instance), "player:gm",
    )["conditions"]

    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"], tag="bolt")
    decision = pending_of(result)[0]
    resolved = resolve_decision(engine, instance, decision, "counter", rolls=[3])

    assert "dnd2024.concentration.ended" not in event_types(resolved)
    assert "dnd2024.spell.cast" not in event_types(resolved)
    assert "blurred" in engine._actor_view(
        instance, combat_of(instance), "player:gm",
    )["conditions"]


# --- #11 triggered / reaction spells never open a counter window ---------

def test_window_excludes_triggered_and_reaction_spell_modes() -> None:
    _runtime, engine, instance = new_combat()
    combat = combat_of(instance)

    # A plain cast still detects the threat, so the setup itself is valid.
    assert engine._counter_spell_window(instance, combat, {
        "intent_id": "probe", "actor_id": "player:gm", "spell_ref": "spell:fire_bolt",
    }) is not None
    assert engine._counter_spell_window(instance, combat, {
        "intent_id": "probe", "actor_id": "player:gm", "spell_ref": "spell:divine_smite",
    }) is None
    assert engine._counter_spell_window(instance, combat, {
        "intent_id": "probe", "actor_id": "player:gm", "spell_ref": "spell:shield",
    }) is None


# --- #12 enemies still cannot cast spells ------------------------------

def test_enemy_spell_casting_stays_forbidden() -> None:
    _runtime, engine, instance = new_combat()
    set_turn(engine, instance, "enemy:goblin-1")

    rejected = validate(
        engine, instance, type="cast_spell", actor_id="enemy:goblin-1",
        spell_ref="spell:fire_bolt", slot_level=0, target_ids=["player:gm"],
    )
    assert rejected["ok"] is False, rejected
    assert rejected["error"] == "enemy spell profiles are not enabled for this actor"


# --- #13 every shipped spell declares a component -----------------------

def test_every_shipped_spell_declares_components() -> None:
    runtime = Dnd2024Runtime()
    rows = runtime.load_bundle("en").get("spell_catalog", "srd_spells")["spells"]
    assert rows
    # The component gate in ``_counter_spell_window`` is therefore
    # conditionally unreachable: no shipped spell can exercise it today.
    assert all(row["components"] for row in rows)


# --- the caster's own GM seat never decides the enemy's counter ---------

def test_a_gm_who_plays_the_caster_does_not_decide_the_enemy_counter() -> None:
    from src.rulesets.automation import advance_automatic_intents

    runtime, engine, instance = new_combat()
    assert instance.gm_uid == "gm" and "gm" in instance.players
    cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])

    intent = engine.next_automatic_intent(instance)
    assert intent["type"] == "decision.resolve" and intent["option"] == "counter"

    advance_automatic_intents(runtime, instance, SequenceRng([1] * 10))

    assert not combat_of(instance)["pending_decisions"]
    assert combat_of(instance)["reactions"]["enemy:goblin-1"] == 0


def test_a_separate_gm_still_decides_the_counter_by_hand() -> None:
    _runtime, engine, instance = new_combat()
    instance.gm_uid = "host"
    cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])

    assert combat_of(instance)["pending_decisions"][0]["kind"] == "counter_spell"
    assert engine.next_automatic_intent(instance) is None
