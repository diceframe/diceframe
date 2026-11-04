"""Read-only projection of the bundled D&D 2024 resource declarations.

The resource *lifecycle* -- creation at the granting level, resize on
advancement, and short/long rest recovery -- remains owned by
``src.rulesets.dnd2024.resting``, which also validates the bundle's
``rest_catalog.class_resources`` table.  This module reads the same bundle
declarations, so the feature boundary can answer "what is Focus right now"
without inventing a second resource table or a second rest system.

Class resources are keyed by class id.  Feat resources (origin-feat pools
such as Lucky's Luck Points) live in a sibling ``feat_resources`` table keyed
by feat id: the class table's bucket keys must match the progression classes
exactly, so a feat pool can never be smuggled into a class bucket.  Both
tables share one entry shape, and the canonical state still lands in the same
``resources.class.<id>`` map, so the combat/reducer read path is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.rulesets.bundle import LoadedRulesetBundle

from .models import ClassFeatureError


MAXIMUM_FIELDS = (
    "maximum_track", "maximum_fixed", "maximum_ability_modifier",
    "maximum_proficiency_bonus",
)


def _require_id(value: Any, field_name: str) -> str:
    text = str(value or "")
    if not text or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for character in text):
        raise ClassFeatureError(f"{field_name} is not a canonical id: {value!r}")
    return text


def _parse_declaration(
    raw_spec: Any, *, owner_id: str, bucket: str, resource_ids: dict[str, Any],
) -> ClassResourceDeclaration:
    """Validate one resource spec shared by class and feat tables."""

    if not isinstance(raw_spec, dict):
        raise ClassFeatureError(f"{bucket}.{owner_id} has an invalid resource")
    resource_id = _require_id(raw_spec.get("id"), f"{bucket}.{owner_id} resource id")
    if resource_id in resource_ids:
        raise ClassFeatureError(f"{bucket}.{owner_id} duplicates {resource_id}")
    maximum_fields = [key for key in MAXIMUM_FIELDS if key in raw_spec]
    if len(maximum_fields) != 1:
        raise ClassFeatureError(f"{bucket}.{owner_id}.{resource_id} needs one maximum")
    if "maximum_proficiency_bonus" in raw_spec and raw_spec["maximum_proficiency_bonus"] is not True:
        raise ClassFeatureError(
            f"{bucket}.{owner_id}.{resource_id} maximum_proficiency_bonus must be true"
        )
    source_ref = str(raw_spec.get("source_ref") or "")
    if not source_ref.startswith("srd-5.2.1:"):
        raise ClassFeatureError(
            f"{bucket}.{owner_id}.{resource_id} source is invalid"
        )
    minimum_level = raw_spec.get("minimum_level", 1)
    if (
        isinstance(minimum_level, bool)
        or not isinstance(minimum_level, int)
        or not 1 <= minimum_level <= 20
    ):
        raise ClassFeatureError(
            f"{bucket}.{owner_id}.{resource_id} minimum level is invalid"
        )
    short_all_from_level = raw_spec.get("short_all_from_level", 99)
    if isinstance(short_all_from_level, bool) or not isinstance(
        short_all_from_level, int,
    ):
        raise ClassFeatureError(
            f"{bucket}.{owner_id}.{resource_id} recovery level is invalid"
        )
    return ClassResourceDeclaration(
        id=resource_id,
        class_id=owner_id,
        minimum_level=minimum_level,
        source_ref=source_ref,
        short_rest=raw_spec.get("short", "none"),
        long_rest=raw_spec.get("long", "none"),
        short_all_from_level=short_all_from_level,
    )


@dataclass(frozen=True, slots=True)
class ClassResourceDeclaration:
    """One declared resource of one class or one feat."""

    id: str
    class_id: str
    minimum_level: int
    source_ref: str
    short_rest: Any
    long_rest: Any
    short_all_from_level: int

    def recovery(self) -> dict[str, Any]:
        return {
            "short": self.short_rest,
            "long": self.long_rest,
            "short_all_from_level": self.short_all_from_level,
        }


@dataclass(frozen=True, slots=True)
class Dnd2024ClassResourceCatalog:
    """Validated, read-only view of ``rest_catalog.class_resources``."""

    source_ref: str
    by_class: Mapping[str, Mapping[str, ClassResourceDeclaration]]
    by_feat: Mapping[str, Mapping[str, ClassResourceDeclaration]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> Dnd2024ClassResourceCatalog:
        """An empty declaration catalog, for bundles that ship no rest catalog."""

        return cls(source_ref="", by_class={}, by_feat={})

    @classmethod
    def from_bundle(cls, bundle: LoadedRulesetBundle) -> Dnd2024ClassResourceCatalog:
        raw = bundle.get("rest_catalog", "srd_recovery")
        if raw is None:
            raise ClassFeatureError("D&D 2024 rest catalog is missing")
        raw_classes = raw.get("class_resources")
        if not isinstance(raw_classes, dict):
            raise ClassFeatureError("rest class_resources must be an object")
        by_class: dict[str, dict[str, ClassResourceDeclaration]] = {}
        for raw_class_id, raw_specs in raw_classes.items():
            class_id = _require_id(raw_class_id, "rest class_resources class id")
            if not isinstance(raw_specs, list):
                raise ClassFeatureError(f"class_resources.{class_id} must be an array")
            declarations: dict[str, ClassResourceDeclaration] = {}
            for raw_spec in raw_specs:
                declaration = _parse_declaration(
                    raw_spec, owner_id=class_id, bucket="class_resources",
                    resource_ids=declarations,
                )
                declarations[declaration.id] = declaration
            by_class[class_id] = declarations
        by_feat: dict[str, dict[str, ClassResourceDeclaration]] = {}
        raw_feats = raw.get("feat_resources")
        if raw_feats is not None:
            if not isinstance(raw_feats, dict):
                raise ClassFeatureError("rest feat_resources must be an object")
            for raw_feat_id, raw_specs in raw_feats.items():
                feat_id = _require_id(raw_feat_id, "rest feat_resources feat id")
                if bundle.get("feat", feat_id) is None:
                    raise ClassFeatureError(
                        f"feat_resources.{feat_id} references an unknown feat"
                    )
                if not isinstance(raw_specs, list):
                    raise ClassFeatureError(f"feat_resources.{feat_id} must be an array")
                declarations = {}
                for raw_spec in raw_specs:
                    declaration = _parse_declaration(
                        raw_spec, owner_id=feat_id, bucket="feat_resources",
                        resource_ids=declarations,
                    )
                    declarations[declaration.id] = declaration
                by_feat[feat_id] = declarations
        return cls(
            source_ref=str(raw.get("source_ref") or ""),
            by_class=by_class,
            by_feat=by_feat,
        )

    def declaration(self, class_id: str, resource_id: str) -> ClassResourceDeclaration | None:
        return self.by_class.get(str(class_id), {}).get(str(resource_id))

    def feat_declarations(self, feat_id: str) -> Mapping[str, ClassResourceDeclaration]:
        return self.by_feat.get(str(feat_id), {})


def resource_state(
    character: Mapping[str, Any] | None, resource_id: str,
) -> Mapping[str, Any]:
    """Return the canonical ``resources.class.<id>`` entry, or an empty mapping."""

    resources = character.get("resources") if isinstance(character, Mapping) else None
    resources = resources if isinstance(resources, Mapping) else {}
    class_state = resources.get("class")
    class_state = class_state if isinstance(class_state, Mapping) else {}
    state = class_state.get(str(resource_id))
    return state if isinstance(state, Mapping) else {}


@dataclass(frozen=True, slots=True)
class ResourceDefinition:
    """Presentation-safe view of one class resource the character owns."""

    id: str
    name: str
    current: int
    maximum: int
    minimum_level: int
    source_ref: str
    recovery: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "current": self.current,
            "maximum": self.maximum,
            "minimum_level": self.minimum_level,
            "source_ref": self.source_ref,
            "recovery": dict(self.recovery),
        }
