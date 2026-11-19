"""Cover as a level, not a number.

Cover is a *directional* fact in the rules -- the same crate protects a goblin
from the archer and not from the rogue behind it -- but this module deliberately
models only the part that needs no geometry: an ordered level, and the bonuses
that level implies.  Which obstacle blocks whom is a 2D-map question and stays
out; a table can still say "he is behind the crate, half cover" and get the
right numbers everywhere.

Why an enum instead of "+2":

* three-quarters and total cover are *judgements* other rules ask about
  ("end your turn behind three-quarters cover"), not modifiers;
* total cover is not "+5 AC", it is "cannot be targeted directly" -- a
  distinction a numeric bonus erases;
* a bonus computed per source invites stacking, and cover never stacks.
"""

from __future__ import annotations

from typing import Any, Iterable

#: Ordered, least to most protective.
COVER_LEVELS = ("none", "half", "three_quarters", "total")

#: SRD 5.2.1 Cover: half +2, three-quarters +5, to AC and Dexterity saves.
#: Total cover cannot be targeted directly, so its numbers only matter for
#: area effects that reach the target anyway.
_COVER_BONUS = {"none": 0, "half": 2, "three_quarters": 5, "total": 5}


def cover_rank(level: str) -> int:
    """Position of ``level`` in the ladder; unknown levels rank as none."""

    try:
        return COVER_LEVELS.index(level)
    except ValueError:
        return 0


def cover_bonus(level: str) -> int:
    """AC and Dexterity-save bonus implied by ``level``."""

    return _COVER_BONUS.get(level, 0)


def best_cover(levels: Iterable[str]) -> str:
    """The most protective level among ``levels``.

    Cover never stacks: several sources give the best one, not a sum.  That is
    the whole reason this is a ladder and not an integer.
    """

    best = "none"
    for level in levels:
        if cover_rank(level) > cover_rank(best):
            best = level
    return best


def cover_row(level: str, sources: Iterable[str] = ()) -> dict[str, Any]:
    """One answer shape for every consumer: level, derived bonuses, sources."""

    resolved = level if level in COVER_LEVELS else "none"
    bonus = cover_bonus(resolved)
    return {
        "level": resolved,
        "armor_class_bonus": bonus,
        "dex_save_bonus": bonus,
        "sources": list(sources),
    }


#: Effect axis.  ``horizontal`` is the default: cover between the effect's
#: origin and the target counts.  ``ignored`` covers three different reasons
#: that all mean "horizontal cover has nothing to do with this": rain falling
#: from above, an effect erupting from the ground under the target, or a spell
#: whose text says the target gains no benefit from cover.
COVER_INTERACTIONS = ("horizontal", "ignored")


def cover_axis(effect: dict[str, Any] | None) -> tuple[str, str]:
    """``(mode, reason)`` for one spell effect; unknown shapes are horizontal."""

    declaration = (effect or {}).get("cover_interaction")
    if isinstance(declaration, dict):
        mode = str(declaration.get("mode") or "horizontal")
        if mode in COVER_INTERACTIONS:
            return mode, str(declaration.get("reason") or "")
        return "horizontal", ""
    if isinstance(declaration, str) and declaration in COVER_INTERACTIONS:
        return declaration, ""
    return "horizontal", ""
