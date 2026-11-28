"""dnd5e -> dnd2024 conversion boundary (M2, workplan §6.16).

Pure conversion, display first: Actors and Items become presentation DTOs, a
dnd2024 draft candidate and explicit catalog references, with a structured
report for everything that cannot be mapped. No rules execution, no character
creation (m2-02), no dependency of ``src/compat/foundry`` on dnd5e.
"""

from .actor_converter import convert_actor
from .creation import (
    DND2024_RUNTIME_ID,
    DRAFT_SELECTION_FIELDS,
    Dnd5eCreationError,
    compose_draft,
    has_progression_history,
    is_dnd2024_binding,
    reconcile_runtime,
)
from .item_converter import convert_item
from .mapping import (
    Dnd2024Catalog,
    Dnd5eConversionError,
    Dnd5eConversionReport,
    default_catalog,
)

__all__ = [
    "DND2024_RUNTIME_ID",
    "DRAFT_SELECTION_FIELDS",
    "Dnd2024Catalog",
    "Dnd5eConversionError",
    "Dnd5eConversionReport",
    "Dnd5eCreationError",
    "compose_draft",
    "convert_actor",
    "convert_item",
    "default_catalog",
    "has_progression_history",
    "is_dnd2024_binding",
    "reconcile_runtime",
]