"""Granted strikes, self-healing, and movement granted by class capabilities."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from src.engine.modules import ruleset_runtime
from src.engine.staged_transaction import fork_domain_state

from ..character.builder import ability_modifier
from ..features.attack_declarations import (
    STUNNING_STRIKE, HitConditionContext, granted_strike_selection, plan_hit_conditions,
)
from ..features.models import FeatureCapability, ResourceCost
from .action_adapter import formula_node_from_dice_formula, roll_damage_node
from .check_modes import effective_speed
from .primitives import (
    CombatIntentError, ONE_MINUTE_DURATION, ONE_MINUTE_ROUNDS, UNARMED_STRIKE_REF,
    canonical as _canonical, current_turn_slot as _current_turn_slot,
    world_minutes,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..features.resolver import Dnd2024ClassFeatureResolver
    from .catalog import Dnd2024CombatCatalog


class CombatMartialActionsMixin:
    __slots__ = ()

    if TYPE_CHECKING:
        catalog: Dnd2024CombatCatalog
        features: Dnd2024ClassFeatureResolver
        _actor_view: Callable[[Any, dict[str, Any], str], dict[str, Any]]
        _declared_attack: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
        _distance: Callable[[dict[str, Any], str, str], int]
        _position: Callable[[dict[str, Any], str], int]
        _feature_character: Callable[[dict[str, Any]], dict[str, Any]]
        _feature_used_this_turn: Callable[[dict[str, Any], str, str], bool]
        _reduce_event: Callable[[dict[str, Any], dict[str, Any]], None]
        _validate_attack: Callable[[Any, dict[str, Any], dict[str, Any], dict[str, Any]], None]
        _strength_total_replacement: Callable[[dict[str, Any], str, int], tuple[int, dict[str, Any]]]
        _consume_save_markers: Callable[[str, dict[str, Any]], list[dict[str, Any]]]
        _saving_throw_check: Callable[[Any, dict[str, Any], dict[str, Any], str, str, int, str, list[dict[str, Any]], Any], dict[str, Any]]
        _brutal_effect_events: Callable[[Any, dict[str, Any], str, dict[str, Any], dict[str, Any]], list[dict[str, Any]]]

        def _attack_events(
            self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
            *, spend_attack_action: bool = True, granted_by: str = "",
            opportunity: bool = False,
        ) -> list[dict[str, Any]]: ...

    #: Bonus Action 附带移动的声明表。今天只有两项消费者，所以先写成代码表；
    #: 第三个出现时再搬进 catalog（与 ``unavailable_reason`` 同一条纪律）。
    RIDER_MOVES = {
        "rage": {"feature_id": "instinctive_pounce", "provokes_opportunity": False},
        "second_wind": {"feature_id": "tactical_shift", "provokes_opportunity": False},
    }

    def _validate_capability_unarmed_strike(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: FeatureCapability, target_id: str,
    ) -> None:
        # The capability's own spend precedes every granted hit. Validate
        # rider affordability against that balance, not the original pool.
        rider_actor = deepcopy(actor)
        for cost in capability.cost.resources:
            if not isinstance(cost, ResourceCost):
                continue
            for pool in rider_actor.get("class_resources") or []:
                if pool.get("id") == cost.resource_id:
                    pool["current"] = int(pool.get("current", 0)) - cost.amount
        self._validate_attack(instance, combat, {
            "target_id": target_id,
            "weapon_ref": UNARMED_STRIKE_REF,
            **{key: intent[key] for key in (
                "unarmed_damage_type", "rider_damage_type",
            ) if key in intent},
            **granted_strike_selection(intent),
        }, rider_actor)

    def _capability_unarmed_strike_events(
        self, instance: Any, intent: dict[str, Any], actor: dict[str, Any],
        capability: FeatureCapability, count: int,
        prior_events: tuple[dict[str, Any], ...], rng: Any, target_id: str,
    ) -> list[dict[str, Any]]:
        """Resolve granted strikes against a detached copy of the paid batch."""

        actor_id = str(actor["actor_id"])
        staged = fork_domain_state(instance)
        events: list[dict[str, Any]] = []
        pending = list(prior_events)
        for _ in range(self._strike_count(actor, capability, count)):
            self._simulate_capability_events(staged, pending)
            current_combat = ruleset_runtime.state(staged)["combat"]
            current_actor = self._actor_view(staged, current_combat, actor_id)
            current_target = self._actor_view(staged, current_combat, target_id)
            strike_intent: dict[str, Any] = {
                "actor_id": actor_id, "target_id": target_id,
                "weapon_ref": UNARMED_STRIKE_REF,
                **{key: intent[key] for key in (
                    "unarmed_damage_type", "rider_damage_type",
                ) if key in intent},
                # The first eligible hit consumes the declaration; a miss does not.
                **granted_strike_selection(intent, already_used=self._feature_used_this_turn(
                    current_combat, actor_id, STUNNING_STRIKE.id,
                )),
            }
            weapon = self._declared_attack(current_actor, strike_intent)
            if (
                current_target["hp"] <= 0
                or self._distance(current_combat, actor_id, target_id)
                > int(weapon.get("long_range") or weapon.get("range", 5))
            ):
                break
            self._validate_attack(staged, current_combat, strike_intent, current_actor)
            if intent.get("open_hand_choice"):
                strike_intent["open_hand_choice"] = intent["open_hand_choice"]
            pending = self._attack_events(
                staged, current_combat, strike_intent, rng,
                spend_attack_action=False,
                granted_by=str(capability.feature_id),
            )
            events.extend(pending)
            if any(event["type"] == "dnd2024.combat.ended" for event in pending):
                break
        return events

    def _strike_count(
        self, actor: dict[str, Any], capability: Any, declared: int,
    ) -> int:
        """How many strikes this capability makes, after feature upgrades.

        声明给出的是"未升级"的基线（Flurry 两次），Heightened Focus 把它抬到
        三次——两者的差别不在成本上，所以不改 capability，只在计数处读特性。
        """

        character = self._feature_character(actor)
        if not self.features.has_feature(character, "heightened_focus"):
            return int(declared)
        upgraded = int(self.features.feature_value(
            character, "heightened_focus", "flurry_strikes", 0,
        ) or 0)
        if capability.feature_id != "flurry_of_blows" or upgraded <= 0:
            return int(declared)
        return max(int(declared), upgraded)

    def _simulate_capability_events(
        self, instance: Any, events: list[dict[str, Any]],
    ) -> None:
        """Advance a detached capability plan using the canonical reducer."""

        snapshot = {
            "ruleset_state": ruleset_runtime.state(instance),
            "characters": {
                uid: deepcopy(_canonical(instance.get_character_sheet(uid)))
                for uid in instance.players
            },
        }
        for event in events:
            self._reduce_event(snapshot, event)
        for uid, canonical in snapshot["characters"].items():
            sheet = deepcopy(instance.get_character_sheet(uid))
            sheet["ruleset_character"] = canonical
            instance.set_character_sheet(uid, sheet)

    def _capability_heal_events(
        self, combat: dict[str, Any], actor: dict[str, Any], capability: FeatureCapability,
        intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Roll the declared healing formula and append any granted movement."""

        character = self._feature_character(actor)
        feature_id = capability.feature_id
        dice = str(self.features.feature_value(character, feature_id, "heal_dice", "") or "")
        if dice == "martial_arts":
            # Wholeness of Body uses the character's current Martial Arts die.
            dice = self.features.unarmed_strike_die(character)
        bonus = int(self.features.feature_value(character, feature_id, "heal_bonus", 0) or 0)
        ability = str(self.features.feature_value(character, feature_id, "heal_ability", "") or "")
        if ability:
            bonus += ability_modifier(int((actor.get("abilities") or {}).get(ability, 10) or 10))
        minimum = int(self.features.feature_value(character, feature_id, "heal_minimum", 1) or 1)
        healing_node = formula_node_from_dice_formula(dice)
        if bonus:
            healing_node = {"op": "add", "args": [
                healing_node, {"op": "constant", "value": bonus},
            ]}
        healing, rolls = roll_damage_node(healing_node, rng)
        healing = max(minimum, healing)
        blocked = "cannot_regain_hp" in actor["conditions"]
        return [{
            "type": "resource.changed", "resource": "hp", "target_id": str(actor["actor_id"]),
            "delta": 0 if blocked else healing, "amount": 0 if blocked else healing,
            "healing": True, "rolls": rolls,
            "feature_id": feature_id,
            "heal_die": dice, "heal_bonus": bonus,
            **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
        }, *self._rider_move_events(combat, actor, capability.id, intent)]

    def _rider_move_events(
        self, combat: dict[str, Any], actor: dict[str, Any], capability_id: str,
        intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """附带移动：上限为半速，且**不占用**普通移动额度。

        RAW 把它写成"作为该附赠动作的一部分移动至多半速"，不是"给你更多移动力"，
        所以这里发一条 ``movement_cost: 0`` 的位移事件——额度在结算时按半速校验，
        而不是加进 ``economy.movement`` 让玩家自己再提交一次 move。
        """

        rider = self.RIDER_MOVES.get(capability_id)
        raw = intent.get("rider_move")
        if rider is None or raw is None or raw == 0:
            return []
        character = self._feature_character(actor)
        if not self.features.has_feature(character, str(rider["feature_id"])):
            return []
        distance = int(raw)
        allowance = effective_speed(
            self.catalog.conditions, actor.get("conditions") or {},
            int(actor.get("speed", 30) or 30),
        ) // 2
        if distance <= 0 or abs(distance) > allowance:
            raise CombatIntentError("granted movement exceeds half your speed")
        return [{
            "type": "dnd2024.position.changed",
            "actor_id": str(actor["actor_id"]),
            "distance": distance,
            "movement_cost": 0,
            "granted_by": str(rider["feature_id"]),
            "allowance_ft": allowance,
            "provokes_opportunity": bool(rider["provokes_opportunity"]),
        }]

    def _hit_condition_declarations(
        self, instance: Any, actor: dict[str, Any], weapon: dict[str, Any],
        intent: dict[str, Any], combat: dict[str, Any], granted_by: str = "",
    ) -> list[dict[str, Any]]:
        """Hit-triggered conditions this attack declares (Stunning Strike).

        These are not damage riders: they open a saving throw after the hit
        lands, and the condition they apply lasts until the attacker's next
        turn.  The declaration is attached to the damage event so a pending
        decision can carry it across a save/load without re-deriving it.
        """

        if actor["kind"] not in {"player", "companion"}:
            return []
        character = self._feature_character(actor)
        declarations: list[dict[str, Any]] = []

        def parameter(feature_id: str, key: str) -> Any:
            return self.features.feature_value(character, feature_id, key, None)

        choice = str(intent.get("open_hand_choice") or "")
        if (
            choice
            and granted_by == "flurry_of_blows"
            and self.features.has_feature(character, "open_hand_technique")
        ):
            allowed = [
                str(value)
                for value in parameter("open_hand_technique", "flurry_hit_choices") or []
            ]
            if choice in allowed:
                declarations.append({
                    "kind": "open_hand",
                    "feature_id": "open_hand_technique",
                    "choice": choice,
                    "addle_condition": str(
                        parameter("open_hand_technique", "addle_condition") or ""
                    ),
                    "push_distance_ft": int(
                        parameter("open_hand_technique", "push_distance_ft") or 0
                    ),
                    "push_save_ability": str(
                        parameter("open_hand_technique", "push_save_ability") or ""
                    ),
                    "topple_save_ability": str(
                        parameter("open_hand_technique", "topple_save_ability") or ""
                    ),
                    "topple_condition": str(
                        parameter("open_hand_technique", "topple_condition") or ""
                    ),
                })

        actor_id = str(actor["actor_id"])
        declarations.extend(plan_hit_conditions(intent, HitConditionContext(
            self.features, character, weapon, world_minutes(instance),
            lambda feature_id: self._feature_used_this_turn(combat, actor_id, feature_id),
            intent,
        )))
        return declarations

    def _hit_condition_events(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], damage_event: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Resolve the declared hit conditions after their damage landed.

        Two declaration lists reach this method: ``hit_condition_effects``
        (save-gated conditions such as Stunning Strike and Open Hand
        Technique) and ``brutal_strike_effects`` (the four Brutal Strike
        options, none of which allow a save).  Both are dispatched here so the
        straight-through attack and the decision-finisher path cannot disagree
        about *when* a hit-triggered effect lands.
        """

        declarations = damage_event.get("hit_condition_effects")
        declarations = declarations if isinstance(declarations, list) else []
        brutal = damage_event.get("brutal_strike_effects")
        brutal = brutal if isinstance(brutal, list) else []
        if not declarations and not brutal:
            return []
        events: list[dict[str, Any]] = [
            event for row in brutal if isinstance(row, dict)
            for event in self._brutal_effect_events(
                instance, combat, attacker_id, target, row,
            )
        ]
        if not declarations:
            return events
        attacker = self._actor_view(instance, combat, attacker_id)
        # 没有声明豁免属性的行按僧的默认口径算（8 + PB + WIS）：``open_hand``
        # 的行由 combat 侧构造、不经内容，旧存档也可能缺这个键。任何自带
        # ``save_dc_ability`` 的行按自己的属性算——诡诈打击是敏捷，写死 WIS
        # 会让同一个引擎里长出第二条 DC 公式。
        fallback_dc = (
            8 + int(attacker.get("proficiency_bonus", 0) or 0)
            + ability_modifier(attacker["abilities"].get("wis", 10))
        )
        for row in declarations:
            if not isinstance(row, dict):
                continue
            if row.get("kind") == "cunning_strike":
                events.extend(self._cunning_strike_events(
                    instance, combat, attacker, target, row, rng,
                ))
                continue
            dc = self._declared_save_dc(attacker, row, fallback_dc)
            if row.get("kind") == "open_hand":
                events.extend(self._open_hand_events(
                    instance, combat, attacker_id, target, row, dc, rng,
                ))
                continue
            if row.get("kind") == "quivering_palm":
                # 无需豁免：命中即成立，随后由"结束"动作触发体质豁免。
                cost = int(row.get("focus_cost") or 0)
                if cost:
                    events.append({
                        "type": "dnd2024.class_resource.spent",
                        "actor_id": attacker_id,
                        "resource_id": "focus_points", "amount": cost,
                    })
                events.append({
                    "type": "dnd2024.long_effect.applied",
                    "actor_id": attacker_id,
                    "effect_id": "quivering_palm",
                    "effect": {
                        "feature_id": "quivering_palm",
                        "expires_at": world_minutes(instance)
                        + int(row.get("duration_minutes") or 0),
                        "detail": {"target_id": str(target["actor_id"])},
                    },
                })
                continue
            if row.get("kind") != "stunning_strike":
                continue
            ability = str(row.get("save_ability") or "")
            if not ability:
                continue
            cost = int(row.get("focus_cost") or 0)
            if cost:
                events.append({
                    "type": "dnd2024.class_resource.spent", "actor_id": attacker_id,
                    "resource_id": "focus_points", "amount": cost,
                })
            check = self._saving_throw_check(
                instance, combat, target, str(target["actor_id"]),
                ability, dc, attacker_id, events, rng,
            )
            check["source_feature"] = "stunning_strike"
            total, replacement = self._strength_total_replacement(
                target, ability, int(check["total"]),
            )
            if replacement:
                check["total"] = total
                check["success"] = total >= dc
                check.update(replacement)
            events.append(check)
            succeeded = check["success"]
            # 这是一次真实豁免：Staggering 的"下一次豁免劣势"在这里就该用掉，
            # 否则它会跟着目标走进下一场豁免（S0-F15 §4.3.3）。
            events.extend(
                self._consume_save_markers(str(target["actor_id"]), target)
            )
            condition = str(
                row.get("on_success_condition") if succeeded
                else row.get("on_fail_condition")
            )
            if not condition:
                continue
            events.append({
                "type": "condition.applied", "target_id": str(target["actor_id"]),
                "condition": condition,
                "duration": str(row.get("duration") or ""),
                "source_actor_id": attacker_id,
            })
            if succeeded and row.get("on_success_attack_condition"):
                events.append({
                    "type": "condition.applied", "target_id": str(target["actor_id"]),
                    "condition": str(row["on_success_attack_condition"]),
                    "duration": str(row.get("duration") or ""),
                    "source_actor_id": attacker_id,
                })
            events.append({
                "type": "dnd2024.feature.used", "actor_id": attacker_id,
                "feature_id": "stunning_strike",
                "round": int(combat.get("round", 0)),
                "turn_index": int(combat.get("turn_index", 0)),
            })
        return events

    @staticmethod
    def _declared_save_dc(
        attacker: dict[str, Any], row: dict[str, Any], fallback: int,
    ) -> int:
        """The save DC this row states, or the caller's default.

        Rows that name an ability compute their own DC from the attacker's
        proficiency bonus; the fallback exists only for declarations built in
        code (Open Hand) and for older held data that predates the key.
        """

        ability = str(row.get("save_dc_ability") or "")
        if not ability:
            return fallback
        return (
            8 + int(attacker.get("proficiency_bonus", 0) or 0)
            + ability_modifier(attacker["abilities"].get(ability, 10))
        )

    def _cunning_strike_events(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        target: dict[str, Any], row: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Resolve each declared Cunning Strike effect that landed.

        The dice were already withheld from the damage roll (the pool shrank
        before it was rolled), so nothing here pays for the effect: this only
        runs the save and applies what a failure carries.  Effects resolve in
        catalog order, never in the order the client listed them.
        """

        target_id = str(target["actor_id"])
        attacker_id = str(attacker["actor_id"])
        currently_acting = str((combat.get("economy") or {}).get("actor_id") or "")
        events: list[dict[str, Any]] = []
        for effect in row.get("effects") or []:
            if not isinstance(effect, dict):
                continue
            if effect.get("speed_divisor"):
                events.extend(self._withdraw_move_events(combat, attacker, effect))
                continue
            ability = str(effect.get("save_ability") or "")
            condition = str(effect.get("on_fail_condition") or "")
            if not (ability and condition):
                continue
            dc = self._declared_save_dc(attacker, effect, 0)
            check = self._saving_throw_check(
                instance, combat, target, target_id, ability, dc, attacker_id,
                events, rng,
            )
            check["source_feature"] = str(effect.get("feature_id") or "")
            total, replacement = self._strength_total_replacement(
                target, ability, int(check["total"]),
            )
            if replacement:
                check["total"] = total
                check["success"] = total >= dc
                check.update(replacement)
            events.append(check)
            # 真实的豁免：Staggering 的"下一次豁免劣势"在这里消费。
            events.extend(self._consume_save_markers(target_id, target))
            if check["success"]:
                continue
            events.append(
                self._cunning_condition_event(
                    effect, target_id, attacker_id, ability, dc, currently_acting,
                )
            )
        return events

    @staticmethod
    def _cunning_condition_event(
        effect: dict[str, Any], target_id: str, attacker_id: str,
        ability: str, dc: int, currently_acting: str = "",
    ) -> dict[str, Any]:
        """The condition one failed save carries, with its full metadata."""

        duration = str(effect.get("duration") or "")
        event: dict[str, Any] = {
            "type": "condition.applied", "target_id": target_id,
            "condition": str(effect.get("on_fail_condition") or ""),
            "duration": duration,
            "source_actor_id": attacker_id,
            "source_feature": str(effect.get("feature_id") or ""),
        }
        if effect.get("save_repeat"):
            # 快照豁免的难度：目标之后每回合都用落下时的这个 DC，而不是
            # 重算攻击者的 DC（那会让"后来涨了熟练"变成buff）。
            event["save_repeat"] = {"ability": ability, "dc": dc}
        if effect.get("ends_on_damage"):
            event["ends_on_damage"] = True
        limit = [str(value) for value in effect.get("turn_choice_limit") or [] if str(value)]
        if limit:
            event["turn_choice_limit"] = limit
        if duration == ONE_MINUTE_DURATION:
            event["turns_remaining"] = ONE_MINUTE_ROUNDS
        if duration == "target_turn_end" and currently_acting == target_id:
            # 在目标自己的回合里挂上的（借机攻击是最现实的入口）：这一次
            # 回合结束还不是"下个回合"，要再等一轮。与 statblock rider 同口径。
            event["expires_after"] = 2
        return event

    def _withdraw_move_events(
        self, combat: dict[str, Any], attacker: dict[str, Any], effect: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Withdraw: half a Speed of movement that provokes nothing.

        The allowance is a *budget in feet* like Remarkable Athlete's, not a
        teleport: the player still submits a ``move`` intent and the movement
        validator caps it at the remaining pool.  ``provokes_opportunity`` is
        what the movement check reads to skip the Opportunity Attack window
        for exactly this allowance.
        """

        actor_id = str(attacker["actor_id"])
        slot = _current_turn_slot(combat)
        if slot is None or str(slot["actor_id"]) != actor_id:
            # 额度属于"此刻的行动经济"，授予别人等于把池子交给另一个生物。
            return []
        speed = max(0, int(attacker.get("speed", 0) or 0))
        speed = effective_speed(
            self.catalog.conditions, attacker.get("conditions") or {}, speed,
        )
        divisor = int(effect.get("speed_divisor") or 0)
        if divisor <= 0:
            return []
        budget = speed // divisor
        if budget <= 0:
            return []
        return [{
            "type": "dnd2024.movement.granted", "actor_id": actor_id,
            "amount": budget, "reason": "cunning_strike_withdraw",
            "source_feature": str(effect.get("feature_id") or ""),
            "provokes_opportunity": bool(effect.get("provokes_opportunity", True)),
        }]

    def _open_hand_events(
        self, instance: Any, combat: dict[str, Any], attacker_id: str, target: dict[str, Any],
        row: dict[str, Any], dc: int, rng: Any,
    ) -> list[dict[str, Any]]:
        """Addle / Push / Topple: the three Open Hand Technique outcomes.

        Addle has no saving throw and no damage, so it applies directly.  Push
        and Topple both open the target's save; only a failure produces their
        effect.  Push is expressed as a forced position change (the same event
        spells use) so it never consumes the target's own movement.
        """

        choice = str(row.get("choice") or "")
        target_id = str(target["actor_id"])
        events: list[dict[str, Any]] = []
        if choice == "addle":
            condition = str(row.get("addle_condition") or "")
            if condition:
                events.append({
                    "type": "condition.applied", "target_id": target_id,
                    "condition": condition, "duration": "target_turn_start",
                    "source_actor_id": attacker_id,
                    "source_feature": "open_hand_technique",
                })
            return events
        if choice == "push":
            ability = str(row.get("push_save_ability") or "")
            distance = int(row.get("push_distance_ft") or 0)
        elif choice == "topple":
            ability = str(row.get("topple_save_ability") or "")
            distance = 0
        else:
            return []
        if not ability or (choice == "push" and distance <= 0):
            return []
        check = self._saving_throw_check(
            instance, combat, target, target_id, ability, dc, attacker_id, events, rng,
        )
        check["source_feature"] = "open_hand_technique"
        total, replacement = self._strength_total_replacement(
            target, ability, int(check["total"]),
        )
        if replacement:
            check["total"] = total
            check["success"] = total >= dc
            check.update(replacement)
        events.append(check)
        # 同上：Open Hand 的豁免也是"真实豁免"，标记在这里消费。
        events.extend(self._consume_save_markers(target_id, target))
        if check["success"]:
            return events
        if choice == "push":
            direction = (
                1 if self._position(combat, target_id)
                >= self._position(combat, attacker_id) else -1
            )
            events.append({
                "type": "dnd2024.position.changed", "actor_id": target_id,
                "distance": direction * distance, "forced": True,
                "source_feature": "open_hand_technique",
            })
        else:
            condition = str(row.get("topple_condition") or "")
            if condition:
                events.append({
                    "type": "condition.applied", "target_id": target_id,
                    "condition": condition, "duration": "combat",
                    "source_actor_id": attacker_id,
                    "source_feature": "open_hand_technique",
                })
        return events

    def _fleet_step_grant_events(
        self, actor: dict[str, Any], capability: Any,
    ) -> list[dict[str, Any]]:
        """Fleet Step: a non-Step Bonus Action leaves one free Step behind.

        旗标每回合只由这一个来源写入；Step 自己不再授予（否则链式无限），
        所以判断的是"这个能力是不是 Step of the Wind"而不是"花了附赠动作没有"。
        """

        if capability.feature_id == "step_of_the_wind":
            return []
        if capability.cost.slot != "bonus_action":
            return []
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "fleet_step"):
            return []
        return [{
            "type": "dnd2024.economy.granted", "actor_id": str(actor["actor_id"]),
            "grants": {}, "flags": {"fleet_step_available": True},
        }]
