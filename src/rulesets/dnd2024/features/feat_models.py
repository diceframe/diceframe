"""The typed declaration of a feat, the counterpart of ``ClassFeatureDefinition``.

Subclasses have had a model since F19: every field is validated, unknown shapes
fail at load, and a consumer reads a field instead of guessing a key.  Feats had
none -- twenty entities were ``deepcopy``-ed straight out of the bundle and read
by name at the point of use, which is why ``derivation.py`` still says
a membership test against a feat's name.

This module closes that gap for the fields that have consumers today:

* ``parameters`` -- validated against ``parameter_registry``;
* ``passive_effects`` -- the closed kinds that derivation reads;
* ``choice`` -- the same ``FeatureChoice`` subclass features use, so a skill
  choice means the same thing whichever side declares it.

Two groups are deliberately *not* unified here, and are carried verbatim in
``legacy`` so nothing silently disappears:

* ``ability_choice`` / ``any_ability_minimum`` / ``requires_feature`` /
  ``requires_spellcasting`` -- FD2 turns these into a declared ``requires`` list;
* ``choice_specs`` -- the spell-grant shape belongs to P4 (generalising spell
  grants), which is also where Magic Initiate's dedicated module retires.

``legacy`` is a bounded bag: the loader rejects any key outside a fixed set, so
it can shrink but not quietly grow into "the model again, with no validation".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.features.models import (
    ClassFeatureError,
    FeatureChoice,
    parse_feature_choice,
)
from src.rulesets.dnd2024.features.parameter_registry import (
    PARAMETERS,
    FeatParameterError,
    validate_feat_parameters,
)

#: Every key a feat entity may carry.  A key outside this set is a typo or a
#: field somebody added without a reader, and both are load-time errors.
FEAT_FIELDS = frozenset({
    "schema_version",
    "kind",
    "id",
    "category",
    "source_ref",
    "automation_level",
    "minimum_level",
    "repeatable",
    "parameters",
    "passive_effects",
    "choice",
    # Display names live on the entity so a card can render without a locale
    # lookup; origin feats have always carried them.
    "labels",
    "name",
    "summary",
    "description",
})

#: Fields still read by their old name.  FD2 empties the first four, P4 empties
#: ``choice_specs``.  Until then they are carried, not interpreted.
LEGACY_FEAT_FIELDS = frozenset({
    "ability_choice",
    "any_ability_minimum",
    "requires_feature",
    "requires_spellcasting",
    "choice_specs",
})

PASSIVE_EFFECT_KINDS = frozenset({"armor_class_bonus"})


class FeatDefinitionError(ValueError):
    """Raised when a feat declaration cannot be used authoritatively."""


@dataclass(frozen=True, slots=True)
class PassiveEffect:
    """One always-on effect a feat contributes to the derived sheet."""

    kind: str
    value: int
    requires_armor: bool = False


@dataclass(frozen=True, slots=True)
class FeatDefinition:
    """One validated feat.

    ``legacy`` is intentionally a plain mapping rather than named fields: a
    field would suggest the model owns its shape, and the point of the bag is
    that it is temporary.  Consumers that still need one of those keys read
    ``definition.legacy.get(...)`` and the call site is greppable when FD2/P4
    remove it.
    """

    id: str
    category: str
    source_ref: str
    automation_level: str
    minimum_level: int = 1
    repeatable: bool = False
    parameters: Mapping[str, Any] = field(default_factory=dict)
    passive_effects: tuple[PassiveEffect, ...] = ()
    choice: FeatureChoice | None = None
    legacy: Mapping[str, Any] = field(default_factory=dict)


def parse_feat(raw: Mapping[str, Any]) -> FeatDefinition:
    """Validate one entity and return its declaration.

    ``schema_version`` / ``kind`` are checked by the bundle loader already; they
    are accepted here so a caller holding a raw entity need not strip them.
    """

    feat_id = _require_text(raw.get("id"), "feat.id")
    # ``category`` 必填。允许为空会让"忘了写"的专长既不报错也不进任何池——
    # 能选、能看，但永远选不到，正是本片要消灭的形状。
    category = _require_text(raw.get("category"), f"{feat_id}.category")
    source_ref = _require_text(raw.get("source_ref"), f"{feat_id}.source_ref")
    automation = _require_text(
        raw.get("automation_level"), f"{feat_id}.automation_level",
    )
    if automation not in {"deterministic", "guided", "reference"}:
        raise FeatDefinitionError(
            f"{feat_id}.automation_level is not a supported level: {automation!r}"
        )

    unknown = sorted(set(raw) - FEAT_FIELDS - LEGACY_FEAT_FIELDS)
    if unknown:
        raise FeatDefinitionError(f"{feat_id} has an unknown field: {unknown[0]}")

    try:
        parameters = validate_feat_parameters(feat_id, raw.get("parameters"))
    except FeatParameterError as exc:
        raise FeatDefinitionError(str(exc)) from exc

    minimum_level = raw.get("minimum_level", 1)
    if isinstance(minimum_level, bool) or not isinstance(minimum_level, int) or minimum_level < 1:
        raise FeatDefinitionError(f"{feat_id}.minimum_level must be a positive integer")
    repeatable = raw.get("repeatable", False)
    if not isinstance(repeatable, bool):
        raise FeatDefinitionError(f"{feat_id}.repeatable must be a boolean")

    try:
        # 专长是这两个放宽的唯一使用者：Skilled 的三项可在技能/工具间分配，
        # 且授予的是"熟练"而非 Expertise。
        choice = parse_feature_choice(
            feat_id,
            raw.get("choice"),
            allow_skill_or_tool=True,
            allow_proficiency_grant=True,
        )
    except ClassFeatureError as exc:
        raise FeatDefinitionError(str(exc)) from exc

    legacy = {
        str(key): raw[key] for key in sorted(LEGACY_FEAT_FIELDS) if key in raw
    }
    definition = FeatDefinition(
        id=feat_id,
        category=category,
        source_ref=source_ref,
        automation_level=automation,
        minimum_level=minimum_level,
        repeatable=repeatable,
        parameters=parameters,
        passive_effects=_parse_passive_effects(feat_id, raw.get("passive_effects")),
        choice=choice,
        legacy=legacy,
    )
    check_declaration_completeness(definition)
    return definition


def check_declaration_completeness(definition: FeatDefinition) -> None:
    """A feat that calls itself deterministic may not declare an unread parameter.

    ``automation_level`` used to be a label nothing acted on: the bundle checked
    the value and no path read it.  Here it becomes a claim the loader can test.
    A ``guided`` feat may declare keys nobody reads yet (that is what guided
    means); a ``deterministic`` one may not, because "deterministic" promises the
    declaration is the whole story.
    """

    if definition.automation_level != "deterministic":
        return
    unread = sorted(
        key for key in definition.parameters
        if (spec := PARAMETERS.get(key)) is not None and spec.stage == "unread"
    )
    if unread:
        raise FeatDefinitionError(
            f"{definition.id} is deterministic but declares unread parameters: "
            f"{', '.join(unread)}"
        )


def _parse_passive_effects(feat_id: str, raw: Any) -> tuple[PassiveEffect, ...]:
    """Validate the closed passive-effect declarations.

    The armor class formula reads these instead of embedding numbers, so an
    unknown kind or a non-integer value has to fail at load: silently
    contributing nothing would look like a working feat.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise FeatDefinitionError(f"{feat_id}.passive_effects must be an array")
    effects: list[PassiveEffect] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise FeatDefinitionError(f"{feat_id}.passive_effects entries must be objects")
        kind = str(entry.get("kind") or "")
        if kind not in PASSIVE_EFFECT_KINDS:
            raise FeatDefinitionError(
                f"{feat_id}.passive_effects has an unknown kind: {kind}"
            )
        value = entry.get("value")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
            # 值域与旧白名单一致：这条校验原本就在，迁移时不能悄悄放宽。
            raise FeatDefinitionError(
                f"{feat_id}.passive_effects.{kind} needs an integer from 1 to 10"
            )
        requires_armor = entry.get("requires_armor", False)
        if not isinstance(requires_armor, bool):
            raise FeatDefinitionError(
                f"{feat_id}.passive_effects.{kind}.requires_armor must be a boolean"
            )
        unknown = sorted(set(entry) - {"kind", "value", "requires_armor"})
        if unknown:
            raise FeatDefinitionError(
                f"{feat_id}.passive_effects has an unknown field: {unknown[0]}"
            )
        effects.append(
            PassiveEffect(kind=kind, value=value, requires_armor=requires_armor)
        )
    return tuple(effects)


def load_feat_definitions(bundle: LoadedRulesetBundle) -> dict[str, FeatDefinition]:
    """Every declared feat, keyed by bare id.

    Feats currently live in two places -- one entity per origin feat, and one
    catalog entity holding the advancement feats.  FD3 collapses that into a
    single ``kind``; until then both are read here so callers see one pool.

    The catalog id is named literally on purpose: ``bundle.get`` takes an id, not
    a default, so this is the one place that knows the current layout.  FD3's
    job is to delete this line, not to spread it.
    """

    definitions: dict[str, FeatDefinition] = {}
    for entity in bundle.list("feat"):
        definition = parse_feat(entity)
        definitions[definition.id] = definition

    catalog = bundle.get("advancement_feat_catalog", "srd_advancement_feats")
    entries = catalog.get("feats") if isinstance(catalog, Mapping) else None
    if isinstance(entries, Mapping):
        for feat_id, entry in entries.items():
            if not isinstance(entry, Mapping):
                continue
            # The catalog declares one automation_level for all of its entries.
            # Each row inherits it unless it says otherwise, so the model always
            # holds a concrete value.
            row = dict(entry)
            row.setdefault("id", str(feat_id))
            row.setdefault("kind", "feat")
            row.setdefault("source_ref", str(catalog.get("source_ref") or ""))
            row.setdefault("automation_level", str(catalog.get("automation_level") or ""))
            definition = parse_feat(row)
            if definition.id in definitions:
                raise FeatDefinitionError(
                    f"feat {definition.id} is declared twice: once as an entity "
                    "and once inside the advancement catalog"
                )
            definitions[definition.id] = definition
    return definitions


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FeatDefinitionError(f"{field_name} must be a non-empty string")
    return value
