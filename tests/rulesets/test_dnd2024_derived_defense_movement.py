"""Roving, Second-Story Work, and the movement facts derived from Speed.

The earlier half of this slice (Unarmored Defense, Fast Movement, Unarmored
Movement, armor gating) already lives in
``test_dnd2024_derived_defense_speed.py``; this file covers what that pass did
not: the two features that hand out a Climb/Swim Speed, and the jump distances
that a Thief reads from Dexterity.

Two asymmetric rules are the point of the whole file:

* Roving is one declaration with two halves.  ``speed_bonus_ft`` is gated on
  "not wearing Heavy armor", the Climb/Swim Speeds it grants are not.  So the
  Speed bonus reads *active* declarations and the modes read *owned* ones.
* Second-Story Work *replaces* Strength with Dexterity for jump distance.  It is
  a substitution, never an extra modifier, so nothing here may sum the two.

Everything below runs through the real bundle and the real authoritative chain
(``reconcile_canonical`` / ``derive_*``); no test invents an item, a feature or
a number.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.character.derivation import (
    derive_jump_distances,
    derive_movement_profile,
    derive_speed,
)
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.features import (
    ClassFeatureError,
    Dnd2024ClassFeatureResolver,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"
SPECIES_SPEED = 30


# --------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _resolver(runtime: Dnd2024Runtime, locale: str = LOCALE) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(runtime.load_bundle(locale))


def _proxy(
    class_ref: str, level: int, *, subclass: str | None = None,
    items: tuple[str, ...] = (),
) -> dict:
    """The minimal canonical shape the feature boundary reads."""

    character: dict = {
        "build": {"class_levels": [{"class_ref": class_ref, "level": level}]},
    }
    if subclass:
        character["features"] = {"subclass_ref": subclass}
    if items:
        character["equipment"] = {"item_refs": [str(ref) for ref in items]}
    return character


ABILITIES = {"str": 16, "dex": 10, "con": 14, "int": 8, "wis": 12, "cha": 10}


def _canonical(
    class_ref: str, level: int, *,
    subclass: str | None = None,
    abilities: dict | None = None,
    items: tuple[str, ...] = (),
    species: str = "species:human",
) -> dict:
    """A canonical record reconciliation can re-derive from."""

    character: dict = {
        "build": {"level": level, "class_levels": [{"class_ref": class_ref, "level": level}]},
        "abilities": dict(abilities or ABILITIES),
        "proficiencies": {
            "armor_category_refs": [
                "armor_category:light", "armor_category:medium",
                "armor_category:heavy", "armor_category:shield",
            ],
        },
        "sources": {"class_ref": class_ref, "species_ref": species},
        "equipment": {"item_refs": [str(ref) for ref in items]},
        "features": {},
    }
    if subclass:
        character["features"]["subclass_ref"] = subclass
    return character


def _reconcile(runtime: Dnd2024Runtime, canonical: dict) -> None:
    Dnd2024CharacterStateReconciler(
        runtime.load_bundle(LOCALE),
    ).reconcile_canonical(canonical)


class _StubFeatures:
    """A resolver-shaped stand-in for the two questions the profile asks."""

    def __init__(self, *, climb: bool = False, swim: bool = False, jump: str = "str") -> None:
        self._modes = {"climb": climb, "swim": swim}
        self._jump = jump

    def movement_modes(self, character):  # noqa: ANN001 - duck-typed on purpose
        return dict(self._modes)

    def jump_ability(self, character):  # noqa: ANN001
        return self._jump


# --------------------------------------------------------------------------
# 1. declarations and ownership (matrix 1)
# --------------------------------------------------------------------------

def test_roving_is_a_ranger_six_declaration(runtime) -> None:
    definition = _resolver(runtime).catalog.features_by_key[
        ("class:ranger", "roving")
    ]

    assert definition.minimum_level == 6
    assert definition.source_ref == "srd-5.2.1:p59:roving"
    assert definition.parameters["speed_bonus_ft"] == 10
    assert definition.parameters["climb_speed_from_speed"] is True
    assert definition.parameters["swim_speed_from_speed"] is True
    # Heavy armor is the only thing the Speed half cares about; a Shield never
    # participates, so the requirement must not mention it.
    assert definition.equipment_requirement.no_heavy_armor is True
    assert definition.equipment_requirement.unarmored is False
    assert definition.equipment_requirement.no_shield is False
    assert definition.resource_id == ""


def test_second_story_work_is_a_thief_three_declaration(runtime) -> None:
    definition = _resolver(runtime).catalog.features_by_key[
        ("subclass:thief", "second_story_work")
    ]

    assert definition.minimum_level == 3
    assert definition.source_ref == "srd-5.2.1:p64:second-story-work"
    assert definition.parameters == {
        "climb_speed_from_speed": True,
        "jump_ability": "dex",
    }
    # The SRD gates Second-Story Work on nothing: no armor clause, no Shield
    # clause, so the declaration must carry no requirement at all.
    assert definition.equipment_requirement is None


def test_both_labels_exist_in_both_locales(runtime) -> None:
    english = _resolver(runtime).catalog
    chinese = _resolver(runtime, "zh-CN").catalog

    assert english.label("roving") == "Roving"
    assert english.label("second_story_work") == "Second-Story Work"
    assert chinese.label("roving") == "游走"
    assert chinese.label("second_story_work") == "飞檐走壁"


@pytest.mark.parametrize(
    ("class_ref", "level", "subclass", "expect_roving", "expect_story"),
    [
        ("class:ranger", 5, None, False, False),
        ("class:ranger", 6, None, True, False),
        ("class:rogue", 3, "subclass:thief", False, True),
        ("class:rogue", 2, "subclass:thief", False, False),
        ("class:rogue", 3, "subclass:assassin", False, False),
        ("class:monk", 20, None, False, False),
    ],
)
def test_ownership_follows_the_class_and_the_subclass(
    runtime, class_ref, level, subclass, expect_roving, expect_story,
) -> None:
    resolver = _resolver(runtime)
    character = _proxy(class_ref, level, subclass=subclass)

    assert resolver.has_feature(character, "roving") is expect_roving
    assert resolver.has_feature(character, "second_story_work") is expect_story


# --------------------------------------------------------------------------
# 2. Roving: one declaration, two different gates (matrix 12)
# --------------------------------------------------------------------------

def test_roving_grants_climb_and_swim_at_ranger_six(runtime) -> None:
    resolver = _resolver(runtime)
    character = _proxy("class:ranger", 6)

    assert resolver.speed_bonus(character) == 10
    assert resolver.movement_modes(character) == {"climb": True, "swim": True}


def test_ranger_five_has_neither_the_bonus_nor_the_speeds(runtime) -> None:
    resolver = _resolver(runtime)
    character = _proxy("class:ranger", 5)

    assert resolver.speed_bonus(character) == 0
    assert resolver.movement_modes(character) == {"climb": False, "swim": False}


def test_heavy_armor_drops_the_bonus_but_keeps_both_speeds(runtime) -> None:
    """The whole asymmetry in one assertion.

    Roving's Speed bonus is gated on "while you aren't wearing Heavy armor".
    The Climb and Swim Speeds are not: Heavy armor removes the +10 and nothing
    else.  Reading modes off *owned* declarations is what keeps that true.
    """

    resolver = _resolver(runtime)
    heavy = _proxy("class:ranger", 6, items=("item:chain_mail",))
    light = _proxy("class:ranger", 6, items=("item:leather_armor",))

    assert resolver.speed_bonus(heavy) == 0
    assert resolver.movement_modes(heavy) == {"climb": True, "swim": True}

    # Light armor is not Heavy: both halves stay.
    assert resolver.speed_bonus(light) == 10
    assert resolver.movement_modes(light) == {"climb": True, "swim": True}


def test_a_shield_never_touches_roving(runtime) -> None:
    resolver = _resolver(runtime)
    character = _proxy("class:ranger", 6, items=("item:shield",))

    assert resolver.speed_bonus(character) == 10
    assert resolver.movement_modes(character) == {"climb": True, "swim": True}


# --------------------------------------------------------------------------
# 3. Second-Story Work: Climb only, never Swim (matrix 13)
# --------------------------------------------------------------------------

def test_second_story_work_grants_climb_but_not_swim(runtime) -> None:
    resolver = _resolver(runtime)
    thief = _proxy("class:rogue", 3, subclass="subclass:thief")

    assert resolver.movement_modes(thief) == {"climb": True, "swim": False}


def test_a_second_source_of_the_same_mode_does_not_double_it(runtime) -> None:
    """Two features may both grant a Climb Speed; the answer is still one Speed.

    The mode is a yes/no fact and the distance is copied from the character's
    own Speed, so "Roving + Second-Story Work" cannot produce 2x anything.
    """

    features = _StubFeatures(climb=True, swim=True)
    profile = derive_movement_profile(35, ABILITIES, features, {"build": {}})

    assert profile["climb_speed"] == 35
    assert profile["swim_speed"] == 35
    assert profile["climb_speed"] != 70


def test_a_mode_nobody_declared_is_zero_not_the_speed(runtime) -> None:
    """Absent is a real answer: no Climb Speed means the extra cost applies."""

    profile = derive_movement_profile(
        SPECIES_SPEED, ABILITIES, _StubFeatures(), {"build": {}},
    )

    assert profile["climb_speed"] == 0
    assert profile["swim_speed"] == 0


# --------------------------------------------------------------------------
# 4. Jump distances: substitution, not addition (matrix 14, 15, 16)
# --------------------------------------------------------------------------

def test_long_jump_uses_the_score_and_a_standing_jump_is_half() -> None:
    assert derive_jump_distances({"str": 16, "dex": 10}) == {
        "jump_ability": "str",
        "long_jump_ft": 16,
        "standing_long_jump_ft": 8,
        "high_jump_ft": 6,
        "standing_high_jump_ft": 3,
    }


def test_high_jump_is_three_plus_the_modifier_and_never_negative() -> None:
    # Strength 1 -> modifier -5 -> 3 - 5 floors at 0, and half of 0 is 0.
    assert derive_jump_distances({"str": 1, "dex": 1})["high_jump_ft"] == 0
    assert derive_jump_distances({"str": 1, "dex": 1})["standing_high_jump_ft"] == 0
    # Strength 20 -> modifier +5 -> 8 feet.
    assert derive_jump_distances({"str": 20, "dex": 1})["high_jump_ft"] == 8


def test_dexterity_replaces_strength_rather_than_adding_to_it() -> None:
    strong = derive_jump_distances({"str": 20, "dex": 8})
    nimble = derive_jump_distances({"str": 20, "dex": 8}, ability="dex")

    assert nimble["jump_ability"] == "dex"
    # Dexterity 8 -> score 8, modifier -1 -> High Jump max(0, 2) = 2.
    assert nimble["long_jump_ft"] == 8
    assert nimble["high_jump_ft"] == 2
    # The two abilities are never summed: 8 + 20 would be 28.
    assert nimble["long_jump_ft"] != strong["long_jump_ft"] + 8


def test_standing_distances_round_down_never_up() -> None:
    # Long Jump 15 -> standing 7 (not 8); High Jump 5 -> standing 2.
    distances = derive_jump_distances({"str": 15, "dex": 10})

    assert distances["long_jump_ft"] == 15
    assert distances["standing_long_jump_ft"] == 7
    assert distances["high_jump_ft"] == 5
    assert distances["standing_high_jump_ft"] == 2


def test_a_thief_sheet_reads_dexterity_and_anyone_else_reads_strength(runtime) -> None:
    resolver = _resolver(runtime)
    thief = _proxy("class:rogue", 3, subclass="subclass:thief")
    plain = _proxy("class:rogue", 3, subclass="subclass:assassin")

    assert resolver.jump_ability(thief) == "dex"
    assert resolver.jump_ability(plain) == "str"
    assert resolver.jump_ability(None) == "str"
    assert resolver.jump_ability({"build": {}}) == "str"


# --------------------------------------------------------------------------
# 5. The profile rides on the derived Speed (matrix 25 partial)
# --------------------------------------------------------------------------

def test_the_profile_copies_the_derived_speed_instead_of_recomputing_it() -> None:
    features = _StubFeatures(climb=True, swim=True)

    for speed in (25, 30, 40):
        assert derive_movement_profile(speed, ABILITIES, features, {})["climb_speed"] == speed


def test_reconciliation_writes_the_speeds_and_the_jump_distances(runtime) -> None:
    canonical = _canonical("class:ranger", 6)
    _reconcile(runtime, canonical)
    derived = canonical["derived"]

    assert derived["speed"] == SPECIES_SPEED + 10
    assert derived["climb_speed"] == derived["speed"]
    assert derived["swim_speed"] == derived["speed"]
    assert derived["jump_ability"] == "str"
    assert derived["long_jump_ft"] == 16


def test_reconciliation_recomputes_the_speeds_from_the_worn_armor(runtime) -> None:
    ranger = _canonical("class:ranger", 6, items=("item:chain_mail",))
    _reconcile(runtime, ranger)
    derived = ranger["derived"]

    # Heavy armor removes the +10 ...
    assert derived["speed"] == SPECIES_SPEED
    # ... and the Speeds follow the new value instead of the old one.
    assert derived["climb_speed"] == SPECIES_SPEED
    assert derived["swim_speed"] == SPECIES_SPEED

    ranger["equipment"]["item_refs"] = []
    _reconcile(runtime, ranger)
    assert ranger["derived"]["climb_speed"] == SPECIES_SPEED + 10


def test_a_thief_keeps_climb_at_the_unarmored_speed(runtime) -> None:
    canonical = _canonical("class:rogue", 3, subclass="subclass:thief")
    _reconcile(runtime, canonical)
    derived = canonical["derived"]

    assert derived["speed"] == SPECIES_SPEED
    assert derived["climb_speed"] == SPECIES_SPEED
    assert derived["swim_speed"] == 0
    assert derived["jump_ability"] == "dex"
    # Dexterity 10, not Strength 16: the substitution is visible in the number.
    assert derived["long_jump_ft"] == 10
    assert derive_jump_distances(canonical["abilities"])["long_jump_ft"] == 16


def test_reconciliation_is_idempotent_for_the_new_fields(runtime) -> None:
    canonical = _canonical("class:ranger", 6)
    reconciler = Dnd2024CharacterStateReconciler(runtime.load_bundle(LOCALE))
    reconciler.reconcile_canonical(canonical)

    # Second pass changes nothing, so it reports "already current".
    assert reconciler.reconcile_canonical(canonical) is None


def test_an_unknown_species_leaves_every_speed_derived_field_alone(runtime) -> None:
    canonical = _canonical("class:rogue", 3, subclass="subclass:thief")
    canonical["sources"] = {}
    canonical["derived"] = {"speed": 25, "climb_speed": 25, "swim_speed": 0}

    _reconcile(runtime, canonical)

    # 0 base Speed means "unknown", not "zero": nothing is rewritten.
    assert canonical["derived"]["speed"] == 25
    assert canonical["derived"]["climb_speed"] == 25


def test_derive_speed_still_owns_the_number(runtime) -> None:
    """No jump or climb number is ever an input to Speed."""

    speed = derive_speed(SPECIES_SPEED, feature_bonus=10)
    profile = derive_movement_profile(
        speed, ABILITIES, _StubFeatures(climb=True, swim=True), {},
    )

    assert speed == SPECIES_SPEED + 10
    assert profile["climb_speed"] == speed


# --------------------------------------------------------------------------
# 6. fail-closed declarations
# --------------------------------------------------------------------------

def _mutated(runtime: Dnd2024Runtime, feature_id: str, key: str, value) -> object:
    from dataclasses import replace

    bundle = runtime.load_bundle(LOCALE)
    entities = deepcopy(bundle.entities)
    catalog = entities["class_feature_catalog"]["srd_class_features"]
    target = next(
        row
        for bucket in catalog["classes"].values()
        for row in bucket["features"]
        if row["id"] == feature_id
    )
    target["parameters"][key] = value
    return replace(bundle, entities=entities)


@pytest.mark.parametrize("value", [False, "yes", 1, 0])
def test_a_non_true_climb_flag_fails_closed(runtime, value) -> None:
    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_mutated(runtime, "roving", "climb_speed_from_speed", value))


@pytest.mark.parametrize("value", [False, "yes", 1])
def test_a_non_true_swim_flag_fails_closed(runtime, value) -> None:
    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_mutated(runtime, "roving", "swim_speed_from_speed", value))


@pytest.mark.parametrize("value", ["wis", "con", "", 3, None])
def test_a_jump_ability_that_is_not_str_or_dex_fails_closed(runtime, value) -> None:
    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(
            _mutated(runtime, "second_story_work", "jump_ability", value)
        )


@pytest.mark.parametrize("value", [0, -10, "10", True])
def test_roving_keeps_the_flat_speed_bonus_range(runtime, value) -> None:
    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_mutated(runtime, "roving", "speed_bonus_ft", value))


# --------------------------------------------------------------------------
# 7. what this slice deliberately does not claim (matrix 19-24)
# --------------------------------------------------------------------------

def test_acrobatic_movement_is_not_declared_as_a_mechanical_benefit(runtime) -> None:
    """No surface model exists, so no declaration pretends one does.

    Acrobatic Movement needs an authoritative vertical surface / liquid fact and
    a support model.  The combat state is a one-dimensional lane
    (``position_mode: "theater"``), so declaring the feature would open an entry
    point that can only answer "yes" to something the engine cannot adjudicate.
    §6.5 of the index forbids exactly that; it stays undeclared until the world
    model lands.
    """

    keys = {key[1] for key in _resolver(runtime).catalog.features_by_key}
    assert "acrobatic_movement" not in keys


def test_the_combat_state_has_no_terrain_or_support_fact(runtime) -> None:
    """The stop condition, asserted against the real combat state."""

    from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
    from src.engine.modules import ruleset_runtime
    from dnd2024_monk_common import monk_instance, monk_sheet, start_combat

    engine: Dnd2024CombatEngine
    engine, instance = monk_instance(runtime, monk_sheet(runtime, level=1))
    start_combat(engine, instance)
    combat = ruleset_runtime.state(instance)["combat"]

    assert combat["position_mode"] == "theater"
    for absent in ("difficult_terrain", "vertical_surface", "liquid", "support", "elevation"):
        assert absent not in combat
