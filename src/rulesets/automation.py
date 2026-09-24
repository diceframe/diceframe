"""Transactional execution for server-owned ruleset intents."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from src.engine import progression
from src.rulesets.contracts import (
    AutomaticIntentRuntime,
    NarrativeDirectorAutomationRuntime,
    PublicTimelineProjectionRuntime,
)


def append_public_timeline_entry(
    runtime: Any,
    instance: Any,
    batch: dict[str, Any],
) -> None:
    """Project one applied EventBatch into the shared public story feed.

    Owned by the ruleset layer because the projection itself is the runtime's
    (``public_timeline_projection``); the generic services only decide *when* it
    runs.  Every authoritative path that applies a batch -- a submitted intent
    and a control-change resume -- projects through this one function, so an
    AI-hosted turn is as visible in the public story as a human one.
    """

    intent_type = str(batch.get("intent_type") or "")
    projection = runtime.public_timeline_projection(
        batch, str(getattr(instance, "language", "") or ""),
    )
    action_text = str(projection.get("action_text") or "")
    gm_response = str(projection.get("gm_response") or "")
    submitted_by = next(
        (
            str(event.get("submitted_by") or "")
            for event in batch.get("events", [])
            if isinstance(event, dict) and event.get("type") == "intent.submitted"
        ),
        "",
    )
    next_round = progression.advance_for_public_timeline(instance, instance.log)
    instance.append_log_entry({
        "round": next_round,
        "actions": [{
            "user_id": submitted_by,
            "text": action_text,
            "source": "ruleset_authority",
            "intent_type": intent_type,
            "operation_id": str(batch.get("intent_id") or ""),
        }],
        "gm_response": gm_response,
        "state_changes": [
            str(event.get("type") or "")
            for event in batch.get("events", [])
            if isinstance(event, dict) and str(event.get("type") or "") != "intent.submitted"
        ],
        "check_results": [],
        "swipes": [],
        "current_swipe": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def advance_automatic_intents(
    runtime: AutomaticIntentRuntime,
    instance: Any,
    rng: Any,
    *,
    limit: int = 256,
    on_applied: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve and apply server-owned automatic intents until the runtime stops.

    This is the single implementation of the deterministic automation ladder
    (enemies, companions and AI-hosted player seats): ask
    ``next_automatic_intent`` for one bounded operation, resolve it, apply its
    EventBatch, repeat -- and never let the model decide a rules action.

    The function declares nothing itself and mutates state only through the
    runtime's own ``resolve_intent`` / ``apply_event_batch``, so validation,
    dice and events stay on exactly the same authoritative chain a player
    submission uses.  It fails closed with :class:`ValueError` on a rejected
    intent, a missing/mis-applied batch, or a ladder that does not terminate;
    the **caller** owns the transaction snapshot and must roll back with
    ``instance.restore_ruleset_transaction(before)``, so this helper can be
    reused from any entry point without inventing a second transaction model.
    """

    batches: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for _ in range(limit):
        intent = runtime.next_automatic_intent(instance)
        if intent is None:
            return batches, results
        resolved = runtime.resolve_intent(instance, intent, rng)
        if not resolved.get("ok"):
            raise ValueError(
                str(resolved.get("error") or "automatic intent was rejected")
            )
        batch = resolved.get("event_batch")
        if not isinstance(batch, dict):
            raise ValueError("automatic intent returned no event batch")
        applied = runtime.apply_event_batch(instance, batch)
        if not applied.get("applied"):
            raise ValueError("automatic intent did not advance state")
        batches.append(deepcopy(batch))
        results.append(deepcopy(applied))
        if on_applied is not None:
            on_applied(batch, applied)
    raise ValueError("automatic turn exceeded the safety limit")


def is_public_story_milestone(runtime: Any, batch: dict[str, Any]) -> bool:
    """Does this applied batch belong in the shared public story feed?"""

    return (
        isinstance(runtime, PublicTimelineProjectionRuntime)
        and runtime.is_public_story_milestone(batch)
    )


def apply_director_automation(
    runtime: Any, instance: Any, proposal: dict[str, Any], rng: Any,
    *, limit: int = 256,
) -> list[dict[str, Any]]:
    """Apply one Director intent and bounded follow-up automatic intents atomically."""

    if not isinstance(runtime, NarrativeDirectorAutomationRuntime):
        return []
    initial = runtime.director_automatic_intent(instance, proposal)
    if initial is None:
        return []
    initial_intents = initial if isinstance(initial, list) else [initial]
    if not initial_intents:
        return []
    before = {
        "ruleset_state": deepcopy(instance.ruleset_state),
        "event_ledger": deepcopy(instance.event_ledger),
        "players": deepcopy(instance.players),
        "combat_state": instance.combat_state,
        "combat_active": instance.combat_active,
        "initiative_order": deepcopy(instance.initiative_order),
        "initiative_current": instance.initiative_current,
        "scene": getattr(instance, "scene", None),
    }
    batches: list[dict[str, Any]] = []
    try:
        pending_intents = list(initial_intents)
        pending: dict[str, Any] | None = pending_intents.pop(0)
        for _ in range(limit):
            if pending is None:
                return batches
            resolved = runtime.resolve_intent(instance, pending, rng)
            if not resolved.get("ok"):
                raise ValueError(str(resolved.get("error") or "Director intent was rejected"))
            batch = resolved.get("event_batch")
            if not isinstance(batch, dict):
                raise ValueError("Director intent returned no event batch")
            applied = runtime.apply_event_batch(instance, batch)
            if not applied.get("applied"):
                raise ValueError("Director intent did not advance state")
            batches.append(deepcopy(batch))
            if pending_intents:
                pending = pending_intents.pop(0)
            else:
                pending = (
                    runtime.next_automatic_intent(instance)
                    if isinstance(runtime, AutomaticIntentRuntime)
                    else None
                )
        raise ValueError("Director automation exceeded the safety limit")
    except Exception:
        instance.restore_ruleset_transaction(before)
        raise


def summarize_automation_batches(batches: list[dict[str, Any]], *, chinese: bool = True) -> str:
    """Produce a short public-log note without importing a concrete ruleset."""

    event_types = {
        str(event.get("type") or "")
        for batch in batches
        for event in batch.get("events", [])
        if isinstance(event, dict)
    }
    if "dnd2024.combat.started" in event_types:
        return "自动规则已进入遭遇战。" if chinese else "Automatic rules entered an encounter."
    if "dnd2024.tutorial.choice_applied" in event_types:
        return "AI GM 已根据行动推进当前冒险节点。" if chinese else "The AI GM advanced the adventure node from the action."
    if "dnd2024.party_decision.resolved" in event_types:
        return "AI GM 已根据队伍行动结算分支。" if chinese else "The AI GM resolved the branch from the party actions."
    return "自动规则事件已结算。" if chinese else "An automatic rules event was resolved."
