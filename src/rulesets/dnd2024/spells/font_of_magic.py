"""Font of Magic: Sorcery Points into spell slots, and back (SRD 5.2.1 p66).

Every function here is a pure rule over an authoritative character mapping:
the caller owns the transaction, the state, and the event batch.  That keeps
the two directions symmetric -- each one is planned first and applied second,
so an illegal conversion is rejected before any point or slot moves.

Three facts shape the code:

* A created slot is **temporary**.  It lives in ``spellcasting.class.font_slots``
  next to the ordinary slot table (never inside it), so it can exceed the
  ordinary maximum and is discarded -- not merged -- at the next Long Rest.
* The two directions are not symmetric.  Spending a slot for points costs no
  action, creating one costs a Bonus Action; the action economy belongs to
  combat and is therefore paid by the capability declaration, not here.
* Nothing is clamped.  A conversion that would raise Sorcery Points above
  their maximum is refused as a whole, because a silent truncation would eat
  a spell slot for less than its declared price.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.rulesets.dnd2024.features.resources import resource_state

FONT_SOURCE = "font_of_magic"
POINTS_RESOURCE_ID = "sorcery_points"
MAX_SLOT_LEVEL = 9
# "You can create a spell slot no higher than level 5" -- a hard ceiling that
# does not move with the declared cost table, so it is checked on its own.
MAX_CREATED_SLOT_LEVEL = 5
# The SRD table, level -> (Sorcery Point cost, minimum Sorcerer level).  Content
# declares it (`created_slot_costs`); this copy is the shape the declaration is
# validated against, never a second live source.
CREATED_SLOT_COSTS: tuple[tuple[int, int, int], ...] = (
    (1, 2, 2),
    (2, 3, 3),
    (3, 5, 5),
    (4, 6, 7),
    (5, 7, 9),
)


class FontOfMagicError(ValueError):
    """Raised when a conversion cannot be settled as declared."""


def _canonical(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    nested = payload.get("ruleset_character")
    return dict(nested) if isinstance(nested, Mapping) else dict(payload)


def _class_magic(character: Mapping[str, Any]) -> dict[str, Any]:
    spellcasting = character.get("spellcasting")
    block = spellcasting.get("class") if isinstance(spellcasting, Mapping) else None
    return block if isinstance(block, dict) else {}


def font_slots(payload: Mapping[str, Any] | None) -> dict[str, int]:
    """Unspent temporary slots by level.

    Read-only and forgiving: a stored record that is not a positive count of a
    legal level is dropped rather than trusted, so a hand-edited save cannot
    hand out a 9th-level slot Font of Magic may never create.
    """

    raw = _class_magic(_canonical(payload)).get("font_slots")
    if not isinstance(raw, Mapping):
        return {}
    slots: dict[str, int] = {}
    for key, value in raw.items():
        level = str(key)
        if not level.isdigit() or not 1 <= int(level) <= MAX_CREATED_SLOT_LEVEL:
            continue
        count = int(value) if isinstance(value, int) and not isinstance(value, bool) else 0
        if count > 0:
            slots[level] = count
    return slots


def _font_storage(character: dict[str, Any]) -> dict[str, Any]:
    """The mutable store; callers must have already validated the target."""

    block = _class_magic(character)
    if not block:
        raise FontOfMagicError("this character has no class spellcasting")
    raw = block.get("font_slots")
    if not isinstance(raw, dict):
        raw = {}
        block["font_slots"] = raw
    return raw


def sorcery_points(character: Mapping[str, Any]) -> tuple[int, int]:
    """``(current, maximum)``; missing pool is an error, not zero."""

    state = resource_state(character, POINTS_RESOURCE_ID)
    if not state:
        raise FontOfMagicError("Sorcery Points are not initialized")
    return int(state.get("current", 0) or 0), int(state.get("maximum", 0) or 0)


def parse_created_slot_costs(raw: Any) -> tuple[tuple[int, int, int], ...]:
    """Validate the declared conversion table, or refuse it as a whole.

    The table is content, so a typo is possible; a partially valid table would
    quietly delete the rows that failed, which is worse than loading nothing.
    """

    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        raise FontOfMagicError("created_slot_costs must be a list of rows")
    rows: list[tuple[int, int, int]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise FontOfMagicError("created_slot_costs rows must be [level, points, minimum]")
        level, points, minimum = row
        if any(isinstance(value, bool) or not isinstance(value, int) for value in row):
            raise FontOfMagicError("created_slot_costs rows must be integers")
        if not 1 <= level <= MAX_CREATED_SLOT_LEVEL:
            raise FontOfMagicError("created_slot_costs has an illegal slot level")
        if points < 1 or minimum < 1:
            raise FontOfMagicError("created_slot_costs needs a positive cost and level")
        rows.append((level, points, minimum))
    if not rows:
        raise FontOfMagicError("created_slot_costs must declare at least one row")
    levels = [row[0] for row in rows]
    if len(set(levels)) != len(levels):
        raise FontOfMagicError("created_slot_costs declares a level twice")
    return tuple(sorted(rows))


def created_slot_cost(
    costs: Iterable[tuple[int, int, int]], slot_level: int,
) -> tuple[int, int] | None:
    """``(points, minimum_level)`` for one slot level, or ``None`` if illegal."""

    for level, points, minimum in costs:
        if level == slot_level:
            return points, minimum
    return None


def declaring_class_level(character: Mapping[str, Any], class_id: str) -> int:
    """The level in the class that declares a feature, or ``0``.

    Multiclass sheets are rare but not impossible, and the minimum-level rows
    are about *Sorcerer* level: reading the total character level would let a
    Sorcerer 5 / Wizard 5 create a slot a Sorcerer 5 may not.
    """

    build = character.get("build")
    rows = build.get("class_levels") if isinstance(build, Mapping) else None
    if not isinstance(rows, list):
        return 0
    wanted = f"class:{class_id}"
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("class_ref") or "") != wanted:
            continue
        level = row.get("level")
        if isinstance(level, int) and not isinstance(level, bool):
            return level
    return 0


def slot_to_points_plan(
    payload: Mapping[str, Any] | None, slot_level: int,
) -> dict[str, int]:
    """Plan "expend a slot, gain its level in Sorcery Points"."""

    if isinstance(slot_level, bool) or not isinstance(slot_level, int):
        raise FontOfMagicError("slot_level must be an integer")
    if not 1 <= slot_level <= MAX_SLOT_LEVEL:
        raise FontOfMagicError("slot_level must be between 1 and 9")
    character = _canonical(payload)
    slots = _class_magic(character).get("slots_current")
    slots = slots if isinstance(slots, Mapping) else {}
    if int(slots.get(str(slot_level), 0) or 0) < 1:
        raise FontOfMagicError("the selected spell slot is already spent")
    current, maximum = sorcery_points(character)
    if current + slot_level > maximum:
        raise FontOfMagicError(
            "converting that slot would exceed your Sorcery Points maximum"
        )
    return {
        "slot_level": slot_level,
        "points": slot_level,
        "before": current,
        "after": current + slot_level,
        "maximum": maximum,
    }


def create_slot_plan(
    payload: Mapping[str, Any] | None, slot_level: int,
    costs: Iterable[tuple[int, int, int]], class_level: int,
) -> dict[str, int]:
    """Plan "spend Sorcery Points, create one temporary slot"."""

    if isinstance(slot_level, bool) or not isinstance(slot_level, int):
        raise FontOfMagicError("slot_level must be an integer")
    cost = created_slot_cost(costs, slot_level)
    if cost is None:
        raise FontOfMagicError(
            f"Font of Magic cannot create a spell slot of level {slot_level}"
        )
    points, minimum = cost
    if class_level < minimum:
        raise FontOfMagicError(
            f"creating a level {slot_level} slot requires level {minimum} in this class"
        )
    character = _canonical(payload)
    current, maximum = sorcery_points(character)
    if current < points:
        raise FontOfMagicError("not enough Sorcery Points to create that slot")
    return {
        "slot_level": slot_level,
        "points": points,
        "before": current,
        "after": current - points,
        "maximum": maximum,
    }


def apply_slot_to_points(
    character: dict[str, Any], plan: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Commit a planned conversion into ``character``; returns its events."""

    level = str(int(plan["slot_level"]))
    slots = _class_magic(character).get("slots_current") or {}
    if int(slots.get(level, 0) or 0) < 1:  # pragma: no cover - planned above
        raise FontOfMagicError("the selected spell slot is already spent")
    slots[level] = int(slots[level]) - 1
    pool = dict(resource_state(character, POINTS_RESOURCE_ID))
    if not pool:  # pragma: no cover - planned above
        raise FontOfMagicError("Sorcery Points are not initialized")
    pool["current"] = int(plan["after"])
    _set_points(character, pool)
    events = [
        {
            "type": "dnd2024.spell.slot_spent",
            "slot_level": int(plan["slot_level"]),
            # 槽换点不是施法：它既不用动作，也不占用本回合的"每回合一次施法位"。
            "exchange": True,
        },
        {
            "type": "dnd2024.class_resource.refunded",
            "resource_id": POINTS_RESOURCE_ID,
            "amount": int(plan["points"]),
            "before": int(plan["before"]),
            "after": int(plan["after"]),
            "reason": FONT_SOURCE,
        },
    ]
    return events


def apply_create_slot(
    character: dict[str, Any], plan: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Commit a planned creation into ``character``; returns its events."""

    pool = dict(resource_state(character, POINTS_RESOURCE_ID))
    if not pool:  # pragma: no cover - planned above
        raise FontOfMagicError("Sorcery Points are not initialized")
    pool["current"] = int(plan["after"])
    _set_points(character, pool)
    level = str(int(plan["slot_level"]))
    store = _font_storage(character)
    store[level] = int(store.get(level, 0) or 0) + 1
    events = [
        {
            "type": "dnd2024.class_resource.spent",
            "resource_id": POINTS_RESOURCE_ID,
            "amount": int(plan["points"]),
            "before": int(plan["before"]),
            "after": int(plan["after"]),
        },
        {
            "type": "dnd2024.spell.font_slot_created",
            "slot_level": int(plan["slot_level"]),
            "source": FONT_SOURCE,
            "points": int(plan["points"]),
        },
    ]
    return events


def _set_points(character: dict[str, Any], pool: Mapping[str, Any]) -> None:
    resources = character.setdefault("resources", {})
    class_state = resources.setdefault("class", {})
    class_state[POINTS_RESOURCE_ID] = dict(pool)


def clear_font_slots(character: dict[str, Any]) -> dict[str, int]:
    """Discard every unspent temporary slot, returning what was removed.

    A Long Rest ends the created slots: they are temporary, so they are removed
    rather than merged into the ordinary table, and an already empty store
    yields an empty result so two Long Rests in a row change nothing.
    """

    removed = font_slots(character)
    if removed:
        _class_magic(character).pop("font_slots", None)
    return removed
