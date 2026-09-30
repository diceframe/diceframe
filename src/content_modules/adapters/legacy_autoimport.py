"""Legacy content-pack materialization boundary.

Old content packs are intentionally kept compatible, but their implicit
``enable -> write user data`` behavior lives behind one named adapter.  New
catalog packages never call this adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LegacyContentPackAdapter:
    """Bridge the old auto-import lifecycle without leaking it into catalog paths."""

    sync_lorebooks: Callable[[], Any]
    import_content: Callable[[str], Any]

    def sync(self) -> Any:
        """Synchronize legacy world-template lore before a lifecycle action."""

        return self.sync_lorebooks()

    def materialize(self, plugin_id: str) -> Any:
        """Perform the legacy, explicitly compatibility-scoped materialization."""

        self.sync()
        return self.import_content(str(plugin_id or ""))


__all__ = ["LegacyContentPackAdapter"]
