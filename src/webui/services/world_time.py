"""The one place game time moves, for every authority that spends it.

A rest and a timed ritual both consume logical minutes, and both have to do
exactly the same four things in the same commit: advance the clock, let the
Adventure gate re-evaluate, queue the World Memory the receipts imply, and
stamp the source round.  Keeping that in one function is what stops the second
caller from quietly advancing the clock without the downstream consumers.
"""

from __future__ import annotations

from typing import Any, Callable

from src.engine.modules import progression_state
from src.engine.world.memory_projection import queue_world_memory
from src.engine.world_events import MAX_ADVANCE_MINUTES, advance_world_time


def advance_game_time(
    instance: Any, minutes: int, *,
    advance_adventure_world: Callable[[Any], Any] | None = None,
) -> int:
    """Move the world clock in bounded steps and let the rest of the world follow.

    Returns the number of minutes actually advanced.  Raises if the world
    refuses a step; a caller that has already changed character state must
    restore its before-image, which is why every authority call site keeps one.
    """

    remaining = max(0, int(minutes))
    advanced = 0
    while remaining:
        step = min(remaining, MAX_ADVANCE_MINUTES)
        outcome = advance_world_time(
            instance, step,
            source_round=progression_state.round_value(instance),
        )
        if advance_adventure_world is not None:
            advance_adventure_world(instance)
        queue_world_memory(
            instance, outcome.get("events") or [],
            round_number=progression_state.round_value(instance),
        )
        remaining -= step
        advanced += step
    return advanced
