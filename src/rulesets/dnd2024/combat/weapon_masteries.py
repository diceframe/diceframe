"""Weapon Mastery effect registry (D&D 2024).

Every weapon declares one mastery property in its catalog entry.  This module
maps those property ids to the effect the engine applies; a property that is
declared but not implemented stays ``unimplemented`` so the catalog slot can
land before the effect does (fail closed: no event, no error).
"""

from __future__ import annotations

from typing import Any

WEAPON_MASTERY_FEATURE_ID = "weapon_mastery"

WEAPON_MASTERY_EFFECTS: dict[str, dict[str, Any]] = {
    # Miss face: fixed damage equal to the attack's ability modifier (floored
    # at zero), same damage type as the weapon, no damage dice.
    "graze": {"kind": "miss_damage"},
    # Hit face, damage not required: the target's next attack has disadvantage.
    "sap": {
        "kind": "apply_condition",
        "on": "hit",
        "condition": "next_attack_disadvantage",
        "duration": "after_next_attack",
    },
    # Hit face, damage required: advantage on the next attack against the target.
    "vex": {
        "kind": "apply_condition",
        "on": "hit",
        "requires_damage": True,
        "condition": "next_attack_advantage",
        "duration": "after_next_attack",
    },
    # Hit face, damage required: target speed -10 until the attacker's next turn.
    "slow": {
        "kind": "apply_condition",
        "on": "hit",
        "requires_damage": True,
        "condition": "slowed_10",
        "duration": "attacker_next_turn_start",
    },
    # Hit face: 10 feet straight away from the attacker, Large or smaller only.
    # The event carries the distance and *who* pushed, not a signed number: the
    # direction is geometry, and geometry belongs to whoever owns positions.
    "push": {
        "kind": "push",
        "on": "hit",
        "feet": 10,
        "size_at_most": "large",
    },
    # Hit face: Constitution save (DC 8 + the ability modifier used for the
    # attack + Proficiency Bonus) or Prone.  Both the save and the condition are
    # held until the hit is committed, so a Shield that turns the hit into a
    # miss rolls nothing.
    "topple": {
        "kind": "save_condition",
        "on": "hit",
        "ability": "con",
        "condition": "prone",
        "duration": "until_stand",
    },
    # Not a hit face at all: the Light property's extra attack may be made as
    # part of the Attack action instead of a Bonus Action, once per turn.  The
    # economy change lives in the attack flow; this entry only declares it.
    "nick": {"kind": "light_extra_as_action"},
    # Catalog slot this slice deliberately does not implement (needs a second
    # attack roll and a second target, i.e. a new intent field).
    "cleave": {"kind": "unimplemented"},
}


def nick_active(features: Any, character: Any, weapon: dict[str, Any]) -> bool:
    """Whether this weapon's Nick mastery lets the Light extra attack be free.

    Both the validator and the cost event ask this question, and both must ask
    it the same way: the mastery has to be *this* weapon's, and the character
    has to be someone the mastery is unlocked for.  ``weapon_mastery`` is the
    gate for every property, so it is the gate here too.
    """

    mastery = str(weapon.get("mastery") or "")
    if WEAPON_MASTERY_EFFECTS.get(mastery, {}).get("kind") != "light_extra_as_action":
        return False
    return bool(features.has_feature(character, WEAPON_MASTERY_FEATURE_ID))
