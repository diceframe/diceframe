"""Melee-hit spell decisions, owned by the D&D combat authority.

The attack roll and its base damage dice are rolled once. Damage is held in
the existing pending-decision state until the actor casts or declines, so an
attack plus its added damage causes one concentration check and one victory
decision. Clients cannot supply the hit, critical, target, or damage context.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.engine.player_control import is_ai_controlled
from .action_adapter import (
    double_dice_counts,
    formula_node_from_dice_formula,
    roll_damage_node,
    spell_formula_node,
)
from .primitives import CombatIntentError, actor_kind


HIT_SPELL_DECISION = "melee_hit_spell"


def hit_spell_decision(combat: dict[str, Any], actor_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in combat.get("pending_decisions", [])
            if row.get("kind") == HIT_SPELL_DECISION and row.get("actor_id") == actor_id
        ),
        None,
    )


def free_cast_resource(actor: dict[str, Any], spell_ref: str) -> dict[str, Any] | None:
    # 职业特性免费施放挂在 class resource 上；起源专长（魔法学徒）的
    # 每长休一次免费施放挂在该专长条目上。两者都可能授予同一法术。
    grants = [
        row for row in actor.get("spell_grants", []) if row.get("spell_ref") == spell_ref
    ]
    feat_grant = next((row for row in grants if row.get("free_cast_available")), None)
    if feat_grant is not None:
        return {"kind": "feat", "feat_ref": feat_grant["feat_ref"]}
    grant = next((row for row in grants if row.get("resource_id")), None)
    if grant is None:
        return None
    return next(
        (
            row
            for row in actor.get("class_resources", [])
            if row.get("id") == grant["resource_id"] and int(row.get("current", 0)) > 0
        ),
        None,
    )


class CombatTriggeredSpellsMixin:
    __slots__ = ()

    def _hit_spell_offer(
        self, instance, combat, actor, weapon, target_id, damage_event, intent
    ):
        if (
            actor["kind"] not in {"player", "companion"}
            or not intent.get("intent_id")
            or self._current_actor(combat) != actor["actor_id"]
        ):
            return None
        if weapon.get("ranged") or self._distance(
            combat, actor["actor_id"], target_id
        ) > int(weapon.get("range", 5)):
            return None
        decision = {
            "decision_id": f"melee-hit:{intent['intent_id']}",
            "kind": HIT_SPELL_DECISION,
            "actor_id": actor["actor_id"],
            "target_id": target_id,
            "assigned_to": actor["id"]
            if actor["kind"] == "player"
            else str(instance.gm_uid),
            "options": ["decline"],
            "damage_event": deepcopy(damage_event),
            "critical": bool(damage_event.get("critical")),
        }
        choices = [
            row
            for row in self._available_spells(
                actor, combat.get("economy", {}), decision
            )
            if row.get("mode") == "triggered_damage"
        ]
        return decision if choices else None

    def _spell_cost_events(self, actor, spell, slot_level, *, spend_action=True):
        # 反应法术（护盾术）不占用动作经济里的动作，由调用方另记 reaction.spent。
        events = [
            {
                "type": "dnd2024.action.spent",
                "actor_id": actor["actor_id"],
                "resource": "bonus_action"
                if str(spell["casting_time"]).startswith("Bonus Action")
                else "action",
                "amount": 1,
            }
        ] if spend_action else []
        if int(spell["level"]) > 0:
            if slot_level > 0:
                events.append(
                    {
                        "type": "dnd2024.spell.slot_spent",
                        "actor_id": actor["actor_id"],
                        "slot_level": slot_level,
                        "amount": 1,
                    }
                )
            else:
                resource = free_cast_resource(actor, f"spell:{spell['id']}")
                if resource is None:
                    raise CombatIntentError("the free spell cast is not available")
                if resource.get("kind") == "feat":
                    events.append(
                        {
                            "type": "dnd2024.feat_spell.free_cast_spent",
                            "actor_id": actor["actor_id"],
                            "feat_ref": resource["feat_ref"],
                        }
                    )
                    return events
                events.append(
                    {
                        "type": "dnd2024.class_resource.spent",
                        "actor_id": actor["actor_id"],
                        "resource_id": resource["id"],
                        "amount": 1,
                    }
                )
        return events

    def _finish_hit_spell(self, instance, combat, decision, rng, extra_damage=None):
        target = self._actor_view(instance, combat, decision["target_id"])
        events = [
            {
                "type": "dnd2024.decision.cleared",
                "decision_id": decision["decision_id"],
            },
            *self._submit_hit_damage(decision["damage_event"]),
        ]
        total = int(decision["damage_event"]["amount"])
        if extra_damage:
            events.append(extra_damage)
            total += int(extra_damage["amount"])
        events.extend(self._concentration_events(target, total, rng))
        if self._last_hostile_defeated(combat, target, total):
            events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        return events

    def _triggered_spell_events(self, instance, combat, intent, rng):
        actor = self._actor_view(instance, combat, intent["actor_id"])
        decision = hit_spell_decision(combat, actor["actor_id"])
        if decision is None:
            raise CombatIntentError("this spell requires a pending melee hit")
        spell = self.spells.get(intent["spell_ref"])
        effect = self.catalog.spell_effects[intent["spell_ref"].removeprefix("spell:")]
        slot = int(intent.get("slot_level", spell["level"]))
        node = spell_formula_node(
            effect["damage"], effect.get("upcast_damage"), spell, slot, actor
        )
        target = self._actor_view(instance, combat, decision["target_id"])
        if target.get("creature_type") in effect.get("extra_damage_creature_types", []):
            node = {
                "op": "add",
                "args": [node, formula_node_from_dice_formula(effect["extra_damage"])],
            }
        if decision["critical"]:
            node = double_dice_counts(node)
        damage, rolls = roll_damage_node(node, rng)
        events = self._spell_cost_events(actor, spell, slot)
        events.append(
            {
                "type": "dnd2024.spell.cast",
                "actor_id": actor["actor_id"],
                "spell_ref": intent["spell_ref"],
                "slot_level": slot,
                "target_ids": [decision["target_id"]],
            }
        )
        events.extend(
            self._finish_hit_spell(
                instance,
                combat,
                decision,
                rng,
                {
                    "type": "resource.changed",
                    "resource": "hp",
                    "target_id": decision["target_id"],
                    "delta": -damage,
                    "amount": damage,
                    "damage_type": effect["damage_type"],
                    "critical": decision["critical"],
                    "rolls": rolls,
                },
            )
        )
        return events

    def _automatic_hit_spell(self, instance, combat, decision, version):
        kind, raw_id = actor_kind(decision["actor_id"])
        if kind == "player" and not is_ai_controlled(instance, raw_id):
            return None
        actor = self._actor_view(instance, combat, decision["actor_id"])
        spells = [
            row
            for row in self._available_spells(
                actor, combat.get("economy", {}), decision
            )
            if row["mode"] == "triggered_damage"
        ]
        base = {
            "intent_id": f"auto:hit-spell:{version}:{decision['decision_id']}",
            "expected_version": version,
            "submitted_by": str(instance.gm_uid),
            "actor_id": actor["actor_id"],
        }
        if not spells:
            return {
                **base,
                "type": "decision.resolve",
                "decision_id": decision["decision_id"],
                "option": "decline",
            }
        spell = spells[0]
        return {
            **base,
            "type": "cast_spell",
            "spell_ref": spell["spell_ref"],
            "target_id": decision["target_id"],
            "slot_level": spell["available_slot_levels"][0],
        }
