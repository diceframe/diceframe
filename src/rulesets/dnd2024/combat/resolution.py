"""Deterministic event resolution stage for D&D 2024 combat."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.rulesets.dnd2024.character.builder import ability_modifier
from .action_adapter import (
    attack_damage_node,
    double_dice_counts,
    formula_node_from_dice_formula,
    heal_ability_modifier_node,
    roll_damage_node,
    spell_formula_node,
)
from .primitives import (
    CombatIntentError,
    actor_kind as _actor_kind,
    canonical as _canonical,
    enemy_actor as _enemy_actor,
    player_actor as _player_actor,
)


class CombatResolutionMixin:
    """Turn validated intents and server RNG into immutable event batches."""

    __slots__ = ()

    def _start_combat_event(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        enemies: dict[str, dict[str, Any]] = {}
        initiatives: list[dict[str, Any]] = []
        positions: dict[str, int] = {}
        for uid in sorted(instance.players):
            canonical = _canonical(instance.get_character_sheet(uid))
            actor_id = _player_actor(uid)
            roll = int(rng.randint(1, 20))
            modifier = int(canonical.get("derived", {}).get("initiative", 0) or 0)
            initiatives.append({
                "actor_id": actor_id, "roll": roll, "modifier": modifier,
                "total": roll + modifier, "kind": "player",
            })
            positions[actor_id] = int(intent.get("player_positions", {}).get(uid, 0) or 0)
        for raw in intent["enemies"]:
            enemy_id = str(raw["id"])
            actor_id = _enemy_actor(enemy_id)
            initiative_roll = int(rng.randint(1, 20))
            initiative_modifier = int(raw.get("initiative_modifier", 0) or 0)
            enemy = {
                "id": enemy_id, "name": str(raw.get("name") or enemy_id),
                "hp": int(raw["hp"]), "max_hp": int(raw["hp"]),
                "armor_class": int(raw["armor_class"]),
                "speed": int(raw.get("speed", 30) or 30),
                "abilities": deepcopy(raw.get("abilities") or {}),
                "saving_throws": deepcopy(raw.get("saving_throws") or {}),
                "attacks": deepcopy(raw["attacks"]), "conditions": {},
            }
            enemies[enemy_id] = enemy
            initiatives.append({
                "actor_id": actor_id, "roll": initiative_roll,
                "modifier": initiative_modifier, "total": initiative_roll + initiative_modifier,
                "kind": "enemy",
            })
            positions[actor_id] = int(raw.get("position", 30) or 30)
        initiatives.sort(key=lambda row: (-int(row["total"]), row["kind"] != "player", row["actor_id"]))
        order = [row["actor_id"] for row in initiatives]
        first = self._actor_view_from_data(instance, enemies, order[0])
        return {
            "type": "dnd2024.combat.started", "round": 1, "initiative": order,
            "initiative_rolls": initiatives, "enemies": enemies, "positions": positions,
            "economy": self._fresh_economy(first), "position_mode": "theater",
            "reactions": {actor_id: 1 for actor_id in order},
            "encounter_instance_id": str(intent.get("encounter_instance_id") or ""),
            "encounter_preset_id": str(intent.get("encounter_preset_id") or ""),
            "origin_step_id": str(intent.get("origin_step_id") or ""),
        }

    def _attack_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(intent["actor_id"])
        target_id = str(intent["target_id"])
        actor = self._actor_view(instance, combat, actor_id)
        target = self._actor_view(instance, combat, target_id)
        if actor["kind"] == "player":
            weapon_id = str(intent["weapon_ref"]).removeprefix("item:")
            weapon = self.catalog.weapons[weapon_id]
            distance = self._distance(combat, actor_id, target_id)
            ranged_use = bool(weapon.get("ranged")) or distance > 5
            ability = "dex" if ranged_use else "str"
            if weapon.get("finesse"):
                ability = max(("str", "dex"), key=lambda key: ability_modifier(actor["abilities"][key]))
            modifier = ability_modifier(actor["abilities"][ability])
            proficiency = (
                actor["proficiency_bonus"]
                if f"weapon_category:{weapon['category']}" in actor["weapon_category_refs"] else 0
            )
            attack_bonus = modifier + proficiency
            damage_formula = str(weapon["damage"])
            damage_type = str(weapon["damage_type"])
            long_range = int(weapon.get("long_range") or weapon.get("thrown_range") or weapon["range"])
            normal_range = int(weapon.get("thrown_range") or weapon["range"])
            range_disadvantage = distance > normal_range and distance <= long_range
        else:
            weapon = next(item for item in actor["attacks"] if item["id"] == intent["attack_id"])
            modifier = int(weapon.get("damage_modifier", 0) or 0)
            attack_bonus = int(weapon["attack_bonus"])
            damage_formula = str(weapon["damage"])
            damage_type = str(weapon.get("damage_type") or "bludgeoning")
            distance = self._distance(combat, actor_id, target_id)
            range_disadvantage = distance > int(weapon.get("range", 5) or 5)
        advantage, disadvantage = self._attack_modes(actor, target, range_disadvantage)
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if advantage or disadvantage else None
        natural = max(first, second) if advantage and second else min(first, second) if disadvantage and second else first
        bless_bonus = 0
        if "blessed" in actor["conditions"]:
            bless_bonus = int(rng.randint(1, 4))
        total = natural + attack_bonus + bless_bonus
        target_ac = target["armor_class"] + (2 if "shield_of_faith" in target["conditions"] else 0)
        critical = natural == 20
        hit = natural != 1 and (critical or total >= target_ac)
        events: list[dict[str, Any]] = [self._attack_cost_event(combat, actor)]
        events.append({
            "type": "check.resolved", "kind": "attack", "actor_id": actor_id,
            "target_id": target_id, "rolls": [value for value in (first, second) if value],
            "natural": natural, "modifier": attack_bonus, "bless_bonus": bless_bonus,
            "total": total, "target": target_ac, "success": hit, "critical": critical,
        })
        if hit:
            # 伤害骰经通用公式 AST 求值（Issue 212）：主骰 → 附加骰（魔化）
            # → 属性修正；重击翻倍仍由本层在 AST 上显式表达。
            extra_dice = None
            if "hexed" in target["conditions"] and target["conditions"]["hexed"].get(
                "source_actor_id"
            ) == actor_id:
                extra_dice = "1d6"
            damage_node = attack_damage_node(
                damage_formula=damage_formula,
                modifier=modifier,
                critical=critical,
                extra_dice_formula=extra_dice,
            )
            damage, rolls = roll_damage_node(damage_node, rng)
            damage = max(0, damage)
            events.append({
                "type": "resource.changed", "resource": "hp", "target_id": target_id,
                "delta": -damage, "amount": damage, "damage_type": damage_type,
                "critical": critical, "rolls": rolls,
            })
            events.extend(self._concentration_events(target, damage, rng))
            if self._last_hostile_defeated(combat, target, damage):
                events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        events.extend(self._consume_attack_conditions(actor_id, target_id, actor, target))
        return events
    def _spell_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        spell_ref = str(intent["spell_ref"])
        spell = self.spells.get(spell_ref)
        if spell is None:  # pragma: no cover - validation guards this
            raise CombatIntentError("spell is missing")
        effect = self.catalog.spell_effects[spell_ref.removeprefix("spell:")]
        slot_level = int(intent.get("slot_level", spell["level"]) or 0)
        target_ids = intent.get("target_ids")
        if not isinstance(target_ids, list):
            target_ids = [intent.get("target_id")]
        target_ids = [str(item) for item in target_ids if item]
        action_cost = (
            "bonus_action" if str(spell["casting_time"]).startswith("Bonus Action") else "action"
        )
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.action.spent", "actor_id": actor_id,
            "resource": action_cost, "amount": 1,
        }]
        if spell["level"] > 0:
            events.append({
                "type": "dnd2024.spell.slot_spent", "actor_id": actor_id,
                "slot_level": slot_level, "amount": 1,
            })
        if effect.get("concentration"):
            if actor.get("concentration"):
                events.append({
                    "type": "dnd2024.concentration.ended", "actor_id": actor_id,
                    "reason": "new_concentration_spell",
                })
            events.append({
                "type": "dnd2024.concentration.started", "actor_id": actor_id,
                "spell_ref": spell_ref, "target_ids": target_ids,
            })
        events.append({
            "type": "dnd2024.spell.cast", "actor_id": actor_id, "spell_ref": spell_ref,
            "slot_level": slot_level, "target_ids": target_ids,
        })
        defeated_targets: set[str] = set()
        for target_id in target_ids:
            target = self._actor_view(instance, combat, target_id)
            mode = str(effect["mode"])
            succeeded = False
            if mode == "spell_attack":
                roll = int(rng.randint(1, 20))
                bonus = int(actor["spell_attack_bonus"])
                total = roll + bonus
                succeeded = roll != 1 and (roll == 20 or total >= target["armor_class"])
                events.append({
                    "type": "check.resolved", "kind": "spell_attack", "actor_id": actor_id,
                    "target_id": target_id, "rolls": [roll], "natural": roll,
                    "modifier": bonus, "total": total, "target": target["armor_class"],
                    "success": succeeded, "critical": roll == 20,
                })
                if succeeded:
                    damage_node = spell_formula_node(
                        str(effect["damage"]), effect.get("upcast_damage"),
                        spell, slot_level, actor,
                    )
                    if roll == 20:
                        damage_node = double_dice_counts(damage_node)
                    damage, rolls = roll_damage_node(damage_node, rng)
                    events.append({
                        "type": "resource.changed", "resource": "hp", "target_id": target_id,
                        "delta": -damage, "amount": damage,
                        "damage_type": str(intent.get("damage_type") or effect.get("damage_type")),
                        "critical": roll == 20, "rolls": rolls,
                    })
                    events.extend(self._concentration_events(target, damage, rng))
                    if self._last_hostile_defeated(combat, target, damage):
                        defeated_targets.add(target_id)
            elif mode == "saving_throw":
                save_ability = str(effect["save"])
                roll = int(rng.randint(1, 20))
                bless = int(rng.randint(1, 4)) if "blessed" in target["conditions"] else 0
                modifier = int(target["saving_throws"].get(save_ability, 0) or 0)
                total = roll + modifier + bless
                succeeded = total >= int(actor["spell_save_dc"])
                events.append({
                    "type": "check.resolved", "kind": "saving_throw", "actor_id": target_id,
                    "source_actor_id": actor_id, "ability": save_ability, "rolls": [roll],
                    "natural": roll, "modifier": modifier, "bless_bonus": bless, "total": total,
                    "target": actor["spell_save_dc"], "success": succeeded,
                })
                if effect.get("damage"):
                    damage_node = spell_formula_node(
                        str(effect["damage"]), effect.get("upcast_damage"),
                        spell, slot_level, actor,
                    )
                    damage, rolls = roll_damage_node(damage_node, rng)
                    if succeeded:
                        damage = damage // 2 if effect.get("half_on_success") else 0
                    if damage:
                        events.append({
                            "type": "resource.changed", "resource": "hp",
                            "target_id": target_id, "delta": -damage, "amount": damage,
                            "damage_type": str(effect.get("damage_type") or "force"),
                            "critical": False, "rolls": rolls,
                        })
                        events.extend(self._concentration_events(target, damage, rng))
                        if self._last_hostile_defeated(combat, target, damage):
                            defeated_targets.add(target_id)
            elif mode == "automatic_damage":
                damage_node = spell_formula_node(
                    str(effect["damage"]), effect.get("upcast_damage"),
                    spell, slot_level, actor,
                )
                damage, rolls = roll_damage_node(damage_node, rng)
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": target_id,
                    "delta": -damage, "amount": damage,
                    "damage_type": str(effect.get("damage_type") or "force"),
                    "critical": False, "rolls": rolls,
                })
                events.extend(self._concentration_events(target, damage, rng))
                if self._last_hostile_defeated(combat, target, damage):
                    defeated_targets.add(target_id)
                succeeded = True
            elif mode == "healing":
                healing_node = spell_formula_node(
                    str(effect["healing"]), effect.get("upcast_healing"),
                    spell, slot_level, actor,
                )
                if effect.get("add_spell_ability"):
                    healing_node = heal_ability_modifier_node(healing_node, actor)
                healing, rolls = roll_damage_node(healing_node, rng)
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": target_id,
                    "delta": max(1, healing), "amount": max(1, healing), "healing": True,
                    "rolls": rolls,
                })
                succeeded = True
            elif mode in {"buff", "debuff"}:
                succeeded = True
            condition = str(effect.get("condition") or "")
            condition_applies = (
                (mode in {"spell_attack", "automatic_damage"} and succeeded)
                or (mode == "saving_throw" and not succeeded)
                or mode in {"buff", "debuff"}
            )
            if condition and condition_applies:
                events.append({
                    "type": "condition.applied", "target_id": target_id,
                    "condition": condition, "duration": str(effect.get("condition_duration") or ""),
                    "source_actor_id": actor_id,
                    "concentration_owner": actor_id if effect.get("concentration") else "",
                })
            if succeeded and int(effect.get("push", 0) or 0):
                direction = 1 if self._position(combat, target_id) >= self._position(combat, actor_id) else -1
                events.append({
                    "type": "dnd2024.position.changed", "actor_id": target_id,
                    "distance": direction * int(effect["push"]), "forced": True,
                })
        living_enemy_ids = {
            _enemy_actor(enemy_id)
            for enemy_id, enemy in combat.get("enemies", {}).items()
            if int(enemy.get("hp", 0) or 0) > 0
        }
        if living_enemy_ids and living_enemy_ids.issubset(defeated_targets):
            events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        return events

    def _movement_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        actor_id = str(intent["actor_id"])
        distance = int(intent["distance"])
        economy = combat.get("economy") or {}
        adjacent_hostiles = [
            target for target in self._hostile_targets(instance, combat, actor_id)
            if self._distance(combat, actor_id, target["actor_id"]) <= 5
            and self._distance_after_move(combat, actor_id, target["actor_id"], distance) > 5
        ]
        actor = self._actor_view(instance, combat, actor_id)
        disengaged = bool(economy.get("disengaged")) or "disengaged" in actor["conditions"]
        if adjacent_hostiles and not disengaged:
            decision = {
                "decision_id": f"decision:{intent['intent_id']}",
                "kind": "opportunity_attack",
                "assigned_to": str(getattr(instance, "gm_uid", "") or ""),
                "options": ["resolve", "decline"],
                "threat_actor_ids": [item["actor_id"] for item in adjacent_hostiles],
                "movement": {"actor_id": actor_id, "distance": distance},
            }
            return decision, [{"type": "dnd2024.decision.pending", **decision}]
        return None, [{
            "type": "dnd2024.position.changed", "actor_id": actor_id,
            "distance": distance, "movement_cost": abs(distance), "forced": False,
        }]

    @staticmethod
    def _basic_action_events(
        combat: dict[str, Any], intent_type: str, actor_id: str,
    ) -> list[dict[str, Any]]:
        events = [{
            "type": "dnd2024.action.spent", "actor_id": actor_id,
            "resource": "action", "amount": 1,
        }]
        if intent_type == "dash":
            speed = int(combat.get("economy", {}).get("speed", 0) or 0)
            events.append({
                "type": "dnd2024.movement.granted", "actor_id": actor_id, "amount": speed,
            })
        else:
            events.append({
                "type": "condition.applied", "target_id": actor_id,
                "condition": "dodging" if intent_type == "dodge" else "disengaged",
                "duration": "actor_turn_start", "source_actor_id": actor_id,
            })
        return events

    def _end_turn_events(self, instance: Any, combat: dict[str, Any]) -> list[dict[str, Any]]:
        order = list(combat["initiative"])
        if not any(
            int(enemy.get("hp", 0) or 0) > 0
            for enemy in combat.get("enemies", {}).values()
        ):
            return [{"type": "dnd2024.combat.ended", "reason": "victory"}]
        player_conditions = [
            self._actor_view(instance, combat, actor_id)["conditions"]
            for actor_id in order if actor_id.startswith("player:")
        ]
        if not any(
            "dead" not in conditions and "stable" not in conditions
            for conditions in player_conditions
        ):
            reason = (
                "party_incapacitated"
                if any("stable" in conditions for conditions in player_conditions)
                else "party_defeated"
            )
            return [{"type": "dnd2024.combat.ended", "reason": reason}]
        previous_index = int(combat["turn_index"])
        next_index = previous_index
        wraps = 0
        for _ in range(len(order)):
            next_index = (next_index + 1) % len(order)
            if next_index == 0:
                wraps += 1
            candidate = self._actor_view(instance, combat, order[next_index])
            if candidate["kind"] == "enemy" and candidate["hp"] <= 0:
                continue
            if "dead" in candidate["conditions"] or "stable" in candidate["conditions"]:
                continue
            break
        next_round = int(combat["round"]) + wraps
        next_actor = order[next_index]
        next_view = self._actor_view(instance, combat, next_actor)
        return [{
            "type": "dnd2024.turn.advanced", "round": next_round,
            "turn_index": next_index, "actor_id": next_actor,
            "previous_actor_id": order[previous_index],
            "economy": self._fresh_economy(next_view),
        }]

    def _death_save_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        roll = int(rng.randint(1, 20))
        successes = int(actor["death_saves"].get("successes", 0) or 0)
        failures = int(actor["death_saves"].get("failures", 0) or 0)
        hp = 0
        if roll == 20:
            hp = 1
            successes = failures = 0
        elif roll == 1:
            failures += 2
        elif roll >= 10:
            successes += 1
        else:
            failures += 1
        stable = successes >= 3
        dead = failures >= 3
        return [
            {
                "type": "check.resolved", "kind": "death_save", "actor_id": actor_id,
                "rolls": [roll], "natural": roll, "target": 10, "success": roll >= 10,
            },
            {
                "type": "dnd2024.death_save.resolved", "actor_id": actor_id,
                "roll": roll, "successes": min(successes, 3), "failures": min(failures, 3),
                "stable": stable, "dead": dead, "hp": hp,
            },
            *([] if dead or hp else self._end_turn_events(instance, combat)),
        ]

    def _stabilize_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor = self._actor_view(instance, combat, str(intent["actor_id"]))
        target_id = str(intent["target_id"])
        roll = int(rng.randint(1, 20))
        medicine = int(actor["skill_values"].get("medicine", 0) or 0)
        success = roll + medicine >= 10
        events = [{
            "type": "dnd2024.action.spent", "actor_id": actor["actor_id"],
            "resource": "action", "amount": 1,
        }, {
            "type": "check.resolved", "kind": "medicine", "actor_id": actor["actor_id"],
            "target_id": target_id, "rolls": [roll], "natural": roll,
            "modifier": medicine, "total": roll + medicine, "target": 10,
            "success": success,
        }]
        if success:
            events.append({
                "type": "condition.applied", "target_id": target_id,
                "condition": "stable", "duration": "until_healed",
                "source_actor_id": actor["actor_id"],
            })
        return events

    def _decision_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        decision = next(
            item for item in combat.get("pending_decisions", [])
            if item.get("decision_id") == intent["decision_id"]
        )
        movement = decision["movement"]
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        if intent["option"] == "resolve":
            threat_id = str(decision["threat_actor_ids"][0])
            threat = self._actor_view(instance, combat, threat_id)
            attack = threat["attacks"][0]
            attack_intent = {
                "actor_id": threat_id,
                "target_id": str(movement["actor_id"]),
                "attack_id": str(attack["id"]),
            }
            opportunity = self._attack_events(instance, combat, attack_intent, rng)
            opportunity[0] = {
                "type": "dnd2024.reaction.spent", "actor_id": threat_id,
                "reason": "opportunity_attack",
            }
            events.extend(opportunity)
        events.append({
            "type": "dnd2024.position.changed", "actor_id": movement["actor_id"],
            "distance": int(movement["distance"]),
            "movement_cost": abs(int(movement["distance"])), "forced": False,
        })
        return events
