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
from .check_modes import condition_check_bonus_rolls, condition_flags
from .primitives import CombatIntentError, actor_kind, enemy_actor
from .triggered_spells import free_cast_resource


HIT_REACTION_DECISION = "hit_reaction_spell"
COUNTER_SPELL_DECISION = "counter_spell"

# 受击反应特性效果注册表：条目 = 参数名 + 声明的效果种类。
# 注册表之外不存在硬编码特性分支；新受击反应特性 = 注册表行 + 目录声明。
REACTION_FEATURE_EFFECTS: dict[str, dict[str, Any]] = {
    "uncanny_dodge": {
        "effect_kind": "damage_multiplier",
        "parameter": "damage_multiplier",
        "requires_sight": True,
    },
    "retaliation": {
        "effect_kind": "counterattack",
        "parameter": "counterattack_range",
        "requires_sight": False,
    },
}


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

    def _reaction_eligible(
        self, combat: dict[str, Any], actor: dict[str, Any],
    ) -> bool:
        """Whether ``actor`` may spend a reaction right now.

        Shared by reaction spells, hit-triggered features, counterspell,
        and opportunity attacks so every reaction path enforces the same
        HP / reaction-budget / incapacitated / reaction-blocked boundary at
        both window creation and final resolution.
        """

        if int(actor.get("hp", 0) or 0) <= 0:
            return False
        if int((combat.get("reactions") or {}).get(actor.get("actor_id"), 0) or 0) < 1:
            return False
        conditions = actor.get("conditions") or {}
        if "reaction_blocked" in conditions:
            return False
        if condition_flags(self.catalog.conditions, conditions)["incapacitated"]:
            return False
        return True

    def _reaction_spell_choices(
        self, combat: dict[str, Any], defender: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if defender["kind"] not in {"player", "companion"}:
            return []
        if "shield" in defender["conditions"]:
            return []
        if not self._reaction_eligible(combat, defender):
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
        damage_event: dict[str, Any] | None = None,
        next_decision: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Class features the defender may spend their reaction on when hit.

        The reaction budget and the "already reacted" guards are the same ones
        the reaction-spell path uses: both consume ``combat["reactions"]``, so a
        character can never react twice in the same round through two paths.
        Ownership comes from the feature boundary (class level), never from a
        localized name.

        Effects come from :data:`REACTION_FEATURE_EFFECTS`; every entry reads
        its own parameter and applies its own guards.  A damage multiplier
        changes the landed damage, so it needs to see the attack; a
        counterattack happens after the damage landed and needs the attacker
        within its declared range plus a positive amount.  Missing or invalid
        parameters fail closed (the choice is simply not offered).
        """

        if defender["kind"] not in {"player", "companion"}:
            return []
        if "shield" in defender["conditions"]:
            return []
        if not self._reaction_eligible(combat, defender):
            return []
        character = self._feature_character(defender)
        choices: list[dict[str, Any]] = []
        for feature_id, effect in REACTION_FEATURE_EFFECTS.items():
            if not self.features.has_feature(character, feature_id):
                continue
            value = self.features.feature_value(
                character, feature_id, effect["parameter"], 0,
            )
            if effect["effect_kind"] == "damage_multiplier":
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not 0 < float(value) < 1
                ):
                    continue
            elif effect["effect_kind"] == "counterattack":
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    continue
                if attacker is None or damage_event is None or next_decision is not None:
                    # 反击的前提是伤害已经落地：挂起的后续命中触发法术意味着
                    # 这份伤害还没有结算，此时不产出反击条目（时序矛盾）。
                    continue
                if int(damage_event.get("amount", 0) or 0) <= 0:
                    # 没有造成伤害就不触发（例如全额抗性后归零）。
                    continue
                if self._distance(
                    combat, defender["actor_id"], attacker["actor_id"],
                ) > int(value):
                    continue
            else:  # pragma: no cover - the registry is closed
                continue
            if effect.get("requires_sight"):
                if "blinded" in defender["conditions"]:
                    continue
                if attacker is not None and "invisible" in (attacker.get("conditions") or {}):
                    continue
            choice: dict[str, Any] = {
                "feature_id": feature_id,
                "name": self.features.catalog.label(
                    feature_id, feature_id.replace("_", " ").title(),
                ),
                "mode": "reaction_defense",
                "effect_kind": effect["effect_kind"],
            }
            if effect["effect_kind"] == "damage_multiplier":
                choice["damage_multiplier"] = float(value)
            else:
                choice["counterattack_range"] = int(value)
            choices.append(choice)
        return choices

    def _hit_reaction_offer(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        defender: dict[str, Any], check: dict[str, Any], damage_event: dict[str, Any],
        intent: dict[str, Any], next_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if attacker.get("side") == defender.get("side"):
            return None
        spells = self._reaction_spell_choices(combat, defender)
        features = self._reaction_feature_choices(
            combat, defender, attacker, damage_event, next_decision,
        )
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
                row for row in self._reaction_feature_choices(
                    combat, defender, attacker,
                    decision.get("damage_event"), decision.get("next_decision"),
                )
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
            if str(choice.get("effect_kind") or "") == "counterattack":
                return self._counterattack_events(
                    instance, combat, decision, rng, events,
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
            events.extend(self._enemy_defeated_feature_events(
                instance, combat, str(decision["attacker_id"]), defender, amount,
            ))
            return events
        if intent["option"] == "cast":
            defender = self._actor_view(instance, combat, defender_id)
            # 施放反应法术同样是一条 "spell_cast" 触发：被隐形术保护的防御者
            # 用护盾术反击时会终止自己的隐形术。
            events.extend(self._spell_end_trigger_events(defender, "spell_cast"))
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
        events.extend(self._enemy_defeated_feature_events(
            instance, combat, str(decision["attacker_id"]), defender,
            int(damage_event["amount"]),
        ))
        return events

    def _counterattack_events(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        rng: Any, events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Retaliation: land the full damage first, then make one melee attack.

        The counterattack is a complete melee attack resolved through the same
        ``_attack_events`` chain as every other attack: its own attack roll
        (with the shared advantage / bonus-die pipeline), its own damage dice,
        resistance, and bookkeeping.  Only its cost differs -- it spends the
        defender's reaction instead of the attack action, so the generic attack
        cost event is replaced in place (the opportunity-attack precedent).
        """

        defender_id = str(decision["actor_id"])
        attacker_id = str(decision["attacker_id"])
        damage_event = deepcopy(decision["damage_event"])
        amount = int(damage_event["amount"])
        events.extend(self._submit_hit_damage(damage_event))
        defender = self._actor_view(instance, combat, defender_id)
        events.extend(self._concentration_events(defender, amount, rng))
        events.extend(self._enemy_defeated_feature_events(
            instance, combat, attacker_id, defender, amount,
        ))
        hp = int(defender.get("hp", 0) or 0)
        temp_hp = max(0, int(defender.get("temp_hp", 0) or 0))
        hp_loss = max(0, amount - min(temp_hp, amount))
        if hp - hp_loss <= 0:
            # 伤害先扣除临时生命值；只有剩余伤害把防守者打到 0 HP 才倒地。
            # 倒在 0 HP 的防守者不能花反应反击，反应预算保留，也不发生攻击。
            return events
        melee_attacks = [
            attack for attack in self._available_attacks(defender)
            if int(attack.get("range") or 0) <= 5
        ]
        if not melee_attacks:
            # 没有近战档案的防守者无法反击：不臆造档案，也不支付反应。
            return events
        attack_intent: dict[str, Any] = {
            "intent_id": f"{decision['decision_id']}:retaliation",
            "actor_id": defender_id,
            "target_id": attacker_id,
            "weapon_ref": str(melee_attacks[0]["weapon_ref"]),
        }
        counterattack = self._attack_events(instance, combat, attack_intent, rng)
        cost_index = next(
            index for index, event in enumerate(counterattack)
            if event["type"] == "dnd2024.attack.spent"
        )
        counterattack[cost_index] = {
            "type": "dnd2024.reaction.spent", "actor_id": defender_id,
            "reason": "retaliation",
        }
        events.extend(counterattack)
        return events

    def _counter_spell_window(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Open the counter window when an enemy can interrupt this casting.

        The caster submits the cast; the decision payload keeps a server-side
        deep copy of the whole intent so the decline path can replay it and the
        counter path can inspect the cost.  Clients can never write it.
        """

        spell_ref = str(intent.get("spell_ref") or "")
        spell = self.spells.get(spell_ref)
        effect = self.catalog.spell_effects.get(spell_ref.removeprefix("spell:"))
        if spell is None or effect is None:
            return None
        if str(effect.get("mode") or "") in {"triggered_damage", "reaction_defense"}:
            return None
        if not (set(spell.get("components") or []) & {"V", "S", "M"}):
            return None
        caster_id = str(intent.get("actor_id") or "")
        threats = []
        for enemy_id, enemy in (combat.get("enemies") or {}).items():
            threat_id = enemy_actor(str(enemy_id))
            if int(enemy.get("hp", 0) or 0) <= 0:
                continue
            if int(enemy.get("counterspell_dc", 0) or 0) <= 0:
                continue
            threat = self._actor_view(instance, combat, threat_id)
            if not self._reaction_eligible(combat, threat):
                continue
            if self._distance(combat, caster_id, threat_id) > 60:
                continue
            threats.append(threat_id)
        if not threats:
            return None
        # 单威胁简化（RAW 允许多敌各自反应，结果冗余）：actor_id 排序取第一。
        threat_id = sorted(threats)[0]
        return {
            "decision_id": f"decision:{intent['intent_id']}",
            "kind": COUNTER_SPELL_DECISION,
            "assigned_to": str(getattr(instance, "gm_uid", "") or ""),
            "options": ["counter", "decline"],
            "threat_actor_ids": [threat_id],
            "cast_intent": deepcopy(intent),
        }

    def _finish_counter_spell(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        cast_intent = decision["cast_intent"]
        if intent["option"] != "counter":
            events.extend(self._spell_events(instance, combat, cast_intent, rng))
            return events
        threat_id = str(decision["threat_actor_ids"][0])
        caster_id = str(cast_intent["actor_id"])
        threat = self._actor_view(instance, combat, threat_id)
        if not self._reaction_eligible(combat, threat):
            # 窗口打开后威胁者失去反应资格（失能 / 禁反应 / 反应已耗尽 / 倒地）：
            # 反制无法兑现，按放弃处理并完整重放原施法。
            events.extend(self._spell_events(instance, combat, cast_intent, rng))
            return events
        events.append({
            "type": "dnd2024.reaction.spent", "actor_id": threat_id,
            "reason": "counterspell",
        })
        caster = self._actor_view(instance, combat, caster_id)
        dc = int(threat.get("counterspell_dc", 0) or 0)
        modifier = int(caster["saving_throws"].get("con", 0) or 0)
        auto_failed = "con" in condition_flags(
            self.catalog.conditions, caster["conditions"],
        )["auto_fail_saves"]
        if auto_failed:
            check_event = {
                "type": "check.resolved", "kind": "saving_throw",
                "actor_id": caster_id, "source_actor_id": threat_id,
                "ability": "con", "rolls": [], "natural": 0, "modifier": modifier,
                "check_bonus": 0, "check_bonus_rolls": [],
                "total": 0, "target": dc, "success": False,
                "auto_failed": True, "counterspell": True,
            }
            succeeded = False
        else:
            roll = int(rng.randint(1, 20))
            check_bonus_rolls = condition_check_bonus_rolls(
                self.catalog.conditions, caster["conditions"], "saving_throw", rng,
            )
            check_bonus = sum(row["roll"] for row in check_bonus_rolls)
            total = roll + modifier + check_bonus
            succeeded = total >= dc
            check_event = {
                "type": "check.resolved", "kind": "saving_throw",
                "actor_id": caster_id, "source_actor_id": threat_id,
                "ability": "con", "rolls": [roll], "natural": roll,
                "modifier": modifier,
                "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
                "total": total,
                "target": dc, "success": succeeded, "counterspell": True,
            }
        events.append(check_event)
        if succeeded:
            events.extend(self._spell_events(instance, combat, cast_intent, rng))
            return events
        # 反制失败：动作/附赠动作浪费，法术位与免费施放资源都不消耗（2024 规则）。
        spell = self.spells.get(str(cast_intent["spell_ref"]))
        events.append({
            "type": "dnd2024.action.spent", "actor_id": caster_id,
            "resource": "bonus_action"
            if str(spell["casting_time"]).startswith("Bonus Action")
            else "action",
            "amount": 1,
        })
        events.append({
            "type": "dnd2024.spell.countered", "actor_id": caster_id,
            "spell_ref": str(cast_intent["spell_ref"]), "countered_by": threat_id,
        })
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
            # 反击的期望收益高于减半（原始伤害全额落地、防守者可能存活），
            # 因此 AI 优先选择反击条目；存活守卫在结算内兜底，不会白花反应。
            choose = next(
                (row for row in features if row.get("effect_kind") == "counterattack"),
                features[0],
            )
            return {**base, "option": "feature", "feature_id": choose["feature_id"]}
        return {**base, "option": "decline"}
