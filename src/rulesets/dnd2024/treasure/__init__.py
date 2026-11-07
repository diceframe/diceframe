"""D&D 2024 random treasure table primitives (D4).

This package owns treasure table definitions, load-time validation, and pure
selection. It does not roll dice, write inventory, or enter combat triggers.
"""

from src.rulesets.dnd2024.treasure.tables import (
    Dnd2024TreasureCatalog,
    TreasureTableError,
    canonical_table_ref,
    resolve_table,
    validate_treasure_tables,
)

__all__ = [
    "Dnd2024TreasureCatalog",
    "TreasureTableError",
    "canonical_table_ref",
    "resolve_table",
    "validate_treasure_tables",
]
