"""Dark One's Blessing (S-P4): defeat-triggered temp HP.

The trigger is declared through the closed ``defeat_*`` parameter set, so the
engine never names a feature.  Every scenario drives the same authoritative
chain the web layer uses: ``resolve_intent`` -> ``apply_batch``.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
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
    with_equipment,
)


PREPARED = ["spell:hex", "spell:charm_person", "spell:hellish_rebuke", "spell:bane"]
WARLOCK = "class:warlock"


def spell_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        WARLOCK, level,
    )
    return {
        "cantrip_refs": ["spell:eldritch_blast", "spell:prestidigitation"],
        "prepared_spell_refs": PREPARED[: int(requirements["prepared_spell_count"])],
        "spellbook_refs": [],
    }


def warlock_sheet(runtime: Dnd2024Runtime, *, level: int = 3) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref=WARLOCK,
        class_skill_refs=["skill:arcana", "skill:deception"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:warlock_a",
        base_abilities={
            "str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15,
        },
        name="Warlock",
        class_spell_choices=spell_choices(runtime, 1),
    )
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        choices["class_spell_choices"] = spell_choices(runtime, next_level)
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def rogue_sheet(runtime: Dnd2024Runtime) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "lucky_scout")
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Ally"},
    )
    return with_equipment(runtime, sheet, "item:dagger")


def enemy(enemy_id: str = "goblin-1", *, hp: int = 1, position: int = 5) -> dict:
    profile = goblin(position=position, hp=hp)
    profile["id"] = enemy_id
    return profile


def warlock_instance(*, level: int = 3):
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, warlock_sheet(runtime, level=level))
    return runtime, engine, instance


def party_instance(*, positions: dict | None = None, enemy_position: int = 5):
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, warlock_sheet(runtime, level=3))
    instance.players["ally"] = {
        "character_name": "Ally", "character_sheet": rogue_sheet(runtime),
    }
    # sorted(players) is ["ally", "gm"]: low roll for the ally, high for the GM.
    start(engine, instance, [enemy(position=enemy_position)], rolls=[1, 20, 1],
          positions=positions)
    return runtime, engine, instance


def start(engine, instance, enemies: list[dict], *, rolls=None, positions=None):
    rolls = list(rolls or [20] + [1] * len(enemies))
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemies,
        **({"player_positions": positions} if positions else {}),
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["initiative"][0] == "player:gm"
    return combat


def set_turn(engine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def submitter(actor_id: str) -> str:
    return actor_id.removeprefix("player:") if actor_id.startswith("player:") else "gm"


def attack(
    engine, instance, *, actor_id: str = "player:gm", target_id: str = "enemy:goblin-1",
    weapon: str = "item:dagger", rolls=(10, 4, 1), intent_id: str = "attack-1",
) -> dict:
    resolved = engine.resolve_intent(instance, {
        "intent_id": intent_id, "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": submitter(actor_id), "actor_id": actor_id,
        "target_id": target_id, "weapon_ref": weapon,
    }, SequenceRng(list(rolls)))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return resolved


def cast(engine, instance, spell_ref: str, slot: int, targets: list[str], *, rolls=()):
    set_turn(engine, instance, "player:gm")
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"cast:{spell_ref}:{slot}",
        "type": "cast_spell", "submitted_by": "gm", "actor_id": "player:gm",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": targets,
    }, SequenceRng(list(rolls)))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return resolved


def grant(instance, uid: str, *refs: str) -> None:
    magic = canonical(instance, uid)["spellcasting"]["class"]
    for ref in refs:
        if ref not in magic["prepared_spell_refs"]:
            magic["prepared_spell_refs"].append(ref)


def grant_slot(instance, uid: str, level: int, count: int = 1) -> None:
    magic = canonical(instance, uid)["spellcasting"]["class"]
    magic["slots_current"][str(level)] = count
    magic["slots_max"][str(level)] = count


def direct_damage(engine, instance, amount: int, *, target_id: str = "player:gm") -> dict:
    version = ruleset_runtime.state(instance)["version"]
    return engine.apply_batch(instance, {
        "batch_id": f"batch_damage_{amount}_{version}",
        "intent_id": f"damage-{amount}", "intent_type": "attack",
        "expected_version": version, "result_version": version + 1,
        "events": [{
            "type": "resource.changed", "target_id": target_id, "resource": "hp",
            "delta": -amount, "amount": amount,
        }],
        "source_ref": "test:dark-ones-blessing-damage",
    })


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def temp_events(result: dict) -> list[dict]:
    return [
        event for event in events_of(result)
        if event["type"] == "resource.changed" and event.get("resource") == "temp_hp"
    ]


def temp_event(result: dict) -> dict:
    return temp_events(result)[0]


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def resources(instance, uid: str = "gm") -> dict:
    return canonical(instance, uid)["resources"]


def bundle_with_class_features(base, raw: dict):
    class _Bundle:
        def __getattr__(self, name):
            return getattr(base, name)

        def get(self, kind, ident=None):
            if kind == "class_feature_catalog":
                return raw
            return base.get(kind, ident)

        def list(self, kind):
            return base.list(kind)

    return _Bundle()


# --- #1 catalog validation of the closed defeat payload ---------------------

def test_defeat_trigger_declarations_fail_closed() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    raw = deepcopy(bundle.get("class_feature_catalog", "srd_class_features"))
    definition = raw["classes"]["warlock"]["features"][0]
    parameters = definition["parameters"]

    bad_payloads = [
        {**definition, "parameters": {**parameters, "defeat_temp_hp_ability": "luck"}},
        {**definition, "parameters": {"defeat_share_range_ft": 10}},
        {**definition, "parameters": {**parameters, "defeat_share_range_ft": 0}},
        {**definition, "parameters": {**parameters, "defeat_share_range_ft": 121}},
        {**definition, "parameters": {**parameters, "defeat_share_range_ft": "10"}},
        {**definition,
         "parameters": {**parameters, "defeat_temp_hp_add_class_level": 1}},
    ]
    for payload in bad_payloads:
        raw["classes"]["warlock"]["features"] = [payload]
        with pytest.raises(ClassFeatureError):
            Dnd2024ClassFeatureResolver(bundle_with_class_features(bundle, raw))

    raw["classes"]["warlock"]["features"] = [definition]
    Dnd2024ClassFeatureResolver(bundle_with_class_features(bundle, raw))


# --- #2 ownership: the subclass grants it at level 3 only -------------------

def test_feature_is_owned_from_level_three() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    level_three = warlock_sheet(runtime, level=3)["ruleset_character"]
    level_two = warlock_sheet(runtime, level=2)["ruleset_character"]
    assert resolver.has_feature(level_three, "dark_ones_blessing") is True
    assert resolver.has_feature(level_two, "dark_ones_blessing") is False


# --- #3 path a: the holder's own kill ---------------------------------------

def test_holder_kill_grants_temp_hp() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4])

    event = temp_event(result)
    assert event["target_id"] == "player:gm"
    assert event["amount"] == 5  # Charisma 15 (+2) + Warlock 3
    assert event["feature_id"] == "dark_ones_blessing"
    assert resources(instance)["temp_hp"] == 5
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 0


# --- #4 path a counterexample: damage that does not zero the target --------

def test_non_lethal_damage_grants_nothing() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=20)])
    result = attack(engine, instance, rolls=[10, 4])

    assert temp_events(result) == []
    assert "temp_hp" not in resources(instance)


# --- #5/#6/#7 path b: another party member's kill and the share radius ------

@pytest.mark.parametrize(
    "warlock_position,ally_position,enemy_position,expected",
    [
        (0, 0, 5, True),    # inside the 10 ft share radius
        (0, 15, 20, False),  # outside the 10 ft share radius
        (0, 5, 10, True),   # exactly 10 ft still qualifies
    ],
)
def test_shared_kill_follows_the_share_radius(
    warlock_position, ally_position, enemy_position, expected,
) -> None:
    _runtime, engine, instance = party_instance(
        positions={"gm": warlock_position, "ally": ally_position},
        enemy_position=enemy_position,
    )
    set_turn(engine, instance, "player:ally")
    result = attack(engine, instance, actor_id="player:ally", rolls=[10, 4, 1])

    if expected:
        event = temp_event(result)
        assert event["target_id"] == "player:gm"
        assert temp_event(result)["amount"] == 5
        assert resources(instance)["temp_hp"] == 5
    else:
        assert temp_events(result) == []
        assert "temp_hp" not in resources(instance)


# --- #8 the "minimum 1" floor inside the formula ---------------------------

def test_negative_charisma_still_grants_one() -> None:
    _runtime, engine, instance = warlock_instance()
    canonical(instance)["abilities"]["cha"] = 6  # modifier -2, +3 still floors at 1
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4])

    assert temp_event(result)["amount"] == 1
    assert resources(instance)["temp_hp"] == 1


# --- #9 keep-max: a lower grant never replaces a higher existing value ----

def test_existing_temp_hp_is_kept_when_the_grant_is_lower() -> None:
    _runtime, engine, instance = warlock_instance()
    resources(instance)["temp_hp"] = 8
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4])

    assert temp_event(result)["amount"] == 5
    assert resources(instance)["temp_hp"] == 8


# --- #10 temp HP absorbs damage before hp (existing reducer behaviour) -----

def test_temp_hp_absorbs_damage_before_hp() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=40)])
    resources(instance).update({"temp_hp": 6})
    before = resources(instance)["hp"]

    direct_damage(engine, instance, 4)

    assert resources(instance)["temp_hp"] == 2
    assert resources(instance)["hp"] == before


# --- #11 a defeated enemy is not a fresh kill ------------------------------

def test_defeated_enemy_cannot_be_farmed() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1), enemy("goblin-2", hp=40)])
    attack(engine, instance, rolls=[10, 4])

    set_turn(engine, instance, "player:gm")
    resolved = engine.resolve_intent(instance, {
        "intent_id": "attack-corpse", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:dagger",
    }, SequenceRng([10, 4]))

    # 现有校验拒绝攻击已归零目标，触发点因此不会被重复进入。
    assert resolved["ok"] is False
    assert "living hostile" in resolved["error"]
    assert resources(instance)["temp_hp"] == 5


# --- #12 one batch, two beams, two kills -----------------------------------

def test_two_spell_beams_each_kill_once_and_keep_max() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [
        enemy("goblin-1", hp=1, position=5),
        enemy("goblin-2", hp=1, position=10),
    ], rolls=[20, 1, 1])
    grant(instance, "gm", "spell:scorching_ray")

    result = cast(
        engine, instance, "spell:scorching_ray", 2,
        ["enemy:goblin-1", "enemy:goblin-2"],
        rolls=[10, 1, 1, 10, 1, 1],
    )

    assert len(temp_events(result)) == 2
    assert {event["amount"] for event in temp_events(result)} == {5}
    assert resources(instance)["temp_hp"] == 5
    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["enemies"]["goblin-1"]["hp"] == 0
    assert combat["enemies"]["goblin-2"]["hp"] == 0


# --- #12b the same target hit by several beams triggers once --------------

@pytest.mark.parametrize("beam_count", [2, 3])
def test_repeated_beams_on_one_defeated_target_grant_once(beam_count: int) -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy("goblin-1", hp=1)])
    grant(instance, "gm", "spell:scorching_ray")

    result = cast(
        engine, instance, "spell:scorching_ray", 2,
        ["enemy:goblin-1"] * beam_count,
        rolls=[10, 1, 1] * beam_count,
    )

    damage_events = [
        event for event in events_of(result)
        if event["type"] == "resource.changed"
        and event.get("target_id") == "enemy:goblin-1"
    ]
    assert len(damage_events) == beam_count
    assert len(temp_events(result)) == 1
    assert resources(instance)["temp_hp"] == 5
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 0


def test_a_later_beam_that_first_crosses_the_threshold_grants_once() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy("goblin-1", hp=10)])
    grant(instance, "gm", "spell:scorching_ray")

    result = cast(
        engine, instance, "spell:scorching_ray", 2,
        ["enemy:goblin-1", "enemy:goblin-1"],
        rolls=[10, 1, 1, 10, 6, 6],
    )

    assert len(temp_events(result)) == 1
    assert resources(instance)["temp_hp"] == 5
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 0


# --- #13 the saving-throw damage path also triggers -----------------------

def test_saving_throw_kill_grants_temp_hp() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1)])
    grant(instance, "gm", "spell:inflict_wounds")
    grant_slot(instance, "gm", 1)

    result = cast(
        engine, instance, "spell:inflict_wounds", 1, ["enemy:goblin-1"],
        rolls=[1, 1, 1],
    )

    assert temp_event(result)["amount"] == 5
    assert resources(instance)["temp_hp"] == 5
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 0


# --- #14 without a holder nothing is granted ------------------------------

def test_ordinary_combat_emits_no_temp_hp() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, rogue_sheet(runtime))
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4, 1])

    assert temp_events(result) == []
    assert "temp_hp" not in resources(instance)


# --- #15 a grant does not revive a dying holder ---------------------------

def test_grant_at_zero_hp_does_not_restore_consciousness() -> None:
    _runtime, engine, instance = party_instance()
    canonical(instance)["resources"]["hp"] = 0
    canonical(instance)["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 0},
    }
    set_turn(engine, instance, "player:ally")
    result = attack(engine, instance, actor_id="player:ally", rolls=[10, 4, 1])

    assert temp_event(result)["target_id"] == "player:gm"
    assert resources(instance)["hp"] == 0
    assert resources(instance)["temp_hp"] == 5
    assert "unconscious" in canonical(instance)["conditions"]


# --- #16 event order: damage -> temp HP -> victory -----------------------

def test_temp_hp_event_precedes_victory() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4])

    events = events_of(result)
    damage_index = next(
        index for index, event in enumerate(events)
        if event["type"] == "resource.changed"
        and event.get("target_id") == "enemy:goblin-1"
    )
    temp_index = next(
        index for index, event in enumerate(events)
        if event["type"] == "resource.changed" and event.get("resource") == "temp_hp"
    )
    victory_index = events.index({"type": "dnd2024.combat.ended", "reason": "victory"})
    assert damage_index < temp_index < victory_index
    assert sum(event["type"] == "dnd2024.combat.ended" for event in events) == 1
