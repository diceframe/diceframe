"""Feat-owned resource pools and the feature declarations that name them.

Lucky's Luck Points and Dark One's Own Luck share the canonical
``resources.class`` store with the twelve class buckets, but their ownership is
gated by ``features.feat_refs`` / ``(class, level)`` instead of a class table.
These tests cover the R and D rows of the SP3 matrix: the pool maximum, rest
recovery, resize policy, fail-closed declaration validation, and the two
feature declarations (Cutting Words and Dark One's Own Luck) the check windows
consume.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.rulesets.dnd2024.features.models import ClassFeatureError
from src.rulesets.dnd2024.features.resolver import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.resources import Dnd2024ClassResourceCatalog
from src.rulesets.dnd2024.resting import Dnd2024RestEngine, RestError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import advance, monk_sheet

WARLOCK_ABILITIES = {
    "str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15,
}


def _warlock(level: int = 6, cha: int = 15) -> dict:
    """A single-class warlock sheet deep enough for the rest engine."""

    return {
        "build": {"class_levels": [{"class_ref": "class:warlock", "level": level}]},
        "abilities": {**WARLOCK_ABILITIES, "cha": cha},
        "resources": {"hp": 40, "max_hp": 40, "hit_dice": {"d8": level}},
    }


def _lucky_character() -> dict:
    character = monk_sheet(Dnd2024Runtime())["ruleset_character"]
    character.setdefault("features", {})["feat_refs"] = ["feat:lucky"]
    return character


def _rest_bundle(runtime: Dnd2024Runtime, mutate) -> object:
    """Return the real bundle with one realistic ``srd_recovery`` edit."""

    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    mutate(entities["rest_catalog"]["srd_recovery"])
    return replace(bundle, entities=entities)


def _feature_bundle(runtime: Dnd2024Runtime, mutate) -> object:
    """Return the real bundle with one ``srd_class_features`` edit."""

    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    mutate(entities["class_feature_catalog"]["srd_class_features"])
    return replace(bundle, entities=entities)


def _find_feature(raw: dict, feature_id: str) -> dict:
    for class_row in raw["classes"].values():
        for feature in class_row["features"]:
            if feature["id"] == feature_id:
                return feature
    raise AssertionError(f"{feature_id} is not declared")


def _extra_maximum(raw: dict) -> None:
    raw["feat_resources"]["lucky"][0]["maximum_fixed"] = 2


def _proficiency_flag_is_not_true(raw: dict) -> None:
    raw["feat_resources"]["lucky"][0]["maximum_proficiency_bonus"] = 1


def _cutting_words_at_level_two(raw: dict) -> None:
    _find_feature(raw, "cutting_words")["minimum_level"] = 2


def _cutting_words_unknown_track(raw: dict) -> None:
    _find_feature(raw, "cutting_words")["parameters"]["die_sides_track"] = "not_a_track"


# --------------------------------------------------------------------------
# R — feat resource pools
# --------------------------------------------------------------------------


def test_dark_ones_own_luck_pool_tracks_charisma_from_level_six() -> None:
    """R1: granted at 6, maximum is the Charisma modifier with a floor of 1."""

    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))

    six = engine.sync_resources(_warlock(6, cha=15))
    pool = six["resources"]["class"]["dark_ones_own_luck"]
    assert (pool["current"], pool["maximum"]) == (2, 2)

    five = engine.sync_resources(_warlock(5, cha=15))
    assert "dark_ones_own_luck" not in five["resources"]["class"]

    floored = engine.sync_resources(_warlock(6, cha=8))
    assert floored["resources"]["class"]["dark_ones_own_luck"]["maximum"] == 1


def test_dark_ones_own_luck_recovers_only_on_a_long_rest() -> None:
    """R2: ``short: "none"`` keeps spent uses; ``long: "all"`` refills them."""

    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))
    character = engine.sync_resources(_warlock(6))
    character["resources"]["class"]["dark_ones_own_luck"]["current"] = 0

    short = engine.complete_short_rest(character)["character"]
    assert short["resources"]["class"]["dark_ones_own_luck"]["current"] == 0

    long = engine.complete_long_rest(short)["character"]
    assert long["resources"]["class"]["dark_ones_own_luck"]["current"] == 2


def test_lucky_points_follow_proficiency_bonus_and_preserve_spent() -> None:
    """R3: maximum tracks PB, and levelling keeps the number of spent uses."""

    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))
    character = _lucky_character()

    synced = engine.sync_resources(character)
    pool = synced["resources"]["class"]["lucky_points"]
    assert (pool["current"], pool["maximum"]) == (2, 2)

    synced["resources"]["class"]["lucky_points"]["current"] = 1
    advanced = advance(runtime, {"ruleset_character": synced}, 5)["ruleset_character"]
    pool = advanced["resources"]["class"]["lucky_points"]
    assert pool["maximum"] == 3
    assert pool["current"] == 2

    rested = engine.complete_long_rest(advanced)["character"]
    assert rested["resources"]["class"]["lucky_points"]["current"] == 3


def test_a_character_without_the_feat_has_no_lucky_pool() -> None:
    """R4: no ``feat:lucky`` means no pool, and a stale entry is removed."""

    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))

    plain = engine.sync_resources(monk_sheet(runtime)["ruleset_character"])
    assert "lucky_points" not in plain["resources"]["class"]

    stale = deepcopy(plain)
    stale["resources"]["class"]["lucky_points"] = {"current": 2, "maximum": 2}
    cleaned = engine.sync_resources(stale)
    assert "lucky_points" not in cleaned["resources"]["class"]


@pytest.mark.parametrize("mutate", [_extra_maximum, _proficiency_flag_is_not_true])
def test_feat_resource_declarations_fail_closed(mutate) -> None:
    """R5: two maximum fields, or a non-``true`` flag, are rejected."""

    runtime = Dnd2024Runtime()
    bundle = _rest_bundle(runtime, mutate)

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassResourceCatalog.from_bundle(bundle)
    with pytest.raises(RestError):
        Dnd2024RestEngine(bundle)


def test_feat_resources_coexist_with_the_twelve_class_buckets() -> None:
    """R6: adding ``feat_resources`` leaves the class-bucket checks untouched."""

    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    catalog = Dnd2024ClassResourceCatalog.from_bundle(bundle)
    engine = Dnd2024RestEngine(bundle)

    assert len(catalog.by_class) == 12
    assert set(catalog.by_feat) == {"lucky"}
    assert set(engine.rules["class_resources"]) == set(catalog.by_class)
    assert set(engine.rules["feat_resources"]) == {"lucky"}


# --------------------------------------------------------------------------
# D — feature declarations
# --------------------------------------------------------------------------


def test_cutting_words_declares_the_subclass_grant_level() -> None:
    """D1: the declaration and the subclass table must agree on level 3."""

    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    definition = resolver.catalog.features["cutting_words"]
    assert definition.minimum_level == 3
    assert definition.source_ref.startswith("srd-5.2.1:p35:")

    with pytest.raises(ClassFeatureError, match="minimum_level"):
        Dnd2024ClassFeatureResolver(_feature_bundle(runtime, _cutting_words_at_level_two))


@pytest.mark.parametrize("level,die_sides", [(3, 6), (5, 8), (10, 10), (15, 12)])
def test_cutting_words_die_follows_the_bardic_track(level: int, die_sides: int) -> None:
    """D2: ``die_sides_track`` resolves against the real progression table."""

    resolver = Dnd2024ClassFeatureResolver(Dnd2024Runtime().load_bundle("en"))
    character = {"build": {"class_levels": [{"class_ref": "class:bard", "level": level}]}}
    assert resolver.feature_value(character, "cutting_words", "die_sides") == die_sides


def test_cutting_words_rejects_a_track_the_progression_table_does_not_have() -> None:
    """D2 fail closed: a ``*_track`` must name a real track."""

    runtime = Dnd2024Runtime()
    with pytest.raises(ClassFeatureError, match="unknown progression track"):
        Dnd2024ClassFeatureResolver(_feature_bundle(runtime, _cutting_words_unknown_track))


def test_dark_ones_own_luck_declares_a_d10_at_level_six() -> None:
    """D3: the direct ``die_sides`` parameter and the grant level."""

    resolver = Dnd2024ClassFeatureResolver(Dnd2024Runtime().load_bundle("en"))
    definition = resolver.catalog.features["dark_ones_own_luck"]
    assert definition.minimum_level == 6
    assert definition.parameters["die_sides"] == 10

    character = {"build": {"class_levels": [{"class_ref": "class:warlock", "level": 6}]}}
    assert resolver.feature_value(character, "dark_ones_own_luck", "die_sides") == 10
