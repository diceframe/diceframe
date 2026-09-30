"""Read-only projection contracts for canonical content.

Content projection is an application/read boundary.  A projector may combine
canonical content with already-resolved view context, but it must return a new
mapping and must not mutate the authority it received.  The broad contract is
kept deliberately small here; the domain-specific ``ContentProjectionService``
belongs to the later projection cutover (Track C PR D).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ContentProjection(Protocol):
    """A read-only projector from canonical content to a public view.

    ``context`` is intentionally opaque at this stage.  The Lorebook
    management service uses it for its already-existing binding facts; later
    projection work can add viewer/locale contexts without changing the
    canonical store contract.
    """

    def project(
        self,
        content: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...


__all__ = ["ContentProjection"]
