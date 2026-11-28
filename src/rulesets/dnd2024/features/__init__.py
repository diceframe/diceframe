"""D&D 2024 class feature runtime (v1).

Feature identity and availability, class resource lifecycle projection, and
combat capability projection.  The package is intentionally thin: it answers
what a character owns and what it may declare, and nothing else.
"""

from .combat import CombatContext
from .equipment import Dnd2024EquipmentCatalog, weapon_matches_kind
from .feat_models import (
    FeatDefinition,
    FeatDefinitionError,
    load_feat_definitions,
    parse_feat,
)
from .models import (
    CapabilityCost,
    CapabilityCostView,
    CapabilityAction,
    ClassFeatureDefinition,
    ClassFeatureError,
    CombatCapabilityView,
    Dnd2024ClassFeatureCatalog,
    EquipmentRequirement,
    FeatureCapability,
    FeatureChoice,
    FeatureView,
    ResourceCost,
    SpellSlotCost,
    WEAPON_KINDS,
)
from .resolver import Dnd2024ClassFeatureResolver
from .resources import (
    ClassResourceDeclaration,
    Dnd2024ClassResourceCatalog,
    ResourceDefinition,
)

__all__ = [
    "CapabilityCost",
    "CapabilityCostView",
    "CapabilityAction",
    "ClassFeatureDefinition",
    "ClassFeatureError",
    "ClassResourceDeclaration",
    "CombatCapabilityView",
    "CombatContext",
    "Dnd2024ClassFeatureCatalog",
    "Dnd2024ClassFeatureResolver",
    "Dnd2024ClassResourceCatalog",
    "Dnd2024EquipmentCatalog",
    "EquipmentRequirement",
    "FeatDefinition",
    "FeatDefinitionError",
    "FeatureCapability",
    "FeatureChoice",
    "FeatureView",
    "ResourceCost",
    "ResourceDefinition",
    "SpellSlotCost",
    "WEAPON_KINDS",
    "load_feat_definitions",
    "parse_feat",
    "weapon_matches_kind",
]
