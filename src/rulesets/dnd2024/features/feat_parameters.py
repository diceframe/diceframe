"""Feat parameters whose reader is a plain projection, not a rule branch.

``resolver.py`` is a 1500+ line hotspot with a size ratchet; a new reader that
only walks ``features.feat_refs`` and reads one declaration does not need to
live inside it.  Keeping the walk here also gives the parameter registry a
precise evidence anchor: the symbol below is the thing that reads the key.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .feat_models import FeatDefinition


def two_handed_melee_damage_die_minimum(
    definitions: Mapping[str, FeatDefinition],
    character: Mapping[str, Any] | None,
) -> int:
    """The floor a feat puts under two-handed melee weapon damage dice.

    Great Weapon Fighting says 1s and 2s count as 3.  Only the value lives
    here: whether *this* attack is a two-handed melee one is combat's question,
    because only combat knows the weapon, the grip and the intent.  A
    duplicated ref does not stack -- the floor is a property, not a bonus.
    """

    if not isinstance(character, Mapping):
        return 0
    features = character.get("features")
    feat_refs = features.get("feat_refs") if isinstance(features, Mapping) else None
    if not isinstance(feat_refs, list) or not feat_refs:
        return 0
    floor = 0
    for ref in feat_refs:
        if not isinstance(ref, str):
            continue
        definition = definitions.get(ref.removeprefix("feat:"))
        if definition is None:
            continue
        value = definition.parameters.get("two_handed_melee_damage_die_minimum")
        if isinstance(value, int) and not isinstance(value, bool):
            floor = max(floor, value)
    return floor


def weapon_damage_rerolls_per_turn(
    definitions: Mapping[str, FeatDefinition],
    character: Mapping[str, Any] | None,
) -> int:
    """How many times per turn this character may reroll weapon damage dice.

    Savage Attacker says once.  Whether *this* attack qualifies (a weapon, not
    unarmed) and whether the budget is spent are combat's questions: the
    declaration only carries the number, and two copies do not stack.
    """

    if not isinstance(character, Mapping):
        return 0
    features = character.get("features")
    feat_refs = features.get("feat_refs") if isinstance(features, Mapping) else None
    if not isinstance(feat_refs, list) or not feat_refs:
        return 0
    budget = 0
    for ref in feat_refs:
        if not isinstance(ref, str):
            continue
        definition = definitions.get(ref.removeprefix("feat:"))
        if definition is None:
            continue
        value = definition.parameters.get("weapon_damage_rerolls_per_turn")
        if isinstance(value, int) and not isinstance(value, bool):
            budget = max(budget, value)
    return budget


__all__ = [
    "two_handed_melee_damage_die_minimum",
    "weapon_damage_rerolls_per_turn",
]
