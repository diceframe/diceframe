"""Exploration-level semantics for the revive spell family (R1).

The five revival spells share one shape: restore a dead player, optionally
cleanse a condition, and optionally attach a long-rest penalty or rebuild the
body. Revival is a settled mechanic but its *full* form depends on subsystems
that do not exist yet (D20 aggregation for the −4 penalty, a long-rest decay
hook, character reconstruction for reincarnate, permanent GM name summoning).

This module therefore keeps two things apart:

- :data:`REVIVE_SEMANTICS` — what each spell must do (data, not branches);
- :data:`IMPLEMENTED_KEYS` — which semantics the engine can settle today.

A spell is settled only when *every* semantic it declares is implemented. Any
unimplemented key rejects the cast outright, so a partial revival can never be
silently downgraded. New keys are added here first, then taught to the engine.

Keeping the table in code (rather than a new template catalog file) is
deliberate: five rows is below the point where externalising earns its
schema, loader, and migration surface. Revisit when the exploration effect
surface grows beyond revival.
"""

from __future__ import annotations

from typing import Any

REVIVE_SEMANTICS: dict[str, dict[str, Any]] = {
    "spell:revivify": {"hp": 1},
    # hp + cleanse are implementable today; the −4 penalty needs the generic
    # D20 aggregation (SP10) and a long-rest decay hook, neither of which is in
    # place, so the whole spell is rejected until they are.
    "spell:raise_dead": {
        "hp": 1, "cleanse": ("poisoned",), "penalty": "minus4_rest_decay",
    },
    "spell:resurrection": {
        "hp": "full", "cleanse": ("poisoned",),
        "penalty": "minus4_rest_decay", "caster_cost": "minus4_rest_decay",
    },
    # Body reconstruction is out of scope for R1.
    "spell:reincarnate": {"new_body": True},
    # Summoning by name targets a creature outside the entity registry.
    "spell:true_resurrection": {"hp": "full", "summon_by_name": True},
}

# Semantic keys the exploration engine can settle deterministically today.
IMPLEMENTED_KEYS = frozenset({"hp", "cleanse"})
# ``cleanse`` values implemented so far.
IMPLEMENTED_CLEANSES = frozenset({"poisoned"})
UNSUPPORTED_MESSAGE = "this form of revival is not supported yet"


def revive_definition(spell_ref: str) -> dict[str, Any] | None:
    """Return the declared semantics for a revive spell, or ``None``."""

    return REVIVE_SEMANTICS.get(spell_ref)


def is_fully_implemented(definition: dict[str, Any]) -> bool:
    """Whether every declared semantic can be settled without guessing."""

    for key, value in definition.items():
        if key not in IMPLEMENTED_KEYS:
            return False
        if key == "hp":
            if value != "full" and (isinstance(value, bool) or not isinstance(value, int)):
                return False
        elif key == "cleanse":
            cleanses = {str(item) for item in value or ()}
            if not cleanses.issubset(IMPLEMENTED_CLEANSES):
                return False
    return True


def revive_events(
    target_id: str, definition: dict[str, Any], max_hp: int,
) -> list[dict[str, Any]]:
    """Events that settle a fully implemented revive definition.

    ``condition.removed`` (cleanse) precedes ``character.revived``; the reducer
    removes the death states itself, so only the extra cleanses are emitted.
    ``hp: "full"`` resolves to the target's maximum because the reducer clamps
    to a positive integer and rejects non-numeric payloads.
    """

    events: list[dict[str, Any]] = []
    for condition in definition.get("cleanse") or ():
        events.append({
            "type": "condition.removed", "target_id": target_id,
            "condition": str(condition),
        })
    hp = definition["hp"]
    if hp == "full":
        hp = max(1, int(max_hp or 0))
    events.append({
        "type": "dnd2024.character.revived", "actor_id": target_id, "hp": int(hp),
    })
    return events
