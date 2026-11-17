"""Encounter-scoped hand occupancy: what the actor is gripping *right now*.

``equipment`` is a loadout.  It answers "what does this character carry", and
it decides armor class, attunement and what may be drawn.  It does not answer
"what is in hand this instant" -- the SRD makes that a cheap, repeated
question: *"You can either equip or unequip one weapon when you make an attack
as part of this action."*  A character sheathes and draws all the time, and the
narrator is never going to keep that in sync with the sheet.

This module owns only that second question.  It never writes character state,
it never learns a rule beyond "two hands, one item each", and everything it
produces dies with the encounter.
"""

from __future__ import annotations

from typing import Any

SIDES: tuple[str, str] = ("left", "right")

# 拒绝原因是 intent 层的唯一口径：客户端只显示它，不自行判定合法性。
DRAW_TWO_HANDED_OCCUPIED = "drawing a two-handed weapon needs both hands free"
NO_FREE_HAND = "no free hand to draw a weapon"
STOW_NOT_HELD = "weapon to stow is not in hand"
DRAW_ALREADY_HELD = "weapon to draw is already in hand"
SWAP_SHAPE = "weapon_swap must declare exactly one of draw or stow"


def empty_hands() -> dict[str, str | None]:
    return {side: None for side in SIDES}


def normalize(hands: Any) -> dict[str, str | None]:
    """Copy a stored grip into exactly two keys, dropping anything else."""

    if not isinstance(hands, dict):
        return empty_hands()
    return {side: (str(hands.get(side)) if hands.get(side) else None) for side in SIDES}


def free_hand(hands: dict[str, str | None]) -> str:
    for side in SIDES:
        if not hands.get(side):
            return side
    return ""


def apply_swap(
    hands: Any,
    swap: Any,
    *,
    two_handed_refs: frozenset[str] | set[str] = frozenset(),
) -> tuple[dict[str, str | None], str]:
    """Apply one declared equip or unequip.

    One operation per attack -- that *is* the budget: "either equip or unequip
    one weapon".  Two operations in one declaration is a malformed intent, not
    a rich one, so it fails instead of being split into two free actions.
    Returns ``(hands, "")`` on success and ``({}, reason)`` on refusal; the
    caller owns the error string, because only it knows how to surface it.
    """

    if not isinstance(swap, dict):
        return {}, SWAP_SHAPE
    if set(swap) - {"draw", "stow"}:
        return {}, SWAP_SHAPE
    draw = str(swap.get("draw") or "")
    stow = str(swap.get("stow") or "")
    if bool(draw) == bool(stow):
        return {}, SWAP_SHAPE

    result = normalize(hands)
    if stow:
        if stow not in {result[side] for side in SIDES}:
            return {}, STOW_NOT_HELD
        for side in SIDES:
            if result[side] == stow:
                result[side] = None
        return result, ""
    if draw in {result[side] for side in SIDES}:
        return {}, DRAW_ALREADY_HELD
    if draw in two_handed_refs:
        if any(result[side] for side in SIDES):
            return {}, DRAW_TWO_HANDED_OCCUPIED
        return {"left": draw, "right": draw}, ""
    side = free_hand(result)
    if not side:
        return {}, NO_FREE_HAND
    result[side] = draw
    return result, ""
