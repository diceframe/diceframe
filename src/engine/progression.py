"""Single owner of runtime round-counter writes (Track R5-a).

Callers own locks and transaction boundaries. Each writer preserves its
original expression, including coercion (or its absence) and evaluation order.
Readers may continue to use ``instance.round_number`` directly; settlement eras
remain narrative rounds (ADR-0003). No progression state or mode is persisted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

NARRATIVE_ROUND = "narrative_round"


def current_round(instance: Any) -> int:
    return int(getattr(instance, "round_number", 0) or 0)


def current_era(instance: Any) -> int:
    """Settlement era for economy / effects in narrative-round progression."""
    return current_round(instance)


def open_next_round(instance: Any) -> int:
    """W1: open the next narrative round without normalizing the counter."""
    instance.round_number += 1
    return instance.round_number


def rewind_after_rollback(instance: Any, rolled_back_round: int) -> int:
    """W2: retry the discarded round, with a floor of one."""
    instance.round_number = max(1, rolled_back_round)
    return instance.round_number


def rewind_for_replay(instance: Any, round_number: int) -> int:
    """W3: restore the historical counter on a staged swipe instance."""
    instance.round_number = round_number
    return instance.round_number


def advance_for_public_timeline(instance: Any, log: Iterable[Mapping[str, Any]]) -> int:
    """W4: move past both the live counter and the public log.

    Evaluate the live counter before reading log entries, as at the original
    writer. A conversion failure must occur before any counter assignment.
    """
    next_round = max(
        int(getattr(instance, "round_number", 0) or 0) + 1,
        max((int(item.get("round", 0) or 0) for item in log), default=0) + 1,
    )
    instance.round_number = next_round
    return next_round


def restore_from_snapshot(instance: Any, round_number: int) -> int:
    """W5: restore a failed authoritative transaction's saved counter."""
    instance.round_number = int(round_number)
    return instance.round_number


def reset(instance: Any) -> int:
    """W6: reset the run before any round has started."""
    instance.round_number = 0
    return 0


__all__ = [
    "NARRATIVE_ROUND", "current_round", "current_era", "open_next_round",
    "rewind_after_rollback", "rewind_for_replay", "advance_for_public_timeline",
    "restore_from_snapshot", "reset",
]
