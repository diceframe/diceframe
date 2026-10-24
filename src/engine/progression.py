"""Single owner of runtime round-counter writes (Track R5-a).

Callers own locks and transaction boundaries. Each writer preserves its
original expression, including coercion (or its absence) and evaluation order.
Readers use ``current_round`` here or ``progression_state.round_value``;
settlement eras remain narrative rounds (ADR-0003). Module-backed instances
read and write the progression slot through ``progression_state`` and reject
unknown schemas/modes before writes. Standalone round-bearing objects retain
the R5-a contract; absence of a module container does not invent one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.engine.modules import progression_state

NARRATIVE_ROUND = "narrative_round"


def accepts_narrative(instance: Any) -> bool:
    from src.engine.modules import profile_progression
    return profile_progression.accepts_narrative(instance)


def require_writable(instance: Any) -> None:
    """Preflight progression before any enclosing transaction mutates state.

    Legacy lightweight adapters expose only a round counter. An explicitly
    present module container, even malformed, opts into the module contract.
    """
    if hasattr(instance, "modules"):
        progression_state.require_writable(instance)


def _module_backed(instance: Any) -> bool:
    return hasattr(instance, "modules")


def _read(instance: Any) -> Any:
    if _module_backed(instance):
        return progression_state.round_value(instance)
    return instance.round_number


def _write(instance: Any, value: Any) -> None:
    if _module_backed(instance):
        progression_state.set_round_value(instance, value)
    else:
        instance.round_number = value


def current_round(instance: Any) -> int:
    if _module_backed(instance):
        return progression_state.round_value(instance)
    return int(getattr(instance, "round_number", 0) or 0)


def current_era(instance: Any) -> int:
    """Settlement era for economy / effects.

    An era is one narrative round (ADR-0003); in scene progression (GAL P7)
    the same counter keeps counting advances, so the era stays the advance
    count — this remains the single seam for progression-model branching.
    """
    return current_round(instance)


def ensure_advancement_allowed(instance: Any) -> None:
    """Scene-mode guard: an ended adventure never opens another advance (GAL P7).

    narrative_round tables keep their existing behaviour (a frozen adventure
    progress does not stop a tabletop round); scene progression treats the
    reached ending (``ending_node_id``, track P4) as the run's terminal state.
    """

    if progression_state.mode(instance) != progression_state.SCENE:
        return
    from src.adventures.progress import ending_node_id
    from src.engine.modules import adventure_runtime_state

    if ending_node_id(adventure_runtime_state.progress(instance)):
        raise ValueError("adventure has ended: scene progression does not advance further")


def open_next_round(instance: Any) -> int:
    """W1: open the next narrative round without normalizing the counter."""
    require_writable(instance)
    if not accepts_narrative(instance):
        raise ValueError('phase/calendar games do not advance narrative rounds')
    _write(instance, _read(instance) + 1)
    return _read(instance)


def rewind_after_rollback(instance: Any, rolled_back_round: int) -> int:
    """W2: retry the discarded round, with a floor of one."""
    require_writable(instance)
    _write(instance, max(1, rolled_back_round))
    return _read(instance)


def rewind_for_replay(instance: Any, round_number: int) -> int:
    """W3: restore the historical counter on a staged swipe instance."""
    require_writable(instance)
    _write(instance, round_number)
    return _read(instance)


def advance_for_public_timeline(instance: Any, log: Iterable[Mapping[str, Any]]) -> int:
    """W4: move past both the live counter and the public log.

    Evaluate the live counter before reading log entries, as at the original
    writer. A conversion failure must occur before any counter assignment.
    """
    require_writable(instance)
    next_round = max(
        current_round(instance) + 1,
        max((int(item.get("round", 0) or 0) for item in log), default=0) + 1,
    )
    _write(instance, next_round)
    return next_round


def restore_from_snapshot(instance: Any, round_number: int) -> int:
    """W5: restore a failed authoritative transaction's saved counter."""
    require_writable(instance)
    _write(instance, int(round_number))
    return _read(instance)


def reset(instance: Any) -> int:
    """W6: reset the run before any round has started."""
    require_writable(instance)
    _write(instance, 0)
    return 0


__all__ = [
    "NARRATIVE_ROUND", "current_round", "current_era", "open_next_round",
    "rewind_after_rollback", "rewind_for_replay", "advance_for_public_timeline",
    "restore_from_snapshot", "reset", "require_writable",
    "ensure_advancement_allowed",
]
