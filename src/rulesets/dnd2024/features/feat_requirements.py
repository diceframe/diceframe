"""Feat prerequisites: one declaration, one verdict.

A feat's printed "Prerequisite:" line is a set of AND-ed facts -- level, an
ability minimum, armor training, a named feature, spellcasting.  They used to
live as four ``if`` statements in two different methods, and the two had already
drifted: the level-up path never read ``requires_feature``, while character
creation never read an ability minimum or spellcasting.  The same feat could
therefore be refused at level 5 and accepted from a background at level 1.

Two rules keep that from happening again:

* the verdict comes from one function, and every caller maps its closed set of
  reason codes to its own wording;
* a requirement kind is only allowed in when a reader for it exists here.  A
  kind nothing evaluates is a declaration nobody checks -- the same rule the
  parameter registry applies to parameters.

Scope note: this module answers "may the character *take* this feat".  It says
nothing about whether an individual capability of that feat is usable right
now; that is ``FeatureCapability.requires`` / ``requires_feature_id`` and lives
with the projection.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.rulesets.dnd2024.character.primitives import ABILITY_IDS

#: Kinds of predicate a printed prerequisite can express.  Adding one means
#: adding a branch in ``feat_unavailable_reasons`` in the same commit.
REQUIREMENT_KINDS = frozenset({
    "ability_minimum",  # Strength or Dexterity 13+
    "feature",          # Fighting Style / any named feature
    "proficiency",      # Medium Armor Training
    "spellcasting",     # Spellcasting or Pact Magic
})

#: Armor training, mirroring the class entity's ``armor_proficiencies`` values.
#: A test asserts the two sets agree, so drift fails rather than silently
#: making "Medium Armor Training" unspellable.
ARMOR_TRAINING = frozenset({"light", "medium", "heavy", "shield"})
#: Weapon training, mirroring ``weapon_proficiencies``.
WEAPON_TRAINING = frozenset({"simple", "martial"})

#: Every reason a feat may be unavailable, in the order reporters append them.
#: Closed: a caller mapping these to user-facing text must cover exactly this.
REASON_CODES = (
    "level",
    "already_owned",
    "ability_prerequisite",
    "feature",
    "proficiency",
    "spellcasting",
)

_FAMILIES: dict[str, frozenset[str]] = {
    "armor": ARMOR_TRAINING,
    "weapon": WEAPON_TRAINING,
}


class FeatRequirementError(ValueError):
    """Raised when a declared prerequisite cannot be interpreted."""


@dataclass(frozen=True, slots=True)
class FeatRequirement:
    """One predicate from a prerequisite line."""

    kind: str
    #: ``ability_minimum`` only.  "Strength or Dexterity 13+" is an OR inside
    #: one predicate, which is why the minimum is a mapping rather than a pair.
    any_of: Mapping[str, int] = field(default_factory=dict)
    feature_id: str = ""
    family: str = ""
    proficiency_id: str = ""


class DeclaredFeat(Protocol):
    """What the verdict reads.  Structural, so tests can pass a stub."""

    @property
    def id(self) -> str: ...

    @property
    def minimum_level(self) -> int: ...

    @property
    def repeatable(self) -> bool: ...

    @property
    def requires(self) -> tuple[FeatRequirement, ...]: ...


@dataclass(frozen=True, slots=True)
class FeatContext:
    """The character facts a prerequisite is allowed to look at.

    Callers build it from what they have: the level-up path from the class
    snapshot, character creation from the draft.  Nothing here is optional in
    the sense of "skip the check when unknown" -- a caller that cannot answer a
    predicate must not offer the feat in the first place.
    """

    level: int
    abilities: Mapping[str, int] = field(default_factory=dict)
    owned_refs: Collection[str] = ()
    slot_profile: str = ""
    feature_ids: Collection[str] = ()
    proficiency_refs: Collection[str] = ()


def proficiency_ref(family: str, proficiency_id: str) -> str:
    """How a trained proficiency is spelled in the character's derived facts."""

    if family in _FAMILIES:
        return f"{family}_category:{proficiency_id}"
    return f"{family}:{proficiency_id}"


def parse_requirements(feat_id: str, raw: Any) -> tuple[FeatRequirement, ...]:
    """Validate a ``requires`` list, failing closed on anything unrecognised."""

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise FeatRequirementError(f"{feat_id}.requires must be an array")
    requirements: list[FeatRequirement] = []
    for index, entry in enumerate(raw):
        requirements.append(_parse_requirement(feat_id, index, entry))
    return tuple(requirements)


def _parse_requirement(feat_id: str, index: int, entry: Any) -> FeatRequirement:
    where = f"{feat_id}.requires[{index}]"
    if not isinstance(entry, Mapping):
        raise FeatRequirementError(f"{where} must be an object")
    kind = entry.get("kind")
    if not isinstance(kind, str) or kind not in REQUIREMENT_KINDS:
        raise FeatRequirementError(
            f"{where}.kind is not a supported prerequisite: {kind!r}"
        )
    if kind == "ability_minimum":
        _reject_extra(where, entry, {"kind", "any_of"})
        return FeatRequirement(kind=kind, any_of=_parse_any_of(where, entry.get("any_of")))
    if kind == "feature":
        _reject_extra(where, entry, {"kind", "id"})
        return FeatRequirement(kind=kind, feature_id=_require_id(where, entry.get("id")))
    if kind == "proficiency":
        _reject_extra(where, entry, {"kind", "family", "id"})
        family = entry.get("family")
        if not isinstance(family, str) or family not in _FAMILIES:
            raise FeatRequirementError(
                f"{where}.family is not a supported proficiency family: {family!r}"
            )
        proficiency_id = _require_id(where, entry.get("id"))
        if proficiency_id not in _FAMILIES[family]:
            raise FeatRequirementError(
                f"{where} names an unknown {family} training: {proficiency_id!r}"
            )
        return FeatRequirement(kind=kind, family=family, proficiency_id=proficiency_id)
    # spellcasting is a fact about the character, not a named thing.
    _reject_extra(where, entry, {"kind"})
    return FeatRequirement(kind=kind)


def _reject_extra(where: str, entry: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted({str(key) for key in entry} - allowed)
    if unknown:
        raise FeatRequirementError(f"{where} has an unknown field: {unknown[0]}")


def _require_id(where: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise FeatRequirementError(f"{where} needs an id")
    return value


def _parse_any_of(where: str, raw: Any) -> dict[str, int]:
    if not isinstance(raw, Mapping) or not raw:
        raise FeatRequirementError(f"{where}.any_of must be a non-empty object")
    minimums: dict[str, int] = {}
    for ability, minimum in raw.items():
        if not isinstance(ability, str) or ability not in ABILITY_IDS:
            raise FeatRequirementError(f"{where}.any_of names an unknown ability: {ability!r}")
        if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= 30:
            raise FeatRequirementError(
                f"{where}.any_of.{ability} must be an integer from 1 to 30"
            )
        minimums[ability] = minimum
    return minimums


def feat_unavailable_reasons(
    definition: DeclaredFeat, context: FeatContext,
) -> tuple[str, ...]:
    """Every reason this feat cannot be taken, in a closed vocabulary.

    An empty tuple means available.  Order is stable (level first, then the
    declaration order of ``requires``) so tests and UIs can compare tuples.
    """

    reasons: list[str] = []
    if context.level < definition.minimum_level:
        reasons.append("level")
    if (
        f"feat:{definition.id}" in set(context.owned_refs)
        and not definition.repeatable
    ):
        reasons.append("already_owned")
    trained = set(context.proficiency_refs)
    features = set(context.feature_ids)
    for requirement in definition.requires:
        if requirement.kind == "ability_minimum":
            if not any(
                int(context.abilities.get(ability, 0) or 0) >= minimum
                for ability, minimum in requirement.any_of.items()
            ):
                reasons.append("ability_prerequisite")
        elif requirement.kind == "feature":
            if requirement.feature_id not in features:
                reasons.append("feature")
        elif requirement.kind == "proficiency":
            if proficiency_ref(requirement.family, requirement.proficiency_id) not in trained:
                reasons.append("proficiency")
        elif requirement.kind == "spellcasting":
            if not context.slot_profile:
                reasons.append("spellcasting")
        else:  # pragma: no cover - parse_requirements already rejected it
            raise FeatRequirementError(
                f"{definition.id} has an unhandled requirement kind: {requirement.kind!r}"
            )
    return tuple(reasons)


def proficiencies_from(character: Mapping[str, Any] | None) -> frozenset[str]:
    """The trained refs a character carries, flattened for the predicate.

    Reads the derived sheet, not the class's declared lists: a feat may grant a
    training (Moderately Armored grants medium armor), and a prerequisite must
    see what the character *has*, not what the class alone offered.
    """

    if not isinstance(character, Mapping):
        return frozenset()
    raw = character.get("proficiencies")
    if not isinstance(raw, Mapping):
        return frozenset()
    refs: set[str] = set()
    for key in ("armor_category_refs", "weapon_category_refs", "skill_refs", "tool_refs"):
        values = raw.get(key)
        if isinstance(values, (list, tuple, set, frozenset)):
            refs.update(str(value) for value in values)
    return frozenset(refs)
