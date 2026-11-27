"""Pre-roll declarations and the extra dice they attach to an attack.

One of the resolution mixins; the engine composes them. The class holds no
state of its own."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from src.rulesets.dnd2024.character.builder import ability_modifier
from .action_adapter import attack_damage_node, average_damage, formula_node_from_dice_formula, roll_damage_node
from .primitives import CombatIntentError, attack_damage_parts, current_turn_slot as _current_turn_slot
from .check_modes import HUNTERS_MARK_REF, effective_speed


class CombatAttackRidersMixin:
    """Pre-roll declarations and the extra dice they attach to an attack."""

    __slots__ = ()

    if TYPE_CHECKING:
        _actor_view: Any
        _resist_damage: Any
        catalog: Any
        _feature_character: Any
        features: Any
        @staticmethod
        def _feature_used_this_turn(
            combat: dict[str, Any], actor_id: str, feature_id: str,
        ) -> bool:
            ...
        _position: Any
        _all_targets: Any
        _distance: Any

    @staticmethod
    def _opportunity_attack_average(actor: dict[str, Any], attack: dict[str, Any]) -> float:
        """Estimate the actual damage profile used for a reaction attack."""

        flat_bonus = int(attack.get("damage_modifier", 0) or 0)
        if actor["kind"] in {"player", "companion"}:
            is_ranged_weapon = bool(attack.get("ranged"))
            ability = "dex" if is_ranged_weapon else "str"
            abilities = actor["abilities"]
            if attack.get("finesse"):
                ability = max(
                    ("str", "dex"),
                    key=lambda key: ability_modifier(abilities[key]),
                )
            choices = [
                str(key) for key in attack.get("ability_choice") or []
                if str(key) in abilities
            ]
            if choices and not is_ranged_weapon:
                ability = max(
                    choices,
                    key=lambda key: ability_modifier(abilities[key]),
                )
            flat_bonus += ability_modifier(abilities[ability])
        # 两段伤害的期待值相加：The AI 用这个数决定值不值得花一个反应，
        # 少算第二段会让"火焰附加"的攻击看起来比实际弱。
        return sum(
            average_damage(str(part["dice"]), flat_bonus=flat_bonus if index == 0 else 0.0)
            for index, part in enumerate(attack_damage_parts(attack))
        )

    def _superior_prey_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Superior Hunter's Prey: the mark's die, on a second creature.

        One die of the *mark's* damage -- not the weapon's, not the first
        target's total -- rolled independently and resisted independently, so a
        fire-resistant second target halves that die and nothing else.
        """

        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        original_id = str(intent["target_id"])
        second_id = str(intent["second_target_id"])
        second = self._actor_view(instance, combat, second_id)
        effect = self.catalog.spell_effects.get(
            HUNTERS_MARK_REF.removeprefix("spell:")
        ) or {}
        sides = self._mark_damage_die(actor, effect)
        damage_type = str(effect.get("mark_damage_type") or "force")
        node = attack_damage_node(
            damage_formula=f"1d{sides}", modifier=0, critical=False,
            extra_dice_formulas=[],
        )
        amount, rolls = roll_damage_node(node, rng)
        amount = max(0, amount)
        damage, resistance = self._resist_damage(second, amount, damage_type)
        event: dict[str, Any] = {
            "type": "resource.changed", "resource": "hp",
            "target_id": second_id, "delta": -damage, "amount": damage,
            "damage_type": damage_type, "critical": False, "rolls": rolls,
            "feature_id": "superior_hunters_prey",
            "owner_ref": HUNTERS_MARK_REF,
            "source_actor_id": actor_id,
            "second_target_of": original_id,
        }
        if resistance:
            event["resisted"] = True
            event["resistance"] = resistance
        events: list[dict[str, Any]] = [event]
        events.append({
            "type": "dnd2024.feature.used", "actor_id": actor_id,
            "feature_id": "superior_hunters_prey",
            "round": int(combat.get("round", 0)),
            "turn_index": int(combat.get("turn_index", 0)),
        })
        return events

    def _mark_damage_die(self, actor: dict[str, Any], effect: dict[str, Any]) -> int:
        """The die this caster's Hunter's Mark rolls, in sides."""

        formula = str(effect.get("mark_damage") or "")
        base = formula.rsplit("d", 1)[-1] if "d" in formula else ""
        if not base.isdigit():
            raise CombatIntentError("hunter's mark damage die is not declared")
        upgrade = self.features.feature_value(
            self._feature_character(actor), "foe_slayer", "mark_damage_die", 0,
        )
        return int(upgrade or base)

    def _attack_state_riders(
        self, character: Any, weapon: dict[str, Any], combat: dict[str, Any],
        actor_id: str, *, damage_type: str = "",
        conditions: dict[str, Any] | None = None,
        check: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Frenzy and Brutal Strike: the extra damage an attack *state* adds.

        Both live on the same hit as the Smite-family riders but are separate
        damage instances, because their damage type follows the weapon and the
        resistance rules must still be applied per instance.

        The two gates read different facts on purpose.  Frenzy asks whether
        Reckless was actually in force for this roll (its source is in the
        check's source list); Brutal asks whether the intent declared it this
        time.  Reading Reckless from the *condition* would be wrong for the
        first attack of a turn: the condition is applied by this very batch,
        so the actor view that settlement holds has not seen it yet.
        """

        source_codes = {
            str(row.get("code") or "")
            for row in (check or {}).get("advantage_sources") or []
        }
        rows: list[dict[str, Any]] = []

        def parameter(feature_id: str, key: str) -> Any:
            return self.features.feature_value(character, feature_id, key, None)

        def definition_of(feature_id: str) -> str:
            definition = self.features.feature_definition(feature_id)
            return str(getattr(definition, "owner", "") or "")

        if self.features.has_feature(character, "frenzy"):
            required = str(parameter("frenzy", "requires_condition") or "")
            sides = int(parameter("frenzy", "damage_die_sides") or 0)
            count = int(parameter("frenzy", "dice_count") or 0)
            # Conditions come from the actor view, not from the feature
            # projection: the projection carries ownership, not live state, so
            # reading Rage from it would make Frenzy silently unreachable.
            active = conditions or {}
            # "用 Reckless Attack 时"只发生在自己回合：``reckless`` 条件活到
            # 自己下回合开始，而借机攻击走的是同一条结算路径，只看条件会让
            # 别人回合的反应攻击再吃一次额外伤害（S0-F15 §4.1.5）。
            own_turn = str((combat.get("economy") or {}).get("actor_id") or "") == str(actor_id)
            if (
                own_turn
                and sides and count > 0
                and parameter("frenzy", "damage_type_from_attack")
                and "reckless" in source_codes
                and (not required or required in active)
                and not self._feature_used_this_turn(combat, actor_id, "frenzy")
            ):
                rows.append({
                    "feature_id": "frenzy",
                    "owner": definition_of("frenzy"),
                    "dice": f"{count}d{sides}",
                    "damage_type": damage_type,
                    "once_per_turn": True,
                })

        if bool((check or {}).get("brutal_strike")):
            dice = self._brutal_strike_dice(character)
            if dice:
                rows.append({
                    "feature_id": "brutal_strike",
                    "owner": definition_of("brutal_strike"),
                    "dice": dice,
                    "damage_type": damage_type,
                    # 选择已在掷骰前记账：这里再记一次会让 reducer 因重复拒绝。
                    "once_per_turn": False,
                })
        return rows

    def _brutal_strike_dice(self, character: Any) -> str:
        """The extra damage dice, after the 17th-level upgrade.

        The upgrade is a level, not a second declaration: the progression
        table grants ``improved_brutal_strike`` at both 13 and 17, so a second
        node would collide on ``(owner, id)`` and could not be granted twice.
        Reading the class level here keeps the two numbers in one declaration.
        """

        base = str(self.features.feature_value(
            character, "brutal_strike", "damage_dice", "",
        ) or "")
        upgrade = str(self.features.feature_value(
            character, "brutal_strike", "damage_dice_upgrade", "",
        ) or "")
        needed = int(self.features.feature_value(
            character, "brutal_strike", "upgrade_level", 0,
        ) or 0)
        state = self.features.class_state(character)
        level = int(state[1]) if state else 0
        if upgrade and level >= needed:
            return upgrade
        return base

    def _brutal_effect_declarations(
        self, actor: dict[str, Any], intent: dict[str, Any], check: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """The Brutal Strike options this hit declares, as data.

        Declared with the damage event rather than resolved inline for the same
        reason the riders are: a Shield or Uncanny Dodge that turns this hit
        into a miss must not leave a push, a Speed reduction or a Sundering
        bonus behind.  Nothing here rolls dice -- none of the four options
        allows a save.
        """

        if not check.get("brutal_strike"):
            return []
        if actor["kind"] not in {"player", "companion"}:
            return []
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "brutal_strike"):
            return []

        def parameter(feature_id: str, key: str) -> Any:
            return self.features.feature_value(character, feature_id, key, None)

        rows: list[dict[str, Any]] = []
        for raw in intent.get("brutal_strike_effects") or []:
            kind = str(raw)
            if kind == "forceful":
                rows.append({
                    "kind": "forceful",
                    "feature_id": "brutal_strike",
                    "push_distance_ft": int(
                        parameter("brutal_strike", "forceful_push_distance_ft") or 0
                    ),
                    "follow_divisor": int(
                        parameter("brutal_strike", "forceful_follow_move_divisor") or 0
                    ),
                })
            elif kind == "hamstring":
                rows.append({
                    "kind": "hamstring",
                    "feature_id": "brutal_strike",
                    "condition": str(
                        parameter("brutal_strike", "hamstring_condition") or ""
                    ),
                    "duration": str(
                        parameter("brutal_strike", "hamstring_duration") or ""
                    ),
                })
            elif kind == "staggering":
                if not self.features.has_feature(character, "improved_brutal_strike"):
                    continue
                rows.append({
                    "kind": "staggering",
                    "feature_id": "improved_brutal_strike",
                    "save_condition": str(
                        parameter("improved_brutal_strike", "staggering_save_condition")
                        or ""
                    ),
                    "reaction_condition": str(parameter(
                        "improved_brutal_strike", "staggering_reaction_condition",
                    ) or ""),
                    "duration": str(
                        parameter("improved_brutal_strike", "staggering_duration") or ""
                    ),
                })
            elif kind == "sundering":
                if not self.features.has_feature(character, "improved_brutal_strike"):
                    continue
                bonus = int(
                    parameter("improved_brutal_strike", "sundering_attack_bonus") or 0
                )
                condition = str(
                    parameter("improved_brutal_strike", "sundering_condition") or ""
                )
                if not bonus or not condition:
                    continue
                rows.append({
                    "kind": "sundering",
                    "feature_id": "improved_brutal_strike",
                    "condition": condition,
                    "attack_bonus": bonus,
                    "duration": str(
                        parameter("improved_brutal_strike", "sundering_duration") or ""
                    ),
                })
        return [row for row in rows if self._brutal_effect_ready(row)]

    @staticmethod
    def _brutal_effect_ready(row: dict[str, Any]) -> bool:
        """Every option must carry the magnitudes its resolver reads."""

        kind = str(row.get("kind") or "")
        if kind == "forceful":
            return int(row.get("push_distance_ft") or 0) > 0
        if kind == "hamstring":
            return bool(row.get("condition")) and bool(row.get("duration"))
        if kind == "staggering":
            return bool(row.get("save_condition")) and bool(row.get("reaction_condition"))
        if kind == "sundering":
            return bool(row.get("condition")) and int(row.get("attack_bonus") or 0) > 0
        return False

    def _brutal_effect_events(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], row: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Land one Brutal Strike option after its damage.

        Forceful is a forced position change (the same event spells use), so it
        never spends the target's own movement.  Hamstring reuses the condition
        registry's Speed modifier, which makes "only the most recent one
        counts" fall out of the condition being keyed by id instead of
        accumulating -15 per application.  Staggering's two halves are two
        conditions on purpose: consuming the saving-throw half must not restore
        the Opportunity Attack the other half still forbids.
        """

        kind = str(row.get("kind") or "")
        target_id = str(target["actor_id"])
        feature_id = str(row.get("feature_id") or "brutal_strike")
        if kind == "forceful":
            distance = int(row.get("push_distance_ft") or 0)
            if distance <= 0:
                return []
            direction = (
                1 if self._position(combat, target_id)
                >= self._position(combat, attacker_id) else -1
            )
            events = [{
                "type": "dnd2024.position.changed", "actor_id": target_id,
                # 过渡期双写：distance 给旧消费者，feet/away_from 是 2D 的输入。
                "feet": distance, "away_from": attacker_id,
                "distance": direction * distance, "forced": True,
                "source_feature": "brutal_strike",
            }]
            events.extend(self._brutal_follow_move_events(
                instance, combat, attacker_id, target_id,
                int(row.get("follow_divisor") or 0),
            ))
            return events
        if kind == "hamstring":
            return [{
                "type": "condition.applied", "target_id": target_id,
                "condition": str(row["condition"]),
                "duration": str(row["duration"]),
                "source_actor_id": attacker_id,
                "source_feature": feature_id,
            }]
        if kind == "staggering":
            # 豁免劣势没有时限（"它进行的下一次豁免"），因此不带 duration：
            # 只有真掷过一次豁免才会被消费掉。反应封锁按时点到期，两者互不影响。
            return [
                {
                    "type": "condition.applied", "target_id": target_id,
                    "condition": str(row["save_condition"]),
                    "source_actor_id": attacker_id,
                    "source_feature": feature_id,
                },
                {
                    "type": "condition.applied", "target_id": target_id,
                    "condition": str(row["reaction_condition"]),
                    "duration": str(row["duration"]),
                    "source_actor_id": attacker_id,
                    "source_feature": feature_id,
                },
            ]
        if kind == "sundering":
            return [{
                "type": "condition.applied", "target_id": target_id,
                "condition": str(row["condition"]),
                "duration": str(row["duration"]),
                "source_actor_id": attacker_id,
                "attack_bonus": int(row["attack_bonus"]),
                "source_feature": feature_id,
            }]
        return []

    def _brutal_follow_move_events(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target_id: str, divisor: int,
    ) -> list[dict[str, Any]]:
        """The half-Speed, straight-toward-the-target follow-up allowance.

        Like the critical-hit allowance it is a *budget in feet* with a
        direction: the player still submits a ``move`` intent, and the flag
        only suppresses Opportunity Attacks for a move that actually points at
        the pushed creature and fits inside the allowance.  Granting to anyone
        but the current actor would hand the movement pool to a different
        creature, so it fails closed there.
        """

        if divisor <= 0:
            return []
        # 谁的回合由 slot 回答：首轮第二个 slot 属于同一角色，用 initiative 的
        # 下标去比会在 Thief 回合里把这个额度错判成"不是你的回合"。
        slot = _current_turn_slot(combat)
        if slot is None or str(slot["actor_id"]) != attacker_id:
            return []
        actor = self._actor_view(instance, combat, attacker_id)
        speed = effective_speed(
            self.catalog.conditions, actor.get("conditions") or {},
            max(0, int(actor.get("speed", 0) or 0)),
        )
        budget = speed // divisor
        if budget <= 0:
            return []
        return [{
            "type": "dnd2024.movement.granted", "actor_id": attacker_id,
            "amount": budget, "reason": "brutal_forceful",
            "target_id": target_id, "source_feature": "brutal_strike",
            # 免借机由授予方声明，不靠 reducer 按 reason 猜：同一个额度键
            # （economy.move_allowance）服务三种来源。
            "provokes_opportunity": False,
        }]

    def _critical_hit_move_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
    ) -> list[dict[str, Any]]:
        """Grant the half-Speed move a critical hit pays for.

        The allowance is a *budget in feet*, not a free teleport: the player
        still has to submit a ``move`` intent, and the existing movement
        validation caps it at the remaining pool.  Granting movement into the
        pool keeps one movement authority; the ``critical_hit`` reason is what
        lets the move suppress Opportunity Attacks for exactly this allowance.

        Only the current actor can use it: the movement economy belongs to
        whoever's turn it is, so granting to anyone else would silently hand
        the pool to a different creature.  A critical scored on someone else's
        turn (an Opportunity Attack) therefore grants nothing -- recorded as a
        deviation rather than paid out to the wrong actor.
        """

        slot = _current_turn_slot(combat)
        if slot is None or str(slot["actor_id"]) != actor_id:
            return []
        actor = self._actor_view(instance, combat, actor_id)
        grant = self.features.critical_hit_move(self._feature_character(actor))
        if grant is None:
            return []
        # "half your Speed" 读的是条件生效后的当前 Speed，与回合开始预算
        # （``_fresh_economy``）和 Dash 用的是同一个入口：被速度归零的条件
        # （例如麻痹）压住的单位拿不到游丝一样的额度，Speed 0 就是 0。
        speed = max(0, int(actor.get("speed", 0) or 0))
        speed = effective_speed(
            self.catalog.conditions, actor.get("conditions") or {}, speed,
        )
        budget = speed // int(grant["divisor"])
        if budget <= 0:
            return []
        return [{
            "type": "dnd2024.movement.granted", "actor_id": actor_id,
            "amount": budget, "reason": "critical_hit",
            "source_feature": grant["code"], "source_owner": grant["owner"],
            "provokes_opportunity": False,
        }]

    def _qualifying_sneak_ally(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        target: dict[str, Any],
    ) -> bool:
        """An ally of the attacker within 5 feet of the target, able to help.

        Every clause is rule text: the ally must be the *attacker's* ally, must
        be alive and not Incapacitated (paralyzed/petrified/stunned/unconscious
        all imply it), and the distance checked is ally-to-target.
        """

        actor_side = str(actor.get("side") or "")
        target_id = str(target["actor_id"])
        for row in self._all_targets(instance, combat):
            ally_id = str(row["actor_id"])
            if ally_id == actor["actor_id"] or str(row.get("side") or "") != actor_side:
                continue
            if int(row.get("hp", 0) or 0) <= 0:
                continue
            ally = self._actor_view(instance, combat, ally_id)
            conditions = ally.get("conditions") or {}
            if any(
                code in conditions for code in (
                    "defeated", "dead", "incapacitated", "paralyzed", "petrified",
                    "stunned", "unconscious",
                )
            ):
                continue
            if self._distance(combat, ally_id, target_id) <= 5:
                return True
        return False

    def _colossus_slayer_dice(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        target: dict[str, Any],
    ) -> str | None:
        """Colossus Slayer's extra die, or ``None``.

        Three predicates, in the order the SRD states them: the Hunter owns the
        option, the target is already missing Hit Points *before this hit
        lands*, and the option has not been spent this turn.  The dice come
        from the declaration, so a changed die needs no code change here.
        """

        character = self._feature_character(actor)
        if not self.features.has_feature(
            character, "colossus_slayer", owner="subclass:hunter",
        ):
            return None
        if self._feature_used_this_turn(
            combat, str(actor["actor_id"]), "colossus_slayer",
        ):
            return None
        # "missing any of its Hit Points" 是命中前的状态：这一击的伤害还没
        # 落地，所以读的是当前 hp，而不是扣完之后的。
        if int(target.get("hp", 0) or 0) >= int(target.get("max_hp", 0) or 0):
            return None
        dice = self.features.feature_value(
            character, "colossus_slayer", "bonus_damage", "",
        )
        if not isinstance(dice, str) or not dice:
            raise CombatIntentError("colossus slayer damage dice are unavailable")
        formula_node_from_dice_formula(dice)
        return dice

    def _sneak_attack_dice(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        target: dict[str, Any], weapon: dict[str, Any], check_mode: dict[str, Any],
    ) -> str | None:
        """The Sneak Attack dice this hit may add, or ``None``.

        Ownership, once-per-turn, weapon property, and the advantage/ally
        predicate are checked in that order.  A rogue who owns the feature but
        whose table value cannot be read fails closed instead of silently
        dealing no extra damage.
        """

        character = self._feature_character(actor)
        if not self.features.has_feature(character, "sneak_attack"):
            return None
        if self._feature_used_this_turn(combat, str(actor["actor_id"]), "sneak_attack"):
            return None
        # 武器属性来自目录的 finesse / ranged；投掷一把非灵巧近战武器
        # 不会让它变成远程武器。
        if not (weapon.get("finesse") or weapon.get("ranged")):
            return None
        if check_mode["mode"] != "advantage":
            if check_mode["mode"] == "disadvantage":
                return None
            if not self._qualifying_sneak_ally(instance, combat, actor, target):
                return None
        dice = self.features.feature_value(character, "sneak_attack", "damage_dice", "")
        if not isinstance(dice, str) or not dice:
            raise CombatIntentError("sneak attack damage dice are unavailable")
        formula_node_from_dice_formula(dice)
        return dice
