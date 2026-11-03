"""S-P10: conditional check bonuses — Archery's flat +2 and Bless's 1d4.

The pipeline is catalog-declared.  A fighting style feat carries
``parameters.ranged_attack_bonus`` into the feature projection, and a combat
condition declares ``check_bonus_dice`` for the semantic check kinds it boosts.
Combat never names a feat or a condition: it consumes the projection and the
provenance rows the registry produced.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.combat.catalog import (
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.progression.advancement import Dnd2024AdvancementEngine
from src.rulesets.dnd2024.progression.catalog import ProgressionCatalogError
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
ARCHERY = "feat:archery"
DEFENSE = "feat:defense"


@dataclass
class RecordingRng(SequenceRng):
    """A deterministic source that also records every requested dice range."""

    def __init__(self, values) -> None:
        super().__init__(list(values))
        self.requests: list[tuple[int, int]] = []

    def randint(self, minimum: int, maximum: int) -> int:
        self.requests.append((minimum, maximum))
        return super().randint(minimum, maximum)


def bundle_with(base, **override):
    """The real bundle with selected catalogs replaced (catalog fail-closed tests)."""

    class _Bundle:
        manifest = base.manifest

        def get(self, kind, ident=None):
            if kind in override:
                return deepcopy(override[kind])
            return base.get(kind, ident)

        def list(self, kind):
            return base.list(kind)

    return _Bundle()


# --------------------------------------------------------------------------
# sheets: a real level-1 fighter and the casters the bless regressions need
# --------------------------------------------------------------------------


def fighter_draft(
    runtime: Dnd2024Runtime, *,
    fighting_style: str | None = ARCHERY,
    package: str = "equipment_package:fighter_b",
) -> dict:
    """A legal level-1 fighter draft built from the real SRD bundle."""

    choices = runtime.builder_choices(None, {
        "locale": LOCALE, "class_ref": "class:fighter",
        "background_ref": "background:soldier", "species_ref": "species:human",
    })
    class_entity = runtime.load_bundle(LOCALE).get("class", "fighter")
    draft = {
        "locale": LOCALE,
        "name": "Archer",
        "level": 1,
        "alignment": "neutral_good",
        "ability_method": "standard_array",
        "base_abilities": class_entity["recommended_standard_array"],
        "background_ability_bonuses": {"str": 2, "con": 1},
        "class_ref": "class:fighter",
        "species_ref": "species:human",
        "background_ref": "background:soldier",
        "class_skill_refs": [
            item["ref"] for item in choices["class_skills"][: choices["class_skill_count"]]
        ],
        "equipment_package_ref": package,
        "background_equipment_package_ref": choices["background_equipment_packages"][0]["ref"],
        "language_refs": ["language:common", "language:dwarvish", "language:elvish"],
    }
    if fighting_style is not None:
        draft["class_feature_feat_choices"] = {"fighting_style": fighting_style}
    choices = runtime.builder_choices(None, draft)
    if choices["species_sizes"]:
        draft["species_size"] = "medium"
    species_choices = {}
    for choice in choices["species_choices"]:
        options = choice.get("option_ids") or choice.get("option_refs") or []
        species_choices[choice["id"]] = options[0]
    if species_choices:
        draft["species_choice_answers"] = species_choices
    if choices["species_skill_count"]:
        draft["species_skill_refs"] = [choices["species_skills"][0]["ref"]]
    if choices["species_feat_count"]:
        draft["species_feat_refs"] = ["feat:alert"]
    if choices["class_tool_count"]:
        draft["class_tool_refs"] = [
            item["ref"] for item in choices["class_tools"][: choices["class_tool_count"]]
        ]
    return draft


def fighter_sheet(
    runtime: Dnd2024Runtime, *,
    fighting_style: str | None = ARCHERY,
    package: str = "equipment_package:fighter_b",
) -> dict:
    return runtime.finalize_character(
        None, fighter_draft(runtime, fighting_style=fighting_style, package=package),
    )


CANTRIPS = ["spell:light", "spell:mage_hand", "spell:ray_of_frost", "spell:fire_bolt"]
SPELLBOOK = [
    # Ordered by spell level so every level's minimum spellbook prefix stays
    # inside that level's maximum spell level.
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


def wizard_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle(LOCALE)).requirements(
        "class:wizard", level
    )
    prepared = SPELLBOOK[: requirements["prepared_spell_count"]]
    spellbook = SPELLBOOK[: requirements["spellbook_minimum"]]
    assert set(prepared).issubset(spellbook)
    return {
        "cantrip_refs": CANTRIPS[: requirements["cantrip_count"]],
        "prepared_spell_refs": prepared,
        "spellbook_refs": spellbook,
    }


def wizard_sheet(runtime: Dnd2024Runtime, *, level: int = 5) -> dict:
    draft = monk_draft(runtime, LOCALE)
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


def preset_sheet(runtime: Dnd2024Runtime, preset_id: str) -> dict:
    choices = runtime.builder_choices(None, {"locale": LOCALE})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": LOCALE, "name": "Arden"},
    )


# --------------------------------------------------------------------------
# combat helpers (same authoritative chain the web layer uses)
# --------------------------------------------------------------------------


def enemy(enemy_id: str = "goblin-1", *, position: int = 5, hp: int = 100) -> dict:
    profile = goblin(position=position, hp=hp)
    profile["id"] = enemy_id
    return profile


def new_combat(
    runtime: Dnd2024Runtime, sheet: dict, *,
    enemies: list[dict] | None = None, positions: dict[str, int] | None = None,
) -> tuple[object, object]:
    engine, instance = monk_instance(runtime, sheet, uid="gm")
    enemy_list = list(enemies) if enemies is not None else [enemy()]
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemy_list,
    }
    if positions is not None:
        intent["player_positions"] = positions
    rolls = [20, *[1 for _ in enemy_list]]
    resolved = engine.resolve_intent(instance, intent, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"
    return engine, instance


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def enemy_conditions(instance, enemy_id: str = "goblin-1") -> dict:
    enemies = ruleset_runtime.state(instance)["combat"]["enemies"]
    return enemies[enemy_id].setdefault("conditions", {})


def add_enemy_condition(instance, condition: str, meta: dict | None = None) -> None:
    enemy_conditions(instance)[condition] = meta or {"source": "test"}


def add_player_condition(instance, condition: str, meta: dict | None = None) -> None:
    canonical(instance).setdefault("conditions", {})[condition] = meta or {"source": "test"}


def set_turn(engine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def request(instance, intent: dict) -> dict:
    intent.setdefault("expected_version", ruleset_runtime.state(instance)["version"])
    return intent


def resolve(engine, instance, intent: dict, rolls=(), rng=None):
    result = engine.resolve_intent(
        instance, request(instance, intent), rng if rng is not None else SequenceRng(list(rolls)),
    )
    assert result["ok"], result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"], applied
    return result


def attack(
    engine, instance, *, actor_id: str, target_id: str, weapon: str = "",
    attack_id: str = "", rolls=(), rng=None,
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
    return resolve(engine, instance, payload, rolls, rng=rng)


def cast(
    engine, instance, spell_ref: str, slot: int, targets: list[str], rolls=(),
    *, uid: str = "gm", rng=None,
):
    set_turn(engine, instance, f"player:{uid}")
    return resolve(engine, instance, {
        "intent_id": f"cast:{spell_ref}:{slot}:{len(targets)}",
        "type": "cast_spell", "submitted_by": uid, "actor_id": f"player:{uid}",
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": targets,
    }, rolls, rng=rng)


def end_turn(engine, instance, actor_id: str, rolls=()):
    set_turn(engine, instance, actor_id)
    return resolve(engine, instance, {
        "intent_id": f"end:{actor_id}", "type": "end_turn",
        "submitted_by": "gm", "actor_id": actor_id,
    }, rolls)


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def check_of(result: dict, kind: str = "attack") -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def attack_check(
    fighting_style: str, weapon: str, *, package: str,
    rolls: list[int], positions: dict[str, int] | None = None,
    blessed: bool = False, enemy_position: int = 30,
) -> dict:
    """Run one fighter attack and return its ``check.resolved`` event."""

    runtime = Dnd2024Runtime()
    sheet = fighter_sheet(runtime, fighting_style=fighting_style, package=package)
    engine, instance = new_combat(
        runtime, sheet, enemies=[enemy(position=enemy_position)],
        positions=positions,
    )
    if blessed:
        add_player_condition(instance, "blessed")
    result = attack(
        engine, instance, actor_id="player:gm", target_id="enemy:goblin-1",
        weapon=weapon, rolls=rolls,
    )
    return check_of(result)


# --------------------------------------------------------------------------
# #1 build records the fighting style feat choice
# --------------------------------------------------------------------------


def test_fighter_build_records_the_fighting_style_feat_choice() -> None:
    runtime = Dnd2024Runtime()
    character = fighter_sheet(runtime)["ruleset_character"]

    assert ARCHERY in character["features"]["feat_refs"]
    assert character["build"]["class_feature_feat_choices"] == {
        "fighting_style": ARCHERY,
    }
    # The other origin feats keep their normal derivation.
    assert "feat:alert" in character["features"]["feat_refs"]


# --------------------------------------------------------------------------
# #2 the choice is optional at build time but must be declared when supplied
# --------------------------------------------------------------------------


def test_missing_fighting_style_choice_stays_unselected() -> None:
    runtime = Dnd2024Runtime()
    character = fighter_sheet(runtime, fighting_style=None)["ruleset_character"]

    assert character["build"]["class_feature_feat_choices"] == {}
    assert ARCHERY not in character["features"]["feat_refs"]


def test_supplied_fighting_style_choice_fails_closed() -> None:
    runtime = Dnd2024Runtime()

    monk = monk_draft(runtime)
    monk["class_feature_feat_choices"] = {"fighting_style": ARCHERY}
    assert "class_feature_feat_choices is not available to this class" in (
        runtime.validate_character(None, monk)
    )

    for ref in ("feat:grappler", "feat:not_a_feat"):
        draft = fighter_draft(runtime)
        draft["class_feature_feat_choices"] = {"fighting_style": ref}
        assert (
            f"fighting style feat choice is not an available fighting style: {ref}"
            in runtime.validate_character(None, draft)
        )


# --------------------------------------------------------------------------
# #3 remote weapon attack gains the flat +2
# --------------------------------------------------------------------------


def test_ranged_weapon_attack_gains_the_archery_bonus() -> None:
    archery = attack_check(
        ARCHERY, "item:longbow", package="equipment_package:fighter_b", rolls=[15, 3],
    )
    baseline = attack_check(
        DEFENSE, "item:longbow", package="equipment_package:fighter_b", rolls=[15, 3],
    )

    assert archery["modifier"] == baseline["modifier"] + 2
    assert archery["total"] == (
        archery["natural"] + archery["modifier"] + archery["check_bonus"]
    )


# --------------------------------------------------------------------------
# #4 melee weapon attack does not gain the bonus
# --------------------------------------------------------------------------


def test_melee_weapon_attack_is_unchanged_by_archery() -> None:
    # Melee range (distance 5): the same weapon profile, only the flat bonus
    # would differ if archery leaked outside the ``ranged`` branch.
    archery = attack_check(
        ARCHERY, "item:scimitar", package="equipment_package:fighter_b",
        rolls=[15], positions={"gm": 0}, enemy_position=5,
    )
    baseline = attack_check(
        DEFENSE, "item:scimitar", package="equipment_package:fighter_b",
        rolls=[15], positions={"gm": 0}, enemy_position=5,
    )

    assert archery["modifier"] == baseline["modifier"]


# --------------------------------------------------------------------------
# #5 a thrown melee weapon is not a ranged weapon
# --------------------------------------------------------------------------


def test_thrown_melee_weapon_does_not_gain_the_archery_bonus() -> None:
    # Distance 10 > 5: used at range, but ``ranged`` is false on the profile.
    archery = attack_check(
        ARCHERY, "item:javelin", package="equipment_package:fighter_a",
        rolls=[15], positions={"gm": 0},
    )
    baseline = attack_check(
        DEFENSE, "item:javelin", package="equipment_package:fighter_a",
        rolls=[15], positions={"gm": 0},
    )

    assert archery["modifier"] == baseline["modifier"]


# --------------------------------------------------------------------------
# #6 no archery means a zero projection and an unchanged modifier
# --------------------------------------------------------------------------


def test_fighter_without_archery_projects_a_zero_bonus() -> None:
    runtime = Dnd2024Runtime()
    sheet = fighter_sheet(runtime, fighting_style=DEFENSE)
    engine, instance = new_combat(runtime, sheet, enemies=[enemy(position=30)])

    actor = engine._player_view("gm", canonical(instance))
    assert actor["ranged_attack_bonus"] == 0

    result = attack(
        engine, instance, actor_id="player:gm", target_id="enemy:goblin-1",
        weapon="item:longbow", rolls=[15],
    )
    check = check_of(result)
    assert check["modifier"] == (
        ability_modifier(actor["abilities"]["dex"]) + actor["proficiency_bonus"]
    )


# --------------------------------------------------------------------------
# #7 / #12 / #13 / #17 bless dice, stacking, and field stability
# --------------------------------------------------------------------------


def test_blessed_weapon_attack_records_one_d4() -> None:
    check = attack_check(
        ARCHERY, "item:longbow", package="equipment_package:fighter_b",
        rolls=[15, 3], blessed=True,
    )

    assert check["check_bonus"] == 3
    assert check["check_bonus_rolls"] == [
        {"condition": "blessed", "die": 4, "roll": 3},
    ]
    assert check["total"] == check["natural"] + check["modifier"] + check["check_bonus"]


def test_archery_and_bless_stack_on_the_same_attack() -> None:
    check = attack_check(
        ARCHERY, "item:longbow", package="equipment_package:fighter_b",
        rolls=[15, 3], blessed=True,
    )
    baseline = attack_check(
        DEFENSE, "item:longbow", package="equipment_package:fighter_b", rolls=[15],
    )

    assert check["modifier"] == baseline["modifier"] + 2
    assert check["check_bonus"] == 3
    assert check["total"] == check["natural"] + check["modifier"] + check["check_bonus"]


def test_plain_attack_keeps_the_stable_zero_fields() -> None:
    check = attack_check(
        DEFENSE, "item:longbow", package="equipment_package:fighter_b", rolls=[15],
    )

    assert check["check_bonus"] == 0
    assert check["check_bonus_rolls"] == []


def test_bless_and_archery_consume_d20_then_d4_in_that_order() -> None:
    runtime = Dnd2024Runtime()
    sheet = fighter_sheet(runtime)
    engine, instance = new_combat(runtime, sheet, enemies=[enemy(position=30)])
    add_player_condition(instance, "blessed")
    rng = RecordingRng([15, 3])

    attack(
        engine, instance, actor_id="player:gm", target_id="enemy:goblin-1",
        weapon="item:longbow", rng=rng,
    )

    # d20 attack, then the bless d4, then the weapon damage die.
    assert rng.requests == [(1, 20), (1, 4), (1, 8)]
    assert rng.values == []


# --------------------------------------------------------------------------
# #8 blessed spell attack
# --------------------------------------------------------------------------


def test_blessed_spell_attack_rolls_one_d4() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = new_combat(runtime, wizard_sheet(runtime))
    add_player_condition(instance, "blessed")

    result = cast(
        engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"], rolls=[15, 3, 5, 5],
    )

    check = check_of(result, "spell_attack")
    assert check["check_bonus"] == 3
    assert check["check_bonus_rolls"] == [
        {"condition": "blessed", "die": 4, "roll": 3},
    ]


# --------------------------------------------------------------------------
# #9 blessed saving throws, including the auto-fail branch
# --------------------------------------------------------------------------


def test_blessed_enemy_save_gains_the_d4() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = new_combat(runtime, wizard_sheet(runtime))
    add_enemy_condition(instance, "blessed")

    result = cast(
        engine, instance, "spell:burning_hands", 1, ["enemy:goblin-1"],
        rolls=[1, 3, 1, 1, 1],
    )

    check = check_of(result, "saving_throw")
    assert check["check_bonus"] == 3
    assert check["check_bonus_rolls"] == [
        {"condition": "blessed", "die": 4, "roll": 3},
    ]
    assert check["total"] == check["natural"] + check["modifier"] + check["check_bonus"]


def test_auto_failed_save_rolls_no_bonus_die() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = new_combat(runtime, wizard_sheet(runtime))
    add_enemy_condition(instance, "blessed")
    add_enemy_condition(instance, "paralyzed")
    rng = RecordingRng([1, 1, 1])

    result = cast(
        engine, instance, "spell:burning_hands", 1, ["enemy:goblin-1"], rng=rng,
    )

    check = check_of(result, "saving_throw")
    assert check["auto_failed"] is True
    assert check["check_bonus"] == 0
    assert check["check_bonus_rolls"] == []
    assert (1, 20) not in rng.requests
    assert (1, 4) not in rng.requests


# --------------------------------------------------------------------------
# #10 blessed repeat save
# --------------------------------------------------------------------------


def test_blessed_repeat_save_gains_the_d4() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = new_combat(runtime, wizard_sheet(runtime))
    add_enemy_condition(instance, "blessed")

    cast(engine, instance, "spell:sleep", 1, ["enemy:goblin-1"], rolls=[1, 2])
    assert "incapacitated" in enemy_conditions(instance)

    result = end_turn(engine, instance, "enemy:goblin-1", rolls=[20, 3])
    check = check_of(result, "saving_throw")

    assert check["repeat_save"] is True
    assert check["check_bonus"] == 3
    assert check["check_bonus_rolls"] == [
        {"condition": "blessed", "die": 4, "roll": 3},
    ]


# --------------------------------------------------------------------------
# #11 blessed concentration save
# --------------------------------------------------------------------------


def test_blessed_concentration_save_gains_the_d4() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = new_combat(runtime, preset_sheet(runtime, "kindly_bulwark"))

    cast(engine, instance, "spell:bless", 1, ["player:gm"])
    end_turn(engine, instance, "player:gm")
    result = attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:gm",
        attack_id="scimitar", rolls=[20, 4, 4, 7, 3],
    )

    check = check_of(result, "concentration")
    assert check["check_bonus"] == 3
    assert check["check_bonus_rolls"] == [
        {"condition": "blessed", "die": 4, "roll": 3},
    ]


# --------------------------------------------------------------------------
# #14 catalog and feat-parameter fail-closed validation
# --------------------------------------------------------------------------


def test_combat_catalog_rejects_invalid_bonus_dice() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle(LOCALE)
    core = bundle.get("combat_catalog", "srd_combat_core")
    conditions = deepcopy(core["conditions"])

    # The shipped declaration loads unchanged.
    Dnd2024CombatCatalog.from_bundle(bundle)

    for bad in (
        {"die": 1, "kinds": ["attack"]},
        {"die": 4, "kinds": ["nope"]},
        {"die": 4, "kinds": []},
        {"die": 4},
        "1d4",
    ):
        merged = {**conditions, "blessed": {"check_bonus_dice": [bad]}}
        with pytest.raises(CombatCatalogError):
            Dnd2024CombatCatalog.from_bundle(
                bundle_with(
                    bundle, combat_catalog={**core, "conditions": merged},
                ),
            )


def test_advancement_rejects_invalid_feat_parameters() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle(LOCALE)
    catalog = bundle.get("advancement_feat_catalog", "srd_advancement_feats")
    feats = deepcopy(catalog["feats"])

    Dnd2024AdvancementEngine(bundle)

    for bad in (
        {"ranged_damage_bonus": 2},
        {"ranged_attack_bonus": 0},
        {"ranged_attack_bonus": 11},
        {"ranged_attack_bonus": True},
    ):
        mutated = {**feats, "archery": {**feats["archery"], "parameters": bad}}
        with pytest.raises(ProgressionCatalogError):
            Dnd2024AdvancementEngine(
                bundle_with(
                    bundle,
                    advancement_feat_catalog={**catalog, "feats": mutated},
                ),
            )


# --------------------------------------------------------------------------
# #15 / #16 projections
# --------------------------------------------------------------------------


def test_view_projects_the_bonus_to_players_only() -> None:
    runtime = Dnd2024Runtime()
    sheet = fighter_sheet(runtime)
    engine, instance = new_combat(runtime, sheet)

    player = engine._player_view("gm", canonical(instance))
    assert player["ranged_attack_bonus"] == 2

    combat = ruleset_runtime.state(instance)["combat"]
    enemy_view = engine._actor_view(instance, combat, "enemy:goblin-1")
    assert "ranged_attack_bonus" not in enemy_view


def test_recent_combat_events_project_the_new_fields_only() -> None:
    runtime = Dnd2024Runtime()
    sheet = fighter_sheet(runtime)
    engine, instance = new_combat(runtime, sheet, enemies=[enemy(position=30)])
    add_player_condition(instance, "blessed")
    attack(
        engine, instance, actor_id="player:gm", target_id="enemy:goblin-1",
        weapon="item:longbow", rolls=[15, 3],
    )

    recent = runtime._recent_combat_events(instance)
    checks = [event for event in recent if event.get("type") == "check.resolved"]

    assert checks
    # The projection contract is exhaustive: the legacy bonus field is gone
    # because it is absent from this exact key set, not merely zero.
    assert set(checks[-1]) == {
        "type", "kind", "actor_id", "target_id", "rolls", "natural", "modifier",
        "total", "target", "success", "critical", "advantage_mode",
        "advantage_sources", "check_bonus", "check_bonus_rolls",
        "round", "turn_index", "event_id", "batch_id", "intent_type",
        "state_version", "actor_name", "target_name",
    }
    assert checks[-1]["check_bonus"] == 3
    assert checks[-1]["check_bonus_rolls"] == [
        {"condition": "blessed", "die": 4, "roll": 3},
    ]
