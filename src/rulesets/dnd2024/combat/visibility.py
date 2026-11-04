"""Viewer projection of D&D 2024 combat data: monster stat blocks are GM-only.

The authoritative state keeps full enemy stat blocks (attacks, abilities,
saving throws) because combat resolution needs them.  Non-GM viewers -- seated
players and P2P guests relayed as a seat -- only get what the combat roster
already shows them: name, exact HP, AC, speed and conditions.  The encounter
preset catalog is GM tooling; players see at most a name/description preview
of the encounter the story is asking for.
"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

PUBLIC_ENEMY_FIELDS = ("id", "name", "hp", "max_hp", "armor_class", "speed", "conditions")
PUBLIC_PRESET_FIELDS = ("id", "name", "description", "difficulty")
_BATCH_LIST_KEYS = ("automatic_event_batches", "resolved_event_batches")

# Pending decisions are authoritative state, not a second stat-block channel.
# A non-GM owner needs the rolled result and the choices required to resolve the
# window, but never the held settlement (which may contain an enemy weapon
# profile or an internal intent).  Keep this allow-list explicit so new
# decision fields fail closed at the public boundary.
_PUBLIC_DECISION_FIELDS = frozenset({
    "decision_id", "kind", "surface", "window_code", "window_owner_id",
    "actor_id", "attacker_id", "target_id", "assigned_to", "options",
    "check", "resource_id", "die_sides", "damage_sides", "damage_event",
    "attack_total", "armor_class", "critical", "spells", "features",
    "threat_actor_ids", "movement",
})
_PUBLIC_DAMAGE_FIELDS = frozenset({
    "type", "resource", "target_id", "delta", "amount", "damage_type",
    "critical", "rolls", "resisted", "resistance", "penalty", "penalty_rolls",
})


def public_enemy(enemy: Any) -> Any:
    if not isinstance(enemy, dict):
        return enemy
    return {key: deepcopy(enemy[key]) for key in PUBLIC_ENEMY_FIELDS if key in enemy}


def _public_enemies(enemies: Any) -> Any:
    if isinstance(enemies, dict):
        return {key: public_enemy(value) for key, value in enemies.items()}
    if isinstance(enemies, list):
        return [public_enemy(value) for value in enemies]
    return enemies


def _public_damage_event(event: Any) -> Any:
    if not isinstance(event, dict):
        return event
    return {
        key: deepcopy(event[key])
        for key in _PUBLIC_DAMAGE_FIELDS
        if key in event
    }


def public_pending_decision(
    decision: Any, *, viewer_id: str = "", viewer_is_gm: bool = False,
) -> dict[str, Any] | None:
    """Project one pending decision for a viewer without exposing internals.

    GM tooling may inspect the full authoritative decision.  A non-GM only
    receives a decision assigned to that viewer, and the internal settlement
    chain is deliberately omitted even for the owner.
    """

    if not isinstance(decision, dict):
        return None
    if viewer_is_gm:
        return deepcopy(decision)
    assigned_to = str(decision.get("assigned_to") or "")
    if not str(viewer_id or "") or not assigned_to or assigned_to != str(viewer_id):
        return None
    projected: dict[str, Any] = {}
    for key in _PUBLIC_DECISION_FIELDS:
        if key not in decision:
            continue
        if key == "damage_event":
            projected[key] = _public_damage_event(decision[key])
        elif key == "next_decision":
            # Nested decisions are authoritative continuation state; the owner
            # resolves only the current window and must not receive the chain.
            continue
        else:
            projected[key] = deepcopy(decision[key])
    return projected


def public_pending_decisions(
    decisions: Any, *, viewer_id: str = "", viewer_is_gm: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(decisions, list):
        return []
    return [
        projected
        for decision in decisions
        if (projected := public_pending_decision(
            decision, viewer_id=viewer_id, viewer_is_gm=viewer_is_gm,
        )) is not None
    ]


def public_combat_state(
    combat: Any, *, viewer_id: str = "", viewer_is_gm: bool = False,
) -> Any:
    if not isinstance(combat, dict):
        return combat
    projected = deepcopy(combat)
    if "enemies" in projected:
        projected["enemies"] = _public_enemies(projected["enemies"])
    if "pending_decisions" in projected:
        projected["pending_decisions"] = public_pending_decisions(
            combat.get("pending_decisions"),
            viewer_id=viewer_id,
            viewer_is_gm=viewer_is_gm,
        )
    return projected


def public_event_batch(
    batch: Any, *, viewer_id: str = "", viewer_is_gm: bool = False,
) -> Any:
    if not isinstance(batch, dict):
        return batch
    projected = deepcopy(batch)
    events = projected.get("events")
    if isinstance(events, list):
        public_events: list[Any] = []
        for event in events:
            if not isinstance(event, dict):
                public_events.append(event)
                continue
            if event.get("type") == "dnd2024.decision.pending":
                pending = public_pending_decision(
                    event, viewer_id=viewer_id, viewer_is_gm=viewer_is_gm,
                )
                if pending is None:
                    continue
                public_events.append({"type": event["type"], **pending})
                continue
            if "enemies" in event:
                event["enemies"] = _public_enemies(event["enemies"])
            public_events.append(event)
        projected["events"] = public_events
    return projected


def _public_apply_result(
    result: Any, *, viewer_id: str = "", viewer_is_gm: bool = False,
) -> Any:
    if not isinstance(result, dict):
        return result
    projected = dict(result)
    if "combat" in projected:
        projected["combat"] = public_combat_state(
            projected["combat"], viewer_id=viewer_id, viewer_is_gm=viewer_is_gm,
        )
    if "event_batch" in projected:
        projected["event_batch"] = public_event_batch(
            projected["event_batch"], viewer_id=viewer_id, viewer_is_gm=viewer_is_gm,
        )
    if "pending_decision" in projected:
        pending = public_pending_decision(
            projected["pending_decision"],
            viewer_id=viewer_id,
            viewer_is_gm=viewer_is_gm,
        )
        if pending is None:
            projected.pop("pending_decision", None)
        else:
            projected["pending_decision"] = pending
    return projected


def public_intent_result(
    result: dict[str, Any], *, viewer_id: str = "", viewer_is_gm: bool = False,
) -> dict[str, Any]:
    """Project an intent/automation response for a non-GM viewer (copy)."""

    projected = _public_apply_result(
        deepcopy(result), viewer_id=viewer_id, viewer_is_gm=viewer_is_gm,
    )
    for key in _BATCH_LIST_KEYS:
        batches = projected.get(key)
        if isinstance(batches, list):
            projected[key] = [
                public_event_batch(
                    batch, viewer_id=viewer_id, viewer_is_gm=viewer_is_gm,
                )
                for batch in batches
            ]
    results = projected.get("automatic_results")
    if isinstance(results, list):
        projected["automatic_results"] = [
            _public_apply_result(
                item, viewer_id=viewer_id, viewer_is_gm=viewer_is_gm,
            )
            for item in results
        ]
    return projected


def encounter_preview(
    presets: Iterable[Any], preset_ids: Iterable[str],
) -> dict[str, Any] | None:
    """Name/description of the first preset the story currently points at."""

    by_id = {
        str(preset.get("id") or ""): preset
        for preset in presets if isinstance(preset, dict)
    }
    for preset_id in preset_ids:
        preset = by_id.get(str(preset_id or ""))
        if preset is not None:
            return {
                key: deepcopy(preset[key]) for key in PUBLIC_PRESET_FIELDS if key in preset
            }
    return None


__all__ = [
    "PUBLIC_ENEMY_FIELDS", "PUBLIC_PRESET_FIELDS", "encounter_preview",
    "public_combat_state", "public_enemy", "public_event_batch",
    "public_intent_result", "public_pending_decision", "public_pending_decisions",
]
