"""Isolated aggregate planning with optimistic, all-or-nothing publication."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.engine.modules import room_access


def _projection(instance: Any) -> dict:
    result = deepcopy(instance.to_dict())
    # The save codec bounds inline history. A transaction must fence all history.
    result["log"] = deepcopy(instance.log)
    return result


def fork_domain_state(instance: Any) -> Any:
    """Copy a live aggregate without load-time normalization or shared locks.

    Loading a save intentionally removes ghost players and bounds history; it
    cannot be used to clone a currently running table with newly joined seats.
    The existing aggregate publication contract preserves domain objects while
    leaving runtime coordination attached to a fresh aggregate.
    """
    staged = type(instance)(game_key=instance.game_key, run_id=instance.run_id)
    staged.replace_persisted_state_from(instance)
    return staged


def replace_persisted_state(target: Any, source: Any) -> None:
    """Publish persisted domain fields without replacing runtime coordination.

    The live aggregate keeps its locks, timers, save counters and other
    process-local state. Room access is committed independently so a staged
    copy cannot roll back a password change or a newly issued seat link.
    """

    if source.game_key != target.game_key or source.run_id != target.run_id:
        raise ValueError("staged game state belongs to a different run")
    runtime_only = {
        "last_saved_log_count",
        "pending_luck_after_recovery",
        # Process-local coordination for same-round unpriced purchases.
        "round_unpriced_purchase_intents",
    }
    live_room_access = room_access.capture_slot(target)
    for name, value in source.__dict__.items():
        if name.startswith("_") or name in runtime_only:
            continue
        setattr(target, name, deepcopy(value))
    room_access.restore_slot(target, live_room_access)


class StagedGameTransaction:
    """Caller captures/publishes under authority/state gates; planning is unlocked."""

    def __init__(self, instance: Any):
        self.before = fork_domain_state(instance)
        self.staged = fork_domain_state(instance)
        self._expected = _projection(instance)

    def matches(self, instance: Any) -> bool:
        return _projection(instance) == self._expected

    def publish(self, instance: Any) -> None:
        if not self.matches(instance):
            raise ValueError("game changed during transaction planning")
        instance.replace_persisted_state_from(self.staged)

    def rollback(self, instance: Any) -> None:
        instance.replace_persisted_state_from(self.before)
