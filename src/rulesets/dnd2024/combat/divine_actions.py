"""Target validation and settlement for Channel Divinity and Lay On Hands actions.

The capability registry still selects the handler and the common capability
flow owns shared prerequisites and payment. This mixin implements the divine
actions' target rules and effects using the existing combat authority.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from ..character.builder import ability_modifier
from ..features.resources import resource_state
from .action_adapter import formula_node_from_dice_formula, maximize_dice_nodes, roll_damage_node
from .primitives import (
    ABJURE_FOES_DURATION,
    DIVINE_SENSE_CONDITION,
    DIVINE_SENSE_DURATION,
    TURN_UNDEAD_DURATION,
    TURN_UNDEAD_MAX_ROUNDS,
    CombatIntentError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..features.resolver import Dnd2024ClassFeatureResolver


class CombatDivineActionsMixin:
    __slots__ = ()

    if TYPE_CHECKING:
        # Supplied by the combat engine and its other mixins; no runtime state.
        features: Dnd2024ClassFeatureResolver
        _actor_view: Callable[[Any, dict[str, Any], str], dict[str, Any]]
        _canonical_actor: Callable[[Any, dict[str, Any]], dict[str, Any]]
        _canonical_actor_character: Callable[[Any, str], dict[str, Any]]
        _feature_character: Callable[[dict[str, Any]], dict[str, Any]]
        _require_range: Callable[[dict[str, Any], str, str, int], None]
        _spell_target_visible: Callable[[Any, dict[str, Any], dict[str, Any], dict[str, Any]], bool]
        _logical_minutes: Callable[[Any], int]
        _strength_total_replacement: Callable[[dict[str, Any], str, int], tuple[int, dict[str, Any]]]
        _resist_damage: Callable[[dict[str, Any], int, str], tuple[int, str]]
        _relentless_rage_marker: Callable[[Any, dict[str, Any]], dict[str, Any] | None]
        _condition_immunity_blocker: Callable[[Any, dict[str, Any], dict[str, Any], str], dict[str, str] | None]
        _concentration_events: Callable[[Any, dict[str, Any], dict[str, Any], int, Any], list[dict[str, Any]]]
        _enemy_zero_hp_feature_events: Callable[[Any, dict[str, Any], str, dict[str, Any], int], list[dict[str, Any]]]
        _last_hostile_defeated: Callable[[dict[str, Any], dict[str, Any], int], bool]

    def _capability_range(
        self, actor: dict[str, Any], capability: Any, key: str, default: int,
    ) -> int:
        """The declared reach of one capability, in feet.

        Range comes from the feature declaration, never from the intent: a
        client that sends a friendlier number must not be able to widen it.
        The declaring feature is read (not the capability id) because the
        Channel Divinity options are capabilities *of* one feature, and their
        numbers live in that feature's parameters under a prefixed key.
        """

        range_ft = self.features.feature_value(
            self._feature_character(actor), capability.feature_id, key, default,
        )
        return int(range_ft) if isinstance(range_ft, int) and range_ft > 0 else default

    def _validate_divine_spark(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Divine Spark: another living creature, 30 feet, one of two modes."""

        target_id = str(intent.get("target_id") or "")
        if not target_id:
            raise CombatIntentError("Divine Spark requires a target")
        if target_id == str(actor["actor_id"]):
            raise CombatIntentError("Divine Spark cannot target yourself")
        target = self._actor_view(instance, combat, target_id)
        if target["hp"] <= 0:
            raise CombatIntentError("Divine Spark targets a living creature")
        self._require_range(
            combat, str(actor["actor_id"]), target_id,
            self._capability_range(actor, capability, "spark_target_range_ft", 30),
        )
        mode = str(intent.get("spark_mode") or "")
        if mode not in {"heal", "damage"}:
            raise CombatIntentError("Divine Spark must be declared as heal or damage")
        damage_type = str(intent.get("damage_type") or "")
        allowed = [
            str(kind) for kind in (
                self.features.feature_value(
                    self._feature_character(actor), capability.feature_id,
                    "spark_damage_types", ["necrotic", "radiant"],
                ) or []
            )
        ]
        if mode == "damage":
            if damage_type not in allowed:
                raise CombatIntentError(
                    "Divine Spark damage must be one of its declared damage types"
                )
        elif damage_type:
            raise CombatIntentError("Divine Spark healing does not choose a damage type")

    def _validate_turn_undead(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Turn Undead: a de-duplicated set of Undead inside the declared reach."""

        raw = intent.get("target_ids")
        if not isinstance(raw, list) or not raw:
            raise CombatIntentError("Turn Undead requires at least one Undead target")
        targets = [str(item) for item in raw]
        if len(set(targets)) != len(targets):
            raise CombatIntentError("Turn Undead targets must not repeat")
        limit = self._capability_range(actor, capability, "turn_target_range_ft", 30)
        for target_id in targets:
            target = self._actor_view(instance, combat, target_id)
            if target["hp"] <= 0:
                raise CombatIntentError("Turn Undead targets a living Undead")
            if "undead" not in str(target.get("creature_type") or "").lower():
                raise CombatIntentError("Turn Undead targets only Undead creatures")
            self._require_range(combat, str(actor["actor_id"]), target_id, limit)
        if intent.get("sear_undead") not in (None, False):
            if intent.get("sear_undead") is not True:
                raise CombatIntentError("sear_undead must be a boolean")
            if not self.features.has_feature(
                self._feature_character(actor), "sear_undead",
            ):
                raise CombatIntentError("sear undead is not available to this actor")

    def _validate_abjure_foes(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Abjure Foes: exactly Charisma-modifier creatures you can see.

        "Exactly" is the rule, not a convenience: the feature targets *a number
        of creatures equal to* the modifier, so a shorter list is as illegal as
        a longer one, and refusing the whole intent is the only rejection that
        cannot leave a partially-paid Channel use behind.
        """

        raw = intent.get("target_ids")
        if not isinstance(raw, list) or not raw:
            raise CombatIntentError("Abjure Foes requires at least one target")
        targets = [str(item) for item in raw]
        if len(set(targets)) != len(targets):
            raise CombatIntentError("Abjure Foes targets must not repeat")
        character = self._feature_character(actor)
        ability = str(self.features.feature_value(
            character, capability.feature_id, "target_count_ability", "cha",
        ) or "cha")
        minimum = int(self.features.feature_value(
            character, capability.feature_id, "target_count_minimum", 1,
        ) or 1)
        count = max(
            minimum,
            ability_modifier(int((actor.get("abilities") or {}).get(ability, 10) or 10)),
        )
        if len(targets) != count:
            raise CombatIntentError(
                f"Abjure Foes must target exactly {count} creatures"
            )
        limit = self._capability_range(actor, capability, "target_range_ft", 60)
        for target_id in targets:
            if target_id == str(actor["actor_id"]):
                raise CombatIntentError("Abjure Foes cannot target yourself")
            target = self._actor_view(instance, combat, target_id)
            if target["hp"] <= 0:
                raise CombatIntentError("Abjure Foes targets a living creature")
            self._require_range(combat, str(actor["actor_id"]), target_id, limit)
            if not self._spell_target_visible(instance, combat, actor, target):
                raise CombatIntentError("Abjure Foes targets creatures you can see")

    def _validate_preserve_life(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Preserve Life: a split of one budget, capped by each target's half HP.

        Both ceilings are checked here because both are facts the server owns:
        the budget is 5 x Cleric level for *this* action, and a creature can
        never be restored above half its Hit Point maximum by this feature.
        """

        rows = intent.get("allocations")
        if not isinstance(rows, list) or not rows:
            raise CombatIntentError("Preserve Life requires at least one allocation")
        character = self._feature_character(actor)
        per_level = self.features.feature_value(
            character, capability.id, "preserve_life_per_level", 5,
        )
        state = self.features.class_state(character)
        level = int(state[1]) if state else 0
        budget = int(per_level or 5) * level
        limit = self._capability_range(actor, capability, "target_range_ft", 30)
        seen: set[str] = set()
        total = 0
        for row in rows:
            if not isinstance(row, dict):
                raise CombatIntentError("each Preserve Life allocation must name a target")
            target_id = str(row.get("target_id") or "")
            amount = row.get("amount")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise CombatIntentError("a Preserve Life amount must be a non-negative integer")
            if target_id in seen:
                raise CombatIntentError("a Preserve Life target may appear only once")
            seen.add(target_id)
            target = self._actor_view(instance, combat, target_id)
            if target["hp"] <= 0:
                raise CombatIntentError("Preserve Life targets a living creature")
            self._require_range(combat, str(actor["actor_id"]), target_id, limit)
            maximum = int(target["max_hp"])
            if int(target["hp"]) * 2 > maximum:
                raise CombatIntentError(
                    "Preserve Life targets only Bloodied creatures"
                )
            ceiling = max(0, maximum // 2 - int(target["hp"]))
            if amount > ceiling:
                raise CombatIntentError(
                    "Preserve Life cannot restore a creature above half its Hit Points"
                )
            total += amount
        if total > budget:
            raise CombatIntentError(
                "the Preserve Life allocation exceeds this use's budget"
            )
        if total <= 0:
            raise CombatIntentError("Preserve Life requires a positive allocation")

    def _lay_on_hands_conditions(
        self, actor: dict[str, Any], capability_id: str,
    ) -> tuple[dict[str, int], int]:
        """Conditions this Paladin may pay to remove, and the price of each.

        Restoring Touch (level 14) is a second declaration over the same pool,
        so the allowed set is the union of what each owned feature names.  The
        base list is never edited in place: a character without the feature
        simply has the shorter set.
        """

        character = self._feature_character(actor)
        cost = int(self.features.feature_value(
            character, capability_id, "condition_cost", 5,
        ) or 5)
        allowed: dict[str, int] = {
            str(code): cost
            for code in (self.features.feature_value(
                character, "lay_on_hands", "removable_conditions", ["poisoned"],
            ) or [])
        }
        for code in self.features.feature_value(
            character, "restoring_touch", "removable_conditions", [],
        ) or []:
            allowed[str(code)] = int(self.features.feature_value(
                character, "restoring_touch", "condition_cost", 5,
            ) or 5)
        return allowed, cost

    def _validate_lay_on_hands(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Lay On Hands: one touched creature, a pool that pays, conditions owned."""

        target_id = str(intent.get("target_id") or "")
        if not target_id:
            raise CombatIntentError("Lay On Hands requires a target")
        target = self._actor_view(instance, combat, target_id)
        if "dead" in (target.get("conditions") or {}):
            raise CombatIntentError("Lay On Hands cannot restore a dead creature")
        self._require_range(
            combat, str(actor["actor_id"]), target_id,
            self._capability_range(actor, capability, "target_range_ft", 5),
        )
        amount = intent.get("healing_amount", 0)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise CombatIntentError("healing_amount must be a non-negative integer")
        raw_conditions = intent.get("remove_conditions") or []
        if not isinstance(raw_conditions, list):
            raise CombatIntentError("remove_conditions must be an array")
        conditions = [str(code) for code in raw_conditions]
        if len(set(conditions)) != len(conditions):
            raise CombatIntentError("a condition may be removed only once per use")
        allowed, _base_cost = self._lay_on_hands_conditions(actor, capability.id)
        held = target.get("conditions") or {}
        total = amount
        for condition_id in conditions:
            if condition_id not in allowed:
                raise CombatIntentError(
                    "this Paladin cannot remove that condition with Lay On Hands"
                )
            if condition_id not in held:
                # 目标身上没有这个状态时拒绝，而不是照扣 5 点却什么也没移除。
                raise CombatIntentError(
                    "the target does not have the condition being removed"
                )
            total += allowed[condition_id]
        if total <= 0:
            raise CombatIntentError(
                "Lay On Hands needs healing or a condition to remove"
            )
        character = self._canonical_actor(instance, actor)
        pool = resource_state(character, "lay_on_hands")
        if int(pool.get("current", 0) or 0) < total:
            raise CombatIntentError("the Lay On Hands pool is too small for that use")

    def _spark_die_count(
        self, character: Any, feature_id: str,
    ) -> int:
        """How many d8 Divine Spark rolls at this Cleric level.

        The tiers are declared as two parallel arrays (levels and dice) because
        the feature catalog only carries scalars; the level is the *declaring
        class's* level, so a Cleric 6 / Fighter 14 rolls one d8, not four.
        """

        levels = self.features.feature_value(
            character, feature_id, "spark_tier_levels", [1],
        ) or []
        counts = self.features.feature_value(
            character, feature_id, "spark_tier_counts", [1],
        ) or []
        state = self.features.class_state(character)
        level = int(state[1]) if state else 0
        count = 1
        best = -1
        for threshold, amount in zip(levels, counts):
            try:
                gate = int(threshold)
                dice = int(amount)
            except (TypeError, ValueError):
                continue
            if level >= gate and gate > best:
                best = gate
                count = max(1, dice)
        return count

    def _divine_spark_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        actor: dict[str, Any], capability: Any, intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Divine Spark: one d8 per tier plus Wisdom, as healing or as damage.

        Both modes roll the same dice.  Only the healing mode is maximised by
        Supreme Healing -- the damage mode is damage, and the Wisdom modifier
        is a flat bonus in both, so "maximise the dice" never becomes
        "maximise the total".
        """

        target_id = str(intent.get("target_id") or "")
        target = self._actor_view(instance, combat, target_id)
        character = self._feature_character(actor)
        feature_id = capability.feature_id
        die = str(self.features.feature_value(
            character, feature_id, "spark_die", "d8",
        ) or "d8")
        count = self._spark_die_count(character, feature_id)
        ability = str(self.features.feature_value(
            character, feature_id, "spark_ability", "wis",
        ) or "wis")
        bonus = ability_modifier(
            int((actor.get("abilities") or {}).get(ability, 10) or 10)
        )
        minimum = int(self.features.feature_value(
            character, feature_id, "spark_minimum", 1,
        ) or 1)
        node: dict[str, Any] = formula_node_from_dice_formula(f"{count}{die}")
        if bonus:
            node = {"op": "add", "args": [
                node, {"op": "constant", "value": bonus},
            ]}
        if str(intent.get("spark_mode") or "") == "heal":
            maximized = self.features.has_feature(
                character, "supreme_healing", owner="subclass:life_domain",
            )
            if maximized:
                node = maximize_dice_nodes(node)
            amount, rolls = roll_damage_node(node, rng)
            amount = max(minimum, amount)
            blocked = "cannot_regain_hp" in (target.get("conditions") or {})
            return [{
                "type": "resource.changed", "resource": "hp", "target_id": target_id,
                "delta": 0 if blocked else amount,
                "amount": 0 if blocked else amount,
                "healing": True, "rolls": rolls,
                "feature_id": feature_id, "channel_effect": "divine_spark",
                **({"maximized": True} if maximized else {}),
                **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
            }]
        amount, rolls = roll_damage_node(node, rng)
        save_ability = str(self.features.feature_value(
            character, feature_id, "spark_save_ability", "con",
        ) or "con")
        dc = int(actor.get("spell_save_dc", 0) or 0)
        roll = int(rng.randint(1, 20))
        modifier = int((target.get("saving_throws") or {}).get(save_ability, 0) or 0)
        total = roll + modifier
        total, replacement = self._strength_total_replacement(
            target, save_ability, total,
        )
        saved = total >= dc
        events: list[dict[str, Any]] = [{
            "type": "check.resolved", "kind": "saving_throw",
            "actor_id": target_id, "ability": save_ability,
            "rolls": [roll], "natural": roll, "modifier": modifier,
            "total": total, "target": dc, "success": saved,
            "feature_id": feature_id, "channel_effect": "divine_spark",
            **replacement,
        }]
        if saved:
            # "half as much damage" rounds down, and resistance applies after
            # the halving -- the same order every save-for-half spell uses.
            amount //= 2
        damage_type = str(intent.get("damage_type") or "")
        amount, resistance = self._resist_damage(target, amount, damage_type)
        if amount <= 0:
            return events
        damage_event: dict[str, Any] = {
            "type": "resource.changed", "resource": "hp", "target_id": target_id,
            "delta": -amount, "amount": amount, "damage_type": damage_type,
            "critical": False, "rolls": rolls,
            "feature_id": feature_id, "channel_effect": "divine_spark",
        }
        if resistance:
            damage_event["resisted"] = True
            damage_event["resistance"] = resistance
        relentless = self._relentless_rage_marker(instance, target)
        if relentless:
            damage_event["relentless"] = relentless
        events.append(damage_event)
        events.extend(
            self._concentration_events(instance, combat, target, amount, rng)
        )
        if target["kind"] == "enemy" and target["hp"] - amount <= 0:
            events.extend(self._enemy_zero_hp_feature_events(
                instance, combat, actor_id, target, amount,
            ))
        return events

    def _turn_undead_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        actor: dict[str, Any], capability: Any, intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Turn Undead: one Wisdom save per chosen Undead, then two conditions.

        The effects are ordinary conditions carrying their own enders, so the
        damage sweep and the source's incapacitation need no branch of their
        own here.  ``flees_from`` is a guided fact for the enemy AI: it records
        "move away from this actor", it does not move anything itself.
        """

        character = self._feature_character(actor)
        feature_id = capability.feature_id
        save_ability = str(self.features.feature_value(
            character, feature_id, "turn_save_ability", "wis",
        ) or "wis")
        duration = str(self.features.feature_value(
            character, feature_id, "turn_duration", TURN_UNDEAD_DURATION,
        ) or TURN_UNDEAD_DURATION)
        turns = int(self.features.feature_value(
            character, feature_id, "turn_turns", TURN_UNDEAD_MAX_ROUNDS,
        ) or TURN_UNDEAD_MAX_ROUNDS)
        conditions = [
            str(code) for code in (
                self.features.feature_value(
                    character, feature_id, "turn_conditions",
                    ["frightened", "incapacitated"],
                ) or []
            ) if str(code)
        ]
        ends_on_damage = bool(self.features.feature_value(
            character, feature_id, "turn_ends_on_damage", True,
        ))
        dc = int(actor.get("spell_save_dc", 0) or 0)
        events: list[dict[str, Any]] = []
        failed: list[str] = []
        for target_id in [str(item) for item in (intent.get("target_ids") or [])]:
            target = self._actor_view(instance, combat, target_id)
            roll = int(rng.randint(1, 20))
            modifier = int(
                (target.get("saving_throws") or {}).get(save_ability, 0) or 0
            )
            total = roll + modifier
            total, replacement = self._strength_total_replacement(
                target, save_ability, total,
            )
            saved = total >= dc
            events.append({
                "type": "check.resolved", "kind": "saving_throw",
                "actor_id": target_id, "ability": save_ability,
                "rolls": [roll], "natural": roll, "modifier": modifier,
                "total": total, "target": dc, "success": saved,
                "feature_id": feature_id, "channel_effect": "turn_undead",
                **replacement,
            })
            if saved:
                continue
            failed.append(target_id)
            for condition_id in conditions:
                events.append({
                    "type": "condition.applied", "target_id": target_id,
                    "condition": condition_id,
                    "duration": duration, "turns_remaining": turns,
                    "source_actor_id": actor_id, "feature_id": feature_id,
                    "channel_effect": "turn_undead",
                    "ends_on_damage": ends_on_damage,
                    "ends_on_source_incapacitated": True,
                    "flees_from": actor_id,
                })
        events.extend(self._sear_undead_events(
            instance, combat, actor_id, actor, intent, failed, rng,
        ))
        return events

    def _sear_undead_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        actor: dict[str, Any], intent: dict[str, Any], failed: list[str], rng: Any,
    ) -> list[dict[str, Any]]:
        """Sear Undead: one shared roll of d8s, dealt to the Undead that failed.

        Three rules make this different from an ordinary damage rider, and all
        three live here: the dice are rolled once for the whole use (not per
        target), only creatures that already failed *this* Turn Undead take it,
        and the damage is explicitly marked as not ending the turn effect --
        otherwise the shared "takes any damage ends it" sweep would immediately
        undo what the same action just applied.

        Nothing is rolled when nobody failed: the total could not be applied to
        anyone, and spending RNG on it would make a no-op visible in the dice
        trail.
        """

        if not intent.get("sear_undead") or not failed:
            return []
        character = self._feature_character(actor)
        feature_id = "sear_undead"
        die = str(self.features.feature_value(
            character, feature_id, "sear_die", "d8",
        ) or "d8")
        ability = str(self.features.feature_value(
            character, feature_id, "sear_ability", "wis",
        ) or "wis")
        minimum = int(self.features.feature_value(
            character, feature_id, "sear_minimum", 1,
        ) or 1)
        damage_type = str(self.features.feature_value(
            character, feature_id, "sear_damage_type", "radiant",
        ) or "radiant")
        modifier = ability_modifier(
            int((actor.get("abilities") or {}).get(ability, 10) or 10)
        )
        count = max(minimum, modifier)
        events: list[dict[str, Any]] = []
        total, rolls = roll_damage_node(
            formula_node_from_dice_formula(f"{count}{die}"), rng,
        )
        for target_id in failed:
            target = self._actor_view(instance, combat, target_id)
            amount, resistance = self._resist_damage(target, total, damage_type)
            if amount <= 0:
                continue
            damage_event: dict[str, Any] = {
                "type": "resource.changed", "resource": "hp", "target_id": target_id,
                "delta": -amount, "amount": amount, "damage_type": damage_type,
                "critical": False, "rolls": rolls,
                "feature_id": feature_id, "channel_effect": "turn_undead",
                # 这一条不是"之后又挨了打"，而是同一次驱散的一部分：它不结束
                # Turn Undead 状态，reducer 的伤害扫描读这个标记。
                "turn_effect_preserved": True,
            }
            if resistance:
                damage_event["resisted"] = True
                damage_event["resistance"] = resistance
            events.append(damage_event)
            events.extend(
                self._concentration_events(instance, combat, target, amount, rng)
            )
            if target["kind"] == "enemy" and target["hp"] - amount <= 0:
                events.extend(self._enemy_zero_hp_feature_events(
                    instance, combat, actor_id, target, amount,
                ))
                if self._last_hostile_defeated(combat, target, amount):
                    # 灼烧亡灵是这次驱散的实际伤害来源：它同样可以结束战斗。
                    events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        return events

    def _abjure_foes_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        actor: dict[str, Any], capability: Any, intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Abjure Foes: one Wisdom save each, then a Frightened with a choice cap.

        The three-way limit is written onto the condition as data.  It is not
        Incapacitated, it does not stop reactions, and it must not survive on a
        creature that is immune to Frightened -- which is why the immunity is
        answered *before* the condition exists rather than by a later filter.
        """

        character = self._feature_character(actor)
        feature_id = capability.feature_id
        save_ability = str(self.features.feature_value(
            character, feature_id, "save_ability", "wis",
        ) or "wis")
        condition = str(self.features.feature_value(
            character, feature_id, "condition", "frightened",
        ) or "frightened")
        minutes = int(self.features.feature_value(
            character, feature_id, "condition_minutes", 1,
        ) or 1)
        choices = [
            str(value) for value in (
                self.features.feature_value(
                    character, feature_id, "turn_choice_limit",
                    ["move", "action", "bonus_action"],
                ) or []
            ) if str(value)
        ]
        ends_on_damage = bool(self.features.feature_value(
            character, feature_id, "ends_on_damage", True,
        ))
        dc = int(actor.get("spell_save_dc", 0) or 0)
        events: list[dict[str, Any]] = []
        for target_id in [str(item) for item in (intent.get("target_ids") or [])]:
            target = self._actor_view(instance, combat, target_id)
            roll = int(rng.randint(1, 20))
            modifier = int(
                (target.get("saving_throws") or {}).get(save_ability, 0) or 0
            )
            total = roll + modifier
            total, replacement = self._strength_total_replacement(
                target, save_ability, total,
            )
            saved = total >= dc
            check_index = len(events)
            events.append({
                "type": "check.resolved", "kind": "saving_throw",
                "actor_id": target_id, "ability": save_ability,
                "rolls": [roll], "natural": roll, "modifier": modifier,
                "total": total, "target": dc, "success": saved,
                "feature_id": feature_id, "channel_effect": "abjure_foes",
                **replacement,
            })
            if saved:
                continue
            blocker = self._condition_immunity_blocker(
                instance, combat, target, condition,
            )
            if blocker is not None:
                # 免疫者连"只三选一"的隐藏限制也一起免疫：限制写在条件上，
                # 条件不落地，限制自然不存在。拦截原因写回这次豁免。
                scope = str(blocker.get("scope") or "")
                events[check_index]["blocked_condition"] = condition
                events[check_index][
                    "blocked_by_aura" if scope == "aura" else "blocked_by_feature"
                ] = {key: value for key, value in blocker.items() if key != "scope"}
                continue
            events.append({
                "type": "condition.applied", "target_id": target_id,
                "condition": condition,
                "duration": ABJURE_FOES_DURATION,
                "turns_remaining": minutes * 10,
                "source_actor_id": actor_id, "feature_id": feature_id,
                "channel_effect": "abjure_foes",
                "ends_on_damage": ends_on_damage,
                "turn_choice_limit": choices,
            })
        return events

    def _preserve_life_events(
        self, actor: dict[str, Any], capability: Any, intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Preserve Life: one fixed budget, divided by the intent.

        The budget is answered from the declaring class's level every time it
        is used instead of being stored as a resource: it exists only for this
        action, so a leftover point cannot be carried into the next one.  Each
        positive share is its own ``resource.changed`` event -- two creatures
        healed by one action are two facts, not one total.
        """

        character = self._feature_character(actor)
        feature_id = capability.feature_id
        per_level = int(self.features.feature_value(
            character, feature_id, "preserve_life_per_level", 5,
        ) or 5)
        state = self.features.class_state(character)
        level = int(state[1]) if state else 0
        budget = per_level * level
        events: list[dict[str, Any]] = []
        for row in intent.get("allocations") or []:
            if not isinstance(row, dict):
                continue
            amount = int(row.get("amount", 0) or 0)
            if amount <= 0:
                continue
            events.append({
                "type": "resource.changed", "resource": "hp",
                "target_id": str(row.get("target_id") or ""),
                "delta": amount, "amount": amount,
                "healing": True, "rolls": [],
                "feature_id": feature_id, "channel_effect": "preserve_life",
                "pool_total": budget, "allocated_amount": amount,
            })
        return events

    def _divine_sense_events(
        self, instance: Any, actor: dict[str, Any], capability: Any,
    ) -> list[dict[str, Any]]:
        """Divine Sense: a self-scoped detection window, not a pool spend.

        The sensing itself is projected privately by the view layer when this
        condition is present; the event only records the window and its
        radius, so replaying the ledger reconstructs the same permission
        without re-deriving who could see what.
        """

        character = self._feature_character(actor)
        feature_id = capability.feature_id
        minutes = int(self.features.feature_value(
            character, feature_id, "sense_duration_minutes", 10,
        ) or 10)
        actor_id = str(actor["actor_id"])
        creature_types = [
            str(kind) for kind in (
                self.features.feature_value(
                    character, feature_id, "sense_creature_types",
                    ["celestial", "fiend", "undead"],
                ) or []
            ) if str(kind)
        ]
        return [{
            "type": "condition.applied", "target_id": actor_id,
            "condition": DIVINE_SENSE_CONDITION,
            "duration": DIVINE_SENSE_DURATION,
            "duration_minutes": minutes,
            "expires_at_minute": self._logical_minutes(instance) + minutes,
            "range_ft": int(self.features.feature_value(
                character, feature_id, "sense_range_ft", 60,
            ) or 60),
            "creature_types": creature_types,
            "ends_on_incapacitated": bool(self.features.feature_value(
                character, feature_id, "ends_on_incapacitated", True,
            )),
            "source_actor_id": actor_id, "feature_id": feature_id,
        }]

    def _lay_on_hands_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        capability: Any, intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Lay On Hands: pay the pool the *requested* amount, then heal.

        The pool is charged with what the player asked for, not with what the
        target could actually take: a 30-point Lay On Hands on a creature
        missing 4 Hit Points costs 30 and heals 4.  The condition removals are
        charged before any healing is applied, and neither half heals -- those
        points are spent to remove a condition, exactly as the feature says.
        """

        pool_id = capability.pool_resource_id
        if not pool_id:  # pragma: no cover - catalog validation guards this
            raise CombatIntentError("Lay On Hands has no pool to spend from")
        character = deepcopy(self._canonical_actor_character(instance, actor_id))
        pool = resource_state(character, pool_id)
        current = int(pool.get("current", 0) or 0)
        amount = int(intent.get("healing_amount", 0) or 0)
        conditions = [str(code) for code in (intent.get("remove_conditions") or [])]
        cost = int(self.features.feature_value(
            character, capability.feature_id, "condition_cost", 5,
        ) or 5)
        total = amount + cost * len(conditions)
        if total <= 0:
            raise CombatIntentError(
                "Lay On Hands needs healing or a condition to remove"
            )
        if total > current:
            raise CombatIntentError(
                "the Lay On Hands pool does not hold that many points"
            )
        target_id = str(intent.get("target_id") or "")
        target = self._actor_view(instance, combat, target_id)
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
            "resource_id": pool_id, "amount": total,
            "feature_id": capability.feature_id,
        }]
        if amount > 0:
            blocked = "cannot_regain_hp" in (target.get("conditions") or {})
            events.append({
                "type": "resource.changed", "resource": "hp", "target_id": target_id,
                "delta": 0 if blocked else amount,
                "amount": 0 if blocked else amount,
                "healing": True, "rolls": [],
                "feature_id": capability.feature_id,
                "channel_effect": "lay_on_hands",
                **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
            })
        for condition_id in conditions:
            events.append({
                "type": "condition.removed", "target_id": target_id,
                "condition": condition_id, "reason": "lay_on_hands",
            })
        return events
