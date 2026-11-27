"""Check-time ability replacement and bonuses, driven by content declarations.

All four rolling surfaces share one shape -- ``mod(effective ability) + the
non-ability part`` -- but the engine only ever needs the two operations this
module provides: pick the ability to read, and add up whatever the feature
declares on top.

Nothing here names a feature.  A declaration says what surface it applies to,
which skills or abilities it scopes itself to, under which condition, whether it
replaces the ability and what it adds; a feature that is not in the content has
no effect at all.

The bonus primitives are deliberately few (``constant``, ``ability_modifier``,
``proficiency_bonus``) because a JSON document cannot execute a function:
``(score - 10) // 2`` is a *rule*, so its authority lives in
``ability_modifier`` and the content only says which score to read.  ``minimum``
and ``maximum`` stay parameters -- "at least +1" is Otherworldly Glamour's and
Thaumaturge's wording, not a general behaviour, and Elegant Courtier has no
floor at all.
"""

from __future__ import annotations

from typing import Any, Mapping

from src.rulesets.dnd2024.character.builder import ability_modifier


def active_modifiers(
    rows: list[dict[str, Any]],
    *,
    skill: str = "",
    ability: str = "",
    conditions: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The declared modifiers that apply to one check.

    ``skill`` is the bare skill id (``acrobatics``), ``ability`` the ability the
    check is being made with.  A declaration scoped to both must match both; one
    scoped to neither cannot exist (the loader rejects it), so nothing applies
    everywhere by accident.
    """

    matched: list[dict[str, Any]] = []
    for row in rows:
        declared_skills = row.get("skills") or []
        declared_abilities = row.get("abilities") or []
        if declared_skills and (
            not skill or f"skill:{skill}" not in declared_skills
        ):
            continue
        if declared_abilities and (
            not ability or ability not in declared_abilities
        ):
            continue
        condition = row.get("when_condition")
        if condition and not (conditions or {}).get(str(condition)):
            continue
        matched.append(row)
    return matched


def effective_ability(
    rows: list[dict[str, Any]],
    *,
    default_ability: str,
    abilities: Mapping[str, int] | None = None,
) -> str:
    """The ability this check should actually read.

    When ``abilities`` is supplied, optional ability replacements (such as
    Primal Knowledge: "you can make it as a Strength check") only replace the
    default ability when their modifier is at least as high, so an optional
    feature never penalizes a character whose base ability is higher.
    """

    ability = default_ability
    best_mod = (
        ability_modifier(int(abilities.get(ability, 10) or 10))
        if abilities is not None else None
    )
    for row in rows:
        replacement = str(row.get("replace_ability") or "")
        if not replacement:
            continue
        if abilities is None or best_mod is None:
            ability = replacement
            continue
        candidate_mod = ability_modifier(int(abilities.get(replacement, 10) or 10))
        if candidate_mod >= best_mod:
            ability = replacement
            best_mod = candidate_mod
    return ability


def flat_bonus(
    rows: list[dict[str, Any]],
    abilities: Mapping[str, int],
    *,
    proficiency_bonus: int = 0,
) -> tuple[int, list[str]]:
    """The summed bonus, plus the feature ids that contributed it."""

    total = 0
    sources: list[str] = []
    for row in rows:
        add = row.get("add")
        if not isinstance(add, dict):
            continue
        value = _primitive(add, abilities, proficiency_bonus)
        if value is None:
            continue
        total += value
        sources.append(str(row.get("feature_id") or ""))
    return total, sources


def _primitive(
    add: Mapping[str, Any], abilities: Mapping[str, int], proficiency_bonus: int,
) -> int | None:
    source = str(add.get("source") or "")
    if source == "constant":
        value = add.get("value")
        return int(value) if isinstance(value, int) else None
    if source == "ability_modifier":
        score = int(abilities.get(str(add.get("ability") or ""), 10) or 10)
        value = ability_modifier(score)
        low = add.get("minimum")
        high = add.get("maximum")
        if isinstance(low, int):
            value = max(value, low)
        if isinstance(high, int):
            value = min(value, high)
        return value
    if source == "proficiency_bonus":
        return proficiency_bonus * int(add.get("multiplier", 1) or 1)
    return None


def check_total(
    parts: Mapping[str, Any] | None,
    abilities: Mapping[str, int],
    *,
    default_ability: str,
    fallback_total: int,
    rows: list[dict[str, Any]],
    proficiency_bonus: int = 0,
) -> tuple[int, str, int, list[str]]:
    """Recompute one check from its stored split.

    ``parts`` is the derivation's ``{ability, ability_modifier, contribution}``.
    A sheet written before this field existed has no split; it falls back to
    subtracting the default ability's modifier from the total, which is exact
    for every surface the engine has today.
    """

    modifier = ability_modifier(int(abilities.get(default_ability, 10) or 10))
    if isinstance(parts, Mapping):
        ability = str(parts.get("ability") or default_ability)
        non_ability = int(parts.get("contribution") or 0)
    else:
        ability = default_ability
        non_ability = fallback_total - modifier
    effective = effective_ability(rows, default_ability=ability, abilities=abilities)
    bonus, sources = flat_bonus(rows, abilities, proficiency_bonus=proficiency_bonus)
    effective_modifier = ability_modifier(int(abilities.get(effective, 10) or 10))
    return non_ability + effective_modifier + bonus, effective, effective_modifier, sources
