"""Small deterministic primitives shared by D&D 2024 character stages."""

from __future__ import annotations


ABILITY_IDS = ("str", "dex", "con", "int", "wis", "cha")
STANDARD_ARRAY = (15, 14, 13, 12, 10, 8)
POINT_BUY_COSTS = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
ALIGNMENTS = frozenset({
    "lawful_good", "neutral_good", "chaotic_good",
    "lawful_neutral", "neutral", "chaotic_neutral",
    "lawful_evil", "neutral_evil", "chaotic_evil",
})


def ability_modifier(score: int) -> int:
    return (int(score) - 10) // 2


def derived_save(score: int, *, proficiency_bonus: int, proficient: bool) -> int:
    """A saving throw total: the bare modifier, plus PB when proficient.

    Shared by characters and stat blocks.  A stat block only prints its
    *proficient* saves, so the caller passes ``proficient`` from what it declared
    and this function stays the single place the arithmetic lives.
    """

    return ability_modifier(score) + (int(proficiency_bonus) if proficient else 0)


def derived_skill(
    score: int, *, proficiency_bonus: int, proficient: bool, expertise: bool = False,
) -> int:
    """A skill total. Expertise replaces the PB contribution, never stacks."""

    if not proficient:
        return ability_modifier(score)
    bonus = int(proficiency_bonus)
    return ability_modifier(score) + (2 * bonus if expertise else bonus)


def proficiency_bonus(level: int) -> int:
    return 2 + (max(1, int(level)) - 1) // 4


def ref_id(value: str, expected_kind: str) -> str:
    prefix = f"{expected_kind}:"
    return value[len(prefix):] if value.startswith(prefix) else ""


def humanize_id(value: str) -> str:
    return str(value or "").replace("_", " ").strip().title()
