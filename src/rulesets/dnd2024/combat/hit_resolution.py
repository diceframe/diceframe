"""Hit settlement: what lands after an attack roll, and what it costs the target."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from copy import deepcopy
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.features.resolver import ENEMY_REDUCED_TO_ZERO_HP
from src.rulesets.dnd2024.features.resources import (
    USAGE_SCOPE_LONG_REST,
    feature_usage_count,
)
from .action_adapter import (
    attack_damage_node,
    dice_count,
    reduce_dice_count,
    roll_damage_node,
)
from .reaction_spells import attack_is_ranged
from .primitives import (
    CombatIntentError,
    actor_kind as _actor_kind,
    attack_damage_parts,
    attack_riders,
    rider_attack_kind,
    rider_condition,
    size_at_most,
)
from .check_modes import HUNTERS_MARK_REF, marked_target_id, owned_mark
from src.rulesets.dnd2024.features.attack_declarations import (
    HELD_HIT_KEY,
    HURL,
    committed_hit_events,
    held_hit_declarations,
)
from .weapon_masteries import WEAPON_MASTERY_EFFECTS, WEAPON_MASTERY_FEATURE_ID

if TYPE_CHECKING:
    from ..features.resolver import Dnd2024ClassFeatureResolver
    from .catalog import Dnd2024CombatCatalog


class CombatHitResolutionMixin:
    """Turn a committed hit into damage, riders, and the target's response."""

    __slots__ = ()

    if TYPE_CHECKING:
        # Supplied by the combat engine and its other mixins; no runtime state.
        @staticmethod
        def attack_ability(actor: dict[str, Any], weapon: dict[str, Any]) -> str: ...
        def _actor_view(self, instance: Any, combat: dict[str, Any], actor_id: str) -> dict[str, Any]: ...
        def _all_targets(self, instance: Any, combat: dict[str, Any]) -> list[dict[str, Any]]: ...
        def _attack_state_riders(
            self, character: Any, weapon: dict[str, Any], combat: dict[str, Any],
            actor_id: str, *, damage_type: str = "",
            conditions: dict[str, Any] | None = None,
            check: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]: ...
        def _brutal_effect_declarations(
            self, actor: dict[str, Any], intent: dict[str, Any], check: dict[str, Any],
        ) -> list[dict[str, Any]]: ...
        def _colossus_slayer_dice(
            self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
            target: dict[str, Any],
        ) -> str | None: ...
        def _concentration_events(
            self, instance: Any, combat: dict[str, Any],
            target: dict[str, Any], damage: int, rng: Any,
        ) -> list[dict[str, Any]]: ...
        def _concentration_with_dool(
            self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
            damage: int, rng: Any, settlement: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]: ...
        def _critical_hit_move_events(
            self, instance: Any, combat: dict[str, Any], actor_id: str,
        ) -> list[dict[str, Any]]: ...
        def _damage_window_offer(
            self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
            defender: dict[str, Any], damage_event: dict[str, Any],
            intent: dict[str, Any], *, exclude_bards: tuple[str, ...] = (),
            next_decision: dict[str, Any] | None = None,
        ) -> dict[str, Any] | None: ...
        def _distance(self, combat: dict[str, Any], first: str, second: str) -> int: ...
        @staticmethod
        def _feature_character(actor: dict[str, Any]) -> dict[str, Any]: ...
        @staticmethod
        def _feature_used_this_turn(
            combat: dict[str, Any], actor_id: str, feature_id: str,
        ) -> bool: ...
        def _fiendish_resilience_type(self, target: dict[str, Any]) -> str: ...
        def _hit_condition_declarations(
            self, instance: Any, actor: dict[str, Any], weapon: dict[str, Any],
            intent: dict[str, Any], combat: dict[str, Any], granted_by: str = "",
        ) -> list[dict[str, Any]]: ...
        def _hit_condition_events(
            self, instance: Any, combat: dict[str, Any], attacker_id: str,
            target: dict[str, Any], damage_event: dict[str, Any], rng: Any,
        ) -> list[dict[str, Any]]: ...
        def _multiattack_mark_events(
            self, actor: dict[str, Any], target: dict[str, Any],
        ) -> list[dict[str, Any]]: ...
        def _hit_reaction_offer(
            self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
            defender: dict[str, Any], check: dict[str, Any], damage_event: dict[str, Any],
            intent: dict[str, Any], next_decision: dict[str, Any] | None = None,
            ranged: bool = False,
        ) -> dict[str, Any] | None: ...
        def _hit_spell_offer(
            self, instance, combat, actor, weapon, target_id, damage_event, intent
        ): ...
        @staticmethod
        def _last_hostile_defeated(
            combat: dict[str, Any], target: dict[str, Any], damage: int,
        ) -> bool: ...
        def _mark_damage_die(self, actor: dict[str, Any], effect: dict[str, Any]) -> int: ...
        def _natures_ward_source(self, target: dict[str, Any], damage_type: str) -> str: ...
        @staticmethod
        def _position(combat: dict[str, Any], actor_id: str) -> int: ...
        def _relentless_rage_marker(
            self, instance: Any, target: dict[str, Any],
        ) -> dict[str, Any] | None: ...
        def _saving_throw_check(
            self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
            actor_id: str, ability: str, dc: int, source_actor_id: str,
            events: list[dict[str, Any]], rng: Any, *, against_spell: bool = False,
            forced_outcome: str = "",
        ) -> dict[str, Any]: ...
        def _sneak_attack_dice(
            self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
            target: dict[str, Any], weapon: dict[str, Any], check_mode: dict[str, Any],
        ) -> str | None: ...
        catalog: Dnd2024CombatCatalog
        features: Dnd2024ClassFeatureResolver

    def _resist_damage(
        self, target: dict[str, Any], amount: int, damage_type: str,
    ) -> tuple[int, str]:
        """Halve ``amount`` once when the target resists ``damage_type``.

        Resistance sources are server-authoritative in two places: the combat
        catalog's condition registry (static declarations such as rage and
        stoneskin) and each condition's metadata (written by choice-driven
        spells). A single set-membership test guarantees that multiple
        simultaneous sources never stack; the returned condition id names the
        source for the event, or ``""`` when nothing applies.

        Fiendish Resilience is the third source and the only one stored on the
        character rather than in a condition: the chosen type lives in
        ``features.fiendish_resilience.damage_type`` because it outlives the
        combat and is re-chosen at rest.  It is consulted only after every
        condition source declined, so it can never stack a second halving.
        """

        if amount <= 0 or not damage_type:
            return amount, ""
        # S0-MM1：怪物 stat block 自带的三档防御先于一切条件来源结算。校验层
        # 已保证同一类型不会同时出现在三组里，所以这里按"免疫 → 易伤 → 抗性"
        # 取一条就返回，仍然只有一次结算。
        if damage_type in (target.get("immune_damage_types") or ()):
            return 0, "statblock:immune"
        if damage_type in (target.get("vulnerable_damage_types") or ()):
            return amount * 2, "statblock:vulnerable"
        if damage_type in (target.get("resist_damage_types") or ()):
            return max(0, amount // 2), "statblock:resist"
        for condition_id, meta in (target.get("conditions") or {}).items():
            types = set((self.catalog.conditions.get(condition_id) or {}).get(
                "resist_damage_types"
            ) or [])
            if isinstance(meta, dict):
                types.update(
                    str(kind) for kind in meta.get("resist_damage_types") or []
                    if isinstance(kind, str) and kind
                )
                # 排除式声明（Superior Defense 的"除 Force 外全部"）。它和枚举
                # 写法互斥地放在同一处求值，仍然只有一次 return，所以多个来源
                # 不会把伤害连除两次。
                excepted = meta.get("resist_all_except")
                if isinstance(excepted, list) and damage_type not in excepted:
                    return amount // 2, condition_id
            if damage_type in types:
                return amount // 2, condition_id
        if self._fiendish_resilience_type(target) == damage_type:
            return amount // 2, "fiendish_resilience"
        ward = self._natures_ward_source(target, damage_type)
        if ward:
            return amount // 2, ward
        return amount, ""

    def _weapon_mastery_effect(
        self, actor: dict[str, Any], weapon: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The declared mastery effect this attack may use, or ``None``.

        Unlock is the ``weapon_mastery`` feature; enemies have stat-block
        attacks without a mastery slot and never qualify.  A catalog property
        that is registered as ``unimplemented`` fails closed.
        """

        if actor["kind"] not in {"player", "companion"}:
            return None
        mastery_id = str(weapon.get("mastery") or "")
        effect = WEAPON_MASTERY_EFFECTS.get(mastery_id)
        if not effect or effect.get("kind") == "unimplemented":
            return None
        if not self.features.has_feature(
            self._feature_character(actor), WEAPON_MASTERY_FEATURE_ID,
        ):
            return None
        return effect

    @staticmethod
    def _mastery_effect_entry(mastery: dict[str, Any], actor_id: str) -> dict[str, Any]:
        """One mastery effect as the commit step needs it.

        Conditions are the simple case; push and topple keep their own kind
        because they need positions or a saving throw, and both of those are
        only authoritative at commit time (a Shield can still cancel the hit).
        """

        kind = str(mastery.get("kind") or "")
        if kind == "push":
            return {
                "kind": "push",
                "feet": int(mastery.get("feet") or 0),
                "size_at_most": str(mastery.get("size_at_most") or ""),
                "source_actor_id": actor_id,
            }
        if kind == "save_condition":
            return {
                "kind": "save_condition",
                "ability": str(mastery.get("ability") or "con"),
                "condition": str(mastery["condition"]),
                "duration": str(mastery.get("duration") or "until_stand"),
                "source_actor_id": actor_id,
            }
        if kind == "granted_attack":
            # Cleave 不在命中时产生任何效果：它授予的是"下一击"的资格，
            # 由 attack 意图上的 `cleave_from` 声明、由校验回答。
            return {}
        return {
            "condition": str(mastery["condition"]),
            "duration": str(mastery["duration"]),
            "source_actor_id": actor_id,
        }

    def _miss_mastery_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        target: dict[str, Any], weapon: dict[str, Any], modifier: int,
        damage_type: str, rng: Any,
    ) -> list[dict[str, Any]]:
        """Graze: fixed damage on a miss, with no damage dice.

        ``rolls`` stays empty (SP2 ordering: no extra die slot), the ability
        modifier floors at zero, and the damage still passes resistance,
        concentration and the victory check.
        """

        effect = self._weapon_mastery_effect(actor, weapon)
        if not effect or effect.get("kind") != "miss_damage":
            return []
        amount = max(0, int(modifier))
        if amount <= 0:
            return []
        damage, resistance = self._resist_damage(target, amount, damage_type)
        event = {
            "type": "resource.changed", "resource": "hp",
            "target_id": str(target["actor_id"]),
            "delta": -damage, "amount": damage, "damage_type": damage_type,
            "critical": False, "rolls": [],
        }
        if resistance:
            event["resisted"] = True
            event["resistance"] = resistance
        events: list[dict[str, Any]] = [event]
        events.extend(self._concentration_events(instance, combat, target, damage, rng))
        if self._last_hostile_defeated(combat, target, damage):
            events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        return events

    def _hit_settlement_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        target: dict[str, Any], weapon: dict[str, Any], check: dict[str, Any],
        intent: dict[str, Any], rng: Any, *, ability: str, modifier: int,
        damage_formula: str, damage_type: str, crit_on_hit: bool = False,
        exclude_bards: tuple[str, ...] = (), spent_reactions: tuple[str, ...] = (),
        granted_by: str = "",
        horde_breaker: bool = False,
    ) -> list[dict[str, Any]]:
        """Damage construction and commit for an already-hit attack.

        Shared by the straight-through path and by check-window finishers.
        Only damage dice are rolled here; window dice are rolled at resolve
        time.  The straight-through sequence is byte-identical to the pre-slice
        engine because the same construction, offers, and commits run in the
        same order.
        """

        actor_id = str(actor["actor_id"])
        target_id = str(target["actor_id"])
        critical = bool(check.get("critical"))
        if crit_on_hit and not critical:
            critical = True
        if intent.get("unarmed_damage_type") and bool(weapon.get("unarmed")):
            # Empowered Strikes：徒手打击把伤害类型换成声明里的选项之一。
            # 合法值由 validation 在意图阶段限定，这里不做第二套白名单。
            damage_type = str(intent["unarmed_damage_type"])
        check_mode = {
            "mode": str(check.get("advantage_mode") or ""),
            "sources": list(check.get("advantage_sources") or []),
        }
        # 伤害骰经通用公式 AST 求值（Issue 212）：主骰 → 附加骰（魔化、
        # 偷袭，按调用方顺序）→ 属性修正；重击翻倍仍由本层在 AST 上显式表达。
        extra_dice: list[str] = []
        if "hexed" in target["conditions"] and target["conditions"]["hexed"].get(
            "source_actor_id"
        ) == actor_id:
            extra_dice.append("1d6")
        sneak_dice = self._sneak_attack_dice(
            instance, combat, actor, target, weapon, check_mode,
        )
        # 偷袭是否成立必须在扣骰之前记下：诡诈打击能把骰池花到 0 颗，那时
        # ``sneak_dice`` 变成 None，可这一回合的偷袭确实用过了。
        sneak_qualified = sneak_dice is not None
        # 命中条件提前到掷伤害之前算：诡诈打击的代价是"投掷伤害前扣骰"，
        # 而伤害在下面几行就掷了。这一步只读内容与 used_this_turn，不掷骰。
        conditions = self._hit_condition_declarations(
            instance, actor, weapon, intent, combat, granted_by,
        )
        cunning_skip = ""
        if conditions:
            spent = sum(
                int(effect.get("dice_cost") or 0)
                for row in conditions
                if isinstance(row, dict) and row.get("kind") == "cunning_strike"
                for effect in (row.get("effects") or [])
                if isinstance(effect, dict)
            )
            if spent:
                pool = dice_count(sneak_dice) if sneak_dice else 0
                if pool <= 0:
                    # 偷袭不成立（无优势无盟友 / 有劣势 / 武器非灵巧）：
                    # 盲声明落空不该白扣骰，也不该发半个效果。
                    cunning_skip = "no_sneak_attack"
                elif spent > pool:
                    cunning_skip = "budget"
                else:
                    sneak_dice = reduce_dice_count(sneak_dice, spent)
                if cunning_skip:
                    conditions = [
                        row for row in conditions
                        if not (isinstance(row, dict) and row.get("kind") == "cunning_strike")
                    ]
        if sneak_dice:
            extra_dice.append(sneak_dice)
        slayer_dice = self._colossus_slayer_dice(instance, combat, actor, target)
        if slayer_dice:
            extra_dice.append(slayer_dice)
        damage_node = attack_damage_node(
            damage_formula=damage_formula,
            modifier=modifier,
            critical=critical,
            extra_dice_formulas=extra_dice,
        )
        if ability == "str" and "rage" in actor["conditions"]:
            # 狂暴加值只在力量攻击命中时附加到伤害，不影响攻击加值。
            rage_damage = int(self.features.feature_value(
                self._feature_character(actor), "rage", "rage_damage", 0,
            ) or 0)
            if rage_damage:
                damage_node = {"op": "add", "args": [
                    damage_node, {"op": "constant", "value": rage_damage},
                ]}
        damage, rolls = roll_damage_node(damage_node, rng)
        damage = max(0, damage)
        damage, resistance = self._resist_damage(target, damage, damage_type)
        damage_event = {
            "type": "resource.changed", "resource": "hp", "target_id": target_id,
            "delta": -damage, "amount": damage, "damage_type": damage_type,
            "critical": critical, "rolls": rolls,
        }
        if resistance:
            damage_event["resisted"] = True
            damage_event["resistance"] = resistance
        relentless = self._relentless_rage_marker(instance, target)
        if relentless:
            damage_event["relentless"] = relentless
        if sneak_qualified:
            # 延迟提交：只有这次命中伤害最终落地才记一次偷袭使用。
            # Shield 改判落空时不会走到提交辅助方法，因此不会记账。
            # 用 ``sneak_qualified`` 而不是 ``sneak_dice``：骰池被诡诈打击
            # 花到 0 颗时后者是 None，会凭空多给一次偷袭。
            damage_event["feature_use"] = {
                "actor_id": actor_id,
                "feature_id": "sneak_attack",
                "round": int(combat.get("round", 0)),
                "turn_index": int(combat.get("turn_index", 0)),
            }
        if slayer_dice:
            # 同一条延迟提交规则：命中被改判落空时这一回合的额度不消耗。
            marks = damage_event.setdefault("feature_uses", [])
            marks.append({
                "actor_id": actor_id,
                "feature_id": "colossus_slayer",
                "round": int(combat.get("round", 0)),
                "turn_index": int(combat.get("turn_index", 0)),
            })
        if horde_breaker:
            marks = damage_event.setdefault("feature_uses", [])
            marks.append({
                "actor_id": actor_id,
                "feature_id": "horde_breaker",
                "round": int(combat.get("round", 0)),
                "turn_index": int(combat.get("turn_index", 0)),
            })
        riders = self._hit_rider_declarations(
            instance,
            actor, weapon, intent, combat, damage_type=damage_type, check=check,
        )
        if riders:
            # 声明先挂在伤害事件上，掷骰留到真正提交时：Shield/Uncanny Dodge
            # 把命中改判落空时，不给这些骰子白掷一次的机会。
            damage_event["hit_riders"] = riders
        if cunning_skip:
            # 审计事件也随伤害事件走：Shield 把命中改判落空时这一击根本没
            # 造成伤害，没有什么要解释的。
            damage_event["cunning_strike_skipped"] = {
                "actor_id": actor_id, "reason": cunning_skip,
            }
        if conditions:
            damage_event["hit_condition_effects"] = conditions
        # "这一击声明了哪些攻击命中触发"随伤害事件走，和 hit_riders 同一个
        # 道理：决策路径（至圣斩窗口之后）拿不到原始意图，若只把触发挂在
        # 直通路径上，那条路径会悄悄少掉半个特性。
        triggers = held_hit_declarations(intent)
        if triggers:
            damage_event[HELD_HIT_KEY] = triggers
        brutal_effects = self._brutal_effect_declarations(actor, intent, check)
        if brutal_effects:
            damage_event["brutal_strike_effects"] = brutal_effects
        # 第二段伤害与 stat block 的附带效果都属于"命中之后才生效"的一类，
        # 因此和 hit_riders 一样先挂在伤害事件上：护盾术把命中改判落空时，
        # 它们一个都不能落地。
        extra_parts = attack_damage_parts(weapon)[1:]
        if extra_parts:
            damage_event["extra_damage_parts"] = extra_parts
        statblock_riders = attack_riders(weapon)
        if statblock_riders:
            damage_event["statblock_riders"] = statblock_riders
        mastery = self._weapon_mastery_effect(actor, weapon)
        if mastery is not None and str(mastery.get("on")) == "hit" and (
            not mastery.get("requires_damage") or damage > 0
        ):
            # Held like ``feature_use``: an attack that a reaction turns into a
            # miss must not leave its mastery effect behind.  The entry keeps the
            # mastery's own kind, because push and topple are not conditions.
            entry = self._mastery_effect_entry(mastery, actor_id)
            if entry and entry.get("kind") == "save_condition":
                # DC = 8 + 熟练加值 + 这次攻击用的属性调整。用攻击的权威
                # 属性函数而不是 check["modifier"]：后者含魔法加值，会把
                # DC 抬到一个规则里没有的数。
                ability = self.attack_ability(actor, weapon)
                entry["dc"] = (
                    8 + int(actor["proficiency_bonus"])
                    + ability_modifier(actor["abilities"][ability])
                )
            if entry:
                damage_event["mastery_effects"] = [entry]
        offer = self._hit_spell_offer(
            instance, combat, actor, weapon, target_id, damage_event, intent,
        )
        damage_window = self._damage_window_offer(
            instance, combat, actor, target, damage_event, intent,
            exclude_bards=exclude_bards, next_decision=offer,
        )
        next_decision = damage_window or offer
        reaction = self._hit_reaction_offer(
            instance, combat, actor, target, check, damage_event, intent,
            next_decision=next_decision,
            # 拨挡的重定向范围取决于原攻击是近战还是远程，窗口建立时就要定。
            ranged=attack_is_ranged(weapon),
        )
        if reaction is not None and str(target["actor_id"]) in spent_reactions:
            # 同一反应预算已被本次攻击的检定面花掉：不能再开防守反应窗。
            reaction = None
        if reaction or damage_window or offer:
            return [{"type": "dnd2024.decision.pending", **(reaction or damage_window or offer)}]
        return self._resolve_committed_hit(
            instance, combat, actor_id, target, damage_event, damage, rng,
            settle_attack_declarations=True,
        )

    def _hit_rider_declarations(
        self, instance: Any, actor: dict[str, Any], weapon: dict[str, Any],
        intent: dict[str, Any], combat: dict[str, Any], *,
        damage_type: str = "", check: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """The extra damage this hit declares, as data the commit step rolls.

        Nothing is rolled here: the settlement runs before Shield/Uncanny
        Dodge can turn the hit into a miss, so rolling now would burn dice on
        a hit that never lands.  Each entry becomes a separate damage
        instance, because Radiant damage is resisted on its own even when the
        weapon's damage is not.
        """

        if actor["kind"] not in {"player", "companion"}:
            return []
        character = self._feature_character(actor)
        actor_id = str(actor["actor_id"])
        # Hunter's Mark 排在最前：它是法术效果，不是职业特性，所以走不经过
        # `feature_definition` 的那条装配路径；但它同样是一个独立伤害实例
        # （Force 抗性只作用于这一份），因此与 rider 同形。
        declarations: list[dict[str, Any]] = []
        mark_rider = self._mark_rider_row(actor, str(intent.get("target_id") or ""))
        if mark_rider is not None:
            declarations.append(mark_rider)

        def parameter(feature_id: str, key: str) -> Any:
            return self.features.feature_value(character, feature_id, key, None)

        def declaration(
            feature_id: str, dice: str, damage_type: str, once_per_turn: bool,
        ) -> dict[str, Any]:
            definition = self.features.feature_definition(feature_id)
            return {
                "feature_id": feature_id,
                "owner": str(getattr(definition, "owner", "") or ""),
                "dice": dice,
                "damage_type": damage_type,
                "once_per_turn": once_per_turn,
            }

        def qualifies(feature_id: str) -> bool:
            """Whether this attack's kind is one the rider declares.

            The closed set is the declaration's own ``attack_kinds``: Radiant
            Strikes names "melee weapon or Unarmed Strike", Divine Strike just
            "a weapon" (so a bow counts and an Unarmed Strike does not), and
            Primal Strike adds a Beast form's attack.  Reading it here keeps the
            three riders from sharing one guessed rule.
            """

            declared = {
                str(value)
                for value in parameter(feature_id, "attack_kinds") or []
            }
            if not declared:
                return False
            kind = rider_attack_kind(weapon)
            if kind in declared:
                return True
            # "weapon" is the family (melee or ranged), never an Unarmed Strike.
            return "weapon" in declared and kind != "unarmed_strike"

        if self.features.has_feature(character, "radiant_strikes") \
                and qualifies("radiant_strikes"):
            formula = str(parameter("radiant_strikes", "extra_damage") or "")
            damage_type = str(parameter("radiant_strikes", "extra_damage_type") or "")
            if formula and damage_type:
                declarations.append(
                    declaration("radiant_strikes", formula, damage_type, False)
                )

        for feature_id in ("divine_strike", "primal_strike"):
            if not self.features.has_feature(character, feature_id):
                continue
            if not qualifies(feature_id):
                continue
            if self._feature_used_this_turn(combat, actor_id, feature_id):
                continue
            allowed = [
                str(value) for value in parameter(feature_id, "damage_types") or []
            ]
            damage_type = str(intent.get("rider_damage_type") or "")
            # 未声明类型 = 本次不用这个 rider。它是可选项，不是必须项，所以
            # 这里不是错误；validation 只拒绝"声明了但不在白名单里"的值。
            if damage_type not in allowed:
                continue
            formula = self._rider_dice(character, feature_id)
            if not formula:
                continue
            declarations.append(
                declaration(feature_id, formula, damage_type, True)
            )
        declarations.extend(
            self._attack_state_riders(
                character, weapon, combat, actor_id, damage_type=damage_type,
                conditions=actor.get("conditions") or {}, check=check,
            )
        )
        return declarations

    def _mark_rider_row(self, actor: dict[str, Any], target_id: str) -> dict[str, Any] | None:
        """The Hunter's Mark damage row for one hit, or ``None``.

        The mark is not a condition and not a feature: it is the caster's own
        concentration instance, so both halves of the question -- "is this the
        target I marked" and "is it still mine" -- are answered by one read.
        A second Ranger marking the same creature keeps their own instance, and
        an ally's attack reads no instance at all.

        Foe Slayer replaces the die, never the instance: the spell catalog keeps
        the printed d6 that every other reader of Hunter's Mark still uses.
        """

        if not owned_mark(actor) or marked_target_id(actor) != str(target_id):
            return None
        effect = self.catalog.spell_effects.get(
            HUNTERS_MARK_REF.removeprefix("spell:")
        ) or {}
        sides = self._mark_damage_die(actor, effect)
        return {
            "feature_id": "hunter_s_mark",
            "owner": HUNTERS_MARK_REF,
            "source_actor_id": str(actor.get("actor_id") or ""),
            "dice": f"1d{sides}",
            "damage_type": str(effect.get("mark_damage_type") or "force"),
            "once_per_turn": False,
        }

    def _rider_dice(self, character: Any, feature_id: str) -> str:
        """The dice this rider uses, after its 14th/15th level upgrade.

        The upgrade is its own declaration and only applies when it is owned;
        an un-answered option owns neither the base rider nor its upgrade, so
        there is no "default to 1d8" path here.
        """

        base = str(
            self.features.feature_value(character, feature_id, "damage_dice", "") or ""
        )
        upgrade = {
            "divine_strike": ("improved_blessed_strikes", "divine_strike_dice"),
            "primal_strike": ("improved_elemental_fury", "primal_strike_dice"),
        }.get(feature_id)
        if upgrade is None:
            return base
        upgrade_id, key = upgrade
        if not self.features.has_feature(character, upgrade_id):
            return base
        upgraded = str(
            self.features.feature_value(character, upgrade_id, key, "") or ""
        )
        return upgraded or base

    def _hit_rider_events(
        self, combat: dict[str, Any], target: dict[str, Any],
        damage_event: dict[str, Any], rng: Any, *, attacker_id: str,
    ) -> tuple[list[dict[str, Any]], int]:
        """Roll and resist each declared rider as its own damage instance.

        Returns the events and the damage that actually landed, so the caller
        can hand the concentration save one DC computed from the whole hit
        rather than one save per instance.
        """

        declarations = damage_event.get("hit_riders")
        if not isinstance(declarations, list) or not declarations:
            return [], 0
        # The damage event is addressed to its target; the attacker is passed in
        # because the once-per-turn ledger is keyed by who made the attack.
        actor_id = str(attacker_id)
        critical = bool(damage_event.get("critical"))
        events: list[dict[str, Any]] = []
        total = 0
        for row in declarations:
            if not isinstance(row, dict) or not row.get("dice"):
                continue
            event, damage = self._rider_damage_event(row, target, critical, rng)
            if event is None:
                continue
            events.append(event)
            if row.get("once_per_turn"):
                events.append({
                    "type": "dnd2024.feature.used", "actor_id": actor_id,
                    "feature_id": str(row.get("feature_id") or ""),
                    "round": int(combat.get("round", 0)),
                    "turn_index": int(combat.get("turn_index", 0)),
                })
            total += damage
        return events, total

    def _damage_instance_event(
        self, dice: str, damage_type: str, target: dict[str, Any],
        critical: bool, rng: Any, *, half: bool = False,
    ) -> tuple[dict[str, Any] | None, int]:
        """One damage instance as its own resisted ``resource.changed``.

        Shared by every "not the main damage" producer — a second damage part,
        a hit rider, a Hunter's Mark die — so resistance is applied once per
        instance and every one of them produces the same event shape.
        """

        node = attack_damage_node(
            damage_formula=dice, modifier=0,
            critical=critical, extra_dice_formulas=[],
        )
        amount, rolls = roll_damage_node(node, rng)
        amount = max(0, amount)
        if half:
            # "Success: Half damage" 落到一个独立掷出的骰池上再折半：取整口径
            # 与规则一致（向下取整），且绝不复用失败分支的骰值。
            amount //= 2
        if amount <= 0:
            return None, 0
        damage, resistance = self._resist_damage(target, amount, damage_type)
        event: dict[str, Any] = {
            "type": "resource.changed", "resource": "hp",
            "target_id": str(target["actor_id"]),
            "delta": -damage, "amount": damage, "damage_type": damage_type,
            "critical": critical, "rolls": rolls,
        }
        if resistance:
            event["resisted"] = True
            event["resistance"] = resistance
        return event, damage

    def _rider_damage_event(
        self, row: dict[str, Any], target: dict[str, Any],
        critical: bool, rng: Any,
    ) -> tuple[dict[str, Any] | None, int]:
        """One rider row as its own resisted damage instance.

        Shared by the weapon-hit path (which rolls at commit time, after every
        window that could turn the hit into a miss) and the spell-attack path
        (which has no such window and rolls inline), so a Hunter's Mark that
        arrives with a weapon and one that arrives with a spell produce the
        same event shape and the same resistance boundary.
        """

        event, damage = self._damage_instance_event(
            str(row["dice"]), str(row.get("damage_type") or ""), target,
            critical, rng,
        )
        if event is None:
            return None, 0
        event.update({
            "feature_id": str(row.get("feature_id") or ""),
            "owner_ref": str(row.get("owner") or ""),
            # 谁打出的这一下：Superior Hunter's Prey 问的就是"你本回合对标记
            # 目标造成过伤害吗"，而伤害事件本身不携带施害者。
            "source_actor_id": str(row.get("source_actor_id") or ""),
        })
        return event, damage

    def _resolve_committed_hit(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], damage_event: dict[str, Any], damage: int, rng: Any,
        *, settle_attack_declarations: bool = False,
    ) -> list[dict[str, Any]]:
        """Submit a final hit's damage and run its trailing consequences."""

        consequences, extra_damage = self._hit_tail_events(
            instance, combat, attacker_id, target, damage_event, rng,
        )
        events = list(self._submit_hit_damage(
            damage_event,
            instance=instance, combat=combat, attacker_id=attacker_id,
            target=target, rng=rng,
        ))
        events.extend(self._multiattack_mark_events(
            self._actor_view(instance, combat, attacker_id), target,
        ))
        events.extend(consequences)
        if damage_event.get("critical"):
            events.extend(
                self._critical_hit_move_events(instance, combat, attacker_id)
            )
        # 一次命中只有一个专注 DC：额外伤害实例与主伤害合并计算，而不是每个
        # 实例各掷一次豁免。
        total_damage = damage + extra_damage
        events.extend(
            self._concentration_with_dool(instance, combat, target, total_damage, rng)
        )
        events.extend(self._hit_condition_events(
            instance, combat, attacker_id, target, damage_event, rng,
        ))
        # Keep the existing settlement boundary: direct attacks and the
        # separate hit-spell finisher settle final-hit subscribers. A standalone
        # damage-window finisher settles only its existing damage consequences.
        if settle_attack_declarations:
            events.extend(self._held_attack_hit_events(
                instance, combat, attacker_id, target, damage_event, rng,
            ))
        events.extend(self._enemy_zero_hp_feature_events(
            instance, combat, attacker_id, target, total_damage,
        ))
        if self._last_hostile_defeated(combat, target, total_damage):
            events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        return events

    def _held_attack_hit_events(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], damage_event: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Settle declarations from a final hit, never from a decision intent."""
        held = damage_event.get(HELD_HIT_KEY)
        if not isinstance(held, dict) or not held:
            return []
        return committed_hit_events(
            self, instance, combat, self._actor_view(instance, combat, attacker_id),
            target, held, rng,
        )

    def _hurl_through_hell_hit_events(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        target: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Once per turn: a failed Charisma save hurls the target through Hell.

        要求了却不满足条件时直接失败——静默降级成一次普通命中会让客户端
        以为能力已经生效。
        """

        feature_id = HURL.id
        character = self._feature_character(attacker)
        attacker_id = str(attacker["actor_id"])
        if not self.features.has_feature(character, feature_id):
            raise CombatIntentError(
                "Hurl Through Hell is not available to this actor"
            )
        if self._feature_used_this_turn(combat, attacker_id, feature_id):
            raise CombatIntentError(
                "Hurl Through Hell was already used this turn"
            )
        used = feature_usage_count(character, feature_id, feature_id)
        limit = int(self.features.feature_value(
            character, feature_id, "uses_per_long_rest", 1,
        ) or 1)
        if used >= limit:
            raise CombatIntentError(
                "Hurl Through Hell has no use left until a long rest"
            )
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.feature.used", "actor_id": attacker_id,
            "feature_id": feature_id,
            "round": int((combat.get("economy") or {}).get("round", 0) or 0),
            "turn_index": int(
                (combat.get("economy") or {}).get("turn_index", 0) or 0
            ),
        }]
        events.append({
            "type": "dnd2024.feature_usage.changed",
            "actor_id": attacker_id,
            "owner": feature_id, "feature_id": feature_id,
            "scope": USAGE_SCOPE_LONG_REST,
            "before_count": used, "after_count": used + 1,
            "use_index": used + 1,
        })
        ability = str(self.features.feature_value(
            character, feature_id, "save_ability", "cha",
        ) or "cha")
        dc = int(attacker.get("spell_save_dc", 0) or 0)
        check = self._saving_throw_check(
            instance, combat, target, str(target["actor_id"]),
            ability, dc, attacker_id, events, rng,
        )
        check["source_feature"] = feature_id
        events.append(check)
        if check["success"]:
            return events
        declared = self.features.feature_value(
            character, feature_id, "damage_exempt_creature_types", None,
        )
        exempt = {str(code).lower() for code in (declared or ["fiend"])}
        creature_type = str(target.get("creature_type") or "").lower()
        if creature_type not in exempt:
            damage_type = str(self.features.feature_value(
                character, feature_id, "damage_type", "psychic",
            ) or "psychic")
            dice = str(self.features.feature_value(
                character, feature_id, "damage_dice", "8d10",
            ) or "8d10")
            damage, _total = self._damage_instance_event(
                dice, damage_type, target, False, rng,
            )
            if damage is not None:
                damage["feature_id"] = feature_id
                damage["source_actor_id"] = attacker_id
                events.append(damage)
        events.append({
            "type": "condition.applied",
            "target_id": str(target["actor_id"]),
            "condition": str(self.features.feature_value(
                character, feature_id, "condition", "incapacitated",
            ) or "incapacitated"),
            # "until the end of your next turn" 里的 your 是邪术师：归属是
            # 施法者，不是被失能的目标自己。
            "duration": "caster_next_turn_end",
            "turns_remaining": 2,
            "source_actor_id": attacker_id,
            "feature_id": feature_id,
            "banishment": "guided",
        })
        return events

    def _hit_tail_events(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], damage_event: dict[str, Any], rng: Any,
        *, multiplier: float = 1.0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Everything a landed hit owes beyond its main damage instance.

        Three producers, one entry point: the attack's second damage part, the
        feature riders (Hunter's Mark, Radiant/Divine/Primal strikes) and the
        stat-block riders (prone / grapple / condition / push).  Every path that
        can land a hit calls this — the straight-through one, the post-roll
        decision finishers and the hit-triggered spell — because a hit that
        skipped it would silently lose half its text.

        ``multiplier`` is for the reactions that halve "the damage from that
        attack" (Uncanny Dodge): the reduction applies to every instance of the
        attack, not only to the first one.
        """

        extra_events, extra_damage = self._extra_damage_events(
            combat, target, damage_event, rng,
        )
        rider_events, rider_damage = self._hit_rider_events(
            combat, target, damage_event, rng, attacker_id=attacker_id,
        )
        if multiplier != 1.0:
            for event in (*extra_events, *rider_events):
                if event.get("type") != "resource.changed":
                    continue
                scaled = int(int(event.get("amount", 0) or 0) * multiplier)
                event["amount"] = scaled
                event["delta"] = -scaled
            extra_damage = int(extra_damage * multiplier)
            rider_damage = int(rider_damage * multiplier)
        events = [*extra_events, *rider_events]
        events.extend(self._statblock_rider_events(
            instance, combat, attacker_id, target, damage_event, rng,
            events=events,
        ))
        return events, extra_damage + rider_damage

    def _extra_damage_events(
        self, combat: dict[str, Any], target: dict[str, Any],
        damage_event: dict[str, Any], rng: Any,
    ) -> tuple[list[dict[str, Any]], int]:
        """The second damage instance of an attack, resisted on its own.

        ``2d6+4 slashing plus 1d6 fire`` is one hit and two instances: taking
        resistance once on the sum would let a fire-resistant target halve the
        slashing too.  The dice are rolled here rather than at declaration time
        so a window that turns the hit into a miss wastes no roll.
        """

        parts = damage_event.get("extra_damage_parts")
        if not isinstance(parts, list) or not parts:
            return [], 0
        critical = bool(damage_event.get("critical"))
        events: list[dict[str, Any]] = []
        total = 0
        for row in parts:
            if not isinstance(row, dict) or not row.get("dice"):
                continue
            event, landed = self._damage_instance_event(
                str(row["dice"]), str(row.get("type") or ""), target,
                critical, rng,
            )
            if event is None:
                continue
            events.append(event)
            total += landed
        return events, total

    def _statblock_rider_events(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], damage_event: dict[str, Any], rng: Any,
        *, events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """What a stat-block hit does beyond damage, in declaration order.

        Three of the four kinds ask the same question first — is the target
        small enough for this rider — so the size gate lives here instead of in
        each kind.  A rider whose gate cannot be answered (the target's size is
        unknown) is skipped and said so: a wolf that silently knocks down a
        Huge dragon is worse than a wolf that does nothing.
        """

        riders = damage_event.get("statblock_riders")
        if not isinstance(riders, list) or not riders:
            return []
        target_id = str(target["actor_id"])
        applied: list[dict[str, Any]] = []
        for rider in riders:
            if not isinstance(rider, dict):
                continue
            limit = str(rider.get("max_target_size") or "")
            if limit and not size_at_most(str(target.get("size") or ""), limit):
                applied.append({
                    "type": "dnd2024.rider.skipped", "target_id": target_id,
                    "source_actor_id": attacker_id,
                    "kind": str(rider.get("kind") or ""),
                    "reason": "size_gate",
                })
                continue
            save = rider.get("save")
            if isinstance(save, dict) and save.get("ability"):
                check = self._saving_throw_check(
                    instance, combat, target, target_id,
                    str(save["ability"]), int(save.get("dc") or 0),
                    attacker_id, [*events, *applied], rng,
                )
                applied.append(check)
                if check["success"]:
                    # 失败才吃效果：成功就等于这条 rider 没有发生，不必再
                    # 发一条空事件，`check.resolved` 已经把掷骰留在日志里。
                    continue
            kind = str(rider.get("kind") or "")
            if kind == "push":
                direction = (
                    1 if self._position(combat, target_id)
                    >= self._position(combat, attacker_id) else -1
                )
                applied.append({
                    "type": "dnd2024.position.changed", "actor_id": target_id,
                    "distance": direction * int(rider.get("distance") or 0),
                    "forced": True,
                    "source_rider": "push",
                })
                continue
            condition = rider_condition(rider)
            if not condition:
                continue
            meta: dict[str, Any] = {
                "type": "condition.applied", "target_id": target_id,
                "condition": condition,
                "source_actor_id": attacker_id,
                "source_rider": kind,
                # 缺省按遭遇计时。没有"起身""挣脱"这两个动作时，一条没有
                # 时限的击倒会跟着角色卡活到下一场战斗——那是数据泄漏，不是
                # 规则。登记 GAP-38。
                "duration": str(rider.get("duration") or "combat"),
            }
            if (
                meta["duration"] == "target_turn_end"
                and str((combat.get("economy") or {}).get("actor_id") or "") == target_id
            ):
                # 在目标自己的回合里给它"到其下个回合结束"：这一次回合结束
                # 不是"下个"，所以要多撑一个回合边界。
                meta["expires_after"] = 2
            if kind == "grapple":
                # 挣脱 DC 是擒抱的一部分事实，跟着条件走；挣脱本身是一个
                # 技能检定动作（下一片的消费者），这里只把数字留下。
                meta["escape_dc"] = int(rider.get("escape_dc") or 0)
            applied.append(meta)
        return applied

    def _submit_hit_damage(
        self, damage_event: dict[str, Any], *,
        instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Commit a final hit's damage, then any deferred feature use.

        Hit damage is rolled once and held while the defender decides on
        Shield/Uncanny Dodge or the attacker decides on a hit-triggered spell.
        Only the paths that actually land the damage call this helper, so a
        reaction that turns the hit into a miss never records a feature use or
        applies a mastery condition.  The internal ``feature_use`` and
        ``mastery_effects`` metadata never reach the wire shape of
        ``resource.changed``.
        """

        event = deepcopy(damage_event)
        feature_use = event.pop("feature_use", None)
        # 一次命中可以同时消耗两项额度（例如潜行 + 巨兽杀手），所以除了单条
        # 的 feature_use 之外还要接受一组。
        extra_feature_uses = event.pop("feature_uses", None)
        mastery_effects = event.pop("mastery_effects", None)
        # Hit riders (Radiant/Divine/Primal strikes) are settled by
        # ``_hit_rider_events``: their dice are a separate damage instance, so
        # they can neither be merged into this event nor ride along on the wire.
        event.pop("hit_riders", None)
        event.pop("hit_condition_effects", None)
        event.pop("brutal_strike_effects", None)
        cunning_skip = event.pop("cunning_strike_skipped", None)
        # 第二段伤害与 stat block rider 由 ``_resolve_committed_hit`` 结算：
        # 它们是独立事件，不能混进主伤害的线上形状。
        event.pop("extra_damage_parts", None)
        event.pop("statblock_riders", None)
        # 命中触发的声明只给决策路径用：待定的命中法术决策在创建时留了一份
        # 伤害事件的副本，这里从线上形状里摘掉，客户端不会看到一个它用不上的键。
        event.pop(HELD_HIT_KEY, None)
        events: list[dict[str, Any]] = [event]
        if isinstance(feature_use, dict):
            events.append({"type": "dnd2024.feature.used", **feature_use})
        if isinstance(cunning_skip, dict):
            events.append({"type": "dnd2024.cunning_strike.skipped", **cunning_skip})
        if isinstance(extra_feature_uses, list):
            for row in extra_feature_uses:
                if isinstance(row, dict) and row.get("feature_id"):
                    events.append({"type": "dnd2024.feature.used", **row})
        if isinstance(mastery_effects, list):
            events.extend(self._mastery_commit_events(
                instance, combat, attacker_id, target, event["target_id"],
                mastery_effects, rng,
            ))
        return events

    def _commit_hit_damage(
        self, instance: Any, combat: dict[str, Any], damage_event: dict[str, Any],
        rng: Any, *, attacker_id: str = "",
    ) -> list[dict[str, Any]]:
        """``_submit_hit_damage`` with the context mastery effects need.

        Reaction and triggered-spell paths land a hit they did not compute, so
        they resolve the target here instead of threading it through every
        decision payload.  Inert for them: only weapon hits put ``mastery_effects``
        on the event, and those were built by the attacker's own path.
        """

        target = self._actor_view(instance, combat, str(damage_event["target_id"]))
        return self._submit_hit_damage(
            damage_event,
            instance=instance, combat=combat, attacker_id=attacker_id,
            target=target, rng=rng,
        )

    def _mastery_commit_events(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], target_id: str,
        mastery_effects: list[Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Mastery effects that a committed hit actually lands.

        Called only after the hit is final, so a Shield or Uncanny Dodge that
        turned it into a miss never pushes anyone or rolls a save.  Conditions,
        pushes and save-or-condition all come through here for that reason.
        """

        events: list[dict[str, Any]] = []
        for effect in mastery_effects:
            if not isinstance(effect, dict):
                continue
            kind = str(effect.get("kind") or "condition")
            if kind == "push":
                limit = str(effect.get("size_at_most") or "")
                if limit and not size_at_most(str(target.get("size") or ""), limit):
                    continue
                feet = int(effect.get("feet") or 0)
                if feet <= 0:
                    continue
                # 方向不在这里算：事件只说"推多远、远离谁"。1D 与将来的 2D
                # 都由位置权威（reducer）解释这一句，发射点不认识几何。
                events.append({
                    "type": "dnd2024.position.changed",
                    "actor_id": target_id,
                    "feet": feet,
                    "away_from": attacker_id,
                    # 过渡期双写：旧事件重放与既有消费者仍读这个有符号值。
                    "distance": feet * (
                        1 if self._position(combat, target_id)
                        >= self._position(combat, attacker_id) else -1
                    ),
                    "forced": True,
                    "source_rider": "push",
                })
                continue
            if kind == "save_condition":
                ability = str(effect.get("ability") or "con")
                dc = int(effect.get("dc") or 0)
                if dc <= 0:
                    # DC 由命中方算好写进来；缺失说明声明有误，fail closed：
                    # 不掷骰、也不施加条件，避免"必然失败"的豁免。
                    continue
                check = self._saving_throw_check(
                    instance, combat, target, target_id, ability, dc,
                    attacker_id, events, rng,
                )
                # `_saving_throw_check` 只把"传奇抗性"之类的补充追加到列表，
                # 豁免本身是返回值：不 append 就只剩 apply、没有掷骰记录。
                events.append(check)
                if check["success"]:
                    continue
                events.append({
                    "type": "condition.applied",
                    "target_id": target_id,
                    "condition": str(effect["condition"]),
                    "duration": str(effect.get("duration") or "until_stand"),
                    "source_actor_id": attacker_id,
                })
                continue
            if not effect.get("condition"):
                continue
            events.append({
                "type": "condition.applied", "target_id": target_id,
                **effect,
            })
        return events

    def _enemy_zero_hp_feature_events(
        self, instance: Any, combat: dict[str, Any], source_actor_id: str,
        target: dict[str, Any], damage_taken: int,
    ) -> list[dict[str, Any]]:
        """Temp-HP grants owned by the creature that reduced this enemy to 0 HP.

        Called at every final damage landing point with the damage that
        settlement actually delivered -- after resistance, saves and riders --
        so the trigger agrees with the existing defeat check. SRD 5.2.1 p76
        also grants the benefit when someone else defeats an enemy within
        the feature owner's declared range.

        Features opt in through the closed ``trigger`` / ``temp_hp_ability`` /
        ``temp_hp_class_ref`` / ``temp_hp_minimum`` payload; the engine never
        branches on a feature, class or subclass name.
        """

        if target.get("kind") != "enemy":
            return []
        dealt = int(damage_taken)
        hp_before = int(target.get("hp", 0) or 0)
        # A target already at 0 HP was defeated earlier and cannot be farmed;
        # damage that does not reach 0 is not a defeat at all.
        if dealt <= 0 or hp_before <= 0 or hp_before - dealt > 0:
            return []
        events: list[dict[str, Any]] = []
        for row in self._all_targets(instance, combat):
            recipient_id = str(row["actor_id"])
            kind, _raw = _actor_kind(recipient_id)
            if kind in {"player", "companion"}:
                events.extend(self._defeat_recipient_events(
                    instance, combat, source_actor_id, target, recipient_id,
                ))
        return events

    def _defeat_recipient_events(
        self, instance: Any, combat: dict[str, Any], source_actor_id: str,
        target: dict[str, Any], recipient_id: str,
    ) -> list[dict[str, Any]]:
        view = self._actor_view(instance, combat, recipient_id)
        character = self._feature_character(view)
        state = self.features.class_state(character)
        if state is None:
            # 没有职业的同伴残表不拥有任何职业特性。
            return []
        events: list[dict[str, Any]] = []
        for definition in self.features.features_for(character):
            parameters = definition.parameters
            if parameters.get("trigger") != ENEMY_REDUCED_TO_ZERO_HP:
                continue
            if recipient_id != source_actor_id:
                radius = parameters.get("other_defeat_range_ft")
                if radius is None or self._distance(
                    combat, recipient_id, str(target["actor_id"]),
                ) > radius:
                    continue
            ability = parameters.get("temp_hp_ability")
            class_ref = parameters.get("temp_hp_class_ref")
            minimum = parameters.get("temp_hp_minimum")
            if not isinstance(ability, str) or not isinstance(class_ref, str):
                continue
            if isinstance(minimum, bool) or not isinstance(minimum, int):
                continue
            class_id = class_ref.removeprefix("class:")
            if not class_id:
                continue
            if state[0] != class_id:
                # 授予生效却拿不到对应职业等级，说明目录与成长表已经不一致：
                # 任何"凑一个等级"的回退都会算错数值，必须显式失败。
                raise CombatIntentError(
                    f"{definition.id}.temp_hp_class_ref is {class_ref} but the "
                    f"owning character's class is {state[0]}"
                )
            modifier = ability_modifier(
                int((view.get("abilities") or {}).get(ability, 10) or 10)
            )
            amount = max(minimum, modifier + state[1])
            events.append({
                "type": "resource.changed", "resource": "temp_hp",
                "target_id": recipient_id, "source_actor_id": source_actor_id,
                "defeated_actor_id": str(target["actor_id"]),
                "delta": amount, "amount": amount, "rolls": [],
                "feature_id": definition.id, "owner": definition.owner,
                "ability": ability, "ability_modifier": modifier,
                "class_ref": class_ref, "class_level": state[1],
            })
        return events
