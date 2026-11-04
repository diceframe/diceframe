"""Deterministic event resolution stage for D&D 2024 combat."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.rulesets.dnd2024.character.builder import ability_modifier
from .action_adapter import (
    attack_damage_node,
    average_damage,
    double_dice_counts,
    formula_node_from_dice_formula,
    heal_ability_modifier_node,
    maximize_dice_nodes,
    roll_damage_node,
    spell_formula_node,
)
from .primitives import (
    CombatIntentError,
    RAGE_MAX_ROUNDS,
    UNARMED_STRIKE_REF,
    actor_kind as _actor_kind,
    canonical as _canonical,
    companion_actor as _companion_actor,
    enemy_actor as _enemy_actor,
    player_actor as _player_actor,
)
from .check_modes import (
    condition_check_bonus_rolls,
    condition_flags,
    mode_from_sources,
)
from .check_windows import CHECK_WINDOW_DECISION, DAMAGE_WINDOW_DECISION
from .reaction_spells import COUNTER_SPELL_DECISION, HIT_REACTION_DECISION, effective_armor_class
from .triggered_spells import HIT_SPELL_DECISION
from .weapon_masteries import WEAPON_MASTERY_EFFECTS, WEAPON_MASTERY_FEATURE_ID


def _without_consumed_conditions(
    view: dict[str, Any], consumed: set[tuple[str, str]],
) -> dict[str, Any]:
    """Hide conditions an earlier attack roll of the same batch consumed.

    一次施法只产出一个事件批次，状态要到 ``apply_batch`` 才落地；多道射线在
    同一批次内循环时，每道都会读到施法前的条件集合。RAW 规定"你的下一次攻击
    掷骰"类条件在第一次掷骰后即消耗，因此循环用本函数的副本逐 beam 隐藏已消耗
    条件，既不提前改状态，也不让后续射线重复受益。
    """

    conditions = {
        code: entry
        for code, entry in (view.get("conditions") or {}).items()
        if (str(view.get("actor_id") or ""), str(code)) not in consumed
    }
    return {**view, "conditions": conditions}


class CombatResolutionMixin:
    """Turn validated intents and server RNG into immutable event batches."""

    __slots__ = ()

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
        return average_damage(
            str(attack.get("damage") or ""),
            flat_bonus=flat_bonus,
        )

    def _start_combat_event(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        enemies: dict[str, dict[str, Any]] = {}
        initiatives: list[dict[str, Any]] = []
        positions: dict[str, int] = {}
        companions = self._active_companions(instance)
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
        for companion_id in sorted(companions):
            canonical = companions[companion_id].get("ruleset_character") or {}
            actor_id = _companion_actor(companion_id)
            roll = int(rng.randint(1, 20))
            modifier = int(canonical.get("derived", {}).get("initiative", 0) or 0)
            initiatives.append({
                "actor_id": actor_id, "roll": roll, "modifier": modifier,
                "total": roll + modifier, "kind": "companion",
            })
            # Companion 缺省与队伍同一起始位置，不引入复杂布阵。
            positions[actor_id] = int(intent.get("player_positions", {}).get(companion_id, 0) or 0)
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
                "creature_type": str(raw.get("creature_type") or ""),
                "counterspell_dc": int(raw.get("counterspell_dc", 0) or 0),
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
        first = self._actor_view_from_data(instance, enemies, order[0], companions)
        return {
            "type": "dnd2024.combat.started", "round": 1, "initiative": order,
            "initiative_rolls": initiatives, "enemies": enemies, "positions": positions,
            "economy": self._fresh_economy(first), "position_mode": "theater",
            "reactions": {actor_id: 1 for actor_id in order},
            "encounter_instance_id": str(intent.get("encounter_instance_id") or ""),
            "encounter_preset_id": str(intent.get("encounter_preset_id") or ""),
            "origin_step_id": str(intent.get("origin_step_id") or ""),
            "mode": str(intent.get("mode") or "sandbox"),
            "adventure_binding": deepcopy(intent.get("adventure_binding")),
        }

    @staticmethod
    def _spell_end_trigger_events(
        actor: dict[str, Any], trigger: str,
    ) -> list[dict[str, Any]]:
        """Events that end spells whose condition declares ``ends_on`` ``trigger``.

        Read from the *pre-resolution* actor view, so a condition applied by the
        very spell cast in this batch is never ended by that same cast: the old
        spell ends, the new one takes over intact.

        A condition owned by a concentration spell ends the whole spell: one
        ``dnd2024.concentration.ended`` per owner, and the existing reducer
        cascade removes every condition the owner had applied (to all targets).
        Conditions with no owner are removed individually.

        ``damage_dealt`` is a declared trigger code without its own call site:
        every damage in the engine originates from an attack roll or a spell
        cast, and both of those already trigger first.
        """

        events: list[dict[str, Any]] = []
        owners: set[str] = set()
        for condition_id, meta in (actor.get("conditions") or {}).items():
            if not isinstance(meta, dict):
                continue
            if trigger not in (meta.get("ends_on") or []):
                continue
            owner = str(meta.get("concentration_owner") or "")
            if not owner:
                events.append({
                    "type": "condition.removed", "target_id": actor["actor_id"],
                    "condition": condition_id,
                })
                continue
            if owner in owners:
                continue
            owners.add(owner)
            events.append({
                "type": "dnd2024.concentration.ended", "actor_id": owner,
                "reason": "spell_ended_early",
            })
        return events

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
        """

        if amount <= 0 or not damage_type:
            return amount, ""
        for condition_id, meta in (target.get("conditions") or {}).items():
            types = set((self.catalog.conditions.get(condition_id) or {}).get(
                "resist_damage_types"
            ) or [])
            if isinstance(meta, dict):
                types.update(
                    str(kind) for kind in meta.get("resist_damage_types") or []
                    if isinstance(kind, str) and kind
                )
            if damage_type in types:
                return amount // 2, condition_id
        return amount, ""

    def _attack_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
        *, spend_attack_action: bool = True,
        actor_view: dict[str, Any] | None = None,
        target_view: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        actor_id = str(intent["actor_id"])
        target_id = str(intent["target_id"])
        actor = (
            actor_view if actor_view is not None
            else self._actor_view(instance, combat, actor_id)
        )
        target = (
            target_view if target_view is not None
            else self._actor_view(instance, combat, target_id)
        )
        ability = ""
        if actor["kind"] in {"player", "companion"}:
            # Companion 是"己方角色"：武器与徒手攻击走玩家同一套目录/能力确定性链，
            # 不走 enemy attack profile。
            weapon = self._declared_attack(actor, intent)
            if weapon is None:  # pragma: no cover - validation guards this
                raise CombatIntentError("attack profile is not available to the actor")
            distance = self._distance(combat, actor_id, target_id)
            # 真正的远程武器 vs 近战武器被投掷：2024 Thrown 规定，投掷一把 melee
            # weapon 时，攻击与伤害沿用**近战使用该武器时相同的属性修正**。所以
            # "在远处使用" 不等于 "这是远程武器"。属性选择只应被前者（真远程武器）
            # 排除，否则武僧把长矛/手斧扔出去就会被强制成 DEX。
            is_ranged_weapon = bool(weapon.get("ranged"))
            ability = "dex" if is_ranged_weapon else "str"
            if weapon.get("finesse"):
                ability = max(("str", "dex"), key=lambda key: ability_modifier(actor["abilities"][key]))
            # 职业特性投影出来的属性选择（武艺：徒手打击与 Monk Weapon 都可以改用
            # 力量或敏捷）。客户端不能每次手选属性，因此第一版采用确定性规则——
            # 取修正值较高者，由 feature boundary 投影到武器档案上，Combat 不判断职业。
            choices = [
                str(key) for key in weapon.get("ability_choice") or []
                if str(key) in actor["abilities"]
            ]
            if choices and not is_ranged_weapon:
                ability = max(
                    choices,
                    key=lambda key: ability_modifier(actor["abilities"][key]),
                )
            modifier = ability_modifier(actor["abilities"][ability])
            if weapon.get("unarmed") or (
                f"weapon_category:{weapon.get('category')}" in actor["weapon_category_refs"]
            ):
                # 徒手打击是每个 player-like actor 都会使用的天然攻击：熟练加值始终生效，
                # 不依赖 weapon_category 熟练列表。
                proficiency = actor["proficiency_bonus"]
            else:
                proficiency = 0
            attack_bonus = modifier + proficiency
            if is_ranged_weapon:
                # 战斗风格专长的远程武器加值是特性投影字段：只在真远程武器
                # 生效，投掷近战武器不适用（与上方 Thrown 语义划分一致）。
                attack_bonus += int(actor.get("ranged_attack_bonus") or 0)
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
        from .check_modes import attack_mode, mode_from_sources

        # Lucky's advantage face is declared before the roll: it adds one
        # source and is consumed unconditionally (validation proves the pool).
        spend_luck = bool(intent.get("spend_luck"))
        extra_sources: tuple[dict[str, Any], ...] = (
            ({"code": "lucky", "mode": "advantage"},) if spend_luck else ()
        )
        check_mode = attack_mode(
            self, instance, actor, target, range_disadvantage,
            use_grant=spend_attack_action, extra_sources=extra_sources,
        )
        # 未受训护甲：武器/徒手攻击（力/敏掷骰）具有劣势。这里内联而不进
        # check_modes，因为 spell_attack 也用 attack_mode 但走施法属性，不能误伤。
        if bool((actor.get("armor_training") or {}).get("untrained_armor_worn")):
            sources = [
                *check_mode["sources"],
                {"code": "untrained_armor", "mode": "disadvantage"},
            ]
            check_mode = {"mode": mode_from_sources(sources), "sources": sources}
        advantage, disadvantage = check_mode["mode"] == "advantage", check_mode["mode"] == "disadvantage"
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if advantage or disadvantage else None
        natural = max(first, second) if advantage and second else min(first, second) if disadvantage and second else first
        check_bonus_rolls = condition_check_bonus_rolls(
            self.catalog.conditions, actor["conditions"], "attack", rng,
        )
        check_bonus = sum(row["roll"] for row in check_bonus_rolls)
        total = natural + attack_bonus + check_bonus
        target_ac = effective_armor_class(target)
        # 精通重击 / 高效重击只拓宽武器与徒手攻击的重击阈值：取已拥有特性中
        # 最小的阈值，缺省 20。天然 1 的失手判定继续由下方 hit 组合表达。
        crit_threshold = self.features.min_feature_value(
            self._feature_character(actor), "crit_threshold", 20,
        )
        critical = natural >= crit_threshold
        hit = natural != 1 and (critical or total >= target_ac)
        crit_on_hit = bool(
            hit and not critical
            and condition_flags(self.catalog.conditions, target["conditions"])["crit_on_hit_within_5"]
            and distance <= 5
        )
        if crit_on_hit:
            # 麻痹/昏迷：5 尺内攻击者的"命中"直接变重击（失手不升级）。
            critical = True
        events: list[dict[str, Any]] = [
            # 提前终止先于消耗支付落账：预约的是"发起攻击检定"这一事实，
            # 命中与否、动作是否还能支付都不影响它（借机攻击同样触发）。
            *self._spell_end_trigger_events(actor, "attack_roll"),
            *([self._attack_cost_event(combat, actor)] if spend_attack_action else []),
        ]
        if spend_luck:
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
                "resource_id": "lucky_points",
            })
        check_event = {
            "type": "check.resolved", "kind": "attack", "actor_id": actor_id,
            "advantage_mode": check_mode["mode"], "advantage_sources": check_mode["sources"],
            "target_id": target_id, "rolls": [value for value in (first, second) if value],
            "natural": natural, "modifier": attack_bonus,
            "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
            "total": total, "target": target_ac, "success": hit, "critical": critical,
        }
        if crit_threshold != 20:
            # 默认 20 时不写字段，避免对既有事件消费方的无谓扰动。
            check_event["crit_threshold"] = crit_threshold
        rage_sustain = bool(
            target["kind"] == "enemy" and "rage" in actor["conditions"]
            and str((combat.get("economy") or {}).get("actor_id") or "") == str(actor_id)
        )
        if hit:
            settlement = {
                "actor_id": actor_id, "target_id": target_id,
                "weapon": deepcopy(weapon), "ability": ability, "modifier": modifier,
                "damage_formula": damage_formula, "damage_type": damage_type,
                "crit_on_hit": crit_on_hit, "rage_sustain": rage_sustain,
                "exclude_bards": [], "spent_reactions": [],
                "remaining_windows": [], "intent": deepcopy(intent),
            }
            window = self._attack_check_window_offer(
                instance, combat, actor, target, check_event, settlement,
            )
            if window is not None:
                # The check has been rolled but nothing has landed: no damage
                # die is rolled and no resolution event is emitted until the
                # window owner decides.
                events.append({"type": "dnd2024.decision.pending", **window})
                return events
        events.append(check_event)
        if rage_sustain:
            # 2024 狂暴：在你的回合对敌人进行一次攻击检定即延长一轮，
            # 命中与否都算；借机攻击发生在别人的回合，不延长狂暴。
            events.append({
                "type": "dnd2024.condition.sustained", "target_id": actor_id,
                "condition": "rage",
            })
        if hit:
            events.extend(self._hit_settlement_events(
                instance, combat, actor, target, weapon, check_event, intent, rng,
                ability=ability, modifier=modifier,
                damage_formula=damage_formula, damage_type=damage_type,
                crit_on_hit=crit_on_hit,
            ))
        else:
            events.extend(self._miss_mastery_events(
                combat, actor, target, weapon, modifier, damage_type, rng,
            ))
        events.extend(self._consume_attack_conditions(actor_id, target_id, actor, target))
        return events

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

    def _miss_mastery_events(
        self, combat: dict[str, Any], actor: dict[str, Any], target: dict[str, Any],
        weapon: dict[str, Any], modifier: int, damage_type: str, rng: Any,
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
        events.extend(self._concentration_events(target, damage, rng))
        if self._last_hostile_defeated(combat, target, damage):
            events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        return events

    def _hit_settlement_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        target: dict[str, Any], weapon: dict[str, Any], check: dict[str, Any],
        intent: dict[str, Any], rng: Any, *, ability: str, modifier: int,
        damage_formula: str, damage_type: str, crit_on_hit: bool = False,
        exclude_bards: tuple[str, ...] = (), spent_reactions: tuple[str, ...] = (),
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
        if sneak_dice:
            extra_dice.append(sneak_dice)
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
        if sneak_dice:
            # 延迟提交：只有这次命中伤害最终落地才记一次偷袭使用。
            # Shield 改判落空时不会走到提交辅助方法，因此不会记账。
            damage_event["feature_use"] = {
                "actor_id": actor_id,
                "feature_id": "sneak_attack",
                "round": int(combat.get("round", 0)),
                "turn_index": int(combat.get("turn_index", 0)),
            }
        mastery = self._weapon_mastery_effect(actor, weapon)
        if mastery is not None and str(mastery.get("on")) == "hit" and (
            not mastery.get("requires_damage") or damage > 0
        ):
            # Held like ``feature_use``: an attack that a reaction turns into a
            # miss must not leave its mastery condition behind.
            damage_event["mastery_effects"] = [{
                "condition": str(mastery["condition"]),
                "duration": str(mastery["duration"]),
                "source_actor_id": actor_id,
            }]
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
        )
        if reaction is not None and str(target["actor_id"]) in spent_reactions:
            # 同一反应预算已被本次攻击的检定面花掉：不能再开防守反应窗。
            reaction = None
        if reaction or damage_window or offer:
            return [{"type": "dnd2024.decision.pending", **(reaction or damage_window or offer)}]
        return self._resolve_committed_hit(
            instance, combat, actor_id, target, damage_event, damage, rng,
        )

    def _resolve_committed_hit(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], damage_event: dict[str, Any], damage: int, rng: Any,
    ) -> list[dict[str, Any]]:
        """Submit a final hit's damage and run its trailing consequences."""

        events = list(self._submit_hit_damage(damage_event))
        events.extend(self._concentration_with_dool(instance, combat, target, damage, rng))
        events.extend(self._enemy_defeated_feature_events(
            instance, combat, attacker_id, target, damage,
        ))
        if self._last_hostile_defeated(combat, target, damage):
            events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        return events

    def _maybe_overchannel(self, intent: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
        """门控已由 _validate_spell 完成（fail closed）；此处只做变换。"""

        if not intent.get("maximize_damage"):
            return node
        return maximize_dice_nodes(node)

    def _spell_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(intent["actor_id"])
        spell_ref = str(intent["spell_ref"])
        # 起源专长授予的法术按专长自己的施法属性结算命中、DC 与治疗加值。
        actor = self._spell_caster(self._actor_view(instance, combat, actor_id), spell_ref)
        spell = self.spells.get(spell_ref)
        if spell is None:  # pragma: no cover - validation guards this
            raise CombatIntentError("spell is missing")
        effect = self.catalog.spell_effects[spell_ref.removeprefix("spell:")]
        if effect["mode"] == "triggered_damage":
            return self._triggered_spell_events(instance, combat, intent, rng)
        slot_level = int(intent.get("slot_level", spell["level"]) or 0)
        target_ids = intent.get("target_ids")
        if not isinstance(target_ids, list):
            target_ids = [intent.get("target_id")]
        target_ids = [str(item) for item in target_ids if item]
        events: list[dict[str, Any]] = [
            # 施法本身就会终止"施法即结束"的旧法术（例如隐形术的自重刷）。
            *self._spell_end_trigger_events(actor, "spell_cast"),
        ]
        ended_owners = {
            str(event["actor_id"]) for event in events
            if event.get("type") == "dnd2024.concentration.ended"
        }
        events.extend(self._spell_cost_events(actor, spell, slot_level))
        if effect.get("concentration"):
            if actor.get("concentration") and actor_id not in ended_owners:
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
        # Lucky's advantage face declares one extra source for the first
        # spell attack roll of this cast; validation proves the pool.
        spend_luck = bool(intent.get("spend_luck")) and str(effect["mode"]) == "spell_attack"
        if spend_luck:
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
                "resource_id": "lucky_points",
            })
        defeated_targets: set[str] = set()
        # 多道射线/多目标按目标累计本次伤害：每束都用施法前的 HP 判断会漏掉
        # 几束合计才击倒的目标，全部敌人倒下时战斗也不会结束。
        dealt: dict[str, int] = {}
        # 同一次施法的多道射线共用一个事件批次：已消耗条件只在局部隐藏，
        # 事件仍在批次末尾统一移除，状态写入时机不变。
        consumed_conditions: set[tuple[str, str]] = set()
        for ray_index, target_id in enumerate(target_ids):
            target = self._actor_view(instance, combat, target_id)
            mode = str(effect["mode"])
            succeeded = False
            # 光环免疫拦截要把来源写回本次豁免事件；非 saving_throw 路径为 None。
            save_check_index: int | None = None
            if mode == "spell_attack":
                from .check_modes import attack_mode

                visible_actor = _without_consumed_conditions(actor, consumed_conditions)
                visible_target = _without_consumed_conditions(target, consumed_conditions)
                extra_sources = (
                    ({"code": "lucky", "mode": "advantage"},)
                    if spend_luck and ray_index == 0 else ()
                )
                check_mode = attack_mode(
                    self, instance, visible_actor, visible_target, False,
                    extra_sources=extra_sources,
                )
                rolls = [int(rng.randint(1, 20))]
                if check_mode["mode"]:
                    rolls.append(int(rng.randint(1, 20)))
                roll = max(rolls) if check_mode["mode"] == "advantage" else min(rolls)
                bonus = int(actor["spell_attack_bonus"])
                check_bonus_rolls = condition_check_bonus_rolls(
                    self.catalog.conditions, visible_actor["conditions"], "attack", rng,
                )
                # 加值骰逐掷骰各一枚：多道射线逐 beam 各掷一次。
                check_bonus = sum(row["roll"] for row in check_bonus_rolls)
                total = roll + bonus + check_bonus
                target_ac = effective_armor_class(target)
                succeeded = roll != 1 and (roll == 20 or total >= target_ac)
                critical = roll == 20
                if (
                    succeeded and not critical
                    and condition_flags(self.catalog.conditions, target["conditions"])["crit_on_hit_within_5"]
                    and self._distance(combat, actor_id, target_id) <= 5
                ):
                    # 法术攻击也是攻击检定：5 尺内命中麻痹/昏迷目标自动重击。
                    critical = True
                events.append({
                    "type": "check.resolved", "kind": "spell_attack", "actor_id": actor_id,
                    "target_id": target_id, "rolls": rolls, "natural": roll,
                    "advantage_mode": check_mode["mode"], "advantage_sources": check_mode["sources"],
                    "modifier": bonus,
                    "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
                    "total": total,
                    "target": target_ac, "success": succeeded, "critical": critical,
                })
                if succeeded:
                    damage_node = spell_formula_node(
                        str(effect["damage"]), effect.get("upcast_damage"),
                        spell, slot_level, actor,
                    )
                    if critical:
                        damage_node = double_dice_counts(damage_node)
                    damage_node = self._maybe_overchannel(intent, damage_node)
                    damage, rolls = roll_damage_node(damage_node, rng)
                    damage_type = str(
                        intent.get("damage_type") or effect.get("damage_type") or ""
                    )
                    damage, resistance = self._resist_damage(target, damage, damage_type)
                    damage_event = {
                        "type": "resource.changed", "resource": "hp", "target_id": target_id,
                        "delta": -damage, "amount": damage,
                        "damage_type": damage_type,
                        "critical": critical, "rolls": rolls,
                    }
                    if resistance:
                        damage_event["resisted"] = True
                        damage_event["resistance"] = resistance
                    events.append(damage_event)
                    events.extend(self._concentration_events(target, damage, rng))
                    prior_dealt = dealt.get(target_id, 0)
                    dealt[target_id] = prior_dealt + damage
                    events.extend(self._enemy_defeated_feature_events(
                        instance, combat, actor_id, target, dealt[target_id],
                        prior_damage=prior_dealt,
                    ))
                    if target["kind"] == "enemy" and target["hp"] - dealt[target_id] <= 0:
                        defeated_targets.add(target_id)
                consumed_events = self._consume_attack_conditions(
                    actor_id, target_id, visible_actor, visible_target,
                )
                consumed_conditions.update(
                    (str(event["target_id"]), str(event["condition"]))
                    for event in consumed_events
                )
                events.extend(consumed_events)
            elif mode == "saving_throw":
                save_ability = str(effect["save"])
                modifier = int(target["saving_throws"].get(save_ability, 0) or 0)
                auto_fail = save_ability in condition_flags(
                    self.catalog.conditions, target["conditions"],
                )["auto_fail_saves"]
                if auto_fail:
                    # 麻痹/昏迷等自动失败：不消耗任何骰，也不掷加值骰。
                    save_check_index = len(events)
                    events.append({
                        "type": "check.resolved", "kind": "saving_throw",
                        "actor_id": target_id, "source_actor_id": actor_id,
                        "ability": save_ability, "rolls": [], "natural": 0,
                        "modifier": modifier, "check_bonus": 0, "check_bonus_rolls": [],
                        "total": 0,
                        "target": actor["spell_save_dc"], "success": False,
                        "auto_failed": True,
                    })
                    succeeded = False
                else:
                    # 狂暴（力量豁免优势）与未受训护甲（力/敏豁免劣势）走同一
                    # 归一函数：优劣势相消时按 RAW 回到单骰。
                    rage_advantage = "rage" in target["conditions"] and save_ability == "str"
                    untrained_disadvantage = bool(
                        (target.get("armor_training") or {}).get("untrained_armor_worn")
                    ) and save_ability in {"str", "dex"}
                    save_sources: list[dict[str, Any]] = []
                    if rage_advantage:
                        save_sources.append({"code": "rage", "mode": "advantage"})
                    if untrained_disadvantage:
                        save_sources.append({"code": "untrained_armor", "mode": "disadvantage"})
                    save_mode = mode_from_sources(save_sources)
                    first = int(rng.randint(1, 20))
                    second = int(rng.randint(1, 20)) if save_mode else None
                    if save_mode == "advantage":
                        roll = max(first, second)
                    elif save_mode == "disadvantage":
                        roll = min(first, second)
                    else:
                        roll = first
                    check_bonus_rolls = condition_check_bonus_rolls(
                        self.catalog.conditions, target["conditions"], "saving_throw", rng,
                    )
                    check_bonus = sum(row["roll"] for row in check_bonus_rolls)
                    total = roll + modifier + check_bonus
                    succeeded = total >= int(actor["spell_save_dc"])
                    check_event = {
                        "type": "check.resolved", "kind": "saving_throw", "actor_id": target_id,
                        "source_actor_id": actor_id, "ability": save_ability,
                        "rolls": [value for value in (first, second) if value is not None],
                        "natural": roll, "modifier": modifier,
                        "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
                        "total": total,
                        "target": actor["spell_save_dc"], "success": succeeded,
                    }
                    if save_sources:
                        check_event["advantage_mode"] = save_mode
                        check_event["advantage_sources"] = save_sources
                    save_check_index = len(events)
                    events.append(check_event)
                if effect.get("damage"):
                    damage_node = spell_formula_node(
                        str(effect["damage"]), effect.get("upcast_damage"),
                        spell, slot_level, actor,
                    )
                    damage_node = self._maybe_overchannel(intent, damage_node)
                    damage, rolls = roll_damage_node(damage_node, rng)
                    if succeeded:
                        damage = damage // 2 if effect.get("half_on_success") else 0
                    damage_type = str(effect.get("damage_type") or "force")
                    # 抗性在豁免减半之后结算（RAW 顺序：先修正，后抗性）。
                    damage, resistance = self._resist_damage(target, damage, damage_type)
                    if damage:
                        damage_event = {
                            "type": "resource.changed", "resource": "hp",
                            "target_id": target_id, "delta": -damage, "amount": damage,
                            "damage_type": damage_type,
                            "critical": False, "rolls": rolls,
                        }
                        if resistance:
                            damage_event["resisted"] = True
                            damage_event["resistance"] = resistance
                        events.append(damage_event)
                        events.extend(self._concentration_events(target, damage, rng))
                        prior_dealt = dealt.get(target_id, 0)
                        dealt[target_id] = prior_dealt + damage
                        events.extend(self._enemy_defeated_feature_events(
                            instance, combat, actor_id, target, dealt[target_id],
                            prior_damage=prior_dealt,
                        ))
                        if target["kind"] == "enemy" and target["hp"] - dealt[target_id] <= 0:
                            defeated_targets.add(target_id)
            elif mode == "automatic_damage":
                damage_node = spell_formula_node(
                    str(effect["damage"]), effect.get("upcast_damage"),
                    spell, slot_level, actor,
                )
                damage_node = self._maybe_overchannel(intent, damage_node)
                damage, rolls = roll_damage_node(damage_node, rng)
                damage_type = str(effect.get("damage_type") or "force")
                damage, resistance = self._resist_damage(target, damage, damage_type)
                damage_event = {
                    "type": "resource.changed", "resource": "hp", "target_id": target_id,
                    "delta": -damage, "amount": damage,
                    "damage_type": damage_type,
                    "critical": False, "rolls": rolls,
                }
                if resistance:
                    damage_event["resisted"] = True
                    damage_event["resistance"] = resistance
                events.append(damage_event)
                events.extend(self._concentration_events(target, damage, rng))
                prior_dealt = dealt.get(target_id, 0)
                dealt[target_id] = prior_dealt + damage
                events.extend(self._enemy_defeated_feature_events(
                    instance, combat, actor_id, target, dealt[target_id],
                    prior_damage=prior_dealt,
                ))
                if target["kind"] == "enemy" and target["hp"] - dealt[target_id] <= 0:
                    defeated_targets.add(target_id)
                succeeded = True
            elif mode == "healing":
                healing_node = spell_formula_node(
                    str(effect["healing"]), effect.get("upcast_healing"),
                    spell, slot_level, actor,
                )
                if effect.get("add_spell_ability"):
                    healing_node = heal_ability_modifier_node(healing_node, actor)
                maximized = self.features.has_feature(
                    self._feature_character(actor), "supreme_healing",
                )
                if maximized:
                    healing_node = maximize_dice_nodes(healing_node)
                healing, rolls = roll_damage_node(healing_node, rng)
                healing = max(1, healing)
                # 颤栗之触：目标在施法者回合结束前无法恢复生命值。
                blocked = "cannot_regain_hp" in target["conditions"]
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": target_id,
                    "delta": 0 if blocked else healing, "amount": 0 if blocked else healing,
                    "healing": True, "rolls": rolls,
                    **({"maximized": True} if maximized else {}),
                    **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
                })
                succeeded = True
            elif mode == "temp_hp":
                # 升环是固定加值，不走 spell_formula_node 的同面骰 upcast 参数。
                node = spell_formula_node(str(effect["temp_hp"]), None, spell, slot_level, actor)
                levels = slot_level - int(spell["level"])
                flat = int(effect.get("upcast_flat", 0) or 0)
                if flat and levels > 0:
                    node = {"op": "add", "args": [node, {"op": "constant", "value": flat * levels}]}
                amount, rolls = roll_damage_node(node, rng)
                events.append({
                    "type": "resource.changed", "resource": "temp_hp", "target_id": target_id,
                    "delta": amount, "amount": amount, "rolls": rolls,
                })
                succeeded = True
            elif mode == "teleport":
                # 传送不是移动：事件无 movement_cost，不扣经济、不触发借机攻击。
                events.append({
                    "type": "dnd2024.position.changed", "actor_id": target_id,
                    "distance": int(intent["teleport_distance"]), "forced": True,
                })
            elif mode == "dispel":
                # 逐 condition 判定：≤ 施放环阶自动终止；更高环阶走一次施法属性检定。
                # 专注来源整道终止（reducer 级联移除全部目标），非专注来源只移除本目标。
                dispelled_owners: set[str] = set()
                for condition_id, meta in list(target["conditions"].items()):
                    if not isinstance(meta, dict) or not meta.get("source_spell_ref"):
                        continue
                    source_level = int(meta.get("source_spell_level", 0) or 0)
                    if source_level > slot_level:
                        modifier = ability_modifier(actor["abilities"][actor["spell_ability"]])
                        roll = int(rng.randint(1, 20))
                        success = roll + modifier >= 10 + source_level
                        events.append({
                            "type": "check.resolved", "kind": "ability_check",
                            "actor_id": actor_id, "target_id": target_id,
                            "ability": actor["spell_ability"], "rolls": [roll],
                            "natural": roll, "modifier": modifier,
                            "check_bonus": 0, "check_bonus_rolls": [],
                            "total": roll + modifier, "target": 10 + source_level,
                            "success": success, "condition": condition_id,
                        })
                        if not success:
                            continue
                    owner = str(meta.get("concentration_owner") or "")
                    if not owner:
                        events.append({
                            "type": "condition.removed", "target_id": target_id,
                            "condition": condition_id, "reason": "dispelled",
                        })
                    elif owner not in dispelled_owners:
                        events.append({
                            "type": "dnd2024.concentration.ended", "actor_id": owner,
                            "reason": "dispelled",
                        })
                        dispelled_owners.add(owner)
            elif mode == "stabilize":
                events.append({
                    "type": "condition.applied", "target_id": target_id,
                    "condition": "stable", "duration": "until_healed",
                    "source_actor_id": actor_id,
                })
                succeeded = True
            elif mode == "revive":
                # 复活不是治疗：不经过 healing 管线、不发 resource.changed。
                # 状态语义（hp=1、清 dead/unconscious/stable/death_saves）由
                # reducer 的 character.revived 处理器承载，这里只发事件。
                events.append({
                    "type": "dnd2024.character.revived",
                    "actor_id": target_id, "hp": 1,
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
            # 光环条件免疫：命中则根本不产生 condition.applied，并把首个
            # 声明来源写回本次豁免事件（仅拦截时携带，事件类型不变）。
            blocked_by_aura = ""
            if condition and condition_applies and mode == "saving_throw":
                blocked_by_aura = self._aura_immune_conditions(
                    instance, combat, target,
                ).get(condition, "")
            if blocked_by_aura:
                if save_check_index is not None:
                    events[save_check_index]["blocked_by_aura"] = blocked_by_aura
            elif condition and condition_applies:
                applied = {
                    "type": "condition.applied", "target_id": target_id,
                    "condition": condition, "duration": str(effect.get("condition_duration") or ""),
                    "source_actor_id": actor_id,
                    "concentration_owner": actor_id if effect.get("concentration") else "",
                    # 服务端来源快照：解除魔法只认这两个键，客户端不可声明。
                    "source_spell_ref": spell_ref,
                    "source_spell_level": (
                        int(slot_level) if int(slot_level) > 0 else int(spell["level"])
                    ),
                }
                # 服务端从目录效果拷贝提前终止声明；客户端永远无法声明它。
                if effect.get("ends_on") is not None:
                    applied["ends_on"] = deepcopy(effect["ends_on"])
                # 选择型抗性：施法时选定的伤害类型写进 condition 元数据，
                # 之后由结算期的抗性辅助读取（客户端不能直接写元数据）。
                if effect.get("resist_choice"):
                    applied["resist_damage_types"] = [str(intent["resist_type"])]
                # 每回合重复豁免：DC 是施放当刻的快照，之后施法者属性变化不影响。
                if effect.get("save_repeat") is not None:
                    applied["save_repeat"] = {
                        "ability": str(effect["save_repeat"]),
                        "dc": int(actor["spell_save_dc"]),
                    }
                for removed in effect.get("removes_conditions") or []:
                    if str(removed) in target["conditions"]:
                        events.append({
                            "type": "condition.removed", "target_id": target_id,
                            "condition": str(removed),
                        })
                events.append(applied)
                # 失能打断专注：目标自己正在专注时，施加失能族要终止其专注，
                # 由 reducer 级联移除该目标名下的专注 condition。
                if (self.catalog.conditions.get(condition) or {}).get(
                    "implies_incapacitated"
                ) and any(
                    isinstance(meta, dict)
                    and str(meta.get("concentration_owner") or "") == str(target_id)
                    for meta in (target.get("conditions") or {}).values()
                ):
                    events.append({
                        "type": "dnd2024.concentration.ended", "actor_id": target_id,
                        "reason": "incapacitated",
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
        if intent.get("maximize_damage"):
            # 超限导能自伤：整道施法结算与胜利判定之后一次性落账（RNG 末位），
            # 无视抗性与免疫，因此不经 _resist_damage。
            character = self._feature_character(actor)
            per_slot = int(self.features.feature_value(
                character, "overchannel", "cost_dice_per_slot", 2,
            ) or 0)
            sides = int(self.features.feature_value(
                character, "overchannel", "cost_die_sides", 12,
            ) or 0)
            damage_type = str(self.features.feature_value(
                character, "overchannel", "cost_damage_type", "necrotic",
            ) or "")
            cost_node = formula_node_from_dice_formula(f"{per_slot * slot_level}d{sides}")
            cost, cost_rolls = roll_damage_node(cost_node, rng)
            events.append({
                "type": "resource.changed", "resource": "hp", "target_id": actor_id,
                "delta": -cost, "amount": cost, "damage_type": damage_type,
                "overchannel_cost": True, "rolls": cost_rolls,
            })
            events.extend(self._concentration_events(actor, cost, rng))
        return events

    def _movement_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        actor_id = str(intent["actor_id"])
        distance = int(intent["distance"])
        economy = combat.get("economy") or {}
        adjacent_hostiles: list[dict[str, Any]] = []
        for target in self._hostile_targets(instance, combat, actor_id):
            if self._distance(combat, actor_id, target["actor_id"]) > 5:
                continue
            if self._distance_after_move(combat, actor_id, target["actor_id"], distance) <= 5:
                continue
            threat = self._actor_view(instance, combat, target["actor_id"])
            # 借机攻击同样是反应：失能 / 禁反应 / 反应已耗尽 / 倒地的威胁者
            # 不构成威胁，也不能为一次无法结算的攻击消耗反应。
            if not self._reaction_eligible(combat, threat):
                continue
            # 借机攻击必须是近战武器攻击：远程武器的持有者不构成威胁，
            # 也不应为一次无法结算的借机攻击消耗其反应。敌方档案与玩家
            # 装备链都携带 range，缺失 range 的档案按近战处理。
            if not any(
                int(attack.get("range") or 0) <= 5
                for attack in self._available_attacks(threat)
            ):
                continue
            adjacent_hostiles.append(target)
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
    def _submit_hit_damage(damage_event: dict[str, Any]) -> list[dict[str, Any]]:
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
        mastery_effects = event.pop("mastery_effects", None)
        events: list[dict[str, Any]] = [event]
        if isinstance(feature_use, dict):
            events.append({"type": "dnd2024.feature.used", **feature_use})
        if isinstance(mastery_effects, list):
            for effect in mastery_effects:
                if not isinstance(effect, dict) or not effect.get("condition"):
                    continue
                events.append({
                    "type": "condition.applied", "target_id": event["target_id"],
                    **effect,
                })
        return events

    def _enemy_defeated_feature_events(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], damage: int, prior_damage: int = 0,
    ) -> list[dict[str, Any]]:
        """Temp-HP grants from owned features that trigger on a defeated enemy.

        Called at every damage landing point with the *final* damage (after
        resistance, saves and riders), so the trigger agrees with the existing
        defeat check.  In multi-hit spell batches (extra rays/beams onto the
        same target) callers pass the per-target damage accumulated for this
        cast plus the prior accumulated damage, matching the batch defeat check
        that also uses the accumulated total.  Only the settlement that first
        crosses the threshold triggers, so repeated beams on one target cannot
        grant the same feature twice.  Features opt in through the closed
        parameter set ``defeat_temp_hp_ability`` /
        ``defeat_temp_hp_add_class_level`` / ``defeat_share_range_ft``; the
        engine never branches on a feature name.

        Path a: the holder is the attacker (no range limit).  Path b: someone
        else defeated the enemy while the holder is within the declared share
        radius.  One event per holder and settlement, keep-max handled by the
        reducer.
        """

        if target.get("kind") != "enemy":
            return []
        hp = int(target.get("hp", 0) or 0)
        dealt = int(damage)
        prior = int(prior_damage)
        # Only the settlement that first crosses the defeat threshold triggers.
        # A target already defeated by an earlier ray of the same batch keeps
        # taking its damage events but grants no second defeat feature.
        if hp <= 0 or hp - prior <= 0 or hp - dealt > 0:
            return []
        target_id = str(target["actor_id"])
        events: list[dict[str, Any]] = []
        for raw_actor_id in combat.get("initiative") or []:
            actor_id = str(raw_actor_id)
            kind, _raw = _actor_kind(actor_id)
            if kind not in {"player", "companion"}:
                continue
            view = self._actor_view(instance, combat, actor_id)
            character = self._feature_character(view)
            state = self.features.class_state(character)
            if state is None:
                # 同伴残表没有职业，按"不拥有任何职业特性"处理。
                continue
            for definition in self.features.features_for(character):
                ability = definition.parameters.get("defeat_temp_hp_ability")
                if not isinstance(ability, str) or not ability:
                    continue
                share = definition.parameters.get("defeat_share_range_ft")
                eligible = actor_id == attacker_id
                if not eligible and isinstance(share, int) and not isinstance(share, bool):
                    eligible = self._distance(combat, actor_id, target_id) <= share
                if not eligible:
                    continue
                modifier = ability_modifier(
                    int((view.get("abilities") or {}).get(ability, 10) or 10)
                )
                level = (
                    state[1]
                    if definition.parameters.get("defeat_temp_hp_add_class_level") is True
                    else 0
                )
                events.append({
                    "type": "resource.changed", "resource": "temp_hp",
                    "target_id": actor_id, "amount": max(1, modifier + level),
                    "feature_id": definition.id,
                })
                break
        return events

    @staticmethod
    def _feature_used_this_turn(
        combat: dict[str, Any], actor_id: str, feature_id: str,
    ) -> bool:
        """Whether ``actor_id`` already used ``feature_id`` in this exact turn.

        The ledger is keyed by the actor who made the attack and by
        ``round`` + ``turn_index`` of the turn it happened in.  It is deliberately
        not the current economy: an opportunity attack is made on someone
        else's turn, and the same actor may use a once-per-turn feature again
        there.
        """

        usage = combat.get("feature_usage")
        record = usage.get(actor_id) if isinstance(usage, dict) else None
        if not isinstance(record, dict):
            return False
        if int(record.get("round", -1)) != int(combat.get("round", 0)):
            return False
        if int(record.get("turn_index", -1)) != int(combat.get("turn_index", 0)):
            return False
        return str(feature_id) in {str(item) for item in record.get("feature_ids") or []}

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

    @staticmethod
    def _spend_action_resource(combat: dict[str, Any]) -> str:
        """Which economy pool pays for a non-magical action.

        Action Surge grants a separate ``surge_action`` pool precisely so the
        Magic action can never spend it: spell costs only read ``action`` /
        ``bonus_action``.  A non-magical action therefore spends the surge pool
        first, keeping the ordinary action free for a later Magic action.
        """

        economy = combat.get("economy", {}) or {}
        if int(economy.get("surge_action", 0) or 0) > 0:
            return "surge_action"
        return "action"

    @staticmethod
    def _basic_action_events(
        combat: dict[str, Any], intent_type: str, actor_id: str,
        *, spend_action: bool = True,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if spend_action:
            events.append({
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": CombatResolutionMixin._spend_action_resource(combat),
                "amount": 1,
            })
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

    def _hide_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
        *, spend_action: bool = True,
    ) -> list[dict[str, Any]]:
        """Resolve the Hide action: one Dexterity (Stealth) check.

        RAW 的 DC 由 DM 裁决、通常对抗被动察觉；战斗视图没有察觉熟练数据，
        因此以存活敌人的感知豁免调整值作为被动察觉的数据代理（DC = 10 + 最高
        值）。成功即施加 P1 的 ``invisible`` condition，攻击优劣势与终止全部由
        该 condition 的既有 ``attack_modes`` / ``ends_on`` 元数据驱动。
        """

        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        events: list[dict[str, Any]] = []
        if spend_action:
            events.append({
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": self._spend_action_resource(combat), "amount": 1,
            })
        stealth = int(actor["skill_values"].get("stealth", 0) or 0)
        alive_wisdom = [
            int((self._actor_view(instance, combat, f"enemy:{enemy_id}")["saving_throws"]
                or {}).get("wis", 0) or 0)
            for enemy_id, enemy in (combat.get("enemies") or {}).items()
            if int(enemy.get("hp", 0) or 0) > 0
        ]
        dc = 10 + max(alive_wisdom, default=0)
        # S-P12 迁移债 D2：未受训护甲的隐匿劣势在此接线。
        sources: list[dict[str, Any]] = []
        if bool((actor.get("armor_training") or {}).get("stealth_disadvantage")):
            sources.append({"code": "stealth_disadvantage", "mode": "disadvantage"})
        mode = mode_from_sources(sources)
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if mode == "disadvantage" else None
        rolls = [first] if second is None else [first, second]
        natural = min(rolls) if mode == "disadvantage" else first
        total = natural + stealth
        success = total >= dc
        event: dict[str, Any] = {
            "type": "check.resolved", "kind": "hide", "actor_id": actor_id,
            "ability": "stealth", "rolls": rolls, "natural": natural,
            "modifier": stealth, "total": total, "target": dc, "success": success,
        }
        if mode:
            event["advantage_mode"] = mode
            event["advantage_sources"] = sources
        events.append(event)
        if success:
            events.append({
                "type": "condition.applied", "target_id": actor_id,
                "condition": "invisible", "duration": "combat",
                "source_actor_id": actor_id,
                "ends_on": ["attack_roll", "spell_cast"],
            })
        return events

    def _class_capability_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Resolve a feature-provided combat capability as one atomic batch.

        The capability declaration supplies the cost and the underlying
        canonical actions; the resource spend, the action-economy spend, and
        every canonical attack resolution land in the *same* EventBatch, so a
        rejected second strike can never leave a half-charged Focus point or a
        spent bonus action behind.
        """

        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        capability_id = str(intent.get("capability_id") or "")
        capability = self.features.declared_capability(
            self._feature_character(actor), capability_id,
        )
        if capability is None:  # pragma: no cover - validation guards this
            raise CombatIntentError("class capability is not available to this actor")
        events: list[dict[str, Any]] = []
        if capability.cost.action:
            events.append({
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": capability.cost.action, "amount": 1,
            })
        for resource_cost in capability.cost.resources:
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
                "resource_id": resource_cost.resource_id, "amount": resource_cost.amount,
            })
        target_id = str(intent.get("target_id") or "")
        for action in capability.actions:
            if action.kind == "unarmed_strike":
                for _ in range(action.count):
                    strike_events = self._attack_events(
                        instance,
                        combat,
                        {
                            "actor_id": actor_id, "target_id": target_id,
                            "weapon_ref": UNARMED_STRIKE_REF,
                        },
                        rng,
                        spend_attack_action=False,
                    )
                    events.extend(strike_events)
                    if any(
                        event["type"] == "dnd2024.combat.ended" for event in strike_events
                    ):
                        # 战斗已经结束：不再追加注定落空的后续打击。
                        return events
            elif action.kind in {"dash", "dodge", "disengage"}:
                events.extend(self._basic_action_events(
                    combat, action.kind, actor_id, spend_action=False,
                ))
            elif action.kind == "hide":
                events.extend(self._hide_events(
                    instance, combat, {"actor_id": actor_id}, rng, spend_action=False,
                ))
            elif action.kind == "heal":
                # 特性声明的治疗骰 + 等级加值；事件形状与法术治疗分支一致。
                character = self._feature_character(actor)
                dice = str(self.features.feature_value(
                    character, capability.feature_id, "heal_dice", "",
                ) or "")
                bonus = int(self.features.feature_value(
                    character, capability.feature_id, "heal_bonus", 0,
                ) or 0)
                healing_node = formula_node_from_dice_formula(dice)
                if bonus:
                    healing_node = {"op": "add", "args": [
                        healing_node, {"op": "constant", "value": bonus},
                    ]}
                healing, rolls = roll_damage_node(healing_node, rng)
                healing = max(1, healing)
                blocked = "cannot_regain_hp" in actor["conditions"]
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": actor_id,
                    "delta": 0 if blocked else healing, "amount": 0 if blocked else healing,
                    "healing": True, "rolls": rolls,
                    **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
                })
            elif action.kind == "rage":
                # 狂暴不能维持专注：进入狂暴即结束正在维持的专注法术。
                if actor.get("concentration"):
                    events.append({
                        "type": "dnd2024.concentration.ended", "actor_id": actor_id,
                        "reason": "rage",
                    })
                events.append({
                    "type": "condition.applied", "target_id": actor_id,
                    "condition": "rage", "duration": "rage", "turns_remaining": RAGE_MAX_ROUNDS,
                    "sustained_this_turn": False, "established": False,
                    "source_actor_id": actor_id,
                })
            elif action.kind == "rage_sustain":
                # 只刷新续期标记；``turns_remaining`` 由回合结束的到期检查递减，
                # 延长一次不等于重置 10 分钟上限。
                events.append({
                    "type": "dnd2024.condition.sustained", "target_id": actor_id,
                    "condition": "rage",
                })
            elif action.kind == "action_surge":
                # Action Surge 本身不消耗动作，只写入一份独立的 surge_action。
                # 施法成本只读 action/bonus_action，因此这份额外动作天然不能
                # 用于魔法动作；``action_surge_used`` 在同一回合阻止第二次使用。
                events.append({
                    "type": "dnd2024.economy.granted", "actor_id": actor_id,
                    "grants": {"surge_action": 1},
                    "flags": {"action_surge_used": True},
                })
            else:  # pragma: no cover - catalog validation guards this
                raise CombatIntentError("unsupported class capability action")
        return events

    def _save_repeat_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str, rng: Any,
    ) -> list[dict[str, Any]]:
        """Repeat a condition's save at the end of the target's own turn.

        Only conditions whose metadata carries a ``save_repeat`` snapshot
        participate: the ability and DC were frozen when the spell landed. A
        success removes just this target's condition; it never ends the caster's
        concentration, so other targets of an upcast spell keep theirs.
        """

        actor = self._actor_view(instance, combat, actor_id)
        events: list[dict[str, Any]] = []
        for condition_id, meta in (actor.get("conditions") or {}).items():
            if not isinstance(meta, dict):
                continue
            repeat = meta.get("save_repeat")
            if not isinstance(repeat, dict):
                continue
            ability = str(repeat.get("ability") or "")
            dc = int(repeat.get("dc", 0) or 0)
            if not ability or dc <= 0:
                continue
            modifier = int(actor["saving_throws"].get(ability, 0) or 0)
            auto_fail = ability in condition_flags(
                self.catalog.conditions, actor["conditions"],
            )["auto_fail_saves"]
            if auto_fail:
                events.append({
                    "type": "check.resolved", "kind": "saving_throw",
                    "actor_id": actor_id,
                    "source_actor_id": str(meta.get("source_actor_id") or ""),
                    "ability": ability, "rolls": [], "natural": 0,
                    "modifier": modifier, "check_bonus": 0, "check_bonus_rolls": [],
                    "total": 0,
                    "target": dc, "success": False, "auto_failed": True,
                    "repeat_save": True,
                })
                continue
            rage_advantage = "rage" in actor["conditions"] and ability == "str"
            untrained_disadvantage = bool(
                (actor.get("armor_training") or {}).get("untrained_armor_worn")
            ) and ability in {"str", "dex"}
            save_sources: list[dict[str, Any]] = []
            if rage_advantage:
                save_sources.append({"code": "rage", "mode": "advantage"})
            if untrained_disadvantage:
                save_sources.append({"code": "untrained_armor", "mode": "disadvantage"})
            save_mode = mode_from_sources(save_sources)
            first = int(rng.randint(1, 20))
            second = int(rng.randint(1, 20)) if save_mode else None
            if save_mode == "advantage":
                roll = max(first, second)
            elif save_mode == "disadvantage":
                roll = min(first, second)
            else:
                roll = first
            check_bonus_rolls = condition_check_bonus_rolls(
                self.catalog.conditions, actor["conditions"], "saving_throw", rng,
            )
            check_bonus = sum(row["roll"] for row in check_bonus_rolls)
            total = roll + modifier + check_bonus
            success = total >= dc
            check_event = {
                "type": "check.resolved", "kind": "saving_throw",
                "actor_id": actor_id,
                "source_actor_id": str(meta.get("source_actor_id") or ""),
                "ability": ability,
                "rolls": [value for value in (first, second) if value is not None],
                "natural": roll, "modifier": modifier,
                "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
                "total": total, "target": dc, "success": success,
                "repeat_save": True,
            }
            if save_sources:
                check_event["advantage_mode"] = save_mode
                check_event["advantage_sources"] = save_sources
            events.append(check_event)
            if success:
                events.append({
                    "type": "condition.removed", "target_id": actor_id,
                    "condition": condition_id, "reason": "save_repeat",
                })
        return events

    def _end_turn_events(
        self, instance: Any, combat: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
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
        # 重复豁免 tick 发生在"目标自己回合结束"，排在推进回合之前。
        events = self._save_repeat_events(instance, combat, order[previous_index], rng)
        next_index = previous_index
        wraps = 0
        for _ in range(len(order)):
            next_index = (next_index + 1) % len(order)
            if next_index == 0:
                wraps += 1
            candidate = self._actor_view(instance, combat, order[next_index])
            # 失能单位跳过回合：敌人/队友 HP 归零即跳过；玩家 0 HP 走死亡豁免。
            if candidate["kind"] in {"enemy", "companion"} and candidate["hp"] <= 0:
                continue
            if "dead" in candidate["conditions"] or "stable" in candidate["conditions"]:
                continue
            break
        next_round = int(combat["round"]) + wraps
        next_actor = order[next_index]
        next_view = self._actor_view(instance, combat, next_actor)
        return [*events, {
            "type": "dnd2024.turn.advanced", "round": next_round,
            "turn_index": next_index, "actor_id": next_actor,
            "previous_actor_id": order[previous_index],
            "economy": self._fresh_economy(next_view),
        }, *self._turn_start_regen_events(next_view)]

    def _turn_start_regen_events(
        self, actor: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Turn-start regeneration for the actor whose turn just began.

        Gated by the Bloodied rule (``1 <= hp <= max_hp // 2``): a downed,
        dead, stable, full or merely wounded actor never ticks.  The amount is
        fixed by the declaration (``base + ability modifier``), never rolled,
        and multiple declarations take the single largest base instead of
        stacking.  ``cannot_regain_hp`` blocks the healing with a zero-delta
        event so the reducer stays purely mechanical.
        """

        hp = int(actor["hp"])
        maximum = int(actor["max_hp"])
        if hp < 1 or maximum <= 0 or hp > maximum // 2:
            return []
        character = self._feature_character(actor)
        base, source_id = self.features.max_feature_value_source(
            character, "turn_start_regen_base", 0,
        )
        if base <= 0:
            return []
        ability = str(
            self.features.feature_value(character, source_id, "turn_start_regen_ability", "con")
        )
        amount = max(0, base + ability_modifier(int(character["abilities"].get(ability, 10) or 0)))
        if amount <= 0:
            return []
        blocked = "cannot_regain_hp" in actor["conditions"]
        return [{
            "type": "resource.changed", "resource": "hp",
            "target_id": actor["actor_id"],
            "delta": 0 if blocked else amount, "amount": 0 if blocked else amount,
            "healing": True, "feature_id": source_id,
            **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
        }]

    def _death_save_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        character = self._feature_character(actor)
        advantage_sources = list(
            self.features.feature_flag_sources(character, "death_save_advantage")
        )
        crit_threshold = int(self.features.min_feature_value(
            character, "death_save_crit_threshold", 20,
        ))
        # Lucky's advantage face is declared before the roll and consumed
        # unconditionally; validation proves the pool exists and has a point.
        spend_luck = bool(intent.get("spend_luck"))
        if spend_luck:
            advantage_sources.append("lucky")
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if advantage_sources else None
        roll = max(first, second) if second is not None else first
        # A death saving throw is still a saving throw, so conditional d4s
        # (Bless) apply after the d20 faces and before the outcome is read.
        check_bonus_rolls = condition_check_bonus_rolls(
            self.catalog.conditions, actor["conditions"], "saving_throw", rng,
        )
        check_bonus = sum(row["roll"] for row in check_bonus_rolls)
        total = roll + check_bonus
        check_event = {
            "type": "check.resolved", "kind": "death_save", "actor_id": actor_id,
            "rolls": [value for value in (first, second) if value is not None],
            "natural": roll, "check_bonus": check_bonus,
            "check_bonus_rolls": check_bonus_rolls, "total": total,
            "target": 10, "success": total >= 10,
        }
        if advantage_sources:
            check_event["advantage_mode"] = "advantage"
            check_event["advantage_sources"] = [
                {"code": source_id, "mode": "advantage"}
                for source_id in advantage_sources
            ]
        if crit_threshold != 20:
            check_event["crit_threshold"] = crit_threshold
        events: list[dict[str, Any]] = []
        if spend_luck:
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
                "resource_id": "lucky_points",
            })
        window = self._death_save_check_window(instance, combat, actor, check_event)
        if window is not None:
            # The save has been rolled but its failures are not yet applied:
            # nothing is irreversible until the window owner decides.
            events.append({"type": "dnd2024.decision.pending", **window})
            return events
        events.append(check_event)
        events.extend(
            self._death_save_outcome_events(instance, combat, actor, check_event, rng)
        )
        return events

    def _death_save_outcome_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        check: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Apply a resolved death save to the stable/dead tally.

        Shared by the straight-through path and the window finisher; the only
        values a window can change are the save total and its success flag, so
        the natural-roll special cases stay authoritative here.
        """

        actor_id = str(actor["actor_id"])
        roll = int(check.get("natural", 0) or 0)
        crit_threshold = int(check.get("crit_threshold", 20) or 20)
        successes = int(actor["death_saves"].get("successes", 0) or 0)
        failures = int(actor["death_saves"].get("failures", 0) or 0)
        hp = 0
        if roll >= crit_threshold:
            hp = 1
            successes = failures = 0
        elif roll == 1:
            failures += 2
        elif bool(check.get("success")):
            successes += 1
        else:
            failures += 1
        stable = successes >= 3
        dead = failures >= 3
        return [
            {
                "type": "dnd2024.death_save.resolved", "actor_id": actor_id,
                "roll": int(check.get("total", roll) or roll),
                "successes": min(successes, 3), "failures": min(failures, 3),
                "stable": stable, "dead": dead, "hp": hp,
            },
            *([] if dead or hp else self._end_turn_events(instance, combat, rng)),
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
            "resource": self._spend_action_resource(combat), "amount": 1,
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
        if decision.get("kind") == HIT_SPELL_DECISION:
            return self._finish_hit_spell(instance, combat, decision, rng)
        if decision.get("kind") == HIT_REACTION_DECISION:
            return self._finish_hit_reaction(instance, combat, decision, intent, rng)
        if decision.get("kind") == COUNTER_SPELL_DECISION:
            return self._finish_counter_spell(instance, combat, decision, intent, rng)
        if decision.get("kind") == CHECK_WINDOW_DECISION:
            return self._finish_check_window(instance, combat, decision, intent, rng)
        if decision.get("kind") == DAMAGE_WINDOW_DECISION:
            return self._finish_damage_window(instance, combat, decision, intent, rng)
        movement = decision["movement"]
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        if intent["option"] == "resolve":
            threat_id = str(decision["threat_actor_ids"][0])
            threat = self._actor_view(instance, combat, threat_id)
            # 窗口打开后威胁者可能已经失能 / 禁反应 / 耗尽反应：此时借机
            # 攻击无法发生，移动照常结算，也不支付反应。
            if self._reaction_eligible(combat, threat):
                attack_intent = {
                    "intent_id": f"{decision['decision_id']}:attack",
                    "actor_id": threat_id,
                    "target_id": str(movement["actor_id"]),
                }
                # 借机攻击必须是近战武器攻击：只有射程 5 尺的攻击档案才能
                # 用作借机攻击（长弓等远程武器不构成威胁）；没有近战档案时
                # 不发生攻击，也不臆造档案。敌方档案与玩家装备链都带 range。
                melee_attacks = [
                    attack for attack in self._available_attacks(threat)
                    if int(attack.get("range") or 0) <= 5
                ]
                # 反应只有一次：用平均伤害最高的近战档案（稳定排序，同分保持原顺序），
                # 而不是列表里的第一把（游荡者会拿匕首而不是短剑）。
                melee_attacks.sort(
                    key=lambda attack: -self._opportunity_attack_average(threat, attack),
                )
                if melee_attacks:
                    if threat["kind"] == "enemy":
                        attack_intent["attack_id"] = str(melee_attacks[0]["id"])
                    else:
                        attack_intent["weapon_ref"] = str(melee_attacks[0]["weapon_ref"])
                else:
                    attack_intent = None
                if attack_intent is not None:
                    opportunity = self._attack_events(instance, combat, attack_intent, rng)
                    # 借机攻击花的是反应而不是攻击动作：替换支付事件本身，
                    # 而不是列表头部——法术提前终止事件（若有）排在支付之前，
                    # 不能被反应支付覆盖掉。
                    cost_index = next(
                        index for index, event in enumerate(opportunity)
                        if event["type"] == "dnd2024.attack.spent"
                    )
                    opportunity[cost_index] = {
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
