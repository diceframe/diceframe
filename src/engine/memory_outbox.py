"""Durable outbox for memory side effects attached to economy decisions.

Economy proposals may carry a memory delta that must reach the external
memory store only after the authoritative settlement.  The outbox lives in
``economy.external_effects_outbox`` (it rides the economy persistence and
rollback window), but the delivery state machine belongs to the memory
domain, not to the ledger.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

MAX_EXTERNAL_EFFECT_DELIVERIES = 50


def queue_memory_delivery(
    instance: Any,
    *,
    effect_group_id: str,
    memory_delta: dict[str, Any],
    round_number: int,
) -> dict[str, Any] | None:
    """Persist an idempotent memory side effect before external delivery."""

    if not effect_group_id or not memory_delta:
        return None
    deliveries = instance.economy.setdefault("external_effects_outbox", [])
    delivery_id = f"memory:{effect_group_id}"
    existing = next(
        (
            item for item in deliveries
            if isinstance(item, dict) and item.get("id") == delivery_id
        ),
        None,
    )
    if existing is not None:
        return existing
    delivery = {
        "id": delivery_id,
        "run_id": instance.run_id,
        "effect_group_id": effect_group_id,
        "kind": "memory_delta",
        "payload": deepcopy(memory_delta),
        "round": int(round_number),
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    deliveries.append(delivery)
    if len(deliveries) > MAX_EXTERNAL_EFFECT_DELIVERIES:
        active = [
            item for item in deliveries
            if (
                isinstance(item, dict)
                and item.get("status") in {"pending", "reversal_pending"}
            )
        ]
        resolved = [
            item for item in deliveries
            if (
                isinstance(item, dict)
                and item.get("status") not in {"pending", "reversal_pending"}
            )
        ]
        budget = max(0, MAX_EXTERNAL_EFFECT_DELIVERIES - len(active))
        instance.economy["external_effects_outbox"] = (
            active + resolved[-budget:] if budget else active
        )
    return delivery


def pending_memory_deliveries(instance: Any) -> list[dict[str, Any]]:
    economy = getattr(instance, "economy", {})
    deliveries = (
        economy.get("external_effects_outbox", [])
        if isinstance(economy, dict) else []
    )
    return [
        item for item in deliveries
        if (
            isinstance(item, dict)
            and item.get("status") == "pending"
            and item.get("kind") == "memory_delta"
            and item.get("run_id") == getattr(instance, "run_id", "")
        )
    ]


def pending_memory_reversals(instance: Any) -> list[dict[str, Any]]:
    """Return delivered economy memories that must be undone after rollback."""

    economy = getattr(instance, "economy", {})
    deliveries = (
        economy.get("external_effects_outbox", [])
        if isinstance(economy, dict) else []
    )
    return [
        item for item in deliveries
        if (
            isinstance(item, dict)
            and item.get("status") == "reversal_pending"
            and item.get("kind") == "memory_delta"
            and item.get("run_id") == getattr(instance, "run_id", "")
        )
    ]


def complete_memory_delivery(instance: Any, delivery_id: str) -> bool:
    delivery = next(
        (
            item for item in instance.economy.get("external_effects_outbox", [])
            if isinstance(item, dict) and item.get("id") == delivery_id
        ),
        None,
    )
    if delivery is None or delivery.get("run_id") != instance.run_id:
        return False
    if delivery.get("status") == "delivered":
        return True
    if delivery.get("status") != "pending":
        return False
    delivery["status"] = "delivered"
    delivery["delivered_at"] = datetime.now(timezone.utc).isoformat()
    delivery.pop("payload", None)
    return True


def complete_memory_reversal(instance: Any, delivery_id: str) -> bool:
    delivery = next(
        (
            item for item in instance.economy.get("external_effects_outbox", [])
            if isinstance(item, dict) and item.get("id") == delivery_id
        ),
        None,
    )
    if delivery is None or delivery.get("run_id") != instance.run_id:
        return False
    if delivery.get("status") == "reversed":
        return True
    if delivery.get("status") != "reversal_pending":
        return False
    delivery["status"] = "reversed"
    delivery["reversed_at"] = datetime.now(timezone.utc).isoformat()
    return True
