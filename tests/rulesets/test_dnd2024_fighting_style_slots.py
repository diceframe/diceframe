"""The Fighting Style slot is a declared feature, for every class that grants it.

It used to be a bare id in ``level_1_feature_ids`` with the category hardcoded in
two places.  Fighter could pick (at level 1, by accident of the hardcode);
Paladin and Ranger could not pick at all, because the id they gain at level 2 has
no declaration and the level-up planner skips ids it cannot resolve.

These tests pin the declaration and the planner, not the effects: no fighting
style's numbers change here.
"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.features.models import Dnd2024ClassFeatureCatalog
from src.rulesets.dnd2024.progression.advancement import Dnd2024AdvancementEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"
OWNERS = (("class:fighter", 1), ("class:paladin", 2), ("class:ranger", 2))


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle(LOCALE)


@pytest.fixture(scope="module")
def engine(bundle):
    return Dnd2024AdvancementEngine(bundle)


def _character() -> dict:
    """The minimum a choice planner reads: no features, no proficiencies."""

    return {"features": {}, "proficiencies": {}}


def test_every_class_that_grants_fighting_style_declares_it(bundle) -> None:
    catalog = Dnd2024ClassFeatureCatalog.from_bundle(bundle)
    for owner, level in OWNERS:
        definition = catalog.features_by_key[(owner, "fighting_style")]
        assert definition.minimum_level == level, owner
        assert definition.choice is not None, owner
        assert definition.choice.kind == "advancement_feat", owner
        assert definition.choice.category == "fighting_style", owner
        assert definition.choice.count == 1, owner


def test_the_whitelist_is_what_actually_has_an_effect(bundle) -> None:
    """`enabled_options` is an executability whitelist, not the category.

    Great Weapon Fighting has a declaration and a category but no effect, so it
    must not be selectable; a style that grants nothing would look finished.
    """

    catalog = Dnd2024ClassFeatureCatalog.from_bundle(bundle)
    for owner, _level in OWNERS:
        choice = catalog.features_by_key[(owner, "fighting_style")].choice
        assert choice is not None
        assert "feat:great_weapon_fighting" not in choice.enabled_options, owner
        assert "feat:archery" in choice.enabled_options, owner
        assert "feat:defense" in choice.enabled_options, owner


@pytest.mark.parametrize(("class_ref", "level"), [
    ("class:paladin", 2), ("class:ranger", 2),
])
def test_the_level_up_path_asks_for_a_fighting_style(engine, class_ref, level) -> None:
    """These two are the whole point: at level 2 the slot must be offered.

    Before the declaration existed, the planner resolved no definition for the
    gained id and simply skipped it -- silently, forever.
    """

    snapshot = engine.catalog.snapshot(class_ref, level)
    requirements, records, errors = engine._plan_feature_choices(
        _character(), class_ref, level, ["fighting_style"], snapshot, {},
    )
    assert [item["id"] for item in requirements] == [
        f"feature_choice:{class_ref}:fighting_style:fighting_style_feat",
    ]
    assert records == []
    # 必选：不提交就是错误，而不是"待选"。
    assert errors and "fighting_style" in errors[0]


def test_a_submitted_choice_becomes_a_record(engine) -> None:
    snapshot = engine.catalog.snapshot("class:paladin", 2)
    choices = {"feature_choices": [{
        "owner": "class:paladin",
        "feature_id": "fighting_style",
        "choice_id": "fighting_style_feat",
        "selected_refs": ["feat:archery"],
    }]}
    requirements, records, errors = engine._plan_feature_choices(
        _character(), "class:paladin", 2, ["fighting_style"], snapshot, choices,
    )
    assert requirements and not errors
    assert [(record["feature_id"], record["selected_refs"]) for record in records] == [
        ("fighting_style", ["feat:archery"]),
    ]


def test_a_style_outside_the_whitelist_is_rejected(engine) -> None:
    snapshot = engine.catalog.snapshot("class:ranger", 2)
    choices = {"feature_choices": [{
        "owner": "class:ranger",
        "feature_id": "fighting_style",
        "choice_id": "fighting_style_feat",
        "selected_refs": ["feat:great_weapon_fighting"],
    }]}
    _requirements, records, errors = engine._plan_feature_choices(
        _character(), "class:ranger", 2, ["fighting_style"], snapshot, choices,
    )
    assert records == []
    assert any("great_weapon_fighting" in error for error in errors)
