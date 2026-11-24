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
from src.rulesets.dnd2024.spells.casting import free_cast_resource, spell_cost_events
from .action_adapter import (
    double_dice_counts,
    formula_node_from_dice_formula,
    roll_damage_node,
    spell_formula_node,
)
from .primitives import (
    SMITE_OF_PROTECTION_CONDITION,
    SMITE_OF_PROTECTION_DURATION,
    CombatIntentError,
    actor_kind,
)


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

    def _spell_cost_events(
        self, actor, spell, slot_level, *, spend_action=True, quickened=False,
    ):
        # 反应法术（护盾术）不占用动作经济里的动作，由调用方另记 reaction.spent。
        # Quickened Spell 把这次施法的成本从动作换成附赠动作：换的是**哪一项**，
        # 不是"多扣一项"，所以仍只有一条 action.spent。
        as_bonus = quickened or str(spell["casting_time"]).startswith("Bonus Action")
        events = [
            {
                "type": "dnd2024.action.spent",
                "actor_id": actor["actor_id"],
                "resource": "bonus_action" if as_bonus else "action",
                "amount": 1,
            }
        ] if spend_action else []
        try:
            events.extend(spell_cost_events(actor, spell, slot_level))
        except ValueError as exc:
            raise CombatIntentError(str(exc)) from exc
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

    def _smite_of_protection_events(
        self, actor: dict[str, Any], spell_ref: str,
    ) -> list[dict[str, Any]]:
        """Half Cover for the aura, opened by an actual Divine Smite cast.

        The trigger is the cast, not the damage: a Smite whose damage is later
        cut by resistance still happened, while a declined hit-spell decision
        never reaches this method at all.  Nothing is paid here -- the feature
        costs no Channel use and no slot of its own.

        The condition sits on the Paladin rather than on each ally, because the
        benefit is positional: it comes from the aura and therefore has to be
        re-answered whenever anyone moves.
        """

        character = self._feature_character(actor)
        if not self.features.has_feature(
            character, "smite_of_protection", owner="subclass:oath_of_devotion",
        ):
            return []
        trigger = str(self.features.feature_value(
            character, "smite_of_protection", "cover_trigger_spell_id",
            "divine_smite",
        ) or "divine_smite")
        if str(spell_ref).removeprefix("spell:") != trigger:
            return []
        actor_id = str(actor["actor_id"])
        return [{
            "type": "condition.applied", "target_id": actor_id,
            "condition": SMITE_OF_PROTECTION_CONDITION,
            "duration": SMITE_OF_PROTECTION_DURATION,
            "source_actor_id": actor_id,
            "feature_id": "smite_of_protection",
            "source_spell_ref": spell_ref,
            "cover_source_feature_id": str(self.features.feature_value(
                character, "smite_of_protection", "cover_source_feature_id",
                "aura_of_protection",
            ) or "aura_of_protection"),
            "cover_armor_class_bonus": int(self.features.feature_value(
                character, "smite_of_protection", "cover_armor_class_bonus", 2,
            ) or 2),
            "cover_dex_save_bonus": int(self.features.feature_value(
                character, "smite_of_protection", "cover_dex_save_bonus", 2,
            ) or 2),
        }]

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
        # 施法已成事实之后才开掩蔽：无论走真槽还是免费施法都到这一步，
        # 而"拒绝命中法术决策"根本不构成一次施放。
        events.extend(self._smite_of_protection_events(actor, intent["spell_ref"]))
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
