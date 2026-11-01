"""False Life (P4): temp HP grant, keep-max, and damage absorption."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.catalog import (
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
)


CANTRIPS = ["spell:light", "spell:mage_hand", "spell:ray_of_frost", "spell:fire_bolt"]
# Ordered by spell level so every level's minimum spellbook prefix stays inside
# that level's maximum spell level; false_life sits at position 2 so it is
# inside the level 1 prepared prefix (4) and spellbook prefix (6).
SPELLBOOK = [
    "spell:detect_magic",
    "spell:false_life",
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


def wizard_sheet(runtime: Dnd2024Runtime, *, level: int = 1) -> dict:
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


def new_combat(*, level: int = 1) -> tuple[Dnd2024Runtime, object, object]:
    runtime = Dnd2024Runtime()
    gm_sheet = wizard_sheet(runtime, level=level)
    engine, instance = monk_instance(runtime, gm_sheet, uid="gm")
    start_combat(engine, instance, hp=100)
    return runtime, engine, instance


def start_combat(engine, instance, *, hp: int = 100) -> None:
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin(hp=hp)],
    }, SequenceRng([20, 1, 1]))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def resources(instance, uid: str = "gm") -> dict:
    return canonical(instance, uid)["resources"]


def set_turn(engine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def cast(
    engine, instance, slot: int = 1, rolls=(), *, target_id: str = "player:gm",
) -> dict:
    set_turn(engine, instance, "player:gm")
    payload = {
        "intent_id": f"false-life:{slot}:{ruleset_runtime.state(instance)['version']}",
        "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
        "actor_id": "player:gm",
        "spell_ref": "spell:false_life",
        "slot_level": slot,
        "target_id": target_id,
    }
    result = engine.resolve_intent(instance, payload, SequenceRng(list(rolls)))
    assert result["ok"], result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"], applied
    return result


def damage(
    engine, instance, amount: int, *, target_id: str = "player:gm",
    critical: bool = False,
) -> dict:
    version = ruleset_runtime.state(instance)["version"]
    batch = {
        "batch_id": f"batch_damage_{amount}_{version}",
        "intent_id": f"damage-{amount}",
        "intent_type": "attack",
        "expected_version": version,
        "result_version": version + 1,
        "events": [{
            "type": "resource.changed", "target_id": target_id, "resource": "hp",
            "delta": -amount, "amount": amount,
            **({"critical": True} if critical else {}),
        }],
        "source_ref": "test:false-life-damage",
    }
    return engine.apply_batch(instance, batch)


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def temp_event(result: dict) -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "resource.changed" and event.get("resource") == "temp_hp"
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


# --- #1 catalog validation of the new declarations --------------------------

def test_catalog_rejects_invalid_temp_hp_declarations() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    effects = bundle.get("combat_catalog", "srd_combat_core")["spell_effects"]
    false_life = deepcopy(effects["false_life"])
    entry = {**false_life, "target_count": 1, "range": 5}

    missing_formula = {key: value for key, value in entry.items() if key != "temp_hp"}
    non_string_formula = {**entry, "temp_hp": 6}
    non_int_flat = {**entry, "upcast_flat": "5"}
    too_large_flat = {**entry, "upcast_flat": 51}
    for bad in (missing_formula, non_string_formula, non_int_flat, too_large_flat):
        with pytest.raises(CombatCatalogError):
            Dnd2024CombatCatalog.from_bundle(
                catalog_bundle(bundle, spell_effects={"false_life": bad}),
            )

    Dnd2024CombatCatalog.from_bundle(
        catalog_bundle(bundle, spell_effects={"false_life": false_life}),
    )


# --- #2 grant lands as temp HP without touching hp / max_hp ---------------

def test_false_life_grants_temp_hp_from_the_base_formula() -> None:
    _runtime, engine, instance = new_combat(level=1)
    before = deepcopy(resources(instance))
    result = cast(engine, instance, slot=1, rolls=[2, 3])

    event = temp_event(result)
    assert event["target_id"] == "player:gm"
    assert event["amount"] == 9
    assert event["rolls"] == [2, 3]

    updated = resources(instance)
    assert updated["temp_hp"] == 9
    assert updated["hp"] == before["hp"]
    assert updated["max_hp"] == before["max_hp"]


# --- #3 keep-max: never stacks, only the higher value survives ------------

@pytest.mark.parametrize(
    "initial,rolls,expected",
    [(10, [2, 3], 10), (8, [4, 4], 12)],
)
def test_existing_temp_hp_is_kept_unless_the_new_value_is_higher(
    initial, rolls, expected,
) -> None:
    _runtime, engine, instance = new_combat(level=1)
    resources(instance)["temp_hp"] = initial
    cast(engine, instance, slot=1, rolls=rolls)
    assert resources(instance)["temp_hp"] == expected


# --- #4 damage spends temp HP before touching hp ---------------------------

def test_damage_absorbs_temp_hp_before_reducing_hp() -> None:
    _runtime, engine, instance = new_combat(level=5)
    resources(instance).update({"hp": 20, "temp_hp": 5})
    damage(engine, instance, 7)

    updated = resources(instance)
    assert updated["temp_hp"] == 0
    assert updated["hp"] == 18
    assert "unconscious" not in canonical(instance)["conditions"]


# --- #5 a fully absorbed hit leaves hp untouched ----------------------------

def test_fully_absorbed_damage_leaves_hp_untouched() -> None:
    _runtime, engine, instance = new_combat(level=5)
    resources(instance).update({"hp": 20, "temp_hp": 7})
    damage(engine, instance, 5)

    updated = resources(instance)
    assert updated["temp_hp"] == 2
    assert updated["hp"] == 20
    assert "unconscious" not in canonical(instance)["conditions"]


# --- #6 granting at full HP is legal (no max_hp clamp) ----------------------

def test_temp_hp_is_granted_at_full_hp() -> None:
    _runtime, engine, instance = new_combat(level=1)
    before = deepcopy(resources(instance))
    assert before["hp"] == before["max_hp"]

    result = cast(engine, instance, slot=1, rolls=[4, 4])
    assert resources(instance)["temp_hp"] == temp_event(result)["amount"]
    assert resources(instance)["hp"] == before["max_hp"]


# --- #7 upcasting adds a flat +5 per slot level above the first -----------

@pytest.mark.parametrize(
    "slot,expected",
    [(2, 11), (3, 16)],
)
def test_upcast_adds_five_per_level_above_the_first(slot, expected) -> None:
    _runtime, engine, instance = new_combat(level=5)
    result = cast(engine, instance, slot=slot, rolls=[1, 1])
    assert temp_event(result)["amount"] == expected
    assert resources(instance)["temp_hp"] == expected


# --- #8 temp HP is a beneficial effect and requires an allied target --------

def test_false_life_rejects_a_hostile_target() -> None:
    _runtime, engine, instance = new_combat(level=1)
    set_turn(engine, instance, "player:gm")
    validated = engine.validate_intent(instance, {
        "intent_id": "false-life-hostile",
        "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
        "actor_id": "player:gm",
        "spell_ref": "spell:false_life",
        "slot_level": 1,
        "target_id": "enemy:goblin-1",
    })
    assert validated["ok"] is False
    assert "allied" in validated["error"]


# --- #9 temp HP is not healing: cannot_regain_hp does not block it -----------

def test_cannot_regain_hp_does_not_block_temp_hp() -> None:
    _runtime, engine, instance = new_combat(level=1)
    before = deepcopy(resources(instance))
    canonical(instance).setdefault("conditions", {})["cannot_regain_hp"] = {"source": "test"}
    resource_result = cast(engine, instance, slot=1, rolls=[2, 3])

    assert temp_event(resource_result)["amount"] == 9
    updated = resources(instance)
    assert updated["temp_hp"] == 9
    assert updated["hp"] == before["hp"]


# --- #10 absorbed damage still fails a death save at 0 HP -------------------

def test_fully_absorbed_damage_still_adds_a_death_save_failure() -> None:
    _runtime, engine, instance = new_combat(level=1)
    canonical(instance)["resources"].update({"hp": 0, "temp_hp": 5})
    canonical(instance)["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 1},
    }
    damage(engine, instance, 3)

    updated = resources(instance)
    assert updated["temp_hp"] == 2
    assert updated["hp"] == 0
    assert canonical(instance)["conditions"]["death_saves"]["failures"] == 2


def test_fully_absorbed_critical_hit_adds_two_death_save_failures() -> None:
    _runtime, engine, instance = new_combat(level=1)
    canonical(instance)["resources"].update({"hp": 0, "temp_hp": 5})
    canonical(instance)["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 0},
    }
    damage(engine, instance, 3, critical=True)

    updated = resources(instance)
    assert updated["temp_hp"] == 2
    assert updated["hp"] == 0
    assert canonical(instance)["conditions"]["death_saves"]["failures"] == 2


@pytest.mark.parametrize(
    ("temp_hp", "amount", "expected_temp", "expected_failures"),
    [(5, 8, 0, 1), (0, 4, 0, 1)],
)
def test_partial_and_penetrating_damage_keep_hp_and_failure_counts(
    temp_hp, amount, expected_temp, expected_failures,
) -> None:
    _runtime, engine, instance = new_combat(level=1)
    canonical(instance)["resources"].update({"hp": 0, "temp_hp": temp_hp})
    canonical(instance)["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 0},
    }
    damage(engine, instance, amount)

    updated = resources(instance)
    assert updated["temp_hp"] == expected_temp
    assert updated["hp"] == 0
    assert canonical(instance)["conditions"]["death_saves"]["failures"] == expected_failures
