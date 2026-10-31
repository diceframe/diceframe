"""Being-hit reaction spells (Shield), owned by the D&D combat authority.

When an attack roll hits a party actor who can cast a ``reaction_defense``
spell, the attack roll and its damage are rolled once and held in a pending
decision assigned to the defender. Casting spends the defender's reaction and
a slot (or a free feat cast), applies the AC bonus condition, and re-checks the
same attack total against the raised AC; declining applies the held damage.
Clients never supply the attack total, the AC, or the damage.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.engine.player_control import is_ai_controlled
from .primitives import CombatIntentError, actor_kind
from .triggered_spells import free_cast_resource


HIT_REACTION_DECISION = "hit_reaction_spell"


def effective_armor_class(target: dict[str, Any]) -> int:
    """AC after the defensive spell conditions currently on the target."""

    conditions = target.get("conditions") or {}
    bonus = 2 if "shield_of_faith" in conditions else 0
    shield = conditions.get("shield")
    if isinstance(shield, dict):
        bonus += int(shield.get("armor_class_bonus", 0) or 0)
    return int(target["armor_class"]) + bonus


class CombatReactionSpellsMixin:
    __slots__ = ()

    def _reaction_spell_choices(
        self, combat: dict[str, Any], defender: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if defender["kind"] not in {"player", "companion"} or defender["hp"] <= 0:
            return []
        if int(combat.get("reactions", {}).get(defender["actor_id"], 0) or 0) < 1:
            return []
        if "reaction_blocked" in defender["conditions"] or "shield" in defender["conditions"]:
            return []
        if "rage" in defender["conditions"]:
            return []
        choices = []
        for ref in defender["spell_refs"]:
            spell = self.spells.get(ref)
            effect = self.catalog.spell_effects.get(str(ref).removeprefix("spell:"))
            if spell is None or effect is None or effect["mode"] != "reaction_defense":
                continue
            slots = [
                int(level) for level, count in defender["slots"].items()
                if int(count) > 0 and int(level) >= int(spell["level"])
            ]
            if free_cast_resource(defender, ref):
                slots.append(0)
            if slots:
                choices.append({
                    "spell_ref": ref, "name": spell["name"], "level": spell["level"],
                    "mode": effect["mode"], "available_slot_levels": sorted(slots),
                    "armor_class_bonus": int(effect["armor_class_bonus"]),
                })
        return choices

    def _reaction_feature_choices(
        self, combat: dict[str, Any], defender: dict[str, Any],
        attacker: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Class features the defender may spend their reaction on when hit.

        The reaction budget and the "already reacted" guards are the same ones
        the reaction-spell path uses: both consume ``combat["reactions"]``, so a
        character can never react twice in the same round through two paths.
        Ownership comes from the feature boundary (class level), never from a
        localized name.

        Uncanny Dodge must see the attack, so a blinded defender or an
        invisible attacker removes the choice.  ``attacker`` is optional:
        callers without an attacker view keep the unrestricted behaviour.
        """

        if defender["kind"] not in {"player", "companion"} or defender["hp"] <= 0:
            return []
        if int(combat.get("reactions", {}).get(defender["actor_id"], 0) or 0) < 1:
            return []
        if "reaction_blocked" in defender["conditions"] or "shield" in defender["conditions"]:
            return []
        if "blinded" in defender["conditions"]:
            return []
        if attacker is not None and "invisible" in (attacker.get("conditions") or {}):
            return []
        character = self._feature_character(defender)
        if not self.features.has_feature(character, "uncanny_dodge"):
            return []
        multiplier = self.features.feature_value(character, "uncanny_dodge", "damage_multiplier", 0)
        if (
            isinstance(multiplier, bool)
            or not isinstance(multiplier, (int, float))
            or not 0 < float(multiplier) < 1
        ):
            return []
        return [{
            "feature_id": "uncanny_dodge",
            "name": self.features.catalog.label("uncanny_dodge", "Uncanny Dodge"),
            "mode": "reaction_defense",
            "damage_multiplier": float(multiplier),
        }]

    def _hit_reaction_offer(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        defender: dict[str, Any], check: dict[str, Any], damage_event: dict[str, Any],
        intent: dict[str, Any], next_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if attacker.get("side") == defender.get("side"):
            return None
        spells = self._reaction_spell_choices(combat, defender)
        features = self._reaction_feature_choices(combat, defender, attacker)
        if not spells and not features:
            return None
        kind, raw_id = actor_kind(defender["actor_id"])
        human = kind == "player" and not is_ai_controlled(instance, raw_id)
        source = str(intent.get("intent_id") or f"{attacker['actor_id']}:{combat.get('round', 0)}")
        return {
            "decision_id": f"hit-reaction:{source}:{defender['actor_id']}",
            "kind": HIT_REACTION_DECISION,
            "actor_id": defender["actor_id"],
            "attacker_id": attacker["actor_id"],
            "assigned_to": raw_id if human else str(getattr(instance, "gm_uid", "") or ""),
            "options": [*(["cast"] if spells else []), *(["feature"] if features else []), "decline"],
            "attack_total": int(check["total"]),
            "armor_class": int(check["target"]),
            "critical": bool(check["critical"]),
            "damage_event": deepcopy(damage_event),
            "spells": spells,
            "features": features,
            # A hit-triggered spell (for example Divine Smite) is offered only
            # after the defender has had the reaction opportunity.
            "next_decision": deepcopy(next_decision) if next_decision else None,
        }

    def _validate_hit_reaction(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any],
    ) -> None:
        option = intent.get("option")
        if option not in {"cast", "feature"}:
            return
        defender = self._actor_view(instance, combat, str(decision["actor_id"]))
        if option == "cast":
            choice = next(
                (
                    row for row in self._reaction_spell_choices(combat, defender)
                    if row["spell_ref"] == intent.get("spell_ref")
                ),
                None,
            )
            if choice is None:
                raise CombatIntentError("this reaction spell is not available")
            slot_level = intent.get("slot_level", choice["available_slot_levels"][0])
            if isinstance(slot_level, bool) or slot_level not in choice["available_slot_levels"]:
                raise CombatIntentError("the selected spell slot is not available")
            return
        attacker = self._actor_view(instance, combat, str(decision["attacker_id"]))
        choice = next(
            (
                row for row in self._reaction_feature_choices(combat, defender, attacker)
                if row["feature_id"] == intent.get("feature_id")
            ),
            None,
        )
        if choice is None:
            raise CombatIntentError("this reaction feature is not available")

    def _finish_hit_reaction(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        defender_id = str(decision["actor_id"])
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        armor_class = int(decision["armor_class"])
        if intent["option"] == "feature":
            choice = next(
                row for row in decision["features"]
                if row["feature_id"] == str(intent["feature_id"])
            )
            events.append({
                "type": "dnd2024.reaction.spent", "actor_id": defender_id,
                "reason": "reaction_feature",
            })
            damage_event = deepcopy(decision["damage_event"])
            amount = int(int(damage_event["amount"]) * float(choice["damage_multiplier"]))
            next_decision = decision.get("next_decision")
            if isinstance(next_decision, dict):
                next_decision = deepcopy(next_decision)
                held = next_decision.get("damage_event")
                if isinstance(held, dict):
                    # 后续命中触发法术（如至圣斩）持有同一份基础伤害；
                    # 减半必须发生在它被结算之前，否则半伤会丢失。
                    held["amount"] = amount
                    held["delta"] = -amount
                events.append({"type": "dnd2024.decision.pending", **next_decision})
                return events
            damage_event["amount"] = amount
            damage_event["delta"] = -amount
            events.extend(self._submit_hit_damage(damage_event))
            defender = self._actor_view(instance, combat, defender_id)
            events.extend(self._concentration_events(defender, amount, rng))
            return events
        if intent["option"] == "cast":
            defender = self._actor_view(instance, combat, defender_id)
            spell_ref = str(intent["spell_ref"])
            spell = self.spells.get(spell_ref)
            effect = self.catalog.spell_effects[spell_ref.removeprefix("spell:")]
            choice = next(
                row for row in decision["spells"] if row["spell_ref"] == spell_ref
            )
            slot_level = int(intent.get("slot_level", choice["available_slot_levels"][0]))
            bonus = int(effect["armor_class_bonus"])
            events.append({
                "type": "dnd2024.reaction.spent", "actor_id": defender_id,
                "reason": "reaction_spell",
            })
            events.extend(self._spell_cost_events(defender, spell, slot_level, spend_action=False))
            events.append({
                "type": "dnd2024.spell.cast", "actor_id": defender_id, "spell_ref": spell_ref,
                "slot_level": slot_level, "target_ids": [defender_id],
            })
            events.append({
                "type": "condition.applied", "target_id": defender_id,
                "condition": str(effect["condition"]),
                "duration": str(effect.get("condition_duration") or ""),
                "source_actor_id": defender_id, "armor_class_bonus": bonus,
            })
            armor_class += bonus
            hit = bool(decision.get("critical")) or int(decision["attack_total"]) >= armor_class
            events.append({
                "type": "check.resolved", "kind": "reaction_defense",
                "actor_id": str(decision["attacker_id"]), "target_id": defender_id,
                "spell_ref": spell_ref, "total": int(decision["attack_total"]),
                "target": armor_class, "success": hit,
                "critical": bool(decision.get("critical")),
            })
            if not hit:
                return events
        next_decision = decision.get("next_decision")
        if isinstance(next_decision, dict):
            events.append({"type": "dnd2024.decision.pending", **deepcopy(next_decision)})
            return events
        # Shield 没有改变结果（或只是被放弃）：命中成立，最终提交基础伤害，
        # 连同延迟记录的特性使用。
        damage_event = deepcopy(decision["damage_event"])
        events.extend(self._submit_hit_damage(damage_event))
        defender = self._actor_view(instance, combat, defender_id)
        events.extend(self._concentration_events(defender, int(damage_event["amount"]), rng))
        return events

    def _automatic_hit_reaction(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any], version: int,
    ) -> dict[str, Any] | None:
        kind, raw_id = actor_kind(str(decision["actor_id"]))
        if kind == "player" and not is_ai_controlled(instance, raw_id):
            return None
        base = {
            "intent_id": f"auto:hit-reaction:{version}:{decision['decision_id']}",
            "type": "decision.resolve",
            "expected_version": version,
            "submitted_by": str(getattr(instance, "gm_uid", "") or ""),
            "decision_id": str(decision["decision_id"]),
        }
        # 只有加 AC 能让这次攻击落空时才代为施放，避免白白消耗法术位；
        # 否则有减伤特性就代为使用（不花法术位），都没有才放弃。
        useful = [
            row for row in decision.get("spells") or []
            if not decision.get("critical")
            and int(decision["attack_total"]) < int(decision["armor_class"]) + int(row["armor_class_bonus"])
        ]
        if useful:
            choice = useful[0]
            return {
                **base, "option": "cast", "spell_ref": choice["spell_ref"],
                "slot_level": choice["available_slot_levels"][0],
            }
        features = decision.get("features") or []
        if features and int((decision.get("damage_event") or {}).get("amount", 0) or 0) > 0:
            return {**base, "option": "feature", "feature_id": features[0]["feature_id"]}
        return {**base, "option": "decline"}
