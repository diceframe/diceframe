"""Event reducer stage for D&D 2024 combat."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.rulesets.events import EventBatchError
from .primitives import actor_kind as _actor_kind


class CombatReducerMixin:
    """Apply validated events to a detached authoritative snapshot."""

    __slots__ = ()

    def _reduce_event(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        state = snapshot["ruleset_state"]
        combat = state["combat"]
        event_type = str(event["type"])
        if event_type in {
            "intent.submitted", "check.resolved", "dnd2024.spell.cast",
            "dnd2024.combat.message",
        }:
            return
        if event_type == "dnd2024.encounter.readiness.changed":
            request = state.get("encounter_request")
            if not isinstance(request, dict) or request.get("status") != "pending":
                raise EventBatchError("encounter readiness has no pending request")
            player_id = str(event["player_id"])
            ready_ids = {
                str(item) for item in request.get("ready_player_ids") or [] if str(item)
            }
            if event.get("ready"):
                ready_ids.add(player_id)
            else:
                ready_ids.discard(player_id)
            request["ready_player_ids"] = sorted(ready_ids)
            return
        if event_type == "dnd2024.combat.started":
            state["combat"] = {
                "status": "active", "round": event["round"], "turn_index": 0,
                "initiative": deepcopy(event["initiative"]),
                "initiative_rolls": deepcopy(event["initiative_rolls"]),
                "enemies": deepcopy(event["enemies"]),
                "positions": deepcopy(event["positions"]),
                "economy": deepcopy(event["economy"]),
                "pending_decisions": [], "position_mode": event["position_mode"],
                "reactions": deepcopy(event["reactions"]),
                "encounter_instance_id": str(event.get("encounter_instance_id") or ""),
                "encounter_preset_id": str(event.get("encounter_preset_id") or ""),
                "origin_step_id": str(event.get("origin_step_id") or ""),
                "mode": str(event.get("mode") or "sandbox"),
                "adventure_binding": deepcopy(event.get("adventure_binding")),
            }
            return
        if event_type == "dnd2024.reaction.spent":
            actor_id = str(event["actor_id"])
            reactions = combat.setdefault("reactions", {})
            if int(reactions.get(actor_id, 0) or 0) < 1:
                raise EventBatchError("reaction is already spent")
            reactions[actor_id] = 0
            return
        if event_type == "dnd2024.combat.ended":
            combat["status"] = "ended"
            combat["outcome"] = str(event.get("reason") or "ended")
            combat["economy"] = {}
            encounter_id = str(combat.get("encounter_instance_id") or "")
            if encounter_id:
                history = state.setdefault("combat_history", [])
                if not any(
                    isinstance(item, dict) and item.get("encounter_instance_id") == encounter_id
                    for item in history
                ):
                    history.append({
                        "encounter_instance_id": encounter_id,
                        "encounter_preset_id": str(combat.get("encounter_preset_id") or ""),
                        "origin_step_id": str(combat.get("origin_step_id") or ""),
                        "outcome": combat["outcome"],
                    })
                    del history[:-64]
            return
        if event_type == "dnd2024.action.spent":
            resource = str(event["resource"])
            current = int(combat["economy"].get(resource, 0) or 0)
            amount = int(event.get("amount", 1) or 1)
            if current < amount:
                raise EventBatchError(f"combat resource is already spent: {resource}")
            combat["economy"][resource] = current - amount
            return
        if event_type == "dnd2024.attack.spent":
            economy = combat["economy"]
            if int(economy.get("attacks_remaining", 0) or 0) > 0:
                economy["attacks_remaining"] -= 1
            elif int(economy.get("action", 0) or 0) > 0:
                economy["action"] -= 1
                economy["attacks_remaining"] = max(0, int(event["attacks_per_action"]) - 1)
            else:
                raise EventBatchError("Attack action is already spent")
            return
        if event_type == "dnd2024.movement.granted":
            combat["economy"]["movement"] = int(combat["economy"].get("movement", 0)) + int(
                event["amount"]
            )
            return
        if event_type == "dnd2024.position.changed":
            actor_id = str(event["actor_id"])
            combat["positions"][actor_id] = int(combat["positions"].get(actor_id, 0)) + int(
                event["distance"]
            )
            cost = int(event.get("movement_cost", 0) or 0)
            if cost:
                remaining = int(combat["economy"].get("movement", 0) or 0)
                if remaining < cost:
                    raise EventBatchError("movement resource is already spent")
                combat["economy"]["movement"] = remaining - cost
            return
        if event_type == "resource.changed":
            self._apply_hp_change(snapshot, event)
            return
        if event_type == "condition.applied":
            conditions = self._conditions(snapshot, combat, str(event["target_id"]))
            conditions[str(event["condition"])] = {
                key: deepcopy(value) for key, value in event.items()
                if key not in {"type", "target_id", "condition"}
            }
            if event["condition"] == "stable":
                kind, raw = _actor_kind(str(event["target_id"]))
                if kind == "player":
                    snapshot["characters"][raw].setdefault("conditions", {})[
                        "death_saves"
                    ] = {"successes": 3, "failures": 0}
            return
        if event_type == "condition.removed":
            self._conditions(snapshot, combat, str(event["target_id"])).pop(
                str(event["condition"]), None
            )
            return
        if event_type == "dnd2024.spell.slot_spent":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind != "player":
                raise EventBatchError("only player spell slots are canonical")
            slots = snapshot["characters"][raw_id]["spellcasting"]["class"]["slots_current"]
            level = str(event["slot_level"])
            if int(slots.get(level, 0) or 0) < 1:
                raise EventBatchError("spell slot is already spent")
            slots[level] -= 1
            combat["economy"]["slot_spell_cast"] = True
            return
        if event_type == "dnd2024.concentration.started":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind == "player":
                snapshot["characters"][raw_id]["spellcasting"]["class"]["concentration"] = {
                    "spell_ref": event["spell_ref"], "target_ids": deepcopy(event["target_ids"]),
                }
            return
        if event_type == "dnd2024.concentration.ended":
            owner = str(event["actor_id"])
            kind, raw_id = _actor_kind(owner)
            if kind == "player":
                snapshot["characters"][raw_id]["spellcasting"]["class"]["concentration"] = None
            self._remove_concentration_conditions(snapshot, combat, owner)
            return
        if event_type == "dnd2024.turn.advanced":
            self._expire_conditions(
                snapshot, combat, str(event["actor_id"]),
                str(event["previous_actor_id"]),
            )
            combat["round"] = int(event["round"])
            combat["turn_index"] = int(event["turn_index"])
            combat["economy"] = deepcopy(event["economy"])
            combat.setdefault("reactions", {})[str(event["actor_id"])] = 1
            return
        if event_type == "dnd2024.death_save.resolved":
            _kind, raw_id = _actor_kind(str(event["actor_id"]))
            character = snapshot["characters"][raw_id]
            character.setdefault("conditions", {})["death_saves"] = {
                "successes": int(event["successes"]), "failures": int(event["failures"]),
            }
            if event.get("hp"):
                character["resources"]["hp"] = int(event["hp"])
                character["conditions"].pop("unconscious", None)
            if event.get("stable"):
                character["conditions"]["stable"] = {"duration": "until_healed"}
            if event.get("dead"):
                character["conditions"]["dead"] = {"source": "death_saves"}
                character["conditions"].pop("unconscious", None)
            return
        if event_type == "dnd2024.decision.pending":
            combat.setdefault("pending_decisions", []).append({
                key: deepcopy(value) for key, value in event.items() if key != "type"
            })
            return
        if event_type == "dnd2024.decision.cleared":
            combat["pending_decisions"] = [
                item for item in combat.get("pending_decisions", [])
                if item.get("decision_id") != event["decision_id"]
            ]
            return
        raise EventBatchError(f"unsupported event type: {event_type}")

    def _apply_hp_change(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        target_id = str(event["target_id"])
        kind, raw_id = _actor_kind(target_id)
        delta = int(event["delta"])
        if kind == "player":
            character = snapshot["characters"][raw_id]
            resources = character["resources"]
            before = int(resources.get("hp", 0) or 0)
            maximum = int(resources.get("max_hp", 0) or 0)
            resources["hp"] = max(0, min(maximum, before + delta))
            conditions = character.setdefault("conditions", {})
            if resources["hp"] <= 0:
                conditions["unconscious"] = {"source": "zero_hp"}
                death = conditions.setdefault("death_saves", {"successes": 0, "failures": 0})
                if before == 0 and delta < 0:
                    death["failures"] = min(
                        3, int(death.get("failures", 0)) + (2 if event.get("critical") else 1)
                    )
                    if death["failures"] >= 3:
                        conditions["dead"] = {"source": "damage_at_zero_hp"}
                        conditions.pop("unconscious", None)
            elif delta > 0:
                conditions.pop("unconscious", None)
                conditions.pop("stable", None)
                conditions["death_saves"] = {"successes": 0, "failures": 0}
        elif kind == "enemy":
            enemy = snapshot["ruleset_state"]["combat"]["enemies"][raw_id]
            enemy["hp"] = max(0, min(int(enemy["max_hp"]), int(enemy["hp"]) + delta))
            if enemy["hp"] <= 0:
                enemy.setdefault("conditions", {})["defeated"] = {"source": "zero_hp"}
        else:
            raise EventBatchError("HP target actor is invalid")
