"""Every feat parameter that claims a consumer must still have one.

``stage="unread"`` is the honest escape hatch for a declaration we have not
wired up yet.  The count is ratcheted here so that pile cannot grow silently:
adding a parameter without a reader fails, and adding a reader without lowering
the baseline also fails (a stale baseline makes the next addition look free).

The reader anchor is ``(module, symbol)`` checked by text containment rather than
by import: the other architecture guards in this repo scan text, and making one
of them parse would turn the guard itself into the thing to maintain.
"""

from __future__ import annotations

from pathlib import Path

from src.rulesets.dnd2024.features.feat_models import LEGACY_FEAT_FIELDS
from src.rulesets.dnd2024.features.parameter_registry import (
    FEAT_PARAMETER_STAGES,
    PARAMETERS,
    unread_parameters,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

# Current state: no registered parameter is unread.  Raising a number here is
# the only way to add one, and the commit has to say why (GAP_REGISTER entry).
UNREAD_BASELINE: frozenset[str] = frozenset()

# Legacy feat fields are read by their old name until FD2 (requires) and P4
# (spell grants) migrate them.  The set may shrink, never grow.
LEGACY_FIELD_BASELINE: frozenset[str] = frozenset({
    "ability_choice",
    "any_ability_minimum",
    "requires_feature",
    "requires_spellcasting",
    "choice_specs",
})


def test_every_claimed_reader_exists() -> None:
    missing: list[str] = []
    for key, spec in PARAMETERS.items():
        if spec.stage == "unread":
            continue
        assert spec.reader, f"{key} claims a consumer but names no reader"
        module, symbol = spec.reader
        path = SRC / module
        if not path.is_file():
            missing.append(f"{key}: {module} does not exist")
            continue
        if symbol not in path.read_text(encoding="utf-8"):
            missing.append(f"{key}: {symbol!r} no longer appears in {module}")
    assert not missing, (
        "a parameter claims a reader that is gone; either fix the anchor or move "
        "the key to stage=\"unread\":\n" + "\n".join(missing)
    )


def test_unread_parameters_only_shrink() -> None:
    current = frozenset(unread_parameters())
    added = current - UNREAD_BASELINE
    assert not added, (
        "new unread parameter(s): " + ", ".join(sorted(added))
        + " -- give it a reader, or register it in GAP_REGISTER and raise "
        "UNREAD_BASELINE in the same commit"
    )
    stale = UNREAD_BASELINE - current
    assert not stale, (
        "unread parameter(s) now have a reader: " + ", ".join(sorted(stale))
        + " -- lower UNREAD_BASELINE in the same commit, otherwise the ratchet "
        "forgets the win"
    )


def test_every_registered_stage_is_known() -> None:
    unknown = sorted(
        f"{key}={spec.stage}"
        for key, spec in PARAMETERS.items()
        if spec.stage not in FEAT_PARAMETER_STAGES
    )
    assert not unknown, "parameter registered with an unknown stage: " + ", ".join(unknown)


def test_a_reader_is_a_two_part_anchor() -> None:
    malformed = sorted(
        key for key, spec in PARAMETERS.items()
        if spec.stage != "unread" and len(spec.reader) != 2
    )
    assert not malformed, (
        "reader must be (module, symbol): " + ", ".join(malformed)
    )


def test_legacy_feat_fields_only_shrink() -> None:
    added = sorted(LEGACY_FEAT_FIELDS - LEGACY_FIELD_BASELINE)
    assert not added, (
        "new legacy feat field(s): " + ", ".join(added)
        + " -- a field with no model support belongs in the model, not in the "
        "bag of things waiting to be migrated"
    )
    stale = sorted(LEGACY_FIELD_BASELINE - LEGACY_FEAT_FIELDS)
    assert not stale, (
        "legacy field(s) have been migrated: " + ", ".join(stale)
        + " -- lower LEGACY_FIELD_BASELINE in the same commit"
    )
