"""Versioned adventure content packages.

Adventure packages are data. Ruleset runtimes remain the only mechanics
authority and may opt into one or more adventure graph formats.
"""

from .bundle import (
    ADVENTURE_GRAPH_FORMAT,
    AdventureBundleError,
    AdventureBundleLoader,
    LoadedAdventureBundle,
)
from .catalog import is_builtin_adventure_directory, sync_adventure_catalog
from .registry import AdventureSource, AdventureSourceConflict, AdventureSourceRegistry
from .resolver import (
    BASE_BINDING_FIELDS,
    SOURCE_BINDING_FIELDS,
    AdventureResolution,
    AdventureResolver,
    binding_matches,
    binding_source,
    is_source_aware_binding,
)

__all__ = [
    "ADVENTURE_GRAPH_FORMAT",
    "BASE_BINDING_FIELDS",
    "SOURCE_BINDING_FIELDS",
    "AdventureBundleError",
    "AdventureBundleLoader",
    "AdventureResolution",
    "AdventureResolver",
    "AdventureSource",
    "AdventureSourceConflict",
    "AdventureSourceRegistry",
    "LoadedAdventureBundle",
    "binding_matches",
    "binding_source",
    "is_builtin_adventure_directory",
    "is_source_aware_binding",
    "sync_adventure_catalog",
]
