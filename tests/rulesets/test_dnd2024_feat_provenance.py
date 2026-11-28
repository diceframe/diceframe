"""FD2b: where an owned feat came from, and when a second grant is illegal.

`features.feat_refs` stays a flat list of strings -- it is persisted, and every
source is still recorded elsewhere on the sheet.  So provenance is a pure view
over `identity` / `build` / `features` / `progression`, and the duplicate rule
lives on the acquisition side, where refusing is safe.
"""

from __future__ import annotations

import inspect

import pytest

from src.rulesets.dnd2024.character import derivation as derivation_module
from src.rulesets.dnd2024.features.feat_provenance import (
    FeatAcquisitionError,
    FeatProvenance,
    acquire,
    feat_provenance,
    owned_feat_refs,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from test_dnd2024_fighting_style import fighter_draft, fighter_sheet

LOCALE = "en"


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def bundle(runtime: Dnd2024Runtime):
    return runtime.load_bundle(LOCALE)


def _provenance(sheet: dict, bundle):
    return feat_provenance(bundle, sheet["ruleset_character"])


def _sheet(**parts) -> dict:
    """A derived-sheet shape: identity + build + features + progression."""

    return {
        "identity": {"species_ref": "", "background_ref": ""},
        "build": {"class_levels": [{"class_ref": "class:fighter", "level": 1}]},
        "features": {},
        "progression": {},
        **parts,
    }


# --------------------------------------------------------------------------
# reading the existing fields
# --------------------------------------------------------------------------


def test_a_real_sheet_reports_all_three_build_time_sources(runtime, bundle) -> None:
    """Soldier gives Savage Attacker, Human gives Alert, the slot gives Archery."""

    canonical = fighter_sheet(runtime)["ruleset_character"]
    grants = feat_provenance(bundle, canonical)

    assert [(item.ref, item.source_kind, item.source_ref, item.level) for item in grants] == [
        ("feat:savage_attacker", "background", "background:soldier", None),
        ("feat:alert", "species", "species:human", None),
        ("feat:archery", "class_feature", "class:fighter", 1),
    ]
    # The view and the stored list must agree, or one of them is the wrong
    # answer and nothing would notice.
    assert canonical["features"]["feat_refs"] == [item.ref for item in grants]


def test_advancement_is_read_from_the_diff_not_from_choices(bundle) -> None:
    """`advancement.py` records the feat on `diff`, not in `choices`.

    Reading `choices` would produce no source at all, which is the exact
    failure mode this view exists to prevent.
    """

    character = _sheet(progression={"history": [{
        "from_level": 3,
        "to_level": 4,
        # A decoy the view must not read: the real answer is on ``diff``.
        "choices": {"ability_score_increases": {"str": 2}, "feat_ref": "feat:wrong"},
        "diff": {"advancement_feat_ref": "feat:grappler"},
    }]})

    assert feat_provenance(bundle, character) == (
        FeatProvenance("feat:grappler", "advancement", "level:4", 4),
    )


def test_a_plain_ability_score_increase_is_not_a_grant(bundle) -> None:
    """The ASI pseudo-feat shares the feat path but is a slot, not an owned feat.

    The progression writers already exclude it from `feat_refs`; a sheet that
    still carries it (old writer, repair path) must not read as "owns a feat
    named ability_score_improvement".
    """

    character = _sheet(
        features={"feat_refs": ["feat:ability_score_improvement"]},
        progression={"history": [{
            "to_level": 4,
            "choices": {"ability_score_increases": {"str": 2}},
            "diff": {"advancement_feat_ref": "feat:ability_score_improvement"},
        }]},
    )

    assert feat_provenance(bundle, character) == ()


def test_a_subclass_slot_is_a_class_feature_grant(bundle) -> None:
    character = _sheet(features={
        "feat_refs": ["feat:defense"],
        "feature_choices": [{
            "owner": "subclass:champion",
            "feature_id": "additional_fighting_style",
            "choice_id": "fighting_style_feat",
            "selected_refs": ["feat:defense"],
            "granted_level": 7,
        }],
    })

    assert feat_provenance(bundle, character) == (
        FeatProvenance("feat:defense", "class_feature", "subclass:champion", 7),
    )


def test_a_level_one_slot_the_class_does_not_grant_is_not_a_source(bundle) -> None:
    """An old sheet naming a slot this class never had must not gain a feat."""

    draft = {
        "class_ref": "class:fighter",
        "class_feature_feat_choices": {"fighting_style": "feat:archery"},
    }

    assert feat_provenance(bundle, draft, class_slots=set()) == ()
    assert feat_provenance(bundle, draft, class_slots={"fighting_style"}) == (
        FeatProvenance("feat:archery", "class_feature", "class:fighter", 1),
    )


def test_a_ref_without_a_recorded_source_is_unknown_not_an_error(bundle) -> None:
    character = _sheet(features={"feat_refs": ["feat:lucky"]})

    assert feat_provenance(bundle, character) == (
        FeatProvenance("feat:lucky", "unknown", ""),
    )


def test_the_same_ref_keeps_both_grants_visible(bundle) -> None:
    """Collapsing here would leave the duplicate check with nothing to see."""

    draft = {
        "background_ref": "background:soldier",
        "species_ref": "species:human",
        "species_feat_refs": ["feat:savage_attacker"],
    }

    grants = feat_provenance(bundle, draft)

    assert [(item.ref, item.source_kind) for item in grants] == [
        ("feat:savage_attacker", "background"),
        ("feat:savage_attacker", "species"),
    ]
    assert owned_feat_refs(grants) == ["feat:savage_attacker"]


# --------------------------------------------------------------------------
# acquisition
# --------------------------------------------------------------------------


def _grant(ref: str, kind: str, source_ref: str, level: int | None = None) -> FeatProvenance:
    return FeatProvenance(ref, kind, source_ref, level)


def test_a_second_source_is_refused_for_a_non_repeatable_feat() -> None:
    existing = (_grant("feat:alert", "background", "background:criminal"),)

    with pytest.raises(FeatAcquisitionError, match="not repeatable"):
        acquire(
            existing, "feat:alert", _grant("feat:alert", "species", "species:human"),
            repeatable=False,
        )


def test_a_repeatable_feat_may_come_from_another_source() -> None:
    existing = (_grant("feat:elemental_adept", "advancement", "level:4", 4),)

    result = acquire(
        existing,
        "feat:elemental_adept",
        _grant("feat:elemental_adept", "advancement", "level:8", 8),
        repeatable=True,
    )

    assert len(result) == 2


def test_the_same_source_cannot_grant_the_same_feat_twice() -> None:
    existing = (_grant("feat:elemental_adept", "advancement", "level:4", 4),)

    with pytest.raises(FeatAcquisitionError, match="already acquired"):
        acquire(
            existing,
            "feat:elemental_adept",
            _grant("feat:elemental_adept", "advancement", "level:4", 4),
            repeatable=True,
        )


def test_acquire_keeps_the_grant_it_accepted() -> None:
    source = _grant("feat:alert", "species", "species:human")

    assert acquire((), "feat:alert", source, repeatable=False) == (source,)


# --------------------------------------------------------------------------
# the stored shape and the old collapse
# --------------------------------------------------------------------------


def test_the_stored_refs_stay_a_string_list(runtime, bundle) -> None:
    canonical = fighter_sheet(runtime)["ruleset_character"]
    refs = canonical["features"]["feat_refs"]

    assert refs == ["feat:savage_attacker", "feat:alert", "feat:archery"]
    assert all(isinstance(ref, str) for ref in refs)
    assert refs == [item.ref for item in feat_provenance(bundle, canonical)]


def test_derivation_builds_feat_refs_from_provenance() -> None:
    source = inspect.getsource(derivation_module)

    # The old form concatenated three lists and collapsed them; the string it
    # produced is a visible marker of that shape coming back.
    assert "dict.fromkeys([*background_feat_refs" not in source
    assert "feat_grants = feat_provenance(" in source
    assert "owned_feat_refs(feat_grants)" in source


def test_a_duplicate_background_and_species_feat_is_rejected_at_build_time(
    runtime: Dnd2024Runtime,
) -> None:
    """Soldier grants Savage Attacker; Human may also pick it. One is enough."""

    draft = fighter_draft(runtime, fighting_style="feat:archery")
    draft["species_feat_refs"] = ["feat:savage_attacker"]

    with pytest.raises(ValueError) as excinfo:
        runtime.finalize_character(None, draft)

    message = str(excinfo.value)
    assert "not repeatable" in message
    # Both sources are named, and neither is rendered as background:background.
    assert "background (background:soldier)" in message
    assert "species (species:human)" in message
    assert "background:background:" not in message


def test_provenance_order_does_not_depend_on_dict_insertion(bundle) -> None:
    build_a = {
        "class_ref": "class:fighter",
        "class_feature_feat_choices": {"fighting_style": "feat:archery"},
    }
    build_b = {
        "class_feature_feat_choices": {"fighting_style": "feat:archery"},
        "class_ref": "class:fighter",
    }

    assert feat_provenance(bundle, build_a) == feat_provenance(bundle, build_b)


def test_a_real_sheet_derives_the_same_grants_twice(runtime, bundle) -> None:
    first = _provenance(fighter_sheet(runtime), bundle)
    second = _provenance(fighter_sheet(runtime), bundle)

    assert first == second
