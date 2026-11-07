"""Extra Attack is read from the class tables, not from a class-name switch.

The number of attacks one Attack action grants used to be hard-coded as
``if class_ref == "class:fighter"`` plus a set of four other classes, reading
only ``class_levels[0]``.  That silently mis-counted multiclass characters and
could not follow the class table when a class gained the feature at a new
level.

Two bundle tables now own the answer, and neither of them is code:

* ``progression_catalog`` decides which class grants which feature id and at
  which level (``extra_attack`` at 5 everywhere, plus ``two_extra_attacks`` at
  11 and ``three_extra_attacks`` at 20 for the Fighter);
* ``class_feature_catalog`` declares ``attacks_per_action`` on those ids.

RAW never stacks the feature, so the resolver evaluates every class row on its
own and keeps the highest value -- a Fighter 5 / Barbarian 5 makes two attacks,
not three.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features import ClassFeatureError, Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import (
    SequenceRng, advance, monk_instance, start_combat, with_equipment,
)


EXTRA_ATTACK_CLASSES = (
    "class:barbarian", "class:monk", "class:paladin", "class:ranger",
)
NO_EXTRA_ATTACK_CLASSES = (
    "class:bard", "class:cleric", "class:druid", "class:rogue",
    "class:sorcerer", "class:warlock", "class:wizard",
)


def _resolver(locale: str = "en") -> tuple[Dnd2024Runtime, Dnd2024ClassFeatureResolver]:
    runtime = Dnd2024Runtime()
    return runtime, Dnd2024ClassFeatureResolver(runtime.load_bundle(locale))


def _character(*class_levels: tuple[str, int]) -> dict:
    return {
        "build": {
            "class_levels": [
                {"class_ref": class_ref, "level": level}
                for class_ref, level in class_levels
            ],
        },
    }


def _mutated_bundle(runtime: Dnd2024Runtime, mutate):
    """Return the real bundle with one class-feature-catalog edit applied."""

    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    mutate(entities["class_feature_catalog"]["srd_class_features"])
    return replace(bundle, entities=entities)


def _feature(catalog: dict, feature_id: str) -> dict:
    for class_row in catalog["classes"].values():
        for feature in class_row["features"]:
            if feature["id"] == feature_id:
                return feature
    raise AssertionError(f"{feature_id} is not declared")


# ---- the class table decides the number -------------------------------------


@pytest.mark.parametrize(
    ("level", "expected"),
    [(1, 1), (4, 1), (5, 2), (10, 2), (11, 3), (19, 3), (20, 4)],
)
def test_fighter_scale_follows_the_progression_table(level: int, expected: int) -> None:
    _runtime, resolver = _resolver()

    assert resolver.attacks_per_action(_character(("class:fighter", level))) == expected


@pytest.mark.parametrize("class_ref", EXTRA_ATTACK_CLASSES)
def test_shared_classes_reach_two_attacks_at_level_five(class_ref: str) -> None:
    _runtime, resolver = _resolver()

    assert resolver.attacks_per_action(_character((class_ref, 4))) == 1
    assert resolver.attacks_per_action(_character((class_ref, 5))) == 2
    # None of them ever goes past two: the Fighter owns the higher tiers.
    assert resolver.attacks_per_action(_character((class_ref, 20))) == 2


@pytest.mark.parametrize("class_ref", NO_EXTRA_ATTACK_CLASSES)
def test_classes_without_the_feature_never_gain_attacks(class_ref: str) -> None:
    _runtime, resolver = _resolver()

    assert resolver.attacks_per_action(_character((class_ref, 20))) == 1


# ---- multiclassing never stacks ---------------------------------------------


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ((("class:fighter", 5), ("class:barbarian", 5)), 2),
        ((("class:fighter", 11), ("class:barbarian", 5)), 3),
        ((("class:monk", 5), ("class:ranger", 5)), 2),
        ((("class:fighter", 20), ("class:wizard", 5)), 4),
        ((("class:wizard", 5), ("class:fighter", 11)), 3),
        ((("class:rogue", 5), ("class:wizard", 5)), 1),
    ],
)
def test_multiclass_uses_the_highest_value_not_the_sum(
    rows: tuple[tuple[str, int], ...], expected: int,
) -> None:
    _runtime, resolver = _resolver()

    assert resolver.attacks_per_action(_character(*rows)) == expected


def test_the_first_class_row_alone_no_longer_decides() -> None:
    _runtime, resolver = _resolver()

    # The old implementation read class_levels[0]; both orders must agree now.
    assert resolver.attacks_per_action(
        _character(("class:wizard", 5), ("class:fighter", 11))
    ) == resolver.attacks_per_action(
        _character(("class:fighter", 11), ("class:wizard", 5))
    )


# ---- the declaration, not the class name, carries the number ----------------


def test_the_declared_parameter_drives_the_attack_count() -> None:
    runtime = Dnd2024Runtime()

    def raise_it(catalog: dict) -> None:
        _feature(catalog, "extra_attack")["parameters"]["attacks_per_action"] = 5

    resolver = Dnd2024ClassFeatureResolver(_mutated_bundle(runtime, raise_it))

    assert resolver.attacks_per_action(_character(("class:barbarian", 5))) == 5


def test_a_feature_without_the_parameter_grants_no_attacks() -> None:
    runtime = Dnd2024Runtime()

    def drop_it(catalog: dict) -> None:
        _feature(catalog, "extra_attack").pop("parameters")

    resolver = Dnd2024ClassFeatureResolver(_mutated_bundle(runtime, drop_it))

    # Proof that the Fighter class name no longer implies extra attacks: the
    # class table still grants the feature id at level 5, but nothing declares
    # a number for it any more.
    assert resolver.attacks_per_action(_character(("class:fighter", 5))) == 1


@pytest.mark.parametrize("value", [0, 1, 9, True, 2.5, "2"])
def test_impossible_declarations_fail_closed(value: object) -> None:
    runtime = Dnd2024Runtime()

    def break_it(catalog: dict) -> None:
        _feature(catalog, "extra_attack")["parameters"]["attacks_per_action"] = value

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_mutated_bundle(runtime, break_it))


# ---- degraded inputs never invent attacks -----------------------------------


@pytest.mark.parametrize(
    "character",
    [
        None,
        {},
        {"build": {}},
        {"build": {"class_levels": []}},
        {"build": {"class_levels": [{"class_ref": "", "level": 5}]}},
        {"build": {"class_levels": [{"class_ref": "class:fighter", "level": 0}]}},
        {"build": {"class_levels": [{"class_ref": "class:fighter", "level": 21}]}},
        {"build": {"class_levels": [{"class_ref": "class:fighter", "level": "5"}]}},
        {"build": {"class_levels": [{"class_ref": "class:not_a_class", "level": 20}]}},
    ],
)
def test_unknown_or_malformed_builds_make_one_attack(character: object) -> None:
    _runtime, resolver = _resolver()

    assert resolver.attacks_per_action(character) == 1


# ---- the combat engine consumes the boundary --------------------------------


def _fighter_sheet(runtime: Dnd2024Runtime, *, level: int = 1) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "stalwart_guardian"},
    )
    if level > 1:
        sheet = advance(runtime, sheet, level)
    return with_equipment(runtime, sheet, "item:longsword")


def _attack(engine, instance, rolls: list[int]) -> None:
    version = ruleset_runtime.state(instance)["version"]
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"attack-{version}", "type": "attack",
        "expected_version": version, "submitted_by": "gm",
        "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:longsword",
    }, SequenceRng(rolls))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True


def test_fighter_five_spends_two_attacks_per_action() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, _fighter_sheet(runtime, level=5))
    start_combat(engine, instance)

    _attack(engine, instance, [15, 4])
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    assert economy["attacks_remaining"] == 1

    _attack(engine, instance, [15, 4])
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    assert economy["action"] == 0 and economy["attacks_remaining"] == 0


def test_fighter_one_spends_the_single_attack() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, _fighter_sheet(runtime, level=1))
    start_combat(engine, instance)

    _attack(engine, instance, [15, 4])

    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    assert economy["action"] == 0 and economy["attacks_remaining"] == 0


# ---- the sheet and the attack count share one ownership filter --------------


def _with_entities(runtime: Dnd2024Runtime, mutate):
    """Return the real bundle with an arbitrary entities edit applied."""

    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    mutate(entities)
    return replace(bundle, entities=entities)


def _grant_at_level_one(entities: dict, feature_id: str) -> None:
    entities["progression_catalog"]["srd_classes"]["classes"]["fighter"][
        "features_by_level"
    ][0].append(feature_id)


def test_a_grant_below_the_declared_minimum_level_grants_no_attack() -> None:
    runtime = Dnd2024Runtime()

    resolver = Dnd2024ClassFeatureResolver(
        _with_entities(
            runtime,
            lambda entities: _grant_at_level_one(entities, "extra_attack"),
        )
    )

    # The Fighter table now hands the id out at level 1, but the catalog still
    # says the feature starts at 5.  A third-party bundle can do exactly this,
    # so the sheet and the attack count must agree instead of drifting apart.
    character = _character(("class:fighter", 1))
    assert resolver.has_feature(character, "extra_attack") is False
    assert resolver.attacks_per_action(character) == 1
    assert resolver.attacks_per_action(_character(("class:fighter", 5))) == 2


def test_an_option_feature_never_contributes_an_attack_count() -> None:
    runtime = Dnd2024Runtime()

    def make_option(entities: dict) -> None:
        feature = _feature(
            entities["class_feature_catalog"]["srd_class_features"], "extra_attack",
        )
        feature["minimum_level"] = 1
        # Action Surge arrives at level 2, so at level 1 the parent is not
        # granted and the option is not owned.
        feature["option_of"] = "action_surge"
        _grant_at_level_one(entities, "extra_attack")

    resolver = Dnd2024ClassFeatureResolver(_with_entities(runtime, make_option))

    character = _character(("class:fighter", 1))
    assert resolver.has_feature(character, "extra_attack") is False
    assert resolver.attacks_per_action(character) == 1
