"""Typed, validated D&D 2024 class feature declarations.

This module owns the *shape* of the D&D-owned feature catalog and the
presentation-neutral views the runtime exposes.  It deliberately contains no
combat resolution and no state mutation: identity, availability, and scalar
parameters only.

The authoritative source of "what a character owns" stays the ruleset bundle:
``progression_catalog`` says which feature ids a class table grants at each
level, and ``class_feature_catalog`` parameterizes those ids (minimum level,
resource link, capability declaration, display labels).  Nothing here may be
read from a localized display name, and nothing here is persisted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.rulesets.bundle import LoadedRulesetBundle


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
# v1 只表达本 PR 真正实现的效果原语；未知 kind 必须 fail closed，而不是被静默忽略。
ACTION_KINDS = frozenset({
    "unarmed_strike", "dash", "dodge", "disengage", "heal", "rage", "rage_sustain",
    "action_surge",
})
ACTION_COST_KINDS = ("bonus_action", "action")
# 装备前提里可声明的武器类别；同样 fail closed。``simple_melee`` 是 SRD 的
# Simple Melee 武器栏，``martial_melee_light`` 是带 Light 的 Martial Melee 武器。
WEAPON_KINDS = frozenset({"simple_melee", "martial_melee_light"})
EQUIPMENT_REQUIREMENT_KEYS = frozenset({"unarmored", "no_shield", "weapon_kinds"})


class ClassFeatureError(ValueError):
    """Raised when a class feature catalog cannot be used authoritatively."""


def _require_id(value: Any, field_name: str) -> str:
    text = str(value or "")
    if not _ID_RE.fullmatch(text):
        raise ClassFeatureError(f"{field_name} is not a canonical id: {value!r}")
    return text


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ClassFeatureError(f"{field_name} must not be empty")
    return text


def _require_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ClassFeatureError(f"{field_name} must be a positive integer")
    return value


def _require_scalar(value: Any, field_name: str) -> Any:
    if isinstance(value, list):
        for item in value:
            _require_scalar(item, field_name)
        return list(value)
    if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
        return value
    if isinstance(value, str):
        return value
    raise ClassFeatureError(f"{field_name} must contain scalars only")


@dataclass(frozen=True, slots=True)
class EquipmentRequirement:
    """The canonical equipment precondition a class feature declares.

    ``unarmored`` / ``no_shield`` are evaluated against the character's item
    types; a non-empty ``weapon_kinds`` additionally requires *every* wielded
    weapon to match one of the declared kinds.  The same kinds are the weapons
    the feature's own weapon benefits cover (Martial Arts: the Monk Weapon), so
    a feature never repeats the weapon definition in a second table.
    """

    unarmored: bool = False
    no_shield: bool = False
    weapon_kinds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceCost:
    """One class-resource price of a capability."""

    resource_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class CapabilityCost:
    """The action-economy and resource price of one capability."""

    action: str = ""
    resources: tuple[ResourceCost, ...] = ()


@dataclass(frozen=True, slots=True)
class CapabilityAction:
    """One underlying canonical action a capability performs."""

    kind: str
    count: int = 1


@dataclass(frozen=True, slots=True)
class FeatureCapability:
    """A combat capability a class feature grants."""

    id: str
    feature_id: str
    cost: CapabilityCost
    requires_hostile_target: bool
    actions: tuple[CapabilityAction, ...]
    requires_condition: str | None = None
    forbidden_condition: str | None = None
    once_per_turn: bool = False


@dataclass(frozen=True, slots=True)
class ClassFeatureDefinition:
    """One declared class feature."""

    id: str
    class_id: str
    minimum_level: int
    source_ref: str
    summary: str
    parameters: Mapping[str, Any]
    resource_id: str
    option_of: str
    capabilities: tuple[FeatureCapability, ...]
    equipment_requirement: EquipmentRequirement | None = None


def _parse_equipment_requirement(
    feature_id: str, raw: Any,
) -> EquipmentRequirement | None:
    """Validate a declared equipment precondition, failing closed on unknowns."""

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ClassFeatureError(f"{feature_id}.equipment_requirement must be an object")
    unknown = {str(key) for key in raw}.difference(EQUIPMENT_REQUIREMENT_KEYS)
    if unknown:
        raise ClassFeatureError(
            f"{feature_id}.equipment_requirement has an unknown field: {sorted(unknown)[0]}"
        )
    flags: dict[str, bool] = {}
    for key in ("unarmored", "no_shield"):
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise ClassFeatureError(f"{feature_id}.equipment_requirement.{key} must be a boolean")
        flags[key] = value
    raw_kinds = raw.get("weapon_kinds")
    if raw_kinds is None:
        raw_kinds = []
    if not isinstance(raw_kinds, list):
        raise ClassFeatureError(
            f"{feature_id}.equipment_requirement.weapon_kinds must be an array"
        )
    kinds: list[str] = []
    for raw_kind in raw_kinds:
        kind = str(raw_kind or "")
        if kind not in WEAPON_KINDS:
            raise ClassFeatureError(
                f"{feature_id}.equipment_requirement has an unknown weapon kind: {raw_kind!r}"
            )
        if kind not in kinds:
            kinds.append(kind)
    return EquipmentRequirement(
        unarmored=flags["unarmored"],
        no_shield=flags["no_shield"],
        weapon_kinds=tuple(kinds),
    )


@dataclass(frozen=True, slots=True)
class Dnd2024ClassFeatureCatalog:
    """Read-only, validated access to the bundled class feature declarations."""

    source_ref: str
    labels: Mapping[str, str]
    features: Mapping[str, ClassFeatureDefinition]
    by_class: Mapping[str, tuple[str, ...]]

    @classmethod
    def empty(cls) -> Dnd2024ClassFeatureCatalog:
        """An empty catalog, for bundles that ship no feature declarations."""

        return cls(source_ref="", labels={}, features={}, by_class={})

    @classmethod
    def from_bundle(cls, bundle: LoadedRulesetBundle) -> Dnd2024ClassFeatureCatalog:
        raw = bundle.get("class_feature_catalog", "srd_class_features")
        if raw is None:
            raise ClassFeatureError("D&D 2024 class feature catalog is missing")
        raw_labels = raw.get("labels")
        if not isinstance(raw_labels, dict):
            raise ClassFeatureError("class feature labels must be an object")
        labels = {
            _require_id(key, "class feature label key"): _require_text(
                value, f"class feature label {key}",
            )
            for key, value in raw_labels.items()
        }
        raw_classes = raw.get("classes")
        if not isinstance(raw_classes, dict):
            raise ClassFeatureError("class feature classes must be an object")
        features: dict[str, ClassFeatureDefinition] = {}
        by_class: dict[str, tuple[str, ...]] = {}
        for raw_class_id, raw_class in raw_classes.items():
            class_id = _require_id(raw_class_id, "class feature class id")
            if not isinstance(raw_class, dict):
                raise ClassFeatureError(f"classes.{class_id} must be an object")
            source_ref = _require_text(raw_class.get("source_ref"), f"classes.{class_id}.source_ref")
            raw_features = raw_class.get("features")
            if not isinstance(raw_features, list) or not raw_features:
                raise ClassFeatureError(f"classes.{class_id}.features must be a non-empty array")
            ordered: list[str] = []
            for raw_feature in raw_features:
                definition = cls._parse_feature(class_id, source_ref, raw_feature)
                if definition.id in features:
                    raise ClassFeatureError(f"duplicate class feature id: {definition.id}")
                features[definition.id] = definition
                ordered.append(definition.id)
            by_class[class_id] = tuple(ordered)
        for definition in features.values():
            if definition.option_of and definition.option_of not in features:
                raise ClassFeatureError(
                    f"{definition.id}.option_of references an undeclared feature"
                )
            if definition.option_of:
                parent = features[definition.option_of]
                if parent.class_id != definition.class_id:
                    raise ClassFeatureError(
                        f"{definition.id} cannot be an option of another class's feature"
                    )
        return cls(
            source_ref=_require_text(raw.get("source_ref"), "class feature catalog source_ref"),
            labels=labels,
            features=features,
            by_class=by_class,
        )

    @classmethod
    def _parse_feature(
        cls, class_id: str, class_source_ref: str, raw: Any,
    ) -> ClassFeatureDefinition:
        if not isinstance(raw, dict):
            raise ClassFeatureError(f"classes.{class_id} contains an invalid feature")
        feature_id = _require_id(raw.get("id"), f"classes.{class_id} feature id")
        minimum_level = _require_positive_int(
            raw.get("minimum_level", 1), f"{feature_id}.minimum_level",
        )
        if minimum_level > 20:
            raise ClassFeatureError(f"{feature_id}.minimum_level must not exceed 20")
        raw_parameters = raw.get("parameters") or {}
        if not isinstance(raw_parameters, dict):
            raise ClassFeatureError(f"{feature_id}.parameters must be an object")
        parameters = {
            str(key): _require_scalar(value, f"{feature_id}.parameters.{key}")
            for key, value in raw_parameters.items()
        }
        resource_id = str(raw.get("resource_id") or "")
        if resource_id:
            resource_id = _require_id(resource_id, f"{feature_id}.resource_id")
        option_of = str(raw.get("option_of") or "")
        if option_of:
            option_of = _require_id(option_of, f"{feature_id}.option_of")
        raw_capabilities = raw.get("capabilities") or []
        if not isinstance(raw_capabilities, list):
            raise ClassFeatureError(f"{feature_id}.capabilities must be an array")
        capabilities: list[FeatureCapability] = []
        seen_capability_ids: set[str] = set()
        for raw_capability in raw_capabilities:
            capability = cls._parse_capability(feature_id, raw_capability)
            if capability.id in seen_capability_ids:
                raise ClassFeatureError(f"{feature_id} declares a duplicate capability")
            seen_capability_ids.add(capability.id)
            capabilities.append(capability)
        return ClassFeatureDefinition(
            id=feature_id,
            class_id=class_id,
            minimum_level=minimum_level,
            source_ref=str(raw.get("source_ref") or class_source_ref),
            summary=str(raw.get("summary") or ""),
            parameters=parameters,
            resource_id=resource_id,
            option_of=option_of,
            capabilities=tuple(capabilities),
            equipment_requirement=_parse_equipment_requirement(
                feature_id, raw.get("equipment_requirement"),
            ),
        )

    @classmethod
    def _parse_capability(cls, feature_id: str, raw: Any) -> FeatureCapability:
        if not isinstance(raw, dict):
            raise ClassFeatureError(f"{feature_id} contains an invalid capability")
        capability_id = _require_id(raw.get("id"), f"{feature_id} capability id")
        raw_cost = raw.get("cost") or {}
        if not isinstance(raw_cost, dict):
            raise ClassFeatureError(f"{capability_id}.cost must be an object")
        action = ""
        for action_kind in ACTION_COST_KINDS:
            if action_kind not in raw_cost:
                continue
            if action:
                raise ClassFeatureError(f"{capability_id} declares more than one action cost")
            if _require_positive_int(raw_cost[action_kind], f"{capability_id}.{action_kind}") != 1:
                raise ClassFeatureError(f"{capability_id}.{action_kind} must be 1")
            action = action_kind
        raw_resources = raw_cost.get("resources") or []
        if not isinstance(raw_resources, list):
            raise ClassFeatureError(f"{capability_id}.cost.resources must be an array")
        resources: list[ResourceCost] = []
        for raw_resource in raw_resources:
            if not isinstance(raw_resource, dict):
                raise ClassFeatureError(f"{capability_id} contains an invalid resource cost")
            resources.append(ResourceCost(
                resource_id=_require_id(
                    raw_resource.get("id"), f"{capability_id} resource cost id",
                ),
                amount=_require_positive_int(
                    raw_resource.get("amount"), f"{capability_id} resource cost amount",
                ),
            ))
        raw_actions = raw.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise ClassFeatureError(f"{capability_id}.actions must be a non-empty array")
        actions: list[CapabilityAction] = []
        for raw_action in raw_actions:
            if not isinstance(raw_action, dict):
                raise ClassFeatureError(f"{capability_id} contains an invalid action")
            kind = str(raw_action.get("kind") or "")
            if kind not in ACTION_KINDS:
                raise ClassFeatureError(f"{capability_id} declares an unknown action kind")
            actions.append(CapabilityAction(
                kind=kind,
                count=_require_positive_int(
                    raw_action.get("count", 1), f"{capability_id} action count",
                ),
            ))
        requires_condition = str(raw.get("requires_condition") or "")
        forbidden_condition = str(raw.get("forbidden_condition") or "")
        if requires_condition and forbidden_condition:
            raise ClassFeatureError(
                f"{capability_id} cannot require and forbid a condition at the same time"
            )
        for condition_field, condition_value in (
            ("requires_condition", requires_condition),
            ("forbidden_condition", forbidden_condition),
        ):
            if condition_value:
                _require_id(condition_value, f"{capability_id}.{condition_field}")
        once_per_turn = raw.get("once_per_turn", False)
        if not isinstance(once_per_turn, bool):
            raise ClassFeatureError(f"{capability_id}.once_per_turn must be a boolean")
        return FeatureCapability(
            id=capability_id,
            feature_id=feature_id,
            cost=CapabilityCost(action=action, resources=tuple(resources)),
            requires_hostile_target=bool(raw.get("requires_hostile_target")),
            actions=tuple(actions),
            requires_condition=requires_condition or None,
            forbidden_condition=forbidden_condition or None,
            once_per_turn=once_per_turn,
        )

    def label(self, key: str, default: str = "") -> str:
        return self.labels.get(str(key), default or str(key))

    def class_features(self, class_id: str) -> tuple[ClassFeatureDefinition, ...]:
        return tuple(self.features[feature_id] for feature_id in self.by_class.get(class_id, ()))


@dataclass(frozen=True, slots=True)
class FeatureView:
    """Presentation-safe view of one feature the character actually owns.

    ``active`` says whether the feature's declared equipment precondition
    currently holds.  An owned-but-inactive feature keeps its identity (the
    character really does have it) but exposes no effect values: the numbers
    that are not in force right now must not be rendered as if they were.
    """

    id: str
    name: str
    summary: str
    source_ref: str
    minimum_level: int
    values: Mapping[str, Any] = field(default_factory=dict)
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "source_ref": self.source_ref,
            "minimum_level": self.minimum_level,
            "values": dict(self.values),
            "active": self.active,
        }


@dataclass(frozen=True, slots=True)
class CapabilityCostView:
    """One user-visible price of a capability."""

    kind: str
    name: str
    amount: int
    current: int
    maximum: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "amount": self.amount,
            "current": self.current,
            "maximum": self.maximum,
        }


@dataclass(frozen=True, slots=True)
class CombatCapabilityView:
    """Presentation-safe view of one feature-provided combat capability."""

    id: str
    feature_id: str
    name: str
    available: bool
    blocked_reason: str
    requires_hostile_target: bool
    costs: tuple[CapabilityCostView, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.id,
            "feature_id": self.feature_id,
            "label": self.name,
            "available": self.available,
            "blocked_reason": self.blocked_reason,
            "requires_target": self.requires_hostile_target,
            "costs": [cost.to_dict() for cost in self.costs],
        }
