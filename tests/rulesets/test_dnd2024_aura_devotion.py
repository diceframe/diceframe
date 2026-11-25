"""S-P6: Aura of Devotion condition-immunity interception."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features.models import ClassFeatureError
from src.rulesets.dnd2024.features.validation import validate_aura_parameters
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
from src.rulesets.events import stable_batch_id

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
)


PALADIN_SPELLS = [
    "spell:heroism",
    "spell:cure_wounds",
    "spell:bless",
    "spell:divine_smite",
    "spell:shield_of_faith",
    "spell:lesser_restoration",
    "spell:find_steed",
]
WIZARD_CANTRIPS = [
    "spell:light", "spell:mage_hand", "spell:ray_of_frost", "spell:fire_bolt",
]
WIZARD_SPELLBOOK = [
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:shield",
    "spell:color_spray",
    "spell:charm_person",
    "spell:thunderwave",
    "spell:sleep",
    "spell:hold_person",
    "spell:invisibility",
    "spell:blur",
    "spell:misty_step",
    "spell:mirror_image",
    "spell:fireball",
]


def paladin_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        "class:paladin", level,
    )
    return {
        "cantrip_refs": [],
        "spellbook_refs": [],
        "prepared_spell_refs": PALADIN_SPELLS[: requirements["prepared_spell_count"]],
    }


def wizard_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        "class:wizard", level,
    )
    return {
        "cantrip_refs": WIZARD_CANTRIPS[: requirements["cantrip_count"]],
        "prepared_spell_refs": WIZARD_SPELLBOOK[: requirements["prepared_spell_count"]],
        "spellbook_refs": WIZARD_SPELLBOOK[: requirements["spellbook_minimum"]],
    }


def _advance_with_spells(
    runtime: Dnd2024Runtime, sheet: dict, target_level: int, chooser,
) -> dict:
    while int(sheet["ruleset_character"]["build"]["level"]) < target_level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        if any(
            requirement["id"] == "class_spell_choices"
            for requirement in preview.get("requirements") or []
        ):
            choices["class_spell_choices"] = chooser(runtime, next_level)
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def paladin_sheet(runtime: Dnd2024Runtime, *, level: int = 7) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref="class:paladin",
        class_skill_refs=["skill:athletics", "skill:intimidation"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:paladin_a",
        class_spell_choices=paladin_choices(runtime, 1),
    )
    sheet = runtime.finalize_character(None, draft)
    return _advance_with_spells(runtime, sheet, level, paladin_choices)


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
    return _advance_with_spells(runtime, sheet, level, wizard_choices)


def enemy(*, position: int = 5) -> dict:
    profile = goblin(position=position, hp=100)
    profile["id"] = "goblin-1"
    profile["creature_type"] = "humanoid"
    return profile


def new_combat(
    *, paladin_level: int = 7, allies: dict[str, str] | None = None,
) -> tuple[Dnd2024Runtime, object, object]:
    runtime = Dnd2024Runtime()
    sheets = {"gm": paladin_sheet(runtime, level=paladin_level)}
    for uid, kind in (allies or {}).items():
        sheets[uid] = (
            wizard_sheet(runtime) if kind == "wizard" else paladin_sheet(runtime)
        )
    engine, instance = monk_instance(runtime, sheets["gm"], uid="gm")
    for uid, sheet in sheets.items():
        if uid != "gm":
            instance.players[uid] = {
                "character_name": uid.title(), "character_sheet": sheet,
            }
    player_rolls = [1 for _ in sorted(sheets)]
    player_rolls[sorted(sheets).index("gm")] = 20
    rolls = [*player_rolls, 1]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [enemy()],
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return runtime, engine, instance


def combat_state(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def place(instance, **positions: int) -> None:
    combat = combat_state(instance)
    for actor, position in positions.items():
        combat["positions"][actor] = position


def direct_cast(
    engine, instance, target_id: str, rolls=(), *,
    actor_id: str = "player:gm", spell_ref: str = "spell:charm_person",
    slot_level: int = 1,
) -> list[dict]:
    """Drive the spell resolution entry point with no target-side validation.

    Enemy spell profiles are disabled in this engine, so a hostile caster
    cannot reach ``_spell_events`` through ``resolve_intent``.  The positive
    interception cases therefore call the same authoritative resolver the
    intent path calls; only validation is skipped.
    """

    return engine._spell_events(
        instance,
        combat_state(instance),
        {
            "actor_id": actor_id, "spell_ref": spell_ref,
            "slot_level": slot_level, "target_ids": [target_id],
        },
        SequenceRng(list(rolls)),
    )


def apply_events(engine, instance, events: list[dict]) -> None:
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {"intent_id": f"resolve:{version}", "expected_version": version,
              "type": "cast_spell"}
    batch = {
        "batch_id": stable_batch_id(intent, version),
        "intent_id": intent["intent_id"], "intent_type": "cast_spell",
        "expected_version": version, "result_version": version + 1,
        "events": [
            {"type": "intent.submitted", "intent_type": "cast_spell",
             "actor_id": "player:gm", "submitted_by": "gm"},
            *events,
        ],
        "source_ref": "test",
    }
    result = engine.apply_batch(instance, batch)
    assert result["applied"], result


def real_cast(
    engine, instance, uid: str, spell_ref: str, target_id: str, rolls=(),
) -> dict:
    combat = combat_state(instance)
    actor_id = f"player:{uid}"
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, actor_id),
    )
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {
        "intent_id": f"cast:{spell_ref}:{version}", "type": "cast_spell",
        "expected_version": version, "submitted_by": uid, "actor_id": actor_id,
        "spell_ref": spell_ref, "slot_level": 1, "target_ids": [target_id],
    }
    result = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert result["ok"], result
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    return result


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def check_of(result: dict) -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "check.resolved"
        and event.get("kind") == "saving_throw"
    )


def applied_of(events: list[dict]) -> dict | None:
    return next(
        (event for event in events if event["type"] == "condition.applied"), None,
    )


def conditions_of(engine, instance, actor_id: str) -> dict:
    return engine._actor_view(instance, combat_state(instance), actor_id)["conditions"]


def interception_of(check: dict) -> dict:
    """The interception audit: which declaration, whose owner, which holder.

    S0-E2 replaced the SP6-era bare feature-id string with the three-field
    object the contract asks for (``{feature_id, owner, source_actor_id}``), so
    a consumer can tell two identically named auras apart and name the creature
    that actually supplied the immunity.  The assertions below therefore
    compare the whole object instead of a short id.
    """

    return check["blocked_by_aura"]


AURA_OF_DEVOTION = {
    "feature_id": "aura_of_devotion",
    "owner": "subclass:oath_of_devotion",
    "source_actor_id": "player:gm",
}


# --- A1/A2: aura blocks an in-range ally, outside range it does not ---------

def test_ally_inside_aura_is_immune_and_the_check_names_the_aura() -> None:
    _runtime, engine, instance = new_combat(allies={"ally": "wizard"})
    place(instance, **{"player:gm": 0, "player:ally": 10, "enemy:goblin-1": 5})

    events = direct_cast(engine, instance, "player:ally", rolls=[1])

    check = next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == "saving_throw"
    )
    assert check["success"] is False
    assert interception_of(check) == AURA_OF_DEVOTION
    assert applied_of(events) is None

    apply_events(engine, instance, events)
    assert "charmed" not in conditions_of(engine, instance, "player:ally")


def test_ally_outside_aura_is_charmed_without_the_aura_field() -> None:
    _runtime, engine, instance = new_combat(allies={"ally": "wizard"})
    place(instance, **{"player:gm": 0, "player:ally": 11, "enemy:goblin-1": 5})

    events = direct_cast(engine, instance, "player:ally", rolls=[1])

    check = next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == "saving_throw"
    )
    assert check["success"] is False
    assert "blocked_by_aura" not in check
    applied = applied_of(events)
    assert applied is not None
    assert applied["condition"] == "charmed"
    assert applied["duration"] == "1_hour"

    apply_events(engine, instance, events)
    assert "charmed" in conditions_of(engine, instance, "player:ally")


# --- A3: the paladin's own aura protects the paladin ---------------------

def test_the_paladin_is_immune_too() -> None:
    _runtime, engine, instance = new_combat()
    place(instance, **{"player:gm": 0, "enemy:goblin-1": 5})

    events = direct_cast(engine, instance, "player:gm", rolls=[1])

    check = next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == "saving_throw"
    )
    assert check["success"] is False
    assert interception_of(check) == AURA_OF_DEVOTION
    assert applied_of(events) is None

    apply_events(engine, instance, events)
    assert "charmed" not in conditions_of(engine, instance, "player:gm")


# --- A4: the feature is not granted before paladin level 7 ---------------

def test_level_six_paladin_has_no_aura() -> None:
    _runtime, engine, instance = new_combat(paladin_level=6, allies={"ally": "wizard"})
    place(instance, **{"player:gm": 0, "player:ally": 10, "enemy:goblin-1": 5})

    events = direct_cast(engine, instance, "player:ally", rolls=[1])

    check = next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == "saving_throw"
    )
    assert "blocked_by_aura" not in check
    assert applied_of(events) is not None

    apply_events(engine, instance, events)
    assert "charmed" in conditions_of(engine, instance, "player:ally")


# --- A5: an enemy target is never covered by the party aura --------------

def test_enemy_target_inside_range_is_not_immune() -> None:
    _runtime, engine, instance = new_combat(allies={"mage": "wizard"})
    place(instance, **{"player:gm": 0, "player:mage": 10, "enemy:goblin-1": 5})

    result = real_cast(engine, instance, "mage", "spell:charm_person", "enemy:goblin-1", rolls=[1])

    check = check_of(result)
    assert check["success"] is False
    assert "blocked_by_aura" not in check
    enemy_conditions = engine._actor_view(
        instance, combat_state(instance), "enemy:goblin-1",
    )["conditions"]
    assert "charmed" in enemy_conditions


# --- A6: the boundary is inclusive at exactly the aura range -------------

def test_aura_boundary_is_exactly_ten_feet() -> None:
    _runtime, engine, instance = new_combat(allies={"ally": "wizard"})
    place(instance, **{"player:gm": 0, "player:ally": 10, "enemy:goblin-1": 5})

    inside = direct_cast(engine, instance, "player:ally", rolls=[1])
    inside_check = next(
        event for event in inside
        if event["type"] == "check.resolved" and event.get("kind") == "saving_throw"
    )
    assert interception_of(inside_check) == AURA_OF_DEVOTION
    assert applied_of(inside) is None

    place(instance, **{"player:ally": 11})
    outside = direct_cast(engine, instance, "player:ally", rolls=[1])
    outside_check = next(
        event for event in outside
        if event["type"] == "check.resolved" and event.get("kind") == "saving_throw"
    )
    assert "blocked_by_aura" not in outside_check
    assert applied_of(outside) is not None


# --- A7: repeat saves never re-apply a condition ---------------------------------

def test_repeat_save_failure_does_not_reapply_a_condition() -> None:
    _runtime, engine, instance = new_combat(allies={"ally": "wizard"})
    place(instance, **{"player:gm": 0, "player:ally": 10, "enemy:goblin-1": 5})
    canonical = instance.get_character_sheet("ally")["ruleset_character"]
    canonical.setdefault("conditions", {})["charmed"] = {
        "source": "test",
        "save_repeat": {"ability": "wis", "dc": 20},
    }

    events = engine._save_repeat_events(
        instance, combat_state(instance), "player:ally", SequenceRng([1]),
    )

    repeat = next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == "saving_throw"
    )
    assert repeat["repeat_save"] is True
    assert repeat["success"] is False
    assert not any(event["type"] == "condition.applied" for event in events)


def test_sleep_is_not_immune_and_its_repeat_save_never_reapplies() -> None:
    _runtime, engine, instance = new_combat(allies={"mage": "wizard"})
    place(instance, **{"player:gm": 0, "player:mage": 10, "enemy:goblin-1": 5})

    result = real_cast(engine, instance, "mage", "spell:sleep", "enemy:goblin-1", rolls=[1])
    check = check_of(result)
    assert "blocked_by_aura" not in check
    assert enemy_conditions(engine, instance)["incapacitated"]

    turned = end_turn(engine, instance, "enemy:goblin-1", rolls=[1])
    assert not any(event["type"] == "condition.applied" for event in events_of(turned))
    assert "incapacitated" in enemy_conditions(engine, instance)


def end_turn(engine, instance, actor_id: str, rolls=()) -> dict:
    combat = combat_state(instance)
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, actor_id),
    )
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {
        "intent_id": f"end:{actor_id}:{version}", "type": "end_turn",
        "expected_version": version, "submitted_by": "gm", "actor_id": actor_id,
    }
    result = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert result["ok"], result
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    return result


def enemy_conditions(engine, instance) -> dict:
    return engine._actor_view(
        instance, combat_state(instance), "enemy:goblin-1",
    )["conditions"]


# --- A8: only the declared conditions are immune -------------------------

def test_color_spray_blinded_is_not_blocked_by_the_aura() -> None:
    _runtime, engine, instance = new_combat(allies={"mage": "wizard"})
    place(instance, **{"player:gm": 0, "player:mage": 10, "enemy:goblin-1": 5})

    result = real_cast(engine, instance, "mage", "spell:color_spray", "enemy:goblin-1", rolls=[1])

    check = check_of(result)
    assert "blocked_by_aura" not in check
    assert "blinded" in enemy_conditions(engine, instance)


# --- A9: two holders contribute one union -------------------------------

def aura_source_for(engine, instance, actor_id: str) -> dict | None:
    return engine._aura_condition_immunity_source(
        instance, combat_state(instance),
        engine._actor_view(instance, combat_state(instance), actor_id),
        "charmed",
    )


def test_two_paladins_union_in_range() -> None:
    """Either holder covers the target, and the audit names the one that did.

    SP6 asked ``_aura_immune_conditions`` for a ``{condition: feature_id}``
    map, which S0-E2 replaced with ``_aura_condition_immunity_source``: the
    immunity rides on a named base aura, so the answer is one source object or
    nothing.  The union property itself is unchanged and is now asserted by
    which holder supplied the source, not merely that some aura exists.
    """

    _runtime, engine, instance = new_combat(
        allies={"paladin2": "paladin", "target": "wizard"},
    )
    place(instance, **{
        "player:gm": 20, "player:paladin2": 0, "player:target": 5,
        "enemy:goblin-1": 15,
    })
    assert aura_source_for(engine, instance, "player:target") == {
        **AURA_OF_DEVOTION, "source_actor_id": "player:paladin2",
    }

    place(instance, **{"player:gm": 0, "player:paladin2": 20})
    assert aura_source_for(engine, instance, "player:target") == AURA_OF_DEVOTION

    # 两边都不在 10 尺内时没有任何来源：真正的并集，不是"有一个就算"。
    place(instance, **{"player:gm": 20, "player:paladin2": 40})
    assert aura_source_for(engine, instance, "player:target") is None


# --- A10: malformed declarations fail closed -----------------------------

@pytest.mark.parametrize(
    "feature_id, overrides",
    [
        ("aura_of_protection", {"aura_range_ft": 0}),
        ("aura_of_protection", {"aura_range_ft": True}),
        ("aura_of_devotion", {"aura_condition_immunities": "charmed"}),
        ("aura_of_devotion", {"aura_condition_immunities": ["charmed", 1]}),
    ],
)
def test_malformed_aura_declarations_fail_closed(feature_id, overrides) -> None:
    """A malformed aura never loads, so it can never silently grant nothing.

    SP6 patched the catalog at runtime and asserted the malformed declaration
    yielded no immunity.  S0-E2 moved the whole shape check to the load
    boundary (``validate_aura_parameters``), which is strictly stronger: the
    bundle is rejected instead of quietly projecting an aura that does nothing.
    The four shapes are the same four, retargeted at the surviving keys --
    ``aura_range``/``aura_immune_conditions`` no longer exist, so patching them
    would prove nothing.
    """

    _runtime, engine, instance = new_combat(allies={"ally": "wizard"})
    definition = engine.features.catalog.features_by_key[
        ("class:paladin", feature_id) if feature_id == "aura_of_protection"
        else ("subclass:oath_of_devotion", feature_id)
    ]
    malformed = replace(
        definition, parameters={**definition.parameters, **overrides},
    )
    with pytest.raises(ClassFeatureError):
        validate_aura_parameters(malformed, engine.features.catalog)
