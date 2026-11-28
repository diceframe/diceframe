"""The typed declaration of a feat, the counterpart of ``ClassFeatureDefinition``.

Subclasses have had a model since F19: every field is validated, unknown shapes
fail at load, and a consumer reads a field instead of guessing a key.  Feats had
none -- twenty entities were ``deepcopy``-ed straight out of the bundle and read
by name at the point of use, which is why ``derivation.py`` still says
a membership test against a feat's name.

This module closes that gap for the fields that have consumers today:

* ``parameters`` -- validated against ``parameter_registry``;
* ``passive_effects`` -- the closed kinds that derivation reads;
* ``requires`` -- the printed prerequisite line, evaluated by exactly one
  function (``feat_requirements.feat_unavailable_reasons``);
* ``choice`` -- the same ``FeatureChoice`` subclass features use, so a skill
  choice means the same thing whichever side declares it.

Two shapes are deliberately *not* unified here, and are carried verbatim in
``legacy`` so nothing silently disappears:

* ``ability_choice`` -- an ability increase is a choice, not a bag entry; it
  moves to ``choice`` in its own slice;
* ``choice_specs`` -- the spell-grant shape belongs to P4 (generalising spell
  grants), which is also where Magic Initiate's dedicated module retires.

The three former prerequisite keys (``any_ability_minimum`` /
``requires_feature`` / ``requires_spellcasting``) are not in the bag at all:
they are rejected with a message pointing at ``requires``, because accepting
them would silently strip a feat of its prerequisites.

``legacy`` is a bounded bag: the loader rejects any key outside a fixed set, so
it can shrink but not quietly grow into "the model again, with no validation".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.features.models import (
    ClassFeatureError,
    FeatureCapability,
    FeatureChoice,
    parse_capability,
    parse_feature_choice,
)
from src.rulesets.dnd2024.character.primitives import ABILITY_IDS
from src.rulesets.dnd2024.features.feat_requirements import (
    FeatRequirement,
    FeatRequirementError,
    parse_requirements,
)
from src.rulesets.dnd2024.features.parameter_registry import (
    PARAMETERS,
    FeatParameterError,
    validate_feat_parameters,
)

#: The pseudo-feat a plain ASI is declared as.  It shares the feat code path so
#: "occupy the feat slot" has one implementation, but it is not a feat the
#: character owns: the ledger records it as ``ability_score_increase`` and the
#: progression writers exclude it from ``feat_refs``.  Provenance excludes it
#: for the same reason -- a sheet that carries it (an old writer, a repair
#: path) must not be read as "owns a feat named ability_score_improvement".
#: Defined here, not in ``progression.choices``, so the view can reach it
#: without importing the progression package.
PLAIN_ABILITY_FEAT_REF = "feat:ability_score_improvement"

#: Every key a feat entity may carry.  A key outside this set is a typo or a
#: field somebody added without a reader, and both are load-time errors.
FEAT_FIELDS = frozenset({
    "schema_version",
    "kind",
    "id",
    "category",
    "source_ref",
    "automation_level",
    "content_pack",
    "minimum_level",
    "repeatable",
    "parameters",
    "passive_effects",
    "capabilities",
    "choice",
    # 先决：印刷在专长卡上的 Prerequisite 行，语义见 feat_requirements。
    "requires",
    # 属性提升的档位（点数、可选属性、上限、是否可拆）。
    "ability_increase",
    # Display names live on the entity so a card can render without a locale
    # lookup; origin feats have always carried them.
    "labels",
    "name",
    "summary",
    "description",
})

#: Fields still read by their old name.  P4 empties ``choice_specs``; the
#: ability increase is a choice and moves to ``choice`` separately.
LEGACY_FEAT_FIELDS = frozenset({
    "choice_specs",
})

#: Prerequisite fields that ``requires`` replaced.  They are not accepted in any
#: form: a bundle still using them would otherwise lose its prerequisites
#: silently, and "the feat became freely selectable" is not a loud failure.
RETIRED_PREREQUISITE_FIELDS = frozenset({
    "any_ability_minimum",
    "requires_feature",
    "requires_spellcasting",
})

#: Fields that were renamed.  Accepting the old name would leave two spellings of
#: one rule in the content, and the readers would have to check both.
RETIRED_RENAMED_FIELDS = {"ability_choice": "ability_increase"}

PASSIVE_EFFECT_KINDS = frozenset({"armor_class_bonus"})


@dataclass(frozen=True, slots=True)
class AbilityIncrease:
    """How a feat raises ability scores.

    One shape covers both cases the catalogue has.  Eight feats say "one point,
    from these abilities"; Ability Score Improvement says "two points, as +2 or
    as +1/+1".  Only ``points`` and ``pattern`` differ -- the ability set and the
    score cap are the same vocabulary, so they are one declaration rather than
    two, and the caller never has to ask which kind of feat this is.
    """

    points: int
    #: Empty means every ability (the content says ``"any"``).
    allowed: tuple[str, ...]
    maximum: int
    pattern: str

    def allows(self, ability: str) -> bool:
        """Whether this increase may be spent on ``ability``."""

        return not self.allowed or ability in self.allowed


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
    # 这条内容属于哪个内容包（``srd-5.2.1`` / ``phb-2024`` / …）。空值表示
    # 所属 bundle 没有声明来源体系（极简测试夹具就是这样）。
    content_pack: str = ""
    # 能力/选项的显示名。实体布局写在自己的文件里，catalog 布局写在行内；
    # 投影需要它，而 class feature 的 labels 不在同一张表里。
    labels: Mapping[str, str] = field(default_factory=dict)
    minimum_level: int = 1
    repeatable: bool = False
    parameters: Mapping[str, Any] = field(default_factory=dict)
    passive_effects: tuple[PassiveEffect, ...] = ()
    # 主动能力与被动效果是两类东西：一个在回合里花成本做动作，一个常驻
    # 改变数值。共用 ``FeatureCapability`` 让专长与子职走同一条投影/校验链。
    capabilities: tuple[FeatureCapability, ...] = ()
    #: 属性提升的档位。八条专长的形状是"一点、从这几项里选"，ASI 是"两点、
    #: 可拆成 +1/+1"；差别只在 points 与 pattern，其余是同一套词汇。
    ability_increase: AbilityIncrease | None = None
    #: Printed prerequisites.  Evaluated by ``feat_unavailable_reasons`` only.
    requires: tuple[FeatRequirement, ...] = ()
    choice: FeatureChoice | None = None
    legacy: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_mechanical_effect(self) -> bool:
        """Whether any declared payload actually changes the game.

        ``enabled_options`` is an executability whitelist, not a category
        filter: a style that grants nothing must not look selectable.  Asking
        the declaration instead of maintaining a second hand-written list is
        what keeps the two from drifting -- FD6a's list silently dropped
        Two-Weapon Fighting, which the engine already implemented.

        ``display`` parameters do not count; neither do ``unread`` ones, which
        the completeness gate forbids on a deterministic feat anyway.
        """

        if self.capabilities or self.passive_effects:
            return True
        if self.ability_increase is not None or self.choice is not None:
            return True
        return any(
            (spec := PARAMETERS.get(str(key))) is not None
            and spec.stage in {"combat", "derivation", "progression"}
            for key in self.parameters
        )


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

    retired = sorted(RETIRED_PREREQUISITE_FIELDS & set(raw))
    if retired:
        raise FeatDefinitionError(
            f"{feat_id} still declares {retired[0]!r}; prerequisites are now "
            "expressed in `requires` (see feat_requirements)"
        )
    renamed = sorted(RETIRED_RENAMED_FIELDS.keys() & set(raw))
    if renamed:
        raise FeatDefinitionError(
            f"{feat_id} still declares {renamed[0]!r}; it is now "
            f"{RETIRED_RENAMED_FIELDS[renamed[0]]!r}"
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
        content_pack=_parse_content_pack(feat_id, raw.get("content_pack")),
        labels=_parse_labels(raw.get("labels")),
        minimum_level=minimum_level,
        repeatable=repeatable,
        parameters=parameters,
        passive_effects=_parse_passive_effects(feat_id, raw.get("passive_effects")),
        capabilities=_parse_capabilities(feat_id, raw.get("capabilities")),
        requires=_parse_requirements(feat_id, raw.get("requires")),
        ability_increase=_parse_ability_increase(feat_id, raw.get("ability_increase")),
        choice=choice,
        legacy=legacy,
    )
    check_declaration_completeness(definition)
    return definition


def _parse_ability_increase(feat_id: str, raw: Any) -> AbilityIncrease | None:
    """Validate an ability-increase declaration, failing closed on anything odd."""

    if raw is None:
        return None
    where = f"{feat_id}.ability_increase"
    if not isinstance(raw, Mapping):
        raise FeatDefinitionError(f"{where} must be an object")
    unknown = sorted({str(key) for key in raw} - {"allowed", "points", "pattern", "maximum"})
    if unknown:
        raise FeatDefinitionError(f"{where} has an unknown field: {unknown[0]}")

    points = raw.get("points")
    if isinstance(points, bool) or not isinstance(points, int) or points not in (1, 2):
        raise FeatDefinitionError(f"{where}.points must be 1 or 2")
    pattern = str(raw.get("pattern") or "1")
    if pattern not in {"1", "2_or_1_1"}:
        raise FeatDefinitionError(f"{where}.pattern is not supported: {pattern!r}")
    # 两点才可能拆成 +1/+1；一对不上，声明就是自相矛盾的。
    if (pattern == "1") != (points == 1):
        raise FeatDefinitionError(
            f"{where}.pattern {pattern!r} does not match {points} point(s)"
        )
    maximum = raw.get("maximum", 20)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 30:
        raise FeatDefinitionError(f"{where}.maximum must be an integer from 1 to 30")

    raw_allowed = raw.get("allowed", "any")
    if raw_allowed == "any":
        allowed: tuple[str, ...] = ()
    elif isinstance(raw_allowed, list) and raw_allowed:
        if any(
            not isinstance(item, str) or item not in ABILITY_IDS
            for item in raw_allowed
        ):
            raise FeatDefinitionError(
                f"{where}.allowed must list ability ids"
            )
        if len(set(raw_allowed)) != len(raw_allowed):
            raise FeatDefinitionError(f"{where}.allowed must not repeat an ability")
        allowed = tuple(str(item) for item in raw_allowed)
    else:
        raise FeatDefinitionError(
            f"{where}.allowed must be 'any' or a non-empty array"
        )
    return AbilityIncrease(
        points=points, allowed=allowed, maximum=maximum, pattern=pattern,
    )


def _parse_requirements(feat_id: str, raw: Any) -> tuple[FeatRequirement, ...]:
    try:
        return parse_requirements(feat_id, raw)
    except FeatRequirementError as exc:
        raise FeatDefinitionError(str(exc)) from exc


def _parse_labels(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        # 同文件其余字段一律 fail closed：``labels`` 写坏成数组时静默当空，
        # 界面上只会少一个名字，没人会去查声明。
        raise FeatDefinitionError("feat.labels must be an object")
    return {str(key): str(value) for key, value in raw.items()}


def _parse_capabilities(feat_id: str, raw: Any) -> tuple[FeatureCapability, ...]:
    """Validate the active capabilities a feat grants.

    The capability's ``feature_id`` is filled from the feat's own id rather than
    from the payload: a declaration that could name another grantor would make
    "which feature owns this capability" depend on a field nobody validates.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise FeatDefinitionError(f"{feat_id}.capabilities must be an array")
    capabilities: list[FeatureCapability] = []
    for entry in raw:
        if isinstance(entry, Mapping) and "feature_id" in entry:
            # 授予者由"这条能力写在哪条专长里"决定。允许载荷自己声明，就等于
            # 让一个没人校验的字段决定归属——写错时 uses_per_long_rest 会记到
            # 一条不存在的特性上。
            raise FeatDefinitionError(
                f"{feat_id}.capabilities must not declare feature_id; "
                "the grantor is the feat that declares the capability"
            )
        try:
            capabilities.append(parse_capability(feat_id, entry))
        except ClassFeatureError as exc:
            raise FeatDefinitionError(str(exc)) from exc
    return tuple(capabilities)


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
            # 目录里的 13 条没有各自的 content_pack，继承目录实体的（bundle 已按
            # manifest 的 primary 填好）。不继承的话它们会落进"来源未知"那一组。
            row.setdefault("content_pack", str(catalog.get("content_pack") or ""))
            definition = parse_feat(row)
            if (
                bundle.manifest.content_packs
                and definition.content_pack
                and definition.content_pack not in bundle.manifest.content_packs
            ):
                raise FeatDefinitionError(
                    f"feat {definition.id} declares an undeclared content_pack: "
                    f"{definition.content_pack!r}"
                )
            if definition.id in definitions:
                raise FeatDefinitionError(
                    f"feat {definition.id} is declared twice: once as an entity "
                    "and once inside the advancement catalog"
                )
            definitions[definition.id] = definition
    return definitions


def _parse_content_pack(feat_id: str, value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise FeatDefinitionError(f"{feat_id}.content_pack must be a string")
    return value


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FeatDefinitionError(f"{field_name} must be a non-empty string")
    return value
