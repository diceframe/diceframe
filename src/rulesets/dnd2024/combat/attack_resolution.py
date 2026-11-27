"""Attack resolution: the attack roll, its declaration state, and its outcome.

One of the resolution mixins; the engine composes them. The class holds no
state of its own."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any
from src.rulesets.dnd2024.character.builder import ability_modifier
from .primitives import CombatIntentError, attack_damage_parts, trait_ids
from .check_modes import condition_check_bonus_rolls, condition_flags, mode_from_sources
from src.rulesets.dnd2024.features.attack_declarations import BRUTAL_STRIKE, LIGHT_EXTRA_ATTACK, SACRED_WEAPON, declaration_value
from .reaction_spells import effective_armor_class
from .weapon_masteries import nick_active


class CombatAttackResolutionMixin:
    """Attack resolution: the attack roll, its declaration state, and its outcome."""

    __slots__ = ()

    if TYPE_CHECKING:
        _ally_within: Any
        _defensive_tactics_sources: Any
        _feature_character: Any
        features: Any
        def _brutal_strike_dice(self, character: Any) -> str:
            ...
        @staticmethod
        def _feature_used_this_turn(
            combat: dict[str, Any], actor_id: str, feature_id: str,
        ) -> bool:
            ...
        _actor_view: Any
        _declared_action: Any
        _multiattack_parts: Any
        _attack_check_window_offer: Any
        _attack_cost_event: Any
        _consume_attack_conditions: Any
        _cover_record: Any
        _declared_attack: Any
        _distance: Any
        _effective_conditions: Any
        _failed_attack_window_offer: Any
        _hit_settlement_events: Any
        _miss_mastery_events: Any
        _multiattack_mark_events: Any
        @staticmethod
        def _roll_id(
            events: list[dict[str, Any]], kind: str, actor_id: str,
        ) -> str:
            ...
        _sacred_weapon_bonus: Any
        _sacred_weapon_bonus_value: Any
        _sacred_weapon_events: Any
        _spell_end_trigger_events: Any
        catalog: Any

    @staticmethod
    def attack_ability(actor: dict[str, Any], weapon: dict[str, Any]) -> str:
        """Which ability modifier this weapon attack uses.

        One authority for a rule that used to be written twice: the attack
        resolver chose it and the pre-roll preview guessed it separately, so a
        preview could show a different ability than the roll used.  A real
        ranged weapon is Dexterity; Finesse picks the better of the two;
        Thrown melee weapons keep the melee ability (2024 Thrown); and a
        feature-declared ``ability_choice`` (Martial Arts) covers Unarmed
        Strikes and Monk weapons without the combat layer naming a class.
        """

        abilities = actor.get("abilities") or {}
        if bool(weapon.get("ranged")):
            return "dex"
        ability = "str"
        if weapon.get("finesse"):
            ability = max(
                ("str", "dex"),
                key=lambda key: ability_modifier(abilities.get(key, 10)),
            )
        choices = [
            str(key) for key in weapon.get("ability_choice") or []
            if str(key) in abilities
        ]
        if choices:
            ability = max(
                choices, key=lambda key: ability_modifier(abilities.get(key, 10)),
            )
        return ability

    @staticmethod
    def attack_range_disadvantage(weapon: dict[str, Any], distance: int) -> bool:
        """Whether this attack is past its normal range but still in long range."""

        normal = int(weapon.get("thrown_range") or weapon.get("range", 5) or 5)
        maximum = int(
            weapon.get("long_range") or weapon.get("thrown_range")
            or weapon.get("range", 5) or 5
        )
        return normal < int(distance) <= maximum

    def _attack_check_mode(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        target: dict[str, Any], range_disadvantage: bool, *,
        ability: str, declared_reckless: bool, spend_luck: bool = False,
        use_grant: bool = True, opportunity: bool = False,
    ) -> dict[str, Any]:
        """Every Advantage/Disadvantage source on a weapon attack roll.

        The registry supplies condition-borne sources (including the
        target-side Advantage that Reckless grants to attackers), and this
        method adds the three code facts that are not conditions: Lucky's
        declared face, Reckless's *Strength-only* self-Advantage, and
        untrained armor.

        Reckless cannot live in the condition registry because the registry
        cannot say "only Strength rolls": a Dexterity Barbarian would get
        Advantage on every attack it makes.  The ability comes from the
        declaration, so a second feature offering the same shape only needs
        its own declaration.
        """

        from .check_modes import attack_mode, mode_from_sources

        extra: list[dict[str, Any]] = []
        if spend_luck:
            extra.append({"code": "lucky", "mode": "advantage"})
        character = self._feature_character(actor)
        condition = str(self.features.feature_value(
            character, "reckless_attack", "self_condition", "",
        ) or "")
        declared_ability = str(self.features.feature_value(
            character, "reckless_attack", "ability", "",
        ) or "")
        if condition and declared_ability and declared_ability == ability and (
            declared_reckless or condition in (actor.get("conditions") or {})
        ):
            extra.append({"code": "reckless", "mode": "advantage"})
        # Studied Attacks 的标记是"攻击者 -> 目标"的一条状态，不是条件：
        # 它跟着攻击者走、只对同一个目标生效，挂在目标身上会让所有攻击者
        # 一起受益。
        mark_kind = str(self.features.feature_value(
            character, "studied_attacks", "mark_kind", "",
        ) or "")
        if mark_kind and self._studied_mark(
            combat, str(actor["actor_id"]), str(target["actor_id"]),
        ):
            extra.append({
                "code": "studied_attacks",
                "mode": str(self.features.feature_value(
                    character, "studied_attacks", "mode", "",
                ) or "advantage"),
            })
        if (
            "pack_tactics" in trait_ids(actor.get("traits"))
            and self._ally_within(instance, combat, actor, target, 5)
        ):
            # Pack Tactics 问的不是"攻击者身边有没有同伙"，而是"目标身边有没有
            # 攻击者的同伙"——那只同伙也是目标的敌人。
            extra.append({"code": "pack_tactics", "mode": "advantage"})
        # 猎人 7 的防御战术：两个选项都是"这一掷"的事实，不是条件，来源在
        # 各自的模块里合成（`combat/defensive_tactics.py`）。
        extra.extend(
            self._defensive_tactics_sources(
                combat, actor, target, opportunity=opportunity,
            )
        )
        check_mode = attack_mode(
            self, instance, actor, target, range_disadvantage,
            use_grant=use_grant, extra_sources=tuple(extra), combat=combat,
        )
        # 未受训护甲：武器/徒手攻击（力/敏掷骰）具有劣势。这里内联而不进
        # check_modes，因为 spell_attack 也用 attack_mode 但走施法属性，不能误伤。
        if bool((actor.get("armor_training") or {}).get("untrained_armor_worn")):
            sources = [
                *check_mode["sources"],
                {"code": "untrained_armor", "mode": "disadvantage"},
            ]
            check_mode = {"mode": mode_from_sources(sources), "sources": sources}
        return check_mode

    def _attack_declaration_state(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        intent: dict[str, Any], weapon: dict[str, Any], ability: str,
        target_id: str,
    ) -> dict[str, Any]:
        """Read the pre-roll declarations this intent makes, already qualified.

        This is "may the declaration take effect", not "is the submission
        legal": validation owns rejection, and the internal attack paths
        (Opportunity Attacks, grant-provided attacks) never carry these
        fields.  Every gate fails closed, so a directly called resolution
        cannot hand out an effect the character has not earned.
        """

        state: dict[str, Any] = {
            "reckless": False, "reckless_condition": "", "reckless_duration": "",
            "brutal": False, "brutal_effects": [], "brutal_dice": "",
            "track_roll": False, "studied_consumed": False, "studied_kind": "",
            "target_id": str(target_id),
        }
        if actor["kind"] not in {"player", "companion"}:
            return state
        character = self._feature_character(actor)
        actor_id = str(actor["actor_id"])

        def parameter(feature_id: str, key: str) -> Any:
            return self.features.feature_value(character, feature_id, key, None)

        if self.features.has_feature(character, "reckless_attack"):
            condition = str(parameter("reckless_attack", "self_condition") or "")
            duration = str(parameter("reckless_attack", "condition_duration") or "")
            declared = bool(intent.get("reckless_attack"))
            # 只在"本回合第一次掷骰之前的真实声明"时落条件；声明与否由
            # validation 校验，这里的 already 检查防的是同一回合重复声明：
            # 重挂一次会让到期时间被无意延长。声明本身不限于力量攻击——
            # 规则只把 Advantage *给予*力量掷骰，没说不许在敏捷攻击时声明。
            if (
                declared and condition and duration
                and condition not in (actor.get("conditions") or {})
            ):
                state["reckless"] = True
                state["reckless_condition"] = condition
                state["reckless_duration"] = duration
            # 每次*自己回合内*的攻击掷骰都记一笔，Reckless 的"第一次"窗口靠它
            # 判定。反应里打出的攻击（借机攻击、Retaliation）不算：它们既开不了
            # 也关不了这个窗口，记下来只会让每个既有反应攻击多出一个与规则无关
            # 的事件。
            own_turn = str(
                (combat.get("economy") or {}).get("actor_id") or ""
            ) == actor_id
            state["track_roll"] = own_turn

        mark_kind = str(parameter("studied_attacks", "mark_kind") or "")
        if mark_kind and self._studied_mark(combat, actor_id, str(target_id)):
            state["studied_consumed"] = True
            state["studied_kind"] = mark_kind

        if declaration_value(intent, BRUTAL_STRIKE) and self.features.has_feature(
            character, "brutal_strike",
        ):
            required = str(parameter("brutal_strike", "requires_declaration") or "")
            ready = state["reckless"]
            if required and not ready:
                ready = str(parameter(required, "self_condition") or "") in (
                    actor.get("conditions") or {}
                )
            dice = self._brutal_strike_dice(character)
            upgrade_level = int(parameter("brutal_strike", "upgrade_level") or 0)
            class_state = self.features.class_state(character)
            level = int(class_state[1]) if class_state else 0
            allowed = [str(value) for value in parameter(
                "brutal_strike", "effect_options",
            ) or []]
            if self.features.has_feature(character, "improved_brutal_strike"):
                allowed.extend(
                    str(value) for value in parameter(
                        "improved_brutal_strike", "additional_effect_options",
                    ) or []
                )
            cap = int(parameter("brutal_strike", "effects_per_use") or 0)
            if level >= upgrade_level:
                cap = int(
                    parameter("brutal_strike", "effects_per_use_upgrade") or cap
                )
            submitted = intent.get("brutal_strike_effects")
            effects = [str(value) for value in submitted or []]
            if (
                ready and dice and parameter("brutal_strike", "ability") == ability
                and len(effects) <= cap
                and len(set(effects)) == len(effects)
                and all(value in allowed for value in effects)
                and not self._feature_used_this_turn(combat, actor_id, "brutal_strike")
            ):
                state["brutal"] = True
                state["brutal_effects"] = effects
                state["brutal_dice"] = dice
        return state

    def _attack_declaration_events(
        self, combat: dict[str, Any], actor_id: str, target_id: str,
        declaration: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """The events a pre-roll declaration owes, in the order it owes them."""

        events: list[dict[str, Any]] = []
        round_index = int(combat.get("round", 0))
        turn_index = int(combat.get("turn_index", 0))
        if declaration["reckless"]:
            events.append({
                "type": "condition.applied", "target_id": actor_id,
                "condition": declaration["reckless_condition"],
                "duration": declaration["reckless_duration"],
                # "Until the start of your next turn" is an expiry the engine
                # resolves by comparing this field to whoever's turn begins,
                # so it has to name the declarer even though the target is
                # also the declarer.  Without it the condition would never
                # come off.
                "source_actor_id": actor_id,
                "source_feature": "reckless_attack",
            })
        if declaration["track_roll"]:
            events.append({
                "type": "dnd2024.attack_roll.recorded", "actor_id": actor_id,
                "round": round_index, "turn_index": turn_index,
            })
        if declaration["brutal"]:
            # 选择在掷骰之前就被消耗：未命中也是花掉了这次 Brutal Strike，
            # 不能改投另一次攻击。记账放在这里，命中结算就不再重复记账。
            events.append({
                "type": "dnd2024.feature.used", "actor_id": actor_id,
                "feature_id": "brutal_strike",
                "round": round_index, "turn_index": turn_index,
            })
        if declaration["studied_consumed"]:
            # 标记在掷骰前消费：命中与未中都结束旧标记，未中时再由
            # ``_studied_mark_events`` 建一个新的，两次不互相叠加。
            events.append({
                "type": "dnd2024.attack_mark.cleared", "actor_id": actor_id,
                "kind": declaration["studied_kind"], "target_id": target_id,
            })
        return events

    def _studied_mark(
        self, combat: dict[str, Any], actor_id: str, target_id: str,
    ) -> dict[str, Any] | None:
        """One attacker's Studied Attacks mark against one creature."""

        marks = combat.get("attack_marks")
        if not isinstance(marks, dict):
            return None
        studied = marks.get("studied")
        if not isinstance(studied, dict):
            return None
        per_actor = studied.get(str(actor_id))
        if not isinstance(per_actor, dict):
            return None
        row = per_actor.get(str(target_id))
        return row if isinstance(row, dict) else None

    def _studied_mark_events(
        self, actor: dict[str, Any], target_id: str,
    ) -> list[dict[str, Any]]:
        """Create the mark a missed attack leaves behind."""

        if actor["kind"] not in {"player", "companion"}:
            return []
        character = self._feature_character(actor)
        kind = str(self.features.feature_value(
            character, "studied_attacks", "mark_kind", "",
        ) or "")
        expiry = str(self.features.feature_value(
            character, "studied_attacks", "expiry", "",
        ) or "")
        if not kind or not expiry:
            return []
        return [{
            "type": "dnd2024.attack_mark.set", "actor_id": str(actor["actor_id"]),
            "kind": kind, "target_id": str(target_id),
            # 1 表示"撑过攻击者的下一个回合"：回合开始递减，见 engine 的到期逻辑。
            "turns_remaining": 1, "expiry": expiry,
        }]

    def _sundering_bonus(
        self, target: dict[str, Any], actor_id: str,
    ) -> tuple[int, str]:
        """The flat bonus another creature's Sundering Blow lends this roll.

        Read from the *condition* the Barbarian left behind, never from the
        attacker's own features: the beneficiary is "another creature", which
        by definition does not own Improved Brutal Strike.  Returns ``(0, "")``
        for the creature that applied it, so the Barbarian never spends its own
        setup on its own next swing.
        """

        # 受益者不需要拥有任何特性，只看目标身上留下了什么。
        for condition_id, stored in (target.get("conditions") or {}).items():
            if not isinstance(stored, dict):
                continue
            if str(stored.get("source_feature") or "") != "improved_brutal_strike":
                continue
            if str(stored.get("source_actor_id") or "") == str(actor_id):
                continue
            bonus = int(stored.get("attack_bonus") or 0)
            if bonus:
                return bonus, str(condition_id)
        return 0, ""

    def _multiattack_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """One intent, N attacks, one action.

        The action is bought once: the first attack opens the budget
        (``attacks_per_action`` = total) and every following attack spends from
        it, which is exactly how the Attack action's extra attacks are paid.
        Two things can end a Multiattack early, and both say so in the ledger
        rather than silently dropping the rest: the target being defeated, and a
        decision window opening (a Shield that must be answered before the next
        die can be rolled).  In the window case the untouched attacks stay
        available, so the GM can finish them as ordinary attacks.
        """

        actor_id = str(intent["actor_id"])
        target_id = str(intent["target_id"])
        actor = self._actor_view(instance, combat, actor_id)
        action = self._declared_action(actor, intent)
        if action is None:  # pragma: no cover - validation guards this
            raise CombatIntentError("multiattack profile is not available to the actor")
        parts = self._multiattack_parts(actor, action, intent)
        total = sum(count for _attack, count in parts)
        target = self._actor_view(instance, combat, target_id)
        remaining_hp = int(target["hp"])
        events: list[dict[str, Any]] = []
        # 每一击都在自己的局部 list 里计数，于是每击的攻击检定都会拿到
        # ``attack:<actor>:0``。整批里同名 id 会让重掷推导与前端对不上，
        # 所以收集完按 (kind, actor) 重排一次序号。
        roll_seq: dict[tuple[str, str], int] = {}
        made = 0
        for attack, count in parts:
            for _ in range(count):
                if remaining_hp <= 0:
                    events.append({
                        "type": "dnd2024.multiattack.truncated",
                        "actor_id": actor_id, "action_id": str(action.get("id") or ""),
                        "reason": "target_defeated", "attacks_made": made,
                        "attacks_left": total - made,
                    })
                    return events
                attempt = {
                    **intent,
                    "attack_id": str(attack.get("id") or ""),
                    # 一次 Multiattack 里不存在"换手/双手"这类声明：它们属于
                    # 玩家武器的意图面，随 intent 复制会把上次攻击的选择带进
                    # 第二击。
                }
                # 任一击被拒绝即整批拒绝：这里不吞异常，抛出的事件批次不会
                # 落地，前几击也不落账（任务书 §2 的原子性要求）。
                part_events = self._attack_events(
                    instance, combat, attempt, rng,
                    attacks_in_action=total if made == 0 else 0,
                )
                for event in part_events:
                    if event.get("type") != "check.resolved":
                        continue
                    key = (str(event.get("kind") or ""), str(event.get("actor_id") or ""))
                    seq = roll_seq.get(key, 0)
                    event["roll_id"] = f"{key[0]}:{key[1]}:{seq}"
                    roll_seq[key] = seq + 1
                events.extend(part_events)
                made += 1
                remaining_hp -= sum(
                    -int(event.get("delta", 0) or 0)
                    for event in part_events
                    if event.get("type") == "resource.changed"
                    and event.get("resource") == "hp"
                    and event.get("target_id") == target_id
                )
                if any(
                    event.get("type") == "dnd2024.decision.pending"
                    for event in part_events
                ):
                    events.append({
                        "type": "dnd2024.multiattack.truncated",
                        "actor_id": actor_id, "action_id": str(action.get("id") or ""),
                        "reason": "pending_decision", "attacks_made": made,
                        "attacks_left": total - made,
                    })
                    return events
        return events

    def _attack_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
        *, spend_attack_action: bool = True,
        horde_breaker: bool = False,
        actor_view: dict[str, Any] | None = None,
        target_view: dict[str, Any] | None = None,
        granted_by: str = "",
        attacks_in_action: int = 0,
        opportunity: bool = False,
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
        sacred_bonus = 0
        if actor["kind"] in {"player", "companion"}:
            # Companion 是"己方角色"：武器与徒手攻击走玩家同一套目录/能力确定性链，
            # 不走 enemy attack profile。
            weapon = self._declared_attack(actor, intent)
            if weapon is None:  # pragma: no cover - validation guards this
                raise CombatIntentError("attack profile is not available to the actor")
            distance = self._distance(combat, actor_id, target_id)
            # 形态攻击的加值来自 stat block（狼的 Bite 是 +4），不是由角色的
            # 属性与熟练推出来的：变形后你就是用野兽的攻击表在打。
            if weapon.get("source") == "form":
                ability = ""
                modifier = int(weapon.get("damage_modifier", 0) or 0)
                attack_bonus = int(weapon.get("attack_bonus", 0) or 0)
                # 形态攻击走 MM3 起统一的伤害段读取：catalog 的 beast 是
                # damage: [{dice, type}]，旧存档与冒险包还是平坦字符串。
                form_parts = attack_damage_parts(weapon)
                damage_formula = str(form_parts[0]["dice"]) if form_parts else ""
                damage_type = str(form_parts[0]["type"]) if form_parts else ""
                is_ranged_weapon = bool(weapon.get("ranged"))
                sacred_bonus = 0
                range_disadvantage = self.attack_range_disadvantage(weapon, distance)
            else:
                # 属性与射程劣势都由共用函数判定：validation、掷骰后的预演和这里
                # 读的是同一份规则，否则"面板显示敏捷、结算按力量"这种裂缝永远
                # 只在某个分支上出现。
                is_ranged_weapon = bool(weapon.get("ranged"))
                ability = self.attack_ability(actor, weapon)
                modifier = ability_modifier(actor["abilities"][ability])
                if weapon.get("unarmed") or (
                    f"weapon_category:{weapon.get('category')}" in actor["weapon_category_refs"]
                ):
                    # 徒手打击是每个 player-like actor 都会使用的天然攻击：熟练
                    # 加值始终生效，不依赖 weapon_category 熟练列表。
                    proficiency = actor["proficiency_bonus"]
                else:
                    proficiency = 0
                attack_bonus = modifier + proficiency
                if is_ranged_weapon:
                    # 战斗风格专长的远程武器加值是特性投影字段：只在真远程武器
                    # 生效，投掷近战武器不适用（与上方 Thrown 语义划分一致）。
                    attack_bonus += int(actor.get("ranged_attack_bonus") or 0)
                # 一次攻击的伤害是一到两段。玩家武器只有一段，形态（野兽的
                # ``attacks[]``）可以带第二段；同一个读取函数让两条路都不用
                # 关心数据形状。
                parts = attack_damage_parts(weapon)
                damage_formula = str(parts[0]["dice"]) if parts else ""
                damage_type = str(parts[0]["type"]) if parts else ""
                if intent.get("two_handed"):
                    # 两用武器双手持用换用更大的骰式；validation 已保证只有声明
                    # damage_two_handed 的武器才允许声明该意图。
                    damage_formula = str(weapon.get("damage_two_handed") or damage_formula)
                # 神圣武器只祝福被绑定的那一把：加值是攻击检定上的独立加成，
                # 不改属性、不改熟练，也不波及同名的另一把武器。
                sacred_bonus = self._sacred_weapon_bonus(
                    actor, str(weapon.get("weapon_ref") or ""),
                )
                if not sacred_bonus and declaration_value(intent, SACRED_WEAPON):
                    # 激活与首次攻击是同一个动作：绑定状态要到批次落地才存在，
                    # 但这一掷已经吃到了它的加值。
                    sacred_bonus, _ability = self._sacred_weapon_bonus_value(actor)
                if sacred_bonus:
                    attack_bonus += sacred_bonus
                    if str(intent.get("sacred_weapon_damage") or "") == "radiant":
                        # 选择只改本次该武器的伤害类型：武器骰不变、数量不变，
                        # 也不会把别的 rider 的既定类型一起改写。
                        damage_type = "radiant"
                range_disadvantage = self.attack_range_disadvantage(weapon, distance)
        else:
            weapon = next(item for item in actor["attacks"] if item["id"] == intent["attack_id"])
            modifier = int(weapon.get("damage_modifier", 0) or 0)
            attack_bonus = int(weapon["attack_bonus"])
            parts = attack_damage_parts(weapon)
            # 第一段是"主伤害"：重击阈值、抗性与日志都按它记账，第二段在
            # 提交时作为独立实例结算。缺省空串而不是 bludgeoning——数据没有
            # 说类型时，引擎不该替它挑一个。
            damage_formula = str(parts[0]["dice"]) if parts else ""
            damage_type = str(parts[0]["type"]) if parts else ""
            distance = self._distance(combat, actor_id, target_id)
            range_disadvantage = self.attack_range_disadvantage(weapon, distance)
        from .check_modes import attack_mode, mode_from_sources

        # 掷骰之前的声明：Reckless / Brutal / Studied 的资格在这里只被"读取"，
        # 合法性由 validation 保证；这里再问一次是因为借机攻击与特性授予的攻击
        # 也会走本函数，而它们的意图里根本不带这些字段。
        declaration = self._attack_declaration_state(
            instance, combat, actor, intent, weapon, ability, target_id,
        )
        # Lucky's advantage face is declared before the roll: it adds one
        # source and is consumed unconditionally (validation proves the pool).
        spend_luck = bool(intent.get("spend_luck"))
        check_mode = self._attack_check_mode(
            instance, combat, actor, target, range_disadvantage,
            ability=ability,
            declared_reckless=declaration["reckless"],
            spend_luck=spend_luck, use_grant=spend_attack_action,
            opportunity=opportunity,
        )
        if declaration["brutal"]:
            # 放弃全部 Advantage：来源仍写进事件留痕，但这一掷只掷一颗 d20。
            # 存在任何 Disadvantage 时 validation 已拒绝声明，所以"单骰"在这里
            # 是唯一正确的形态，不需要再猜哪一份 Advantage 该被删掉。
            check_mode = {"mode": "", "sources": check_mode["sources"]}
        advantage, disadvantage = check_mode["mode"] == "advantage", check_mode["mode"] == "disadvantage"
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if advantage or disadvantage else None
        natural = max(first, second) if advantage and second else min(first, second) if disadvantage and second else first
        check_bonus_rolls = condition_check_bonus_rolls(
            self.catalog.conditions,
            self._effective_conditions(instance, combat, actor), "attack", rng,
        )
        check_bonus = sum(row["roll"] for row in check_bonus_rolls)
        # Sundering Blow：另一个生物对该目标的第一次攻击 +5。攻击者本人
        # 不受益，所以这里必须比对施加者；条件本身是唯一的（同一条件下一次
        # 只能挂一份），因此"一次攻击最多 +5"由数据形状保证。
        sundering_bonus, sundering_condition = self._sundering_bonus(target, actor_id)
        total = natural + attack_bonus + check_bonus + sundering_bonus
        # 掩蔽是位置事实，不是目标身上的状态：同一只生物走出灵光就失去它，
        # 走回来（效果尚未到期）又恢复。查询与光环豁免加值同源。
        cover = self._cover_record(instance, combat, target)
        target_ac = effective_armor_class(target) + int(cover["armor_class_bonus"])
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
            *([self._attack_cost_event(
                combat, actor,
                resource=(
                    ""
                    if declaration_value(intent, LIGHT_EXTRA_ATTACK)
                    and nick_active(self.features, self._feature_character(actor), weapon)
                    else "bonus_action"
                    if declaration_value(intent, LIGHT_EXTRA_ATTACK) else ""
                ),
                nick_extra=bool(
                    declaration_value(intent, LIGHT_EXTRA_ATTACK)
                    and nick_active(self.features, self._feature_character(actor), weapon)
                ),
                free=bool(
                    declaration_value(intent, LIGHT_EXTRA_ATTACK)
                    and nick_active(self.features, self._feature_character(actor), weapon)
                ),
                weapon=weapon,
                # Multiattack 的第一次攻击为"这一次动作"开一张总额度，后续
                # 每一击都从同一份额度里扣：动作只付一次，次数不重复付。
                attacks_per_action=attacks_in_action,
            )] if spend_attack_action else []),
        ]
        events.extend(self._attack_declaration_events(
            combat, actor_id, target_id, declaration,
        ))
        if declaration_value(intent, SACRED_WEAPON):
            # 激活不是独立动作：它占用的是这次真实 Attack action 的首个攻击，
            # 所以支付与绑定都落在这个攻击的同一批事件里。
            events.extend(self._sacred_weapon_events(
                instance, actor, str(weapon.get("weapon_ref") or ""),
            ))
        if sundering_bonus and sundering_condition:
            # 消费时机是"被这次攻击用掉"，与命中与否无关：+5 已经加进了这一掷。
            events.append({
                "type": "condition.removed", "target_id": target_id,
                "condition": sundering_condition, "reason": "sundering_consumed",
            })
        ammo_ref = weapon.get("ammunition_item_ref")
        if ammo_ref and actor["kind"] == "player":
            events.append({
                "type": "dnd2024.item.consumed",
                "actor_id": actor_id,
                "item_ref": str(ammo_ref),
                "amount": 1,
            })
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
            "roll_id": self._roll_id(events, "attack", actor_id),
        }
        if crit_threshold != 20:
            # 默认 20 时不写字段，避免对既有事件消费方的无谓扰动。
            check_event["crit_threshold"] = crit_threshold
        if sundering_bonus:
            # 单独成项：modifier 仍是攻击者本来的加值，+5 不并进去，
            # 否则事件里再也看不出这一掷吃过破甲一击。
            check_event["sundering_bonus"] = sundering_bonus
        if sacred_bonus:
            # 神圣武器是真实攻击加值（已计入 modifier），这里只留一份可审计的
            # 来源，让日志看得出这一掷里有多少来自被祝福的武器。
            check_event["sacred_weapon_bonus"] = sacred_bonus
        if int(cover["armor_class_bonus"]):
            # AC 已经包含掩蔽，这里同样只留来源：否则日志里 target 变了却
            # 看不出是谁给的掩蔽。
            check_event["cover_bonus"] = int(cover["armor_class_bonus"])
            check_event["cover_sources"] = cover["sources"]
        if declaration["brutal"]:
            # 记录"这次放弃了 Advantage"这一事实。advantage_mode 留空是因为
            # 真的只掷了一颗骰；来源列表保留，让审计看得出放弃了哪几份。
            check_event["brutal_strike"] = True
        if crit_on_hit:
            # 这是"命中之后"才升级的重击，不是 d20 本身掷出的重击。标记出来，
            # 掷后窗口才知道扣减总数可以把它退回普通命中甚至失手；不标记的话
            # ``critical`` 会被当成必中，Cutting Words 就永远改不成 miss。
            check_event["critical_upgrade"] = True
        rage_sustain = bool(
            target["kind"] == "enemy" and "rage" in actor["conditions"]
            and str((combat.get("economy") or {}).get("actor_id") or "") == str(actor_id)
        )
        if (
            declaration_value(intent, LIGHT_EXTRA_ATTACK)
            and actor["kind"] in {"player", "companion"}
            and not bool(actor.get("light_extra_attack_ability_damage"))
        ):
            # Light 追加攻击默认不吃属性调整值，但负值仍然生效（规则原文：
            # "unless that modifier is negative"），所以这里只压掉正数。
            # 唯一例外是 Two-Weapon Fighting 风格，由角色投影回答。
            modifier = min(0, modifier)
        settlement = {
            "actor_id": actor_id, "target_id": target_id,
            "weapon": deepcopy(weapon), "ability": ability, "modifier": modifier,
            "damage_formula": damage_formula, "damage_type": damage_type,
            "crit_on_hit": crit_on_hit, "rage_sustain": rage_sustain,
            "exclude_bards": [], "spent_reactions": [],
            "remaining_windows": [], "intent": deepcopy(intent),
        }
        if hit:
            window = self._attack_check_window_offer(
                instance, combat, actor, target, check_event, settlement,
            )
            if window is not None:
                # The check has been rolled but nothing has landed: no damage
                # die is rolled and no resolution event is emitted until the
                # window owner decides.
                events.append({"type": "dnd2024.decision.pending", **window})
                return events
        elif actor.get("side") == "party":
            # A failed attack roll opens the holder-side windows: the
            # attacker's own Bardic Inspiration die or Peerless Skill can
            # still turn the miss around, and the hit-face windows re-derive
            # from the settlement if the die flips it.
            window = self._failed_attack_window_offer(
                instance, combat, actor, check_event, settlement,
            )
            if window is not None:
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
            # Multiattack Defense：命中之后才立标记，所以这一击自己不受影响，
            # 只有本回合后续对该防御者的攻击骰吃劣势。
            events.extend(self._multiattack_mark_events(actor, target))
            events.extend(self._hit_settlement_events(
                instance, combat, actor, target, weapon, check_event, intent, rng,
                ability=ability, modifier=modifier,
                damage_formula=damage_formula, damage_type=damage_type,
                crit_on_hit=crit_on_hit, granted_by=granted_by,
                horde_breaker=horde_breaker,
            ))
        else:
            events.extend(self._miss_mastery_events(
                instance, combat, actor, target, weapon, modifier, damage_type, rng,
            ))
            # Studied Attacks：未命中才建立标记。**只在直通路径**建立——
            # 开窗（诗人的激励骰可能把这次失手改判成命中）时不写标记，
            # 宁可少一次机会，也不给一次并未失手的攻击留下优势。
            events.extend(self._studied_mark_events(actor, target_id))
        events.extend(self._consume_attack_conditions(actor_id, target_id, actor, target))
        return events
