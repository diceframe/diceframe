"""Known Wild Shape forms: eligibility, selection, and long-rest replacement.

The list is *owner-scoped state*: which four beasts a druid knows is part of the
character, not of the combat, and it changes only on a long rest.  Keeping the
rules here means the builder, the rest engine and the combat validator all ask
the same question with the same answer.
"""

from __future__ import annotations

from typing import Any, Iterable

from .panel import cr_value
from .state import known_form_count

#: 德鲁伊等级 -> CR 上限（SRD p42）。
CR_CAP = ((4, "1/4"), (8, "1/2"), (99, "1"))

FLY_LEVEL = 8
MINIMUM_LEVEL = 2


class KnownFormsError(ValueError):
    """Raised when a selection cannot be made legal."""


def cr_cap(druid_level: int) -> str:
    for ceiling, cap in CR_CAP:
        if druid_level < ceiling:
            return cap
    return "1"


def eligible_forms(
    entries: Iterable[dict[str, Any]], druid_level: int,
) -> list[dict[str, Any]]:
    """Beasts this druid may know right now, cheapest CR first."""

    cap = cr_value(cr_cap(druid_level))
    chosen: list[dict[str, Any]] = []
    for entry in entries:
        if str(entry.get("creature_type") or "") != "beast":
            continue
        if cr_value(str(entry.get("cr") or "0")) > cap:
            continue
        if druid_level < FLY_LEVEL and int(
            (entry.get("speeds") or {}).get("fly", 0) or 0
        ) > 0:
            continue
        chosen.append({
            "ref": str(entry.get("id") or ""),
            "name": str(entry.get("name") or ""),
            "cr": str(entry.get("cr") or ""),
        })
    chosen.sort(key=lambda row: (cr_value(row["cr"]), row["name"]))
    return chosen


def validate_selection(
    refs: Any, druid_level: int, entries: Iterable[dict[str, Any]],
) -> list[str]:
    """Exactly ``known_form_count(level)`` distinct, eligible beasts."""

    if druid_level < MINIMUM_LEVEL:
        return []
    if not isinstance(refs, list):
        return ["known_form_refs must be a list of beast refs"]
    wanted = known_form_count(druid_level)
    if len(refs) != wanted:
        return [f"a level {druid_level} druid knows exactly {wanted} forms"]
    allowed = {row["ref"] for row in eligible_forms(entries, druid_level)}
    errors: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        text = str(ref)
        if text in seen:
            errors.append(f"known form is duplicated: {text}")
        seen.add(text)
        if text not in allowed:
            errors.append(f"known form is not eligible: {text}")
    return errors


def replace_one(
    known: list[str], old_ref: str, new_ref: str, druid_level: int,
    entries: Iterable[dict[str, Any]],
) -> list[str]:
    """A long rest may swap exactly one known form."""

    if old_ref not in known:
        raise KnownFormsError(f"that form is not known: {old_ref}")
    if new_ref in known:
        raise KnownFormsError(f"that form is already known: {new_ref}")
    allowed = {row["ref"] for row in eligible_forms(entries, druid_level)}
    if new_ref not in allowed:
        raise KnownFormsError(f"that form is not eligible: {new_ref}")
    return [new_ref if item == old_ref else item for item in known]


__all__ = [
    "CR_CAP",
    "FLY_LEVEL",
    "MINIMUM_LEVEL",
    "KnownFormsError",
    "cr_cap",
    "eligible_forms",
    "replace_one",
    "validate_selection",
]
