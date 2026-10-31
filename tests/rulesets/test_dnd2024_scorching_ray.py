"""Scorching Ray (P3): repeat-target beams resolve as independent attacks."""

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
# Ordered by spell level: the first eight are level 1, so the level 1/2
# spellbook prefixes never ask for a spell above that level's maximum.
SPELLBOOK = [
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:shield",
    "spell:thunderwave",
    "spell:charm_person",
    "spell:sleep",
    "spell:burning_hands",
    "spell:scorching_ray",
    "spell:invisibility",
    "spell:misty_step",
    "spell:mirror_image",
    "spell:fireball",
    "spell:fly",
]
LEVEL_ONE = SPELLBOOK[:8]


def wizard_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        "class:wizard", level
    )
    spellbook = SPELLBOOK[: requirements["spellbook_minimum"]]
    if "spell:scorching_ray" in spellbook:
        prepared = LEVEL_ONE[: requirements["prepared_spell_count"] - 1] + [
            "spell:scorching_ray"
        ]
    else:
        prepared = LEVEL_ONE[: requirements["prepared_spell_count"]]
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
        choices = {
            "class_spell_choices": {
                "cantrip_refs": [],
                "spellbook_refs": [],
                "prepared_spell_refs": [
                    "spell:heroism", "spell:cure_wounds", "spell:bless",
                ],
            }
        }
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def enemy(enemy_id: str, *, position: int = 5, hp: int = 200) -> dict:
    profile = goblin(position=position, hp=hp)
    profile["id"] = enemy_id
    return profile


def new_combat(
    *, level: int = 5, enemies=None, sheet: dict | None = None,
) -> tuple[Dnd2024Runtime, object, object]:
    runtime = Dnd2024Runtime()
    if sheet is None:
        sheet = wizard_sheet(runtime, level=level)
    engine, instance = monk_instance(runtime, sheet, uid="gm")
    enemy_list = list(enemies) if enemies is not None else [enemy("goblin-1")]
    rolls = [20, *[1 for _ in enemy_list]]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemy_list,
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"
    return runtime, engine, instance


def set_turn(engine, instance, actor_id: str = "player:gm") -> None:
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
    *, uid: str = "gm",
):
    set_turn(engine, instance, f"player:{uid}")
    return resolve(engine, instance, {
        "intent_id": f"cast:{spell_ref}:{slot}:{len(targets)}",
        "type": "cast_spell", "submitted_by": uid, "actor_id": f"player:{uid}",
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": targets,
    }, rolls)


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def checks_of(result: dict, kind: str) -> list[dict]:
    return [
        event for event in events_of(result)
        if event["type"] == "check.resolved" and event.get("kind") == kind
    ]


def damages_to(result: dict, target_id: str) -> list[dict]:
    return [
        event for event in events_of(result)
        if event["type"] == "resource.changed"
        and event.get("target_id") == target_id
        and not event.get("healing")
    ]


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


# --- #1 catalog validation of the new declaration -------------------------

def test_catalog_rejects_non_boolean_repeat_targets() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    effects = bundle.get("combat_catalog", "srd_combat_core")["spell_effects"]
    scorching_ray = deepcopy(effects["scorching_ray"])

    for bad in ("yes", 1):
        entry = {**scorching_ray, "repeat_targets": bad}
        with pytest.raises(CombatCatalogError):
            Dnd2024CombatCatalog.from_bundle(
                catalog_bundle(bundle, spell_effects={"scorching_ray": entry}),
            )


# --- #2 three beams focused on one goblin ---------------------------------

def test_three_beams_on_one_target_resolve_independently() -> None:
    _runtime, engine, instance = new_combat()
    result = cast(
        engine, instance, "spell:scorching_ray", 2, ["enemy:goblin-1"] * 3,
        rolls=[10, 1, 1, 10, 1, 1, 10, 1, 1],
    )

    checks = checks_of(result, "spell_attack")
    assert len(checks) == 3
    assert [check["natural"] for check in checks] == [10, 10, 10]
    assert [check["target_id"] for check in checks] == ["enemy:goblin-1"] * 3
    assert all(check["success"] for check in checks)

    damages = damages_to(result, "enemy:goblin-1")
    assert len(damages) == 3
    assert [damage["amount"] for damage in damages] == [2, 2, 2]
    assert [damage["rolls"] for damage in damages] == [[1, 1], [1, 1], [1, 1]]


# --- #3 three beams spread across three targets ---------------------------

def test_three_beams_split_across_three_targets() -> None:
    _runtime, engine, instance = new_combat(enemies=[
        enemy("goblin-1", position=5),
        enemy("goblin-2", position=10),
        enemy("goblin-3", position=15),
    ])
    targets = ["enemy:goblin-1", "enemy:goblin-2", "enemy:goblin-3"]
    result = cast(
        engine, instance, "spell:scorching_ray", 2, targets,
        rolls=[10, 1, 1, 10, 1, 1, 10, 1, 1],
    )

    checks = checks_of(result, "spell_attack")
    assert len(checks) == 3
    assert [check["target_id"] for check in checks] == targets
    for target_id in targets:
        damages = damages_to(result, target_id)
        assert len(damages) == 1
        assert damages[0]["amount"] == 2


# --- #4 duplicate targets only allowed for repeat_targets -----------------

def test_duplicate_targets_rejected_for_non_repeat_spell() -> None:
    runtime = Dnd2024Runtime()
    _runtime, paladin_engine, paladin_instance = new_combat(
        sheet=paladin_sheet(runtime),
    )
    set_turn(paladin_engine, paladin_instance)
    rejected = validate(
        paladin_engine, paladin_instance, type="cast_spell", actor_id="player:gm",
        spell_ref="spell:bless", slot_level=1,
        target_ids=["enemy:goblin-1"] * 3,
    )
    assert rejected["ok"] is False
    assert "unique targets" in rejected["error"]


def test_duplicate_targets_allowed_for_scorching_ray() -> None:
    _runtime, engine, instance = new_combat()
    set_turn(engine, instance)
    allowed = validate(
        engine, instance, type="cast_spell", actor_id="player:gm",
        spell_ref="spell:scorching_ray", slot_level=2,
        target_ids=["enemy:goblin-1"] * 3,
    )
    assert allowed["ok"] is True


# --- #5 upcasting adds one beam per slot level ----------------------------

def test_upcast_third_level_slot_allows_four_beams_but_rejects_five() -> None:
    _runtime, engine, instance = new_combat(level=5)
    set_turn(engine, instance)
    four = validate(
        engine, instance, type="cast_spell", actor_id="player:gm",
        spell_ref="spell:scorching_ray", slot_level=3,
        target_ids=["enemy:goblin-1"] * 4,
    )
    assert four["ok"] is True
    five = validate(
        engine, instance, type="cast_spell", actor_id="player:gm",
        spell_ref="spell:scorching_ray", slot_level=3,
        target_ids=["enemy:goblin-1"] * 5,
    )
    assert five["ok"] is False
    assert "unique targets" in five["error"]


# --- #6 a single critical beam doubles only its own damage ----------------

def test_single_critical_beam_doubles_only_its_damage() -> None:
    _runtime, engine, instance = new_combat()
    result = cast(
        engine, instance, "spell:scorching_ray", 2, ["enemy:goblin-1"] * 3,
        rolls=[10, 1, 1, 20, 2, 2, 2, 2, 10, 3, 3],
    )

    checks = checks_of(result, "spell_attack")
    assert [check["critical"] for check in checks] == [False, True, False]

    damages = damages_to(result, "enemy:goblin-1")
    assert [damage["critical"] for damage in damages] == [False, True, False]
    assert [len(damage["rolls"]) for damage in damages] == [2, 4, 2]
    assert [damage["amount"] for damage in damages] == [2, 8, 6]


# --- #7 an attack-consuming condition only covers the first beam ----------

def test_next_attack_advantage_is_consumed_by_the_first_beam_only() -> None:
    _runtime, engine, instance = new_combat()
    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["conditions"][
        "next_attack_advantage"
    ] = {"source": "test"}
    result = cast(
        engine, instance, "spell:scorching_ray", 2, ["enemy:goblin-1"] * 3,
        rolls=[10, 10, 1, 1, 5, 1, 1, 5, 1, 1],
    )

    checks = checks_of(result, "spell_attack")
    assert [check["advantage_mode"] for check in checks] == ["advantage", "", ""]
    assert [len(check["rolls"]) for check in checks] == [2, 1, 1]

    removed = [
        event for event in events_of(result)
        if event["type"] == "condition.removed"
        and event.get("condition") == "next_attack_advantage"
    ]
    assert len(removed) == 1
    assert "next_attack_advantage" not in ruleset_runtime.state(instance)["combat"][
        "enemies"
    ]["goblin-1"]["conditions"]


# --- #10 bless adds one d4 to every beam's attack roll --------------------

def test_blessed_caster_adds_a_d4_to_each_beam() -> None:
    _runtime, engine, instance = new_combat()
    canonical(instance).setdefault("conditions", {})["blessed"] = {"source": "test"}
    result = cast(
        engine, instance, "spell:scorching_ray", 2, ["enemy:goblin-1"] * 3,
        rolls=[10, 3, 1, 1, 10, 4, 1, 1, 10, 2, 1, 1],
    )

    checks = checks_of(result, "spell_attack")
    assert [check["bless_bonus"] for check in checks] == [3, 4, 2]
    assert [check["total"] for check in checks] == [
        check["natural"] + check["modifier"] + check["bless_bonus"] for check in checks
    ]


# --- #11 bless also feeds the concentration save --------------------------

def test_bless_also_applies_to_the_concentration_save() -> None:
    _runtime, engine, instance = new_combat()
    canonical(instance).setdefault("conditions", {})["blessed"] = {"source": "test"}
    view = engine._actor_view(
        instance, ruleset_runtime.state(instance)["combat"], "player:gm",
    )
    view["concentration"] = {"spell_ref": "spell:bless"}
    check = engine._concentration_events(view, 10, SequenceRng([12, 4]))[0]
    assert check["bless_bonus"] == 4
    assert check["total"] == 12 + check["modifier"] + 4


# --- #8 firing fewer beams is tolerated -----------------------------------

def test_second_level_slot_allows_firing_only_two_beams() -> None:
    _runtime, engine, instance = new_combat()
    set_turn(engine, instance)
    two = validate(
        engine, instance, type="cast_spell", actor_id="player:gm",
        spell_ref="spell:scorching_ray", slot_level=2,
        target_ids=["enemy:goblin-1"] * 2,
    )
    assert two["ok"] is True


# --- victory counts every beam of the same casting ------------------------

@pytest.mark.parametrize("enemies,targets", [
    ([("goblin-1", 5)], ["enemy:goblin-1"] * 3),  # no single beam kills it
    ([("goblin-1", 2), ("goblin-2", 2)], ["enemy:goblin-1", "enemy:goblin-2", "enemy:goblin-1"]),
])
def test_beams_that_together_defeat_every_enemy_end_the_combat(enemies, targets) -> None:
    _runtime, engine, instance = new_combat(enemies=[
        enemy(enemy_id, position=5 * (index + 1), hp=hp)
        for index, (enemy_id, hp) in enumerate(enemies)
    ])

    result = cast(
        engine, instance, "spell:scorching_ray", 2, targets,
        rolls=[10, 1, 1, 10, 1, 1, 10, 1, 1],
    )

    assert {"type": "dnd2024.combat.ended", "reason": "victory"} in events_of(result)
    assert ruleset_runtime.state(instance)["combat"]["status"] == "ended"


def test_beams_that_leave_an_enemy_standing_do_not_end_the_combat() -> None:
    _runtime, engine, instance = new_combat(enemies=[enemy("goblin-1", hp=7)])

    result = cast(
        engine, instance, "spell:scorching_ray", 2, ["enemy:goblin-1"] * 3,
        rolls=[10, 1, 1, 10, 1, 1, 10, 1, 1],
    )

    assert "dnd2024.combat.ended" not in [event["type"] for event in events_of(result)]
