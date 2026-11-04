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
    # Catalog slots that this slice deliberately does not implement.
    "cleave": {"kind": "unimplemented"},
    "nick": {"kind": "unimplemented"},
    "push": {"kind": "unimplemented"},
    "topple": {"kind": "unimplemented"},
}
