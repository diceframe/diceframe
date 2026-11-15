"""The eight preparing classes differ on when, how many, and from where.

These tests pin the policy table to content and to the SRD text quoted in the
task spec: a class's swap rule is data, never a class-name branch in code.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells import (
    Dnd2024SpellSelection,
    SpellCatalogError,
    preparation,
)
from src.rulesets.dnd2024.spells.preparation import PreparationPolicy


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def bundle(runtime: Dnd2024Runtime):
    return runtime.load_bundle("en")


# ---------------------------------------------------------------------------
# the declared table, one row per class
# ---------------------------------------------------------------------------

EXPECTED = {
    # class  source        lvl  rest  cantrip      book   gain
    "bard": ("class_list", 1, 0, "level_up", (0, 0), 0),
    "cleric": ("class_list", 0, "any", "level_up", (0, 0), 0),
    "druid": ("class_list", 0, "any", "level_up", (0, 0), 0),
    "paladin": ("class_list", 0, 1, "none", (0, 0), 0),
    "ranger": ("class_list", 0, 1, "none", (0, 0), 0),
    "sorcerer": ("class_list", 1, 0, "level_up", (0, 0), 0),
    "warlock": ("class_list", 1, 0, "level_up", (0, 0), 0),
    "wizard": ("spellbook", 0, "any", "long_rest", (6, 2), 2),
}


@pytest.mark.parametrize("class_id", sorted(EXPECTED))
def test_each_preparing_class_declares_the_srd_rule(bundle, class_id: str) -> None:
    source, level_up, rest, cantrip, book, gain = EXPECTED[class_id]

    policy = PreparationPolicy.for_class(bundle, f"class:{class_id}")

    assert policy is not None
    assert policy.source == source
    assert policy.level_up_replace == level_up
    assert policy.long_rest_replace == (None if rest == "any" else rest)
    assert policy.cantrip_swap_on == cantrip
    assert (policy.spellbook_initial, policy.spellbook_per_level) == book
    assert policy.spellbook_gain_per_level == gain


@pytest.mark.parametrize("class_id", ["barbarian", "fighter", "monk", "rogue"])
def test_non_casters_have_no_policy(bundle, class_id: str) -> None:
    """Missing block means "does not prepare spells", not an error."""

    assert PreparationPolicy.for_class(bundle, f"class:{class_id}") is None


def test_wizard_spellbook_floor_comes_from_data(bundle) -> None:
    policy = PreparationPolicy.for_class(bundle, "class:wizard")

    assert policy is not None
    assert policy.spellbook_minimum(1) == 6
    assert policy.spellbook_minimum(2) == 8
    assert policy.spellbook_minimum(20) == 44
    # 44 is 6 + 2 * 19; the numbers live in the JSON, not here.


@pytest.mark.parametrize(
    ("class_id", "rest_capable"),
    [("cleric", True), ("wizard", True), ("paladin", True),
     ("bard", False), ("sorcerer", False), ("warlock", False)],
)
def test_long_rest_capability_follows_the_timing_axis(
    bundle, class_id: str, rest_capable: bool,
) -> None:
    policy = PreparationPolicy.for_class(bundle, f"class:{class_id}")

    assert policy is not None
    assert policy.can_change_on_long_rest() is rest_capable


def test_a_misspelled_source_is_refused_instead_of_silently_degrading(
    runtime: Dnd2024Runtime,
) -> None:
    bundle = runtime.load_bundle("en")
    entity = bundle.get("class", "cleric")
    broken = {**entity, "preparation_policy": {**entity["preparation_policy"],
                                               "source": "book"}}
    # 不改共享 fixture：替换一份带坏数据的 bundle。
    scoped = replace(bundle, entities={
        **bundle.entities,
        "class": {**bundle.entities["class"], "cleric": broken},
    })

    with pytest.raises(SpellCatalogError, match="class_list or spellbook"):
        PreparationPolicy.for_class(scoped, "class:cleric")


def test_selection_reads_the_book_floor_from_the_policy(bundle) -> None:
    selection = Dnd2024SpellSelection(bundle)

    assert selection.requirements("class:wizard", 1)["spellbook_minimum"] == 6
    assert selection.requirements("class:wizard", 20)["spellbook_minimum"] == 44
    # A class-list caster has no spellbook floor at all.
    assert selection.requirements("class:cleric", 5)["spellbook_minimum"] == 0


def test_the_selection_module_keeps_no_class_name_literals() -> None:
    """The policy read is the only place that decides who has a spellbook."""

    source = preparation.__file__
    assert source is not None
    body = open(source, encoding="utf-8").read()
    assert "class:wizard" not in body
