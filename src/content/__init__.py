"""Content V2 contracts used by core and plugin adapters."""

from .contracts import ContentResource, ResourceRef, canonical_id
from .locale import LocaleOverlayError, resolve_locale, apply_locale_overlay
from .snapshot import mechanics_snapshot

__all__ = [
    "ContentResource", "ResourceRef", "canonical_id", "LocaleOverlayError",
    "resolve_locale", "apply_locale_overlay", "mechanics_snapshot",
]
