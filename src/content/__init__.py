"""Content V2 contracts used by core and plugin adapters."""

from .contracts import ContentResource, ResourceRef, canonical_id
from .locale import LocaleOverlayError, resolve_locale, apply_locale_overlay
from .snapshot import mechanics_snapshot
from .worlds import WorldDraft, load_lorebook_resource, load_world_definition

__all__ = [
    "ContentResource", "ResourceRef", "canonical_id", "LocaleOverlayError",
    "resolve_locale", "apply_locale_overlay", "mechanics_snapshot",
    "WorldDraft", "load_world_definition", "load_lorebook_resource",
]
