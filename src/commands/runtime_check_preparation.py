"""Snapshot/commit boundary for asynchronous runtime narrative preparation."""
from __future__ import annotations

from copy import deepcopy
from typing import Awaitable, Callable, Any

from src.engine.game_instance import GameInstance, GameState
from src.engine.modules import checks
from src.engine.staged_transaction import StagedGameTransaction


async def prepare_runtime_checks(
    instance: GameInstance,
    registry: Any,
    prepare: Callable[[GameInstance], Awaitable[list[dict]]],
) -> list[dict]:
    """Compute without live references, then publish under authority/state gates.

    Cancellation can leave a synchronous RPC running in its worker thread. It
    only owns the detached candidate; no completion callback can publish it.
    The optimistic transaction fences all persisted inputs, while the generation
    also detects abort -> retry ABA and the registry check fences replacement.
    """
    async with instance._lock:
        if registry.get(instance.game_key) is not instance:
            return []
        if checks.round_checks_prepared(instance):
            return list(checks.last_checks(instance))
        if instance.state != GameState.ACTIVE_JUDGMENT:
            return []
        generation = instance._round_check_generation
        unpriced = deepcopy(instance.round_unpriced_purchase_intents)
        transaction = StagedGameTransaction(instance)
        transaction.staged.round_unpriced_purchase_intents = deepcopy(unpriced)

    await prepare(transaction.staged)

    async with instance.authoritative_write() as allowed:
        if not allowed:
            return []
        async with instance._lock:
            if (
                registry.get(instance.game_key) is not instance
                or instance.run_id != transaction.before.run_id
                or instance._round_check_generation != generation
                or instance.state != GameState.ACTIVE_JUDGMENT
            ):
                return []
            # A concurrent successful preparation is authoritative; never let a
            # later RPC overwrite its dice, identity or preparation metadata.
            if checks.round_checks_prepared(instance):
                return list(checks.last_checks(instance))
            if (
                instance.round_unpriced_purchase_intents != unpriced
                or not transaction.matches(instance)
            ):
                return []
            transaction.publish(instance)
            instance.round_unpriced_purchase_intents = deepcopy(
                transaction.staged.round_unpriced_purchase_intents,
            )
            instance._round_check_generation += 1
            return list(checks.last_checks(instance))
