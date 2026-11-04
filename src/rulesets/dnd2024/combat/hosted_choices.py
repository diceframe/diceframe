"""Finite D&D intents; a hosted model chooses an ID, never authors mechanics."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.engine.player_control import is_ai_controlled


def hosted_intent_choices(engine: Any, instance: Any, uid: str, *, limit: int = 48) -> list[dict]:
    if not is_ai_controlled(instance, uid):
        return []
    fallback = engine.next_automatic_intent(instance)
    actor = f"player:{uid}"
    if not fallback or fallback.get("actor_id") != actor:
        return []
    # A required death save is compulsory; bonus actions/movement remain choices.
    if fallback["type"] == "death_save":
        return [fallback]
    base = {key: fallback[key] for key in ("submitted_by", "actor_id", "expected_version")}
    options: list[dict] = [deepcopy(fallback)]

    def add(kind: str, **fields: Any) -> None:
        if len(options) >= limit:
            return
        candidate = {**base, "type": kind, **fields,
                     "intent_id": f"hosted:{instance.run_id}:{base['expected_version']}:{actor}:{len(options)}"}
        if engine.validate_intent(instance, candidate).get("ok"):
            options.append(candidate)

    # Always retain an explicit way to end the turn before expanding targets.
    for kind in ("end_turn", "dodge", "disengage", "dash", "hide"):
        add(kind)
    for action in engine.available_intents(instance, uid):
        kind = action["type"]
        if kind == "attack":
            for weapon in action.get("weapons", []):
                for target in action.get("targets", []):
                    add(kind, weapon_ref=weapon["weapon_ref"], target_id=target["actor_id"])
        elif kind == "stabilize":
            for target in action.get("targets", []):
                add(kind, target_id=target["actor_id"])
        elif kind == "cast_spell":
            for spell in action.get("spells", []):
                for level in spell.get("available_slot_levels", []):
                    for target in action.get("targets", []):
                        add(kind, spell_ref=spell["spell_ref"], slot_level=level, target_ids=[target["actor_id"]])
        elif kind == "class_capability":
            for target in action.get("targets", [{}]):
                add(kind, capability_id=action["capability_id"], **({"target_id": target["actor_id"]} if target else {}))
        elif kind == "move":
            movement = int(action["movement_remaining"])
            for distance in dict.fromkeys((5, -5, movement, -movement)):
                add(kind, distance=distance)
    # Deduplicate equivalent operations while keeping the safe fallback first.
    result, seen = [], set()
    for intent in options:
        signature = repr(sorted((key, value) for key, value in intent.items() if key != "intent_id"))
        if signature not in seen:
            seen.add(signature)
            result.append(intent)
    return result
