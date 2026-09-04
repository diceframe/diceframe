"""Minimal economy predicate used by the narrative effect barrier.

The former narration/evidence/price-repair pipeline is intentionally gone.
"""

from __future__ import annotations

from typing import Any


def has_economy_proposal(data: dict[str, Any]) -> bool:
    state_update = data.get("state_update")
    if not isinstance(state_update, dict):
        return False
    return bool(state_update.get("economy_proposals"))
