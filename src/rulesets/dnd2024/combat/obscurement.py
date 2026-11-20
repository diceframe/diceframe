"""Obscurement as a level, not a pair of booleans.

The rules only define two of them -- lightly and heavily -- and this module
keeps it that way.  Two neighbouring ideas are deliberately *not* levels here:

* something solid blocking sight (a wall, a closed door) is already expressed by
  ``total`` cover: the creature cannot be targeted directly.  Modelling it a
  second time as "opaque obscurement" would give one fact two answers whose
  boundary exists only by convention;
* bright / dim / darkness is a property of a light source and an area, not of a
  creature.  When lighting lands it resolves *to* lightly or heavily; it does not
  add rungs.

Why a level instead of ``{disadvantage, cannot_see}``: the rules ask about the
level itself ("can he Hide here?" needs ``heavily``, not "cannot see"), and a
pair of booleans flattens that question into something you cannot ask back.
"""

from __future__ import annotations

from typing import Any, Iterable

#: Ordered, least to most obscuring.
OBSCUREMENT_LEVELS = ("none", "lightly", "heavily")

#: SRD 5.2.1 Obscured.  Lightly: Disadvantage on sight-based Perception.
#: Heavily: you cannot see into it, and are treated as Blinded for seeing.
_OBSCUREMENT_EFFECTS = {
    "none": {"sight_disadvantage": False, "blocks_sight": False},
    "lightly": {"sight_disadvantage": True, "blocks_sight": False},
    "heavily": {"sight_disadvantage": True, "blocks_sight": True},
}


def obscurement_rank(level: str) -> int:
    """Position of ``level`` in the ladder; unknown levels rank as none."""

    try:
        return OBSCUREMENT_LEVELS.index(level)
    except ValueError:
        return 0


def obscurement_effects(level: str) -> dict[str, bool]:
    """The two consequences ``level`` implies, copied so callers cannot mutate it."""

    return dict(_OBSCUREMENT_EFFECTS.get(level, _OBSCUREMENT_EFFECTS["none"]))


def best_obscurement(levels: Iterable[str]) -> str:
    """The most obscuring level among ``levels``.

    Obscurement never stacks: two sources give the worse one, not a sum.  It is
    the same reason cover is a ladder -- "heavily + lightly" is not "worse than
    heavily", it is heavily.
    """

    best = "none"
    for level in levels:
        if obscurement_rank(level) > obscurement_rank(best):
            best = level
    return best


def obscurement_row(level: str, sources: Iterable[str] = ()) -> dict[str, Any]:
    """One answer shape for every consumer: level, its effects, and sources."""

    resolved = level if level in OBSCUREMENT_LEVELS else "none"
    return {
        "level": resolved,
        **obscurement_effects(resolved),
        "sources": list(sources),
    }


#: Zones are an approximation: without a 2D map, every shape is its bounding
#: radius.  A cylinder or a cube therefore covers a sphere of the same reach --
#: recorded here so the shortcut is a decision, not an accident.
ZONE_KINDS = ("sphere", "emanation", "cylinder", "cube")


def zone_covers(combat_row: dict, origin: int, position: int) -> bool:
    """Whether a zone centred at ``origin`` reaches ``position``."""

    radius = int(combat_row.get("radius_ft", 0) or 0)
    return abs(int(origin) - int(position)) <= radius


def zone_row(zone_id: str, zone: dict) -> dict:
    """One zone as a source row: the level plus where it came from."""

    return {
        "level": str(zone.get("level") or "none"),
        "magical": bool(zone.get("magical")),
        "source": f"zone:{zone_id}",
        "spell_ref": str(zone.get("spell_ref") or ""),
    }


def combine_sources(levels: Iterable[str]) -> str:
    """Thin alias kept for readability at call sites that merge sources."""

    return best_obscurement(levels)


#: Senses that change the answer, and how.  ``darkvision`` downgrades
#: *nonmagical* obscurement by one rung; the others ignore it entirely because
#: they do not use sight at all (Blindsight) or see through its causes (Truesight).
SENSE_KINDS = ("darkvision", "blindsight", "truesight")


def sense_range(senses: dict | None, kind: str) -> int:
    """Declared range of one sense in feet, 0 when the creature does not have it."""

    if not isinstance(senses, dict):
        return 0
    value = senses.get(kind)
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def effective_level(
    level: str, *, magical: bool, senses: dict | None, distance_ft: int,
) -> tuple[str, str]:
    """``(level, bypass)`` after the observer's senses narrow the answer.

    ``bypass`` is the reason recorded on the event/answer so a table can see why
    a creature in heavy fog still fights normally -- silence here would look
    like a bug.
    """

    if level == "none":
        return "none", ""
    blindsight = sense_range(senses, "blindsight")
    if blindsight and distance_ft <= blindsight:
        return "none", "blindsight"
    truesight = sense_range(senses, "truesight")
    if truesight and distance_ft <= truesight:
        return "none", "truesight"
    darkvision = sense_range(senses, "darkvision")
    if darkvision and distance_ft <= darkvision:
        if magical:
            # 魔法黑暗明文"Darkvision can't see through it"：降级不成立，
            # 但要把原因记下来，免得看起来像没生效。
            return level, "darkvision_fails_magical_darkness"
        if level == "heavily":
            return "lightly", "darkvision"
        if level == "lightly":
            return "none", "darkvision"
    return level, ""
