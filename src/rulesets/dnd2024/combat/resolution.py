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
    RAGE_MAX_ROUNDS,
    UNARMED_STRIKE_REF,
    actor_kind as _actor_kind,
    canonical as _canonical,
    companion_actor as _companion_actor,
    enemy_actor as _enemy_actor,
    player_actor as _player_actor,
)
from .reaction_spells import HIT_REACTION_DECISION, effective_armor_class
from .triggered_spells import HIT_SPELL_DECISION


class CombatResolutionMixin:
    """Turn validated intents and server RNG into immutable event batches."""

    __slots__ = ()

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

    def _attack_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
        *, spend_attack_action: bool = True,
    ) -> list[dict[str, Any]]:
        actor_id = str(intent["actor_id"])
        target_id = str(intent["target_id"])
        actor = self._actor_view(instance, combat, actor_id)
        target = self._actor_view(instance, combat, target_id)
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
        from .check_modes import attack_mode

        check_mode = attack_mode(self, instance, actor, target, range_disadvantage, use_grant=spend_attack_action)
        advantage, disadvantage = check_mode["mode"] == "advantage", check_mode["mode"] == "disadvantage"
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if advantage or disadvantage else None
        natural = max(first, second) if advantage and second else min(first, second) if disadvantage and second else first
        bless_bonus = 0
        if "blessed" in actor["conditions"]:
            bless_bonus = int(rng.randint(1, 4))
        total = natural + attack_bonus + bless_bonus
        target_ac = effective_armor_class(target)
        critical = natural == 20
        hit = natural != 1 and (critical or total >= target_ac)
        events: list[dict[str, Any]] = (
            [self._attack_cost_event(combat, actor)] if spend_attack_action else []
        )
        check_event = {
            "type": "check.resolved", "kind": "attack", "actor_id": actor_id,
            "advantage_mode": check_mode["mode"], "advantage_sources": check_mode["sources"],
            "target_id": target_id, "rolls": [value for value in (first, second) if value],
            "natural": natural, "modifier": attack_bonus, "bless_bonus": bless_bonus,
            "total": total, "target": target_ac, "success": hit, "critical": critical,
        }
        events.append(check_event)
        if (
            target["kind"] == "enemy" and "rage" in actor["conditions"]
            and str((combat.get("economy") or {}).get("actor_id") or "") == str(actor_id)
        ):
            # 2024 狂暴：在你的回合对敌人进行一次攻击检定即延长一轮，
            # 命中与否都算；借机攻击发生在别人的回合，不延长狂暴。
            events.append({
                "type": "dnd2024.condition.sustained", "target_id": actor_id,
                "condition": "rage",
            })
        if hit:
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
            resistance = None
            if (
                "rage" in target["conditions"]
                and str(damage_type) in ("bludgeoning", "piercing", "slashing")
            ):
                # 2024 狂暴：承受的钝击/穿刺/挥砍伤害减半，
                # 取整方式与反应减半（damage_multiplier）一致：向下取整。
                halved = int(damage * 0.5)
                if halved != damage:
                    resistance = "rage"
                    damage = halved
            damage_event = {
                "type": "resource.changed", "resource": "hp", "target_id": target_id,
                "delta": -damage, "amount": damage, "damage_type": damage_type,
                "critical": critical, "rolls": rolls,
            }
            if resistance:
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
            offer = self._hit_spell_offer(instance, combat, actor, weapon, target_id, damage_event, intent)
            reaction = self._hit_reaction_offer(
                instance, combat, actor, target, check_event, damage_event, intent,
                next_decision=offer,
            )
            if reaction or offer:
                events.append({"type": "dnd2024.decision.pending", **(reaction or offer)})
            else:
                events.extend(self._submit_hit_damage(damage_event))
                events.extend(self._concentration_events(target, damage, rng))
                if self._last_hostile_defeated(combat, target, damage):
                    events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        events.extend(self._consume_attack_conditions(actor_id, target_id, actor, target))
        return events
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
        events: list[dict[str, Any]] = self._spell_cost_events(actor, spell, slot_level)
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
                from .check_modes import attack_mode

                check_mode = attack_mode(self, instance, actor, target, False)
                rolls = [int(rng.randint(1, 20))]
                if check_mode["mode"]:
                    rolls.append(int(rng.randint(1, 20)))
                roll = max(rolls) if check_mode["mode"] == "advantage" else min(rolls)
                bonus = int(actor["spell_attack_bonus"])
                total = roll + bonus
                target_ac = effective_armor_class(target)
                succeeded = roll != 1 and (roll == 20 or total >= target_ac)
                events.append({
                    "type": "check.resolved", "kind": "spell_attack", "actor_id": actor_id,
                    "target_id": target_id, "rolls": rolls, "natural": roll,
                    "advantage_mode": check_mode["mode"], "advantage_sources": check_mode["sources"],
                    "modifier": bonus, "total": total, "target": target_ac,
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
                events.extend(self._consume_attack_conditions(actor_id, target_id, actor, target))
            elif mode == "saving_throw":
                save_ability = str(effect["save"])
                # 2024 狂暴：力量豁免具有优势（双骰取高），其它属性仍为单骰。
                rage_advantage = "rage" in target["conditions"] and save_ability == "str"
                first = int(rng.randint(1, 20))
                second = int(rng.randint(1, 20)) if rage_advantage else None
                roll = max(first, second) if second is not None else first
                bless = int(rng.randint(1, 4)) if "blessed" in target["conditions"] else 0
                modifier = int(target["saving_throws"].get(save_ability, 0) or 0)
                total = roll + modifier + bless
                succeeded = total >= int(actor["spell_save_dc"])
                check_event = {
                    "type": "check.resolved", "kind": "saving_throw", "actor_id": target_id,
                    "source_actor_id": actor_id, "ability": save_ability,
                    "rolls": [value for value in (first, second) if value is not None],
                    "natural": roll, "modifier": modifier, "bless_bonus": bless, "total": total,
                    "target": actor["spell_save_dc"], "success": succeeded,
                }
                if rage_advantage:
                    check_event["advantage_mode"] = "advantage"
                    check_event["advantage_sources"] = [
                        {"code": "rage", "mode": "advantage"},
                    ]
                events.append(check_event)
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
                healing = max(1, healing)
                # 颤栗之触：目标在施法者回合结束前无法恢复生命值。
                blocked = "cannot_regain_hp" in target["conditions"]
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": target_id,
                    "delta": 0 if blocked else healing, "amount": 0 if blocked else healing,
                    "healing": True, "rolls": rolls,
                    **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
                })
                succeeded = True
            elif mode == "stabilize":
                events.append({
                    "type": "condition.applied", "target_id": target_id,
                    "condition": "stable", "duration": "until_healed",
                    "source_actor_id": actor_id,
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
            and int(combat.get("reactions", {}).get(target["actor_id"], 0)) > 0
            # 借机攻击必须是近战武器攻击：远程武器的持有者不构成威胁，
            # 也不应为一次无法结算的借机攻击消耗其反应。敌方档案与玩家
            # 装备链都携带 range，缺失 range 的档案按近战处理。
            and any(
                int(attack.get("range") or 0) <= 5
                for attack in self._available_attacks(
                    self._actor_view(instance, combat, target["actor_id"])
                )
            )
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
    def _submit_hit_damage(damage_event: dict[str, Any]) -> list[dict[str, Any]]:
        """Commit a final hit's damage, then any deferred feature use.

        Hit damage is rolled once and held while the defender decides on
        Shield/Uncanny Dodge or the attacker decides on a hit-triggered spell.
        Only the paths that actually land the damage call this helper, so a
        reaction that turns the hit into a miss never records a feature use.
        The internal ``feature_use`` metadata never reaches the wire shape of
        ``resource.changed``.
        """

        event = deepcopy(damage_event)
        feature_use = event.pop("feature_use", None)
        events: list[dict[str, Any]] = [event]
        if isinstance(feature_use, dict):
            events.append({"type": "dnd2024.feature.used", **feature_use})
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
            # 失能单位跳过回合：敌人/队友 HP 归零即跳过；玩家 0 HP 走死亡豁免。
            if candidate["kind"] in {"enemy", "companion"} and candidate["hp"] <= 0:
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
        movement = decision["movement"]
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        if intent["option"] == "resolve":
            threat_id = str(decision["threat_actor_ids"][0])
            threat = self._actor_view(instance, combat, threat_id)
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
            if melee_attacks:
                if threat["kind"] == "enemy":
                    attack_intent["attack_id"] = str(melee_attacks[0]["id"])
                else:
                    attack_intent["weapon_ref"] = str(melee_attacks[0]["weapon_ref"])
            else:
                attack_intent = None
            if attack_intent is not None:
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
