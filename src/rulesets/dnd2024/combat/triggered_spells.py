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


def free_cast_resource(
    actor: dict[str, Any], spell_ref: str, spell_level: int | None = None,
) -> dict[str, Any] | None:
    """The single free source this cast may draw on, or ``None``.

    One cast spends exactly one source, so this returns at most one row and the
    caller never has to arbitrate.  An unlimited slot wins over every finite
    source: Spell Mastery never runs out, and burning a Magic Initiate use (or
    a Mystic Arcanum, or a Signature use) on a spell the character could cast
    for free forever would be a silent, unrecoverable loss.

    ``spell_level`` is the level the cast actually uses.  Free casting never
    upcasts -- a level 6 arcanum is cast at level 6 -- so a slot only matches
    when its fixed level equals the spell's own.
    """

    slots = [
        row for row in actor.get("free_cast_slots", []) or []
        if row.get("valid", True)
        and row.get("spell_ref") == spell_ref
        and (spell_level is None or int(row.get("spell_level", 0) or 0) == int(spell_level))
    ]
    unlimited = next(
        (row for row in slots if row.get("recharge") == "unlimited"), None,
    )
    if unlimited is not None:
        return {
            "kind": "free_slot",
            "free_slot_id": unlimited["slot_id"],
            "spell_level": int(unlimited["spell_level"]),
            "unlimited": True,
        }
    # 职业特性免费施放挂在 class resource 上；起源专长（魔法学徒）的
    # 每长休一次免费施放挂在该专长条目上。两者都可能授予同一法术。
    grants = [
        row for row in actor.get("spell_grants", []) if row.get("spell_ref") == spell_ref
    ]
    feat_grant = next((row for row in grants if row.get("free_cast_available")), None)
    if feat_grant is not None:
        return {"kind": "feat", "feat_ref": feat_grant["feat_ref"]}
    limited = next(
        (
            row for row in slots
            if row.get("uses_remaining") is None or int(row["uses_remaining"]) > 0
        ),
        None,
    )
    if limited is not None:
        return {
            "kind": "free_slot",
            "free_slot_id": limited["slot_id"],
            "spell_level": int(limited["spell_level"]),
        }
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
                instance, actor, combat.get("economy", {}), decision
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
                # Font of Magic 造的临时槽是 **真槽**：同一环阶两者都在时先花
                # 临时槽（两者都在长休重置，先花它不损失任何可选性），并且
                # 一笔施法只发一个扣槽事件——永远不会同时扣两边。
                font = actor.get("font_slots") or {}
                if int(font.get(str(slot_level), 0) or 0) > 0:
                    events.append(
                        {
                            "type": "dnd2024.spell.font_slot_spent",
                            "actor_id": actor["actor_id"],
                            "slot_level": slot_level,
                            "amount": 1,
                            "source": "font_of_magic",
                        }
                    )
                else:
                    events.append(
                        {
                            "type": "dnd2024.spell.slot_spent",
                            "actor_id": actor["actor_id"],
                            "slot_level": slot_level,
                            "amount": 1,
                        }
                    )
            else:
                resource = free_cast_resource(
                    actor, f"spell:{spell['id']}", int(spell["level"]),
                )
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
                if resource.get("kind") == "free_slot":
                    if not resource.get("unlimited"):
                        # Spell Mastery never runs out, so there is no counter to
                        # spend: emitting an event for it would need a number the
                        # slot deliberately does not carry.
                        events.append(
                            {
                                "type": "dnd2024.free_cast.spent",
                                "actor_id": actor["actor_id"],
                                "free_slot_id": resource["free_slot_id"],
                                "spell_ref": f"spell:{spell['id']}",
                                "spell_level": int(resource["spell_level"]),
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

    def _finish_hit_spell(
        self, instance, combat, decision, rng, extra_damage=None, *, spell_cast=False,
    ):
        target = self._actor_view(instance, combat, decision["target_id"])
        events = [
            {
                "type": "dnd2024.decision.cleared",
                "decision_id": decision["decision_id"],
            },
        ]
        if spell_cast:
            # 施放命中触发法术（例如至圣斩）也是一条 "spell_cast" 触发；
            # 在决策清空之后、伤害落地之前结算提前终止。
            events.extend(self._spell_end_trigger_events(
                self._actor_view(instance, combat, decision["actor_id"]), "spell_cast",
            ))
        events.extend(self._submit_hit_damage(decision["damage_event"]))
        # 这条路径同样是"命中落地"：rider（光耀/神圣/原始打击）与命中后条件
        # （震慑击、散手技巧）在直通路径由 ``_resolve_committed_hit`` 结算，
        # 决策路径若漏掉它们，会变成"有至圣斩窗口的角色悄悄少了半个特性"。
        attacker_id = str(decision["actor_id"])
        rider_events, rider_damage = self._hit_rider_events(
            combat, target, decision["damage_event"], rng, attacker_id=attacker_id,
        )
        events.extend(rider_events)
        total = int(decision["damage_event"]["amount"]) + rider_damage
        if extra_damage:
            events.append(extra_damage)
            total += int(extra_damage["amount"])
        events.extend(self._concentration_events(instance, combat, target, total, rng))
        events.extend(self._hit_condition_events(
            instance, combat, attacker_id, target, decision["damage_event"], rng,
        ))
        events.extend(self._enemy_zero_hp_feature_events(
            instance, combat, attacker_id, target, total,
        ))
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
                spell_cast=True,
            ),
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
                instance, actor, combat.get("economy", {}), decision
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
