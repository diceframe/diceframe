"""S-P2: die manipulation (Supreme Healing / Overchannel) and RNG ordering.

The two end-to-end carriers are subclass features, so every sheet is built
through the real advancement path (``advance()`` scaffolding from the shared
monk fixtures) rather than by hand-writing canonical state.
"""

from __future__ import annotations

from copy import deepcopy
from itertools import count

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.combat.action_adapter import (
    double_dice_counts,
    maximize_dice_nodes,
)
from src.rulesets.dnd2024.features import (
    Dnd2024ClassFeatureCatalog,
    Dnd2024ClassFeatureResolver,
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


LOCALE = "en"
CLERIC_SKILLS = ["skill:history", "skill:medicine"]
WIZARD_SKILLS = ["skill:arcana", "skill:history"]
# STR stays low so four ASIs (levels 4/8/12/16) never push it past 20.
CLERIC_ABILITIES = {"str": 8, "dex": 10, "con": 13, "int": 12, "wis": 15, "cha": 14}
WIZARD_ABILITIES = {"str": 8, "dex": 12, "con": 13, "int": 15, "wis": 14, "cha": 10}

_INTENT_IDS = count(1)


# --- sheets ----------------------------------------------------------------

def _spell_choices(runtime: Dnd2024Runtime, class_ref: str, level: int) -> dict:
    options = Dnd2024SpellSelection(runtime.load_bundle(LOCALE)).options(class_ref, level)
    requirement = options["requirements"]
    cantrips = [row["ref"] for row in options["cantrips"]]
    leveled = [row["ref"] for row in options["leveled_spells"]]
    cantrip_refs = cantrips[: int(requirement["cantrip_count"])]
    if class_ref == "class:wizard":
        minimum = int(requirement["spellbook_minimum"])
        spellbook = leveled[:minimum]
        assert len(spellbook) == minimum, (level, minimum, len(leveled))
        prepared = spellbook[: int(requirement["prepared_spell_count"])]
        return {
            "cantrip_refs": cantrip_refs,
            "prepared_spell_refs": prepared,
            "spellbook_refs": spellbook,
        }
    prepared = leveled[: int(requirement["prepared_spell_count"])]
    assert len(prepared) == requirement["prepared_spell_count"], level
    return {
        "cantrip_refs": cantrip_refs,
        "prepared_spell_refs": prepared,
        "spellbook_refs": [],
    }


def _build_sheet(
    runtime: Dnd2024Runtime, class_ref: str, skills: list[str],
    package: str, abilities: dict[str, int], level: int,
) -> dict:
    draft = monk_draft(runtime, LOCALE)
    draft.update(
        class_ref=class_ref,
        class_skill_refs=skills,
        class_tool_refs=[],
        equipment_package_ref=package,
        base_abilities=abilities,
        class_spell_choices=_spell_choices(runtime, class_ref, 1),
    )
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        choices["class_spell_choices"] = _spell_choices(runtime, class_ref, next_level)
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def cleric17(runtime: Dnd2024Runtime) -> dict:
    return _build_sheet(
        runtime, "class:cleric", CLERIC_SKILLS, "equipment_package:cleric_a",
        CLERIC_ABILITIES, 17,
    )


@pytest.fixture(scope="module")
def cleric16(runtime: Dnd2024Runtime) -> dict:
    return _build_sheet(
        runtime, "class:cleric", CLERIC_SKILLS, "equipment_package:cleric_a",
        CLERIC_ABILITIES, 16,
    )


@pytest.fixture(scope="module")
def wizard14(runtime: Dnd2024Runtime) -> dict:
    return _build_sheet(
        runtime, "class:wizard", WIZARD_SKILLS, "equipment_package:wizard_a",
        WIZARD_ABILITIES, 14,
    )


@pytest.fixture(scope="module")
def wizard13(runtime: Dnd2024Runtime) -> dict:
    return _build_sheet(
        runtime, "class:wizard", WIZARD_SKILLS, "equipment_package:wizard_a",
        WIZARD_ABILITIES, 13,
    )


# --- combat helpers --------------------------------------------------------

def enemy(enemy_id: str = "goblin-1", *, position: int = 5, hp: int = 40) -> dict:
    raw = goblin(position=position, hp=hp)
    raw["id"] = enemy_id
    raw["name"] = enemy_id
    return raw


def new_combat(runtime: Dnd2024Runtime, sheet: dict, *, enemies=None):
    """Start a combat with the player acting first (initiative roll 20)."""

    engine, instance = monk_instance(runtime, deepcopy(sheet))
    enemies = enemies or [goblin(position=5, hp=40)]
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"start-{next(_INTENT_IDS)}", "type": "combat.start",
        "expected_version": 0, "submitted_by": "gm", "enemies": enemies,
    }, SequenceRng([20, *([1] * len(enemies))]))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"
    return engine, instance


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def set_turn(engine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def grant(instance, *refs: str, uid: str = "gm") -> None:
    magic = canonical(instance, uid)["spellcasting"]["class"]
    for ref in refs:
        if ref not in magic["prepared_spell_refs"]:
            magic["prepared_spell_refs"].append(ref)


def grant_cantrip(instance, *refs: str, uid: str = "gm") -> None:
    magic = canonical(instance, uid)["spellcasting"]["class"]
    for ref in refs:
        if ref not in magic["cantrip_refs"]:
            magic["cantrip_refs"].append(ref)


def cast_result(
    engine, instance, spell_ref: str, slot: int, targets: list[str],
    *, rolls=(), uid: str = "gm", **extra,
):
    set_turn(engine, instance, f"player:{uid}")
    request = {
        "intent_id": f"cast-{next(_INTENT_IDS)}",
        "type": "cast_spell",
        "submitted_by": uid,
        "actor_id": f"player:{uid}",
        "spell_ref": spell_ref,
        "slot_level": slot,
        "target_ids": list(targets),
        "expected_version": ruleset_runtime.state(instance)["version"],
        **extra,
    }
    rng = SequenceRng(list(rolls))
    return engine.resolve_intent(instance, request, rng), rng


def cast(engine, instance, spell_ref: str, slot: int, targets: list[str], **kwargs):
    result, _rng = cast_result(engine, instance, spell_ref, slot, targets, **kwargs)
    assert result["ok"], result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"], applied
    return result


def reject(engine, instance, spell_ref: str, slot: int, targets: list[str], **kwargs) -> str:
    result, _rng = cast_result(engine, instance, spell_ref, slot, targets, **kwargs)
    assert result["ok"] is False, result
    return str(result["error"])


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def healing_to(result: dict, target_id: str) -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "resource.changed"
        and event.get("target_id") == target_id
        and event.get("healing")
    )


def damage_to(result: dict, target_id: str) -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "resource.changed"
        and event.get("resource") == "hp"
        and event.get("target_id") == target_id
        and not event.get("healing")
    )


def overchannel_cost(result: dict) -> dict:
    return next(event for event in events_of(result) if event.get("overchannel_cost"))


def check_of(result: dict, kind: str) -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def heal_modifier(instance) -> int:
    return ability_modifier(int(canonical(instance)["abilities"]["wis"]))


def set_hp(instance, hp: int, uid: str = "gm") -> None:
    canonical(instance, uid)["resources"]["hp"] = hp


# --- T1: catalog declarations ---------------------------------------------

def test_catalog_declares_cleric_and_wizard_features() -> None:
    runtime = Dnd2024Runtime()
    for locale in ("en", "zh-CN"):
        catalog = Dnd2024ClassFeatureCatalog.from_bundle(runtime.load_bundle(locale))
        supreme = catalog.features["supreme_healing"]
        overchannel = catalog.features["overchannel"]
        assert supreme.class_id == "cleric"
        assert supreme.minimum_level == 17
        assert dict(supreme.parameters) == {}
        assert overchannel.class_id == "wizard"
        assert overchannel.minimum_level == 14
        assert dict(overchannel.parameters) == {
            "max_slot_level": 5,
            "cost_dice_per_slot": 2,
            "cost_die_sides": 12,
            "cost_damage_type": "necrotic",
        }

    english = Dnd2024ClassFeatureCatalog.from_bundle(runtime.load_bundle("en"))
    assert english.labels["supreme_healing"] == "Supreme Healing"
    assert english.labels["overchannel"] == "Overchannel"
    chinese = Dnd2024ClassFeatureCatalog.from_bundle(runtime.load_bundle("zh-CN"))
    assert chinese.labels["supreme_healing"] == "极效治疗"
    assert chinese.labels["overchannel"] == "超限导能"


# --- T2: ownership follows the class/subclass level ------------------------

def test_feature_ownership_follows_levels(runtime, cleric17, cleric16, wizard14, wizard13) -> None:
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))
    assert resolver.has_feature(cleric17["ruleset_character"], "supreme_healing") is True
    assert resolver.has_feature(cleric16["ruleset_character"], "supreme_healing") is False
    assert resolver.has_feature(wizard14["ruleset_character"], "overchannel") is True
    assert resolver.has_feature(wizard13["ruleset_character"], "overchannel") is False


# --- T3: AST transform -----------------------------------------------------

def test_maximize_dice_nodes_transforms_only_dice() -> None:
    assert maximize_dice_nodes({"op": "dice", "formula": "2d8"}) == {
        "op": "constant", "value": 16,
    }
    tree = {
        "op": "add",
        "args": [
            {"op": "dice", "formula": "2d6"},
            {"op": "constant", "value": 3},
            {
                "op": "multiply",
                "args": [
                    {"op": "dice", "formula": "1d4"},
                    {"op": "constant", "value": 2},
                ],
            },
        ],
    }
    snapshot = deepcopy(tree)
    maximized = maximize_dice_nodes(tree)
    assert tree == snapshot, "input tree must not be mutated"
    assert maximized == {
        "op": "add",
        "args": [
            {"op": "constant", "value": 12},
            {"op": "constant", "value": 3},
            {
                "op": "multiply",
                "args": [
                    {"op": "constant", "value": 4},
                    {"op": "constant", "value": 2},
                ],
            },
        ],
    }
    for op in ("subtract", "min", "max"):
        node = {"op": op, "args": [{"op": "dice", "formula": "1d10"}, {"op": "constant", "value": 2}]}
        assert maximize_dice_nodes(node) == {
            "op": op, "args": [{"op": "constant", "value": 10}, {"op": "constant", "value": 2}],
        }
    # Constants and unknown ops pass through unchanged (same as double_dice_counts).
    assert maximize_dice_nodes({"op": "constant", "value": 5}) == {"op": "constant", "value": 5}
    unknown = {"op": "floor", "args": [{"op": "dice", "formula": "1d6"}]}
    assert maximize_dice_nodes(unknown) == unknown
    # Crit first, maximize second: doubling the count then maximizing = face value × 2.
    assert maximize_dice_nodes(double_dice_counts({"op": "dice", "formula": "2d8"})) == {
        "op": "constant", "value": 32,
    }


# --- T4: Supreme Healing on cure_wounds ------------------------------------

def test_supreme_healing_maximizes_cure_wounds_without_rng(cleric17, runtime) -> None:
    engine, instance = new_combat(runtime, cleric17)
    result, rng = cast_result(engine, instance, "spell:cure_wounds", 1, ["player:gm"], rolls=[99])
    assert result["ok"], result
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    assert rng.values == [99], "maximized dice must not consume RNG"
    healed = healing_to(result, "player:gm")
    assert healed["amount"] == 16 + heal_modifier(instance)
    assert healed["rolls"] == []
    assert healed["maximized"] is True


# --- T5: Supreme Healing scales with the slot ------------------------------

def test_supreme_healing_maximizes_upcast_dice(cleric17, runtime) -> None:
    engine, instance = new_combat(runtime, cleric17)
    modifier = heal_modifier(instance)

    result = cast(engine, instance, "spell:cure_wounds", 3, ["player:gm"], rolls=[99])
    healed = healing_to(result, "player:gm")
    # 2d8 base + 2d8 per extra slot level → 6d8, all maximized.
    assert healed["amount"] == 48 + modifier
    assert healed["rolls"] == []
    assert healed["maximized"] is True

    result = cast(engine, instance, "spell:healing_word", 2, ["player:gm"], rolls=[99])
    healed = healing_to(result, "player:gm")
    # 2d4 base + 2d4 per extra slot level → 4d4, all maximized.
    assert healed["amount"] == 16 + modifier
    assert healed["rolls"] == []
    assert healed["maximized"] is True


# --- T6: below level 17 nothing is maximized -------------------------------

def test_cure_wounds_below_seventeen_still_rolls(cleric16, runtime) -> None:
    engine, instance = new_combat(runtime, cleric16)
    result = cast(engine, instance, "spell:cure_wounds", 1, ["player:gm"], rolls=[4, 5])
    healed = healing_to(result, "player:gm")
    assert healed["rolls"] == [4, 5]
    assert healed["amount"] == 9 + heal_modifier(instance)
    assert "maximized" not in healed


# --- T7: temporary HP is not healing ---------------------------------------

def test_supreme_healing_does_not_touch_temp_hp(cleric17, runtime) -> None:
    engine, instance = new_combat(runtime, cleric17)
    grant(instance, "spell:false_life")
    result = cast(engine, instance, "spell:false_life", 1, ["player:gm"], rolls=[2, 3])
    temp = next(
        event for event in events_of(result)
        if event["type"] == "resource.changed" and event.get("resource") == "temp_hp"
    )
    assert temp["amount"] == 9
    assert temp["rolls"] == [2, 3]
    assert "maximized" not in temp


# --- T8: intent validation fails closed ------------------------------------

def test_maximize_damage_validation_matrix(cleric17, wizard13, wizard14, runtime) -> None:
    engine, instance = new_combat(runtime, cleric17)
    assert reject(
        engine, instance, "spell:cure_wounds", 1, ["player:gm"], maximize_damage=True,
    ) == "overchannel is required to maximize damage"

    engine, instance = new_combat(runtime, wizard13)
    grant(instance, "spell:magic_missile")
    assert reject(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"], maximize_damage=True,
    ) == "overchannel is required to maximize damage"

    # Explicit false is a no-op, not a request that still requires Overchannel.
    result = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        maximize_damage=False, rolls=[1, 2, 3],
    )
    assert damage_to(result, "enemy:goblin-1")["amount"] == 9
    assert not any(event.get("overchannel_cost") for event in events_of(result))

    engine, instance = new_combat(runtime, wizard14)
    grant(instance, "spell:cure_wounds", "spell:thunderwave")
    grant_cantrip(instance, "spell:fire_bolt")
    assert reject(
        engine, instance, "spell:cure_wounds", 1, ["player:gm"], maximize_damage=True,
    ) == "this spell does not deal damage"
    assert reject(
        engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"], maximize_damage=True,
    ) == "maximized casting requires a spell slot of level 1-5"
    assert reject(
        engine, instance, "spell:thunderwave", 6, ["enemy:goblin-1"], maximize_damage=True,
    ) == "maximized casting requires a spell slot of level 1-5"
    assert reject(
        engine, instance, "spell:thunderwave", 1, ["enemy:goblin-1"], maximize_damage="yes",
    ) == "maximize_damage must be a boolean"

    # No intent parameter → the same spell still casts normally, with real dice.
    result = cast(engine, instance, "spell:thunderwave", 1, ["enemy:goblin-1"], rolls=[1, 3, 4])
    damage = damage_to(result, "enemy:goblin-1")
    assert damage["amount"] == 7
    assert damage["rolls"] == [3, 4]
    assert not any(event.get("overchannel_cost") for event in events_of(result))


def _available_spell_rows(engine, instance) -> dict[str, dict]:
    action = next(
        row for row in engine.available_intents(instance, "gm")
        if row["type"] == "cast_spell"
    )
    return {row["spell_ref"]: row for row in action["spells"]}


def test_available_spells_expose_overchannel_only_for_eligible_casts(
    wizard14, wizard13, runtime,
) -> None:
    engine, instance = new_combat(runtime, wizard14)
    rows = _available_spell_rows(engine, instance)
    assert rows["spell:chromatic_orb"]["maximize_damage_available"] is True
    assert rows["spell:burning_hands"]["maximize_damage_available"] is True
    # A non-damaging spell or a cantrip with no leveled slot is never eligible.
    assert rows["spell:charm_person"]["maximize_damage_available"] is False
    assert rows["spell:fire_bolt"]["maximize_damage_available"] is False

    # A 6th-level slot can pay for the spell, but Overchannel stops at 5.
    canonical(instance)["spellcasting"]["class"]["slots_current"] = {"6": 1}
    rows = _available_spell_rows(engine, instance)
    assert rows["spell:burning_hands"]["available_slot_levels"] == [6]
    assert rows["spell:burning_hands"]["maximize_damage_available"] is False

    engine, instance = new_combat(runtime, wizard13)
    rows = _available_spell_rows(engine, instance)
    assert rows["spell:chromatic_orb"]["maximize_damage_available"] is False


# --- T9: saving_throw damage face ------------------------------------------

def test_overchannel_maximizes_saving_throw_damage(wizard14, runtime) -> None:
    engine, instance = new_combat(runtime, wizard14)
    grant(instance, "spell:thunderwave")

    result = cast(
        engine, instance, "spell:thunderwave", 1, ["enemy:goblin-1"],
        rolls=[1, 5, 6], maximize_damage=True,
    )
    damage = damage_to(result, "enemy:goblin-1")
    assert damage["amount"] == 16, "2d8 maximized = 16 on a failed save"
    assert damage["rolls"] == []
    assert damage["damage_type"] == "thunder"
    assert overchannel_cost(result)["amount"] == 11

    engine, instance = new_combat(runtime, wizard14)
    grant(instance, "spell:thunderwave")
    result = cast(
        engine, instance, "spell:thunderwave", 1, ["enemy:goblin-1"],
        rolls=[20, 5, 6], maximize_damage=True,
    )
    damage = damage_to(result, "enemy:goblin-1")
    assert damage["amount"] == 8, "maximize first, then halve on a successful save"
    assert damage["rolls"] == []


# --- T10: automatic_damage face --------------------------------------------

def test_overchannel_maximizes_automatic_damage(wizard14, runtime) -> None:
    # magic_missile declares target_count 1, so a single cast cannot cover two
    # enemies; the multi-target dimension is exercised with two independent casts.
    enemies = [enemy("goblin-1", hp=40), enemy("goblin-2", hp=40)]
    engine, instance = new_combat(runtime, wizard14, enemies=enemies)
    grant(instance, "spell:magic_missile")

    first = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[1, 2], maximize_damage=True,
    )
    assert damage_to(first, "enemy:goblin-1")["amount"] == 15  # 3d4+3 maximized
    assert damage_to(first, "enemy:goblin-1")["rolls"] == []

    second = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-2"],
        rolls=[3, 4], maximize_damage=True,
    )
    assert damage_to(second, "enemy:goblin-2")["amount"] == 15
    enemies_state = ruleset_runtime.state(instance)["combat"]["enemies"]
    assert enemies_state["goblin-1"]["hp"] == 25
    assert enemies_state["goblin-2"]["hp"] == 25


# --- T11: spell_attack face, crit-before-maximize ordering -----------------

def test_overchannel_maximizes_each_ray_after_crit_doubling(wizard14, runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[enemy("goblin-1", hp=100)])
    grant(instance, "spell:scorching_ray")
    # Scorching Ray is a level 2 spell; a level 2 slot still yields three rays.
    result = cast(
        engine, instance, "spell:scorching_ray", 2, ["enemy:goblin-1"] * 3,
        rolls=[20, 10, 10, 5, 6, 7, 8], maximize_damage=True,
    )
    amounts = [
        event["amount"] for event in events_of(result)
        if event["type"] == "resource.changed"
        and event.get("resource") == "hp"
        and event.get("target_id") == "enemy:goblin-1"
    ]
    # First ray is a natural 20: 2d6 doubles to 4d6, then maximizes to 24.
    assert amounts == [24, 12, 12]
    for event in events_of(result):
        if event["type"] == "resource.changed" and event.get("target_id") == "enemy:goblin-1":
            assert event["rolls"] == []
        if event["type"] == "check.resolved":
            assert "maximized" not in event
    checks = [
        event for event in events_of(result)
        if event["type"] == "check.resolved" and event.get("kind") == "spell_attack"
    ]
    assert checks[0]["critical"] is True
    assert overchannel_cost(result)["amount"] == 26  # 2d12 per slot level × 2


# --- T12: self damage lands after the whole cast and victory ----------------

def test_overchannel_cost_lands_after_victory(wizard14, runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[enemy("goblin-1", hp=10)])
    grant(instance, "spell:magic_missile")
    before = int(canonical(instance)["resources"]["hp"])
    result = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[3, 4], maximize_damage=True,
    )
    events = events_of(result)
    victory_index = next(
        index for index, event in enumerate(events) if event["type"] == "dnd2024.combat.ended"
    )
    cost_index = next(
        index for index, event in enumerate(events) if event.get("overchannel_cost")
    )
    assert victory_index < cost_index, "victory resolves before the self-damage cost"
    cost = overchannel_cost(result)
    assert cost["amount"] == 7
    assert cost["delta"] == -7
    assert cost["damage_type"] == "necrotic"
    assert cost["rolls"] == [3, 4]
    assert int(canonical(instance)["resources"]["hp"]) == before - 7


# --- T13: self damage ignores resistance and immunity ----------------------

def test_overchannel_cost_ignores_resistance(wizard14, runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[enemy("goblin-1", hp=100)])
    canonical(instance).setdefault("conditions", {})["necrotic_resistant"] = {
        "source": "test", "resist_damage_types": ["necrotic"],
    }
    grant(instance, "spell:magic_missile")
    result = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[6, 6], maximize_damage=True,
    )
    cost = overchannel_cost(result)
    assert cost["amount"] == 12
    assert "resisted" not in cost
    assert "resistance" not in cost


# --- T14: self damage triggers an existing concentration check -------------

def test_overchannel_cost_triggers_concentration_check(wizard14, runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[enemy("goblin-1", hp=100)])
    grant(instance, "spell:stoneskin", "spell:thunderwave")
    cast(engine, instance, "spell:stoneskin", 4, ["player:gm"])
    assert canonical(instance)["spellcasting"]["class"]["concentration"] is not None

    result = cast(
        engine, instance, "spell:thunderwave", 1, ["enemy:goblin-1"],
        rolls=[10, 6, 5, 10], maximize_damage=True,
    )
    events = events_of(result)
    cost_index = next(
        index for index, event in enumerate(events) if event.get("overchannel_cost")
    )
    check_index = next(
        index for index, event in enumerate(events)
        if event["type"] == "check.resolved" and event.get("kind") == "concentration"
    )
    assert cost_index < check_index
    concentration_check = check_of(result, "concentration")
    assert concentration_check["target"] == 10  # max(10, 11 // 2)
    assert concentration_check["rolls"] == [10]
    assert concentration_check["success"] is True


# --- T15: self damage can drop the caster, using the generic death path ----

def test_overchannel_cost_can_drop_the_caster(wizard14, runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[enemy("goblin-1", hp=100)])
    set_hp(instance, 1)
    grant(instance, "spell:magic_missile")
    result = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[12, 12], maximize_damage=True,
    )
    cost = overchannel_cost(result)
    assert cost["amount"] == 24
    resources = canonical(instance)["resources"]
    conditions = canonical(instance)["conditions"]
    assert int(resources["hp"]) == 0
    assert "unconscious" in conditions
    assert conditions["death_saves"] == {"successes": 0, "failures": 0}
    assert "dead" not in conditions
