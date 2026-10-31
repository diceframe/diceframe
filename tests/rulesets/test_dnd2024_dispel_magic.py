"""Dispel Magic (P8): spell-source snapshots and magic condition removal."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.builder import ability_modifier
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
    start_combat,
)


CANTRIPS = ["spell:light", "spell:mage_hand", "spell:ray_of_frost", "spell:fire_bolt"]
# Ordered by spell level so every level's spellbook prefix stays inside that
# level's maximum spell level. Dispel Magic joins at level 5 and the two level
# 4 spells only become reachable at level 7.
SPELLBOOK = [
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:shield",
    "spell:charm_person",
    "spell:thunderwave",
    "spell:sleep",
    "spell:burning_hands",
    "spell:hold_person",
    "spell:invisibility",
    "spell:blur",
    "spell:misty_step",
    "spell:mirror_image",
    "spell:dispel_magic",
    "spell:fireball",
    "spell:fly",
    "spell:stoneskin",
    "spell:greater_invisibility",
]
PREPARED = [
    "spell:shield",
    "spell:magic_missile",
    "spell:mage_armor",
    "spell:detect_magic",
    "spell:charm_person",
    "spell:hold_person",
    "spell:blur",
    "spell:misty_step",
    "spell:dispel_magic",
    "spell:fireball",
    "spell:fly",
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


def wizard_sheet(runtime: Dnd2024Runtime, *, level: int = 7) -> dict:
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
    *, level: int = 7, allies=(), enemies=None,
) -> tuple[Dnd2024Runtime, object, object]:
    runtime = Dnd2024Runtime()
    sheets = {"gm": wizard_sheet(runtime, level=level)}
    for uid in allies:
        sheets[uid] = wizard_sheet(runtime, level=level)
    engine, instance = monk_instance(runtime, sheets["gm"], uid="gm")
    for uid, sheet in sheets.items():
        if uid != "gm":
            instance.players[uid] = {
                "character_name": uid.title(), "character_sheet": sheet,
            }
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
        "intent_id": f"cast:{spell_ref}:{slot}:{','.join(targets)}",
        "type": "cast_spell", "submitted_by": uid, "actor_id": f"player:{uid}",
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": targets,
        **extra,
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


# --- #1 catalog validation of the new mode -------------------------------

def test_catalog_rejects_unknown_modes_and_accepts_dispel() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    effects = bundle.get("combat_catalog", "srd_combat_core")["spell_effects"]
    dispel = deepcopy(effects["dispel_magic"])

    # The shipped entry loads unchanged.
    Dnd2024CombatCatalog.from_bundle(bundle)

    for bad in (
        {**dispel, "mode": "banish"},
        {**dispel, "mode": "dispel", "range": 4},
    ):
        merged = {**effects, "dispel_magic": bad}
        with pytest.raises(CombatCatalogError):
            Dnd2024CombatCatalog.from_bundle(
                catalog_bundle(bundle, spell_effects=merged),
            )


# --- #2 the applied-condition snapshot records the source spell ----------

def test_condition_snapshot_records_spell_ref_and_cast_level() -> None:
    _runtime, engine, instance = new_combat()

    cast(engine, instance, "spell:hold_person", 2, ["enemy:goblin-1"], rolls=[1])

    meta = enemy_conditions(instance)["paralyzed"]
    assert meta["source_spell_ref"] == "spell:hold_person"
    assert meta["source_spell_level"] == 2
    assert meta["concentration_owner"] == "player:gm"

    cast(engine, instance, "spell:ray_of_frost", 0, ["enemy:goblin-1"], rolls=[15, 3, 4])

    slowed = enemy_conditions(instance)["slowed_10"]
    assert slowed["source_spell_ref"] == "spell:ray_of_frost"
    assert slowed["source_spell_level"] == 0


# --- #3 free casts snapshot the spell's own level ------------------------

def test_feat_free_cast_snapshots_the_spell_level() -> None:
    runtime = Dnd2024Runtime()
    draft = monk_draft(runtime)
    draft.update(
        background_ref="background:sage",
        background_equipment_package_ref="equipment_package:sage_b",
    )
    choices = runtime.builder_choices(None, draft)
    refs = choices["background_ability_refs"]
    draft["background_ability_bonuses"] = {
        refs[0].split(":", 1)[1]: 2, refs[1].split(":", 1)[1]: 1,
    }
    draft["feat_choice_answers"] = {
        feat["feat_ref"]: {
            spec["id"]: [option["value"] for option in spec["options"]][: int(spec["count"])]
            for spec in feat["specs"]
        }
        for feat in choices["feat_choices"]
    }
    draft["feat_choice_answers"]["feat:magic_initiate_wizard"] = {
        "spellcasting_ability": ["int"],
        "cantrips": ["fire_bolt", "chill_touch"],
        "level_1_spell": ["charm_person"],
    }
    assert runtime.validate_character(None, draft) == []
    sheet = runtime.finalize_character(None, draft)
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance, hp=100)

    result = resolve(engine, instance, {
        "intent_id": "cast:free-charm",
        "type": "cast_spell", "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:charm_person", "slot_level": 0,
        "target_ids": ["enemy:goblin-1"],
    }, [1])

    meta = enemy_conditions(instance)["charmed"]
    assert meta["source_spell_ref"] == "spell:charm_person"
    assert meta["source_spell_level"] == 1
    assert any(
        event["type"] == "dnd2024.feat_spell.free_cast_spent"
        for event in events_of(result)
    )


# --- #4 a source at or below the slot level needs no roll ---------------

def test_low_level_source_is_removed_without_consuming_a_roll() -> None:
    _runtime, engine, instance = new_combat()
    add_enemy_condition(
        instance, "slowed_10",
        {"source_spell_ref": "spell:ray_of_frost", "source_spell_level": 0},
    )
    add_enemy_condition(instance, "rage", {"source": "test"})
    add_enemy_condition(instance, "dodging", {"source": "test"})

    rng = RecordingRng([1, 2])
    result = engine.resolve_intent(instance, request(instance, {
        "intent_id": "dispel:auto", "type": "cast_spell", "submitted_by": "gm",
        "actor_id": "player:gm", "spell_ref": "spell:dispel_magic",
        "slot_level": 3, "target_ids": ["enemy:goblin-1"],
    }), rng)
    assert result["ok"], result
    assert engine.apply_batch(instance, result["event_batch"])["applied"]

    removed = event_of(result, "condition.removed", condition="slowed_10")
    assert removed["reason"] == "dispelled"
    assert not any(
        event["type"] == "check.resolved" for event in events_of(result)
    )
    assert rng.d20_requests() == 0
    assert rng.values == [1, 2]

    after = enemy_conditions(instance)
    assert "slowed_10" not in after
    assert "rage" in after and "dodging" in after


# --- #5 a concentration source ends the whole spell on every target ------

def test_concentration_source_ends_the_whole_spell_across_targets() -> None:
    _runtime, engine, instance = new_combat(
        enemies=[enemy("goblin-1"), enemy("goblin-2")],
    )
    cast(
        engine, instance, "spell:hold_person", 3,
        ["enemy:goblin-1", "enemy:goblin-2"], rolls=[1, 1],
    )
    assert "paralyzed" in enemy_conditions(instance, "goblin-1")
    assert "paralyzed" in enemy_conditions(instance, "goblin-2")
    assert canonical(instance)["spellcasting"]["class"]["concentration"] is not None

    result = cast(engine, instance, "spell:dispel_magic", 3, ["enemy:goblin-1"])

    ended = [
        event for event in events_of(result)
        if event["type"] == "dnd2024.concentration.ended"
    ]
    assert len(ended) == 1
    assert ended[0]["actor_id"] == "player:gm"
    assert ended[0]["reason"] == "dispelled"
    # The cascade removes every target of the dispelled concentration spell;
    # this branch never emits per-target condition.removed.
    assert not any(
        event["type"] == "condition.removed" and event.get("condition") == "paralyzed"
        for event in events_of(result)
    )
    assert "paralyzed" not in enemy_conditions(instance, "goblin-1")
    assert "paralyzed" not in enemy_conditions(instance, "goblin-2")
    assert canonical(instance)["spellcasting"]["class"]["concentration"] is None


# --- #6 a higher-level source needs an ability check ---------------------

def inject_high_level_source(instance, owner: str = "enemy:goblin-1") -> None:
    add_enemy_condition(instance, "paralyzed", {
        "source_spell_ref": "spell:hold_person",
        "source_spell_level": 4,
        "concentration_owner": owner,
    })


def test_high_level_source_failure_keeps_the_condition() -> None:
    _runtime, engine, instance = new_combat()
    inject_high_level_source(instance)
    modifier = ability_modifier(canonical(instance)["abilities"]["int"])

    result = cast(engine, instance, "spell:dispel_magic", 3, ["enemy:goblin-1"], rolls=[5])

    check = check_of(result, "ability_check")
    assert check["ability"] == "int"
    assert check["target"] == 14
    assert check["modifier"] == modifier
    assert check["bless_bonus"] == 0
    assert check["rolls"] == [5]
    assert check["natural"] == 5
    assert check["total"] == 5 + modifier
    assert check["success"] is False
    assert check["condition"] == "paralyzed"

    assert "paralyzed" in enemy_conditions(instance)
    assert not any(
        event["type"] == "dnd2024.concentration.ended" for event in events_of(result)
    )


# --- #7 a successful check ends the source concentration -----------------

def test_high_level_source_success_ends_the_concentration() -> None:
    _runtime, engine, instance = new_combat()
    inject_high_level_source(instance)
    modifier = ability_modifier(canonical(instance)["abilities"]["int"])

    result = cast(engine, instance, "spell:dispel_magic", 3, ["enemy:goblin-1"], rolls=[15])

    check = check_of(result, "ability_check")
    assert check["success"] is True
    assert check["total"] == 15 + modifier
    ended = event_of(
        result, "dnd2024.concentration.ended", actor_id="enemy:goblin-1",
    )
    assert ended["reason"] == "dispelled"
    assert "paralyzed" not in enemy_conditions(instance)


# --- #8 upcasting removes a source equal to the slot level --------------

def test_upcast_dispel_removes_a_source_matching_the_slot_level() -> None:
    _runtime, engine, instance = new_combat()
    add_enemy_condition(instance, "paralyzed", {
        "source_spell_ref": "spell:hold_person", "source_spell_level": 4,
    })

    result = cast(engine, instance, "spell:dispel_magic", 4, ["enemy:goblin-1"])

    assert not any(
        event["type"] == "check.resolved" for event in events_of(result)
    )
    removed = event_of(result, "condition.removed", condition="paralyzed")
    assert removed["reason"] == "dispelled"
    assert "paralyzed" not in enemy_conditions(instance)


# --- #9 targets may belong to either side -------------------------------

def test_dispel_targets_hostiles_and_allies() -> None:
    _runtime, engine, instance = new_combat(allies=["ally"])
    add_enemy_condition(instance, "slowed_10", {
        "source_spell_ref": "spell:ray_of_frost", "source_spell_level": 0,
    })

    hostile = cast(engine, instance, "spell:dispel_magic", 3, ["enemy:goblin-1"])
    assert event_of(hostile, "condition.removed", condition="slowed_10")
    assert "slowed_10" not in enemy_conditions(instance)

    cast(engine, instance, "spell:blur", 2, ["player:ally"], uid="ally")
    assert "blurred" in canonical(instance, "ally")["conditions"]

    allied = cast(engine, instance, "spell:dispel_magic", 3, ["player:ally"])
    ended = event_of(
        allied, "dnd2024.concentration.ended", actor_id="player:ally",
    )
    assert ended["reason"] == "dispelled"
    assert "blurred" not in canonical(instance, "ally")["conditions"]


# --- #10 dead targets are rejected, dying targets are allowed -----------

def test_dispel_rejects_dead_targets_but_allows_dying_ones() -> None:
    _runtime, engine, instance = new_combat(allies=["ally"])
    add_player_condition(instance, "ally", "dead")

    rejected = validate(
        engine, instance, type="cast_spell", actor_id="player:gm",
        spell_ref="spell:dispel_magic", slot_level=3, target_ids=["player:ally"],
    )
    assert rejected["ok"] is False
    assert "dead" in rejected["error"]

    _runtime, engine, instance = new_combat(allies=["ally"])
    canonical(instance, "ally")["resources"]["hp"] = 0

    dying = validate(
        engine, instance, type="cast_spell", actor_id="player:gm",
        spell_ref="spell:dispel_magic", slot_level=3, target_ids=["player:ally"],
    )
    assert dying["ok"] is True


# --- #11 multiple sources are resolved in insertion order ---------------

def test_multiple_sources_are_resolved_in_insertion_order() -> None:
    _runtime, engine, instance = new_combat()
    add_enemy_condition(instance, "slowed_10", {
        "source_spell_ref": "spell:ray_of_frost", "source_spell_level": 0,
    })
    inject_high_level_source(instance)

    result = cast(engine, instance, "spell:dispel_magic", 3, ["enemy:goblin-1"], rolls=[15])

    events = events_of(result)
    removed_index = next(
        index for index, event in enumerate(events)
        if event["type"] == "condition.removed"
    )
    check_index = next(
        index for index, event in enumerate(events)
        if event["type"] == "check.resolved"
    )
    ended_index = next(
        index for index, event in enumerate(events)
        if event["type"] == "dnd2024.concentration.ended"
    )
    assert removed_index < check_index < ended_index
    assert "slowed_10" not in enemy_conditions(instance)
    assert "paralyzed" not in enemy_conditions(instance)


# --- #12 dispel spends the action and one third-level slot --------------

def test_dispel_spends_the_action_and_a_slot() -> None:
    _runtime, engine, instance = new_combat()
    assert canonical(instance)["spellcasting"]["class"]["slots_current"]["3"] == 3

    result = cast(engine, instance, "spell:dispel_magic", 3, ["enemy:goblin-1"])

    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["economy"]["action"] == 0
    assert combat["economy"]["slot_spell_cast"] is True
    assert canonical(instance)["spellcasting"]["class"]["slots_current"]["3"] == 2
    cast_event = event_of(result, "dnd2024.spell.cast", spell_ref="spell:dispel_magic")
    assert cast_event["slot_level"] == 3
