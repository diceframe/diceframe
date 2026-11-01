"""Hold Person (P2): incapacitation gates, repeat saves, and auto-fail saves."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.catalog import (
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.combat.reaction_spells import HIT_REACTION_DECISION
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
    with_equipment,
)


CANTRIPS = ["spell:light", "spell:mage_hand", "spell:ray_of_frost", "spell:fire_bolt"]
SPELLBOOK = [
    # Ordered by spell level so every level's minimum spellbook prefix stays
    # inside that level's maximum spell level. hold_person joins at position 9
    # so it is inside the level 5 spellbook (14) and prepared (9) prefixes.
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:shield",
    "spell:thunderwave",
    "spell:charm_person",
    "spell:sleep",
    "spell:burning_hands",
    "spell:hold_person",
    "spell:invisibility",
    "spell:blur",
    "spell:misty_step",
    "spell:mirror_image",
    "spell:fireball",
]
PREPARED = SPELLBOOK[:9]


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


def enemy(
    enemy_id: str = "goblin-1", *, position: int = 5, hp: int = 100,
    creature_type: str = "humanoid",
) -> dict:
    profile = goblin(position=position, hp=hp)
    profile["id"] = enemy_id
    profile["creature_type"] = creature_type
    return profile


def new_combat(
    *, level: int = 5, equipment=(), allies=(), enemies=None,
) -> tuple[Dnd2024Runtime, object, object]:
    runtime = Dnd2024Runtime()
    gm_sheet = wizard_sheet(runtime, level=level)
    if equipment:
        gm_sheet = with_equipment(runtime, gm_sheet, *equipment)
    sheets = {"gm": gm_sheet}
    for uid in allies:
        sheets[uid] = wizard_sheet(runtime, level=level)
    engine, instance = monk_instance(runtime, gm_sheet, uid="gm")
    for uid, sheet in sheets.items():
        if uid != "gm":
            instance.players[uid] = {"character_name": uid.title(), "character_sheet": sheet}
    enemy_list = list(enemies) if enemies is not None else [enemy()]
    # sorted(players) places every ally before "gm"; the GM always wins
    # initiative so the turn starts on the party side.
    player_rolls = [1 for _ in sorted(sheets)]
    player_rolls[sorted(sheets).index("gm")] = 20
    rolls = [*player_rolls, *[1 for _ in enemy_list]]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemy_list,
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"
    return runtime, engine, instance


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def spell_save_dc(instance, uid: str = "gm") -> int:
    return int(canonical(instance, uid)["derived"]["spell_save_dc"])


def enemy_conditions(instance, enemy_id: str = "goblin-1") -> dict:
    enemies = ruleset_runtime.state(instance)["combat"]["enemies"]
    return enemies[enemy_id].setdefault("conditions", {})


def add_enemy_condition(
    instance, condition: str, meta: dict | None = None, *, enemy_id: str = "goblin-1",
) -> None:
    enemy_conditions(instance, enemy_id)[condition] = meta or {"source": "test"}


def add_player_condition(instance, uid: str, condition: str, meta: dict | None = None) -> None:
    canonical(instance, uid).setdefault("conditions", {})[condition] = meta or {"source": "test"}


def set_turn(engine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def request(instance, intent: dict) -> dict:
    intent.setdefault("expected_version", ruleset_runtime.state(instance)["version"])
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
    *, uid: str = "gm", **extra,
):
    set_turn(engine, instance, f"player:{uid}")
    return resolve(engine, instance, {
        "intent_id": f"cast:{spell_ref}:{slot}:{len(targets)}",
        "type": "cast_spell", "submitted_by": uid, "actor_id": f"player:{uid}",
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": targets,
        **extra,
    }, rolls)


def attack(
    engine, instance, *, actor_id: str, target_id: str, weapon=None,
    attack_id=None, rolls=(),
):
    set_turn(engine, instance, actor_id)
    payload = {
        "intent_id": f"attack:{actor_id}:{target_id}",
        "type": "attack", "submitted_by": "gm", "actor_id": actor_id,
        "target_id": target_id,
    }
    if weapon:
        payload["weapon_ref"] = weapon
    if attack_id:
        payload["attack_id"] = attack_id
    return resolve(engine, instance, payload, rolls)


def end_turn(engine, instance, actor_id: str, rolls=()):
    set_turn(engine, instance, actor_id)
    return resolve(engine, instance, {
        "intent_id": f"end:{actor_id}", "type": "end_turn",
        "submitted_by": "gm", "actor_id": actor_id,
    }, rolls)


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def event_of(result: dict, kind: str, **match) -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == kind
        and all(event.get(key) == value for key, value in match.items())
    )


def check_of(result: dict, kind: str) -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def damage_to(result: dict, target_id: str) -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "resource.changed"
        and event.get("target_id") == target_id
        and not event.get("healing")
    )


def catalog_bundle(base, **override):
    class _Bundle:
        def get(self, kind, ident=None):
            if kind == "combat_catalog":
                raw = deepcopy(base.get(kind, ident))
                raw.update(deepcopy(override))
                return raw
            return base.get(kind, ident)

        def list(self, kind):
            return base.list(kind)

    return _Bundle()


class RecordingRng(SequenceRng):
    """A deterministic source that also records every requested dice range."""

    def __init__(self, values) -> None:
        super().__init__(list(values))
        self.requests: list[tuple[int, int]] = []

    def randint(self, minimum: int, maximum: int) -> int:
        self.requests.append((minimum, maximum))
        return super().randint(minimum, maximum)

    def d20_requests(self) -> int:
        return sum(1 for request in self.requests if request == (1, 20))


# --- #1 catalog validation of the new declarations ------------------------

def test_catalog_rejects_invalid_incapacitation_declarations() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    effects = bundle.get("combat_catalog", "srd_combat_core")["spell_effects"]
    hold_person = deepcopy(effects["hold_person"])

    bad_conditions = {
        "incapacitated": {"implies_incapacitated": "yes"},
        "paralyzed": {"auto_fail_saves": ["luck"]},
        "stunned": {"speed_zero": 1},
    }

    for condition_id, entry in bad_conditions.items():
        with pytest.raises(CombatCatalogError):
            Dnd2024CombatCatalog.from_bundle(
                catalog_bundle(bundle, conditions={condition_id: entry}),
            )

    bad_repeat = {**hold_person, "save_repeat": "luck"}
    bad_types = {**hold_person, "target_creature_types": "humanoid"}
    bad_empty_types = {**hold_person, "target_creature_types": []}
    for entry in (bad_repeat, bad_types, bad_empty_types):
        with pytest.raises(CombatCatalogError):
            Dnd2024CombatCatalog.from_bundle(
                catalog_bundle(bundle, spell_effects={"hold_person": entry}),
            )


# --- #2 hold_person lands paralyzed with a repeat-save snapshot ----------

def test_hold_person_paralyzes_a_humanoid_with_a_repeat_save_snapshot() -> None:
    _runtime, engine, instance = new_combat()

    result = cast(engine, instance, "spell:hold_person", 2, ["enemy:goblin-1"], rolls=[1])

    check = check_of(result, "saving_throw")
    assert check["ability"] == "wis"
    assert check["success"] is False
    event_of(result, "condition.applied", condition="paralyzed")

    applied = enemy_conditions(instance)["paralyzed"]
    assert applied["duration"] == "concentration"
    assert applied["concentration_owner"] == "player:gm"
    assert applied["save_repeat"] == {
        "ability": "wis", "dc": spell_save_dc(instance),
    }


# --- #3 a successful save leaves no condition ----------------------------

def test_hold_person_does_nothing_on_a_successful_save() -> None:
    _runtime, engine, instance = new_combat()

    result = cast(engine, instance, "spell:hold_person", 2, ["enemy:goblin-1"], rolls=[20])

    check = check_of(result, "saving_throw")
    assert check["success"] is True
    assert not any(
        event["type"] == "condition.applied" for event in events_of(result)
    )
    assert "paralyzed" not in enemy_conditions(instance)


# --- #4 paralysis gates actions and movement -----------------------------

def test_paralyzed_actor_cannot_attack_cast_or_move() -> None:
    _runtime, engine, instance = new_combat()
    add_enemy_condition(instance, "paralyzed", {"source": "test"})
    set_turn(engine, instance, "enemy:goblin-1")

    attack_intent = validate(
        engine, instance, type="attack", actor_id="enemy:goblin-1",
        target_id="player:gm", attack_id="scimitar",
    )
    assert attack_intent["ok"] is False
    assert "incapacitated" in attack_intent["error"]

    spell_intent = validate(
        engine, instance, type="cast_spell", actor_id="enemy:goblin-1",
        spell_ref="spell:fire_bolt", slot_level=0, target_ids=["player:gm"],
    )
    assert spell_intent["ok"] is False
    assert "incapacitated" in spell_intent["error"]

    move_intent = validate(
        engine, instance, type="move", actor_id="enemy:goblin-1", distance=5,
    )
    assert move_intent["ok"] is False
    assert "speed zero" in move_intent["error"]


# --- #5 plain incapacitation still allows movement -----------------------

def test_sleep_incapacitation_does_not_block_movement() -> None:
    _runtime, engine, instance = new_combat()
    cast(engine, instance, "spell:sleep", 1, ["enemy:goblin-1"], rolls=[1])
    assert "incapacitated" in enemy_conditions(instance)
    set_turn(engine, instance, "enemy:goblin-1")

    move_intent = validate(
        engine, instance, type="move", actor_id="enemy:goblin-1", distance=5,
    )
    assert move_intent["ok"] is True

    move_beyond_speed = validate(
        engine, instance, type="move", actor_id="enemy:goblin-1", distance=40,
    )
    assert move_beyond_speed["ok"] is False


# --- #6 a successful repeat save ends just that target's condition -------

def test_repeat_save_success_removes_paralyzed_without_ending_concentration() -> None:
    _runtime, engine, instance = new_combat()
    cast(engine, instance, "spell:hold_person", 2, ["enemy:goblin-1"], rolls=[1])

    result = end_turn(engine, instance, "enemy:goblin-1", rolls=[20])

    types = [event["type"] for event in events_of(result)]
    repeat = check_of(result, "saving_throw")
    assert repeat["repeat_save"] is True
    assert repeat["success"] is True
    removed = event_of(result, "condition.removed", condition="paralyzed")
    assert removed["reason"] == "save_repeat"
    assert types.index("check.resolved") < types.index("condition.removed")
    assert types.index("condition.removed") < types.index("dnd2024.turn.advanced")
    assert not any(
        event["type"] == "dnd2024.concentration.ended" for event in events_of(result)
    )
    assert "paralyzed" not in enemy_conditions(instance)
    assert canonical(instance)["spellcasting"]["class"]["concentration"] is not None


# --- #7 a failed repeat save keeps the condition and the concentration ---

def test_repeat_save_failure_keeps_paralyzed_and_the_caster_concentration() -> None:
    _runtime, engine, instance = new_combat()
    cast(engine, instance, "spell:hold_person", 2, ["enemy:goblin-1"], rolls=[1])

    result = end_turn(engine, instance, "enemy:goblin-1", rolls=[1])

    repeat = check_of(result, "saving_throw")
    assert repeat["repeat_save"] is True
    assert repeat["success"] is False
    assert "condition.removed" not in [event["type"] for event in events_of(result)]
    assert "paralyzed" in enemy_conditions(instance)
    assert canonical(instance)["spellcasting"]["class"]["concentration"] is not None


# --- #8 upcasting targets two humanoids independently -------------------

def test_upcast_hold_person_saves_each_target_independently() -> None:
    _runtime, engine, instance = new_combat(
        enemies=[enemy("goblin-1"), enemy("goblin-2")],
    )

    too_many = validate(
        engine, instance, type="cast_spell", actor_id="player:gm",
        spell_ref="spell:hold_person", slot_level=2,
        target_ids=["enemy:goblin-1", "enemy:goblin-2"],
    )
    assert too_many["ok"] is False
    assert "unique targets" in too_many["error"]

    result = cast(
        engine, instance, "spell:hold_person", 3,
        ["enemy:goblin-1", "enemy:goblin-2"], rolls=[1, 20],
    )

    checks = [
        event for event in events_of(result)
        if event["type"] == "check.resolved" and event.get("kind") == "saving_throw"
    ]
    assert [event["actor_id"] for event in checks] == [
        "enemy:goblin-1", "enemy:goblin-2",
    ]
    assert [event["success"] for event in checks] == [False, True]
    assert "paralyzed" in enemy_conditions(instance, "goblin-1")
    assert "paralyzed" not in enemy_conditions(instance, "goblin-2")


# --- #9 hold_person only targets humanoids ------------------------------

def test_hold_person_rejects_a_non_humanoid_target() -> None:
    _runtime, engine, instance = new_combat(
        enemies=[enemy("goblin-1", creature_type="beast")],
    )

    rejected = validate(
        engine, instance, type="cast_spell", actor_id="player:gm",
        spell_ref="spell:hold_person", slot_level=2, target_ids=["enemy:goblin-1"],
    )
    assert rejected["ok"] is False
    assert "creature type" in rejected["error"]


# --- #10 auto-failed saves consume no d20 -------------------------------

def test_paralyzed_target_auto_fails_dex_saves_without_rolling() -> None:
    _runtime, engine, instance = new_combat()
    add_enemy_condition(instance, "paralyzed", {"source": "test"})
    set_turn(engine, instance, "player:gm")

    rng = RecordingRng([3, 3, 3])
    result = engine.resolve_intent(instance, request(instance, {
        "intent_id": "auto-fail-burning-hands",
        "type": "cast_spell", "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:burning_hands", "slot_level": 1,
        "target_ids": ["enemy:goblin-1"],
    }), rng)
    assert result["ok"], result
    assert engine.apply_batch(instance, result["event_batch"])["applied"]

    check = check_of(result, "saving_throw")
    assert check["rolls"] == []
    assert check["success"] is False
    assert check["auto_failed"] is True
    assert rng.d20_requests() == 0
    assert damage_to(result, "enemy:goblin-1")["amount"] == 9


# --- #11 melee weapon hits become critical within 5 feet ----------------

def test_melee_hit_on_a_paralyzed_target_becomes_critical() -> None:
    _runtime, engine, instance = new_combat(equipment=["item:dagger"])
    add_enemy_condition(instance, "paralyzed", {"source": "test"})

    result = attack(
        engine, instance, actor_id="player:gm", target_id="enemy:goblin-1",
        weapon="item:dagger", rolls=[15, 3, 1, 2],
    )

    check = check_of(result, "attack")
    assert check["natural"] == 15
    assert check["success"] is True
    assert check["critical"] is True
    damage = damage_to(result, "enemy:goblin-1")
    assert damage["critical"] is True
    assert len(damage["rolls"]) == 2  # 1d4 doubled


def test_ranged_hit_on_a_paralyzed_target_is_not_critical() -> None:
    _runtime, engine, instance = new_combat(
        equipment=["item:shortbow"], enemies=[enemy(position=30)],
    )
    add_enemy_condition(instance, "paralyzed", {"source": "test"})

    result = attack(
        engine, instance, actor_id="player:gm", target_id="enemy:goblin-1",
        weapon="item:shortbow", rolls=[15, 3, 4],
    )

    check = check_of(result, "attack")
    assert check["success"] is True
    assert check["critical"] is False
    damage = damage_to(result, "enemy:goblin-1")
    assert damage["critical"] is False
    assert len(damage["rolls"]) == 1


# --- #12 spell attacks follow the same critical upgrade -----------------

def test_spell_attack_on_a_paralyzed_target_within_5_feet_is_critical() -> None:
    _runtime, engine, instance = new_combat()
    add_enemy_condition(instance, "paralyzed", {"source": "test"})

    result = cast(
        engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"], rolls=[15, 3, 1, 2],
    )

    check = check_of(result, "spell_attack")
    assert check["natural"] == 15
    assert check["success"] is True
    assert check["critical"] is True
    damage = damage_to(result, "enemy:goblin-1")
    assert damage["critical"] is True
    assert len(damage["rolls"]) == 4  # fire bolt is 2d10 at level 5, doubled on a crit


def test_distant_spell_attack_on_a_paralyzed_target_is_not_critical() -> None:
    _runtime, engine, instance = new_combat(enemies=[enemy(position=30)])
    add_enemy_condition(instance, "paralyzed", {"source": "test"})

    result = cast(
        engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"], rolls=[15, 3, 4],
    )

    check = check_of(result, "spell_attack")
    assert check["success"] is True
    assert check["critical"] is False
    damage = damage_to(result, "enemy:goblin-1")
    assert damage["critical"] is False
    assert len(damage["rolls"]) == 2  # fire bolt is 2d10 at level 5, not doubled


# --- #13 incapacitation ends the target's own concentration -------------

def test_incapacitation_breaks_the_targets_concentration() -> None:
    _runtime, engine, instance = new_combat()
    add_enemy_condition(instance, "blessed", {
        "duration": "concentration",
        "source_actor_id": "enemy:goblin-1",
        "concentration_owner": "enemy:goblin-1",
    })

    result = cast(engine, instance, "spell:sleep", 1, ["enemy:goblin-1"], rolls=[1, 2])

    ended = event_of(result, "dnd2024.concentration.ended", actor_id="enemy:goblin-1")
    assert ended["reason"] == "incapacitated"
    assert "blessed" not in enemy_conditions(instance)
    assert "incapacitated" in enemy_conditions(instance)


# --- #14 sleep repeats its save at the end of the target's turn ---------

def test_sleep_repeat_save_removes_incapacitated_and_keeps_it_on_a_failure() -> None:
    _runtime, engine, instance = new_combat()
    cast(engine, instance, "spell:sleep", 1, ["enemy:goblin-1"], rolls=[1])

    result = end_turn(engine, instance, "enemy:goblin-1", rolls=[20])
    repeat = check_of(result, "saving_throw")
    assert repeat["repeat_save"] is True
    assert repeat["success"] is True
    assert event_of(result, "condition.removed", condition="incapacitated")
    assert "incapacitated" not in enemy_conditions(instance)

    _runtime, engine, instance = new_combat()
    cast(engine, instance, "spell:sleep", 1, ["enemy:goblin-1"], rolls=[1])
    result = end_turn(engine, instance, "enemy:goblin-1", rolls=[1])
    repeat = check_of(result, "saving_throw")
    assert repeat["repeat_save"] is True
    assert repeat["success"] is False
    assert "incapacitated" in enemy_conditions(instance)


# --- #15 incapacitated defenders get no reaction offer ------------------

def test_incapacitated_defender_gets_no_reaction_decision() -> None:
    _runtime, engine, instance = new_combat(allies=["ally"])
    add_player_condition(instance, "ally", "paralyzed", {
        "duration": "concentration", "source": "test",
    })

    result = attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:ally",
        attack_id="scimitar", rolls=[15, 3, 1, 2],
    )

    assert not any(
        event["type"] == "dnd2024.decision.pending"
        and event["kind"] == HIT_REACTION_DECISION
        for event in events_of(result)
    )
    assert damage_to(result, "player:ally")["amount"] > 0


# --- automation: an incapacitated enemy still gets (and ends) its turn ---

@pytest.mark.parametrize("spell_ref,slot,position", [
    ("spell:hold_person", 2, 5),
    ("spell:sleep", 1, 50),  # out of reach: the ladder must not walk it over either
])
def test_automation_ends_the_turn_of_an_incapacitated_enemy(spell_ref, slot, position) -> None:
    from src.rulesets.automation import advance_automatic_intents

    runtime, engine, instance = new_combat(enemies=[enemy(position=position)])
    cast(engine, instance, spell_ref, slot, ["enemy:goblin-1"], rolls=[1])
    hp_before = canonical(instance)["resources"]["hp"]
    resolve(engine, instance, {
        "intent_id": "end-gm", "type": "end_turn", "submitted_by": "gm",
        "actor_id": "player:gm",
    })
    combat = ruleset_runtime.state(instance)["combat"]
    position_before = engine._position(combat, "enemy:goblin-1")

    advance_automatic_intents(runtime, instance, SequenceRng([1] * 20))

    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["initiative"][combat["turn_index"]] == "player:gm"
    assert engine._position(combat, "enemy:goblin-1") == position_before
    assert canonical(instance)["resources"]["hp"] == hp_before
    assert enemy_conditions(instance)  # the failed repeat save kept the condition
