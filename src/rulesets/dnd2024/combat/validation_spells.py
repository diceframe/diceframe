"""Spell, metamagic and mark-declaration checks.

One of the validation mixins: the engine assembles them into
``CombatValidationMixin``; the class holds no state of its own."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from ..features.attack_declarations import validate_uses
from ..features.models import ClassFeatureError
from ..features.resources import OVERCHANNEL_FEATURE_ID, OVERCHANNEL_OWNER, OVERCHANNEL_SPELL_CLASS, feature_usage_count
from ..spells.metamagic import MetamagicError
from ..character.primitives import ability_modifier
from .primitives import slot_available, CombatIntentError
from .check_modes import owned_mark, vision_codes
from .triggered_spells import free_cast_resource, hit_spell_decision


class CombatValidationSpellsMixin:
    """Spell, metamagic and mark-declaration checks."""

    __slots__ = ()

    if TYPE_CHECKING:
        _feature_character: Any
        features: Any
        spells: Any
        _actor_view: Any
        _require_range: Any
        _spell_effective_range: Any
        def _validate_lucky_spend(
            self, instance: Any, intent: dict[str, Any], actor: dict[str, Any],
        ) -> None:
            ...
        catalog: Any
        _distance: Any
        _potent_spellcasting_owner: Any

    def _validate_metamagic(
        self, intent: dict[str, Any], actor: dict[str, Any],
        spell: dict[str, Any], effect: dict[str, Any], combat: dict[str, Any],
    ) -> None:
        """Metamagic is priced and checked before anything is rolled.

        Validation and settlement both call ``metamagic.plan`` -- the difference
        is only that this one turns its refusal into a combat error, so a bad
        request never deducts a point or spends a slot.
        """

        if not (intent.get("metamagic") or []):
            return
        if effect.get("mode") == "triggered_damage":
            # 触发型法术（至圣斩等）走另一条结算路径，不经过 _spell_events 的
            # 效果处理；接受超魔只会"扣了点、什么也没改"，所以直接拒绝。
            raise CombatIntentError("metamagic cannot modify a triggered spell")
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "metamagic"):
            raise CombatIntentError("this character has no Metamagic feature")
        try:
            plan = self._metamagic_plan(
                intent, character, spell, effect, combat,
                conditions=actor.get("conditions") or {},
            )
        except MetamagicError as exc:
            raise CombatIntentError(str(exc)) from None
        if plan.has("careful_spell"):
            # 原文：最多等于魅力调整值（至少 1 个）的生物，且必须是本法术的
            # 目标之一——否则 1 点就能让任意多个生物免疫。
            limit = max(1, ability_modifier(int(actor["abilities"].get("cha", 10))))
            chosen = list(plan.parameter("careful_spell") or ())
            if len(chosen) > limit:
                raise CombatIntentError(
                    f"Careful Spell can protect at most {limit} creatures"
                )
            declared = {
                str(intent.get("target_id") or ""),
                *[str(item) for item in intent.get("target_ids") or []],
            } - {""}
            if not set(chosen) <= declared:
                raise CombatIntentError(
                    "Careful Spell targets must be targets of this spell"
                )
        if plan.has("empowered_spell"):
            # Empowered Spell 的重掷数量上限是魅力调整值（至少一颗）。
            limit = max(1, ability_modifier(int(actor["abilities"].get("cha", 10))))
            count = int(plan.parameter("empowered_spell") or 0)
            if count > limit:
                raise CombatIntentError(
                    f"Empowered Spell can reroll at most {limit} dice"
                )

    def _metamagic_plan(
        self, intent: dict[str, Any], character: dict[str, Any],
        spell: dict[str, Any], effect: dict[str, Any], combat: dict[str, Any],
        conditions: dict[str, Any] | None = None,
    ) -> Any:
        from ..spells import metamagic

        known = self.features.metamagic_known(character)
        economy = combat.get("economy") or {}
        # `_feature_character` 只带特性切片，不带条件：Innate 是否激活必须问
        # actor view，否则 Incarnate 的双名额与 Apotheosis 的免费永远为假。
        active = "innate_sorcery" in (
            conditions if conditions is not None
            else (character.get("conditions") or {})
        )
        incarnate = (
            active and self.features.has_feature(character, "sorcery_incarnate")
        )
        apotheosis = (
            active and self.features.has_feature(character, "arcane_apotheosis")
        )
        return metamagic.plan(
            intent,
            spell=spell,
            effect=effect,
            known=known,
            points=int((character.get("resources") or {}).get("class", {})
                       .get("sorcery_points", {}).get("current", 0) or 0),
            incarnate=incarnate,
            apotheosis=apotheosis,
            free_used_this_turn=bool(economy.get("free_metamagic_used")),
            cast_level_one_plus_this_turn=bool(economy.get("cast_level_one_plus")),
        )

    def _spell_entity(self, spell_ref: str) -> dict[str, Any] | None:
        """与其它施法校验读同一本目录：`self.spells`，不是 bundle.get。

        `bundle.get("spell", id)` 对法术目录返回空，写在那里的门控永远不会触发。
        """

        if not spell_ref:
            return None
        return self.spells.get(spell_ref)

    def _class_resource_state(self, character: dict[str, Any], resource_id: str) -> dict[str, int]:
        state = (character.get("resources") or {}).get("class", {}).get(resource_id)
        if not isinstance(state, dict):
            return {"current": 0, "maximum": 0}
        return {
            "current": int(state.get("current", 0) or 0),
            "maximum": int(state.get("maximum", 0) or 0),
        }

    def _validate_spell(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        if actor["kind"] not in {"player", "companion"}:
            raise CombatIntentError("enemy spell profiles are not enabled for this actor")
        if "rage" in actor["conditions"]:
            raise CombatIntentError("you cannot cast spells while raging")
        if bool((actor.get("armor_training") or {}).get("untrained_armor_worn")):
            raise CombatIntentError("armor you are not trained in blocks spellcasting")
        spell_ref = str(intent.get("spell_ref") or "")
        spell = self.spells.get(spell_ref)
        effect = self.catalog.spell_effects.get(spell_ref.removeprefix("spell:"))
        if spell is None or effect is None:
            raise CombatIntentError("spell is not deterministically automated yet")
        if spell_ref not in actor["spell_refs"]:
            raise CombatIntentError("spell is not prepared or known by the actor")
        self._validate_metamagic(intent, actor, spell, effect, combat)
        if effect["mode"] == "reaction_defense":
            raise CombatIntentError("this spell is cast as a reaction when you are hit")
        error = validate_uses(
            self.features, self._feature_character(actor), intent, "cast_spell",
            attack_roll=effect["mode"] == "spell_attack",
        )
        if error:
            raise CombatIntentError(error)
        trigger = hit_spell_decision(combat, actor["actor_id"])
        if effect["mode"] == "triggered_damage" and trigger is None:
            raise CombatIntentError("this spell requires a pending melee hit")
        if trigger is not None and effect["mode"] != "triggered_damage":
            raise CombatIntentError("resolve the melee hit spell decision first")
        if intent.get("retarget_mark"):
            # 转移标记是一次独立的声明：它复用同一个法术实例，因此不走
            # 施法成本、目标数量与免费次数那一整套。
            self._validate_mark_retarget(instance, combat, intent, actor, economy, effect)
            return
        if intent.get("spend_luck") and effect["mode"] != "spell_attack":
            raise CombatIntentError("spend_luck requires a spell attack roll")
        self._validate_lucky_spend(instance, intent, actor)
        plan = self._metamagic_plan(
            intent, self._feature_character(actor), spell, effect, combat,
            conditions=actor.get("conditions") or {},
        ) if intent.get("metamagic") else None
        # Quickened Spell 把"一个动作的施法"改成附赠动作：成本要按它读，
        # 否则玩家付了附赠动作却仍被要求有动作。
        quickened = bool(plan and plan.has("quickened_spell"))
        cost = "bonus_action" if (
            quickened or str(spell["casting_time"]).startswith("Bonus Action")
        ) else "action"
        if not slot_available(economy, cost):
            raise CombatIntentError(f"the {cost.replace('_', ' ')} has already been spent")
        # Quickened 的双向互斥都靠这一条：本回合只要施放过 1 环+ 法术（含免费
        # 施法与专长来源），就不能再 Quickened，也不能在被 Quickened 之后放。

        slot_level = intent.get("slot_level", spell["level"])
        if isinstance(slot_level, bool) or not isinstance(slot_level, int):
            raise CombatIntentError("slot_level must be an integer")
        if economy.get("quickened_cast") and slot_level >= 1:
            # Quickened 之后本回合不能再放 1 环+（戏法不受限）。
            raise CombatIntentError(
                "a level 1+ spell cannot be cast after Quickened Spell this turn"
            )
        if spell["level"] == 0:
            if slot_level != 0:
                raise CombatIntentError("a cantrip does not use a spell slot")
        else:
            if slot_level == 0:
                if free_cast_resource(actor, spell_ref, int(spell["level"])) is None:
                    raise CombatIntentError("the free spell cast is not available")
            elif economy.get("slot_spell_cast"):
                raise CombatIntentError("only one spell slot can be expended on a turn")
            if slot_level != 0 and slot_level < spell["level"]:
                raise CombatIntentError("slot_level is lower than the spell level")
            if slot_level != 0 and int(actor["slots"].get(str(slot_level), 0) or 0) < 1 \
                    and int((actor.get("font_slots") or {}).get(str(slot_level), 0) or 0) < 1:
                raise CombatIntentError("the selected spell slot is not available")
        raw_targets = intent.get("target_ids")
        target_values = raw_targets if isinstance(raw_targets, list) else [intent.get("target_id")]
        target_ids: list[str] = [
            str(item or "") for item in target_values if str(item or "")
        ]
        required_count = int(effect.get("target_count", 1) or 1)
        upcast_targets = int(effect.get("upcast_target_count", 0) or 0)
        if not upcast_targets and bool(effect.get("upcast_target_bonus")):
            # 数据里"可升环多选一个目标"有两种写法：布尔标记只说明"每升一环
            # 多一个目标"，等价于 count=1。
            upcast_targets = 1
        if upcast_targets:
            required_count += upcast_targets * max(0, slot_level - int(spell["level"]))
        if plan is not None and plan.has("twinned_spell") and upcast_targets:
            # Twinned Spell 把法术的**有效环阶** +1：多出来的一个目标不花更高
            # 的槽，槽位仍按原环阶结算。
            required_count += upcast_targets
        has_duplicates = len(set(target_ids)) != len(target_ids)
        if not target_ids or len(target_ids) > required_count or (
            has_duplicates and not effect.get("repeat_targets")
        ):
            raise CombatIntentError(f"spell requires 1 to {required_count} unique targets")
        if effect["mode"] == "triggered_damage" and target_ids != [trigger["target_id"]]:
            raise CombatIntentError("this spell must target the creature hit by the melee attack")
        for target_id in target_ids:
            target = self._actor_view(instance, combat, target_id)
            actor_side = str(actor.get("side") or "")
            target_side = str(target.get("side") or "")
            if effect["mode"] in {
                "healing", "buff", "stabilize", "temp_hp", "teleport", "revive",
            }:
                if target_side != actor_side:
                    raise CombatIntentError("healing and beneficial spells require an allied target")
                if effect["mode"] == "revive":
                    if target["kind"] != "player":
                        raise CombatIntentError("revive spells target players only")
                    if "dead" not in target.get("conditions", {}):
                        raise CombatIntentError("revive spells require a dead target")
                elif "dead" in target.get("conditions", {}):
                    raise CombatIntentError("dead targets require a resurrection effect")
            elif target_side == actor_side and effect["mode"] not in {
                "dispel", "obscuring_zone",
            }:
                # 区域法术只借用目标所在的坐标来定圆心，不区分敌我：
                # 一团雾盖住自己也盖住敌人。
                raise CombatIntentError("offensive spells require a hostile target")
            if effect.get("target_scope") == "self" and target_id != str(actor["actor_id"]):
                # 声明式 Self 约束：内容声明什么，这里就只校验什么，
                # 不再为具体法术 mode 复制特判。
                raise CombatIntentError("this spell can only target the caster")
            if effect["mode"] == "dispel" and "dead" in target.get("conditions", {}):
                # 解除魔法可对任意阵营，但死亡目标不在"生物"范围内。
                raise CombatIntentError("dead targets cannot be dispelled")
            if effect["mode"] == "stabilize" and (
                target["kind"] != "player" or target["hp"] > 0
                or "stable" in target.get("conditions", {})
            ):
                raise CombatIntentError("this spell requires a dying ally at 0 HP")
            if target["hp"] <= 0 and effect["mode"] not in {
                "healing", "buff", "stabilize", "dispel", "revive",
            }:
                raise CombatIntentError("spell target is not active")
            allowed_types = effect.get("target_creature_types")
            if allowed_types and str(target.get("creature_type") or "") not in allowed_types:
                raise CombatIntentError("the spell target's creature type is not eligible")
            if effect["mode"] != "triggered_damage":
                # 射程是特性可能改写过的值（德鲁伊 15 的元素之怒），不是目录原文。
                self._require_range(
                    combat, actor["actor_id"], target_id,
                    self._spell_effective_range(actor, spell, effect, plan),
                )
            if effect.get("marks_target") and not self._spell_target_visible(
                instance, combat, actor, target,
            ):
                # 只有"你能看见的一个生物"才能被标记；不可见目标在 30 尺内
                # 被 Blindsight 覆盖时算看得见，这正是 Feral Senses 的作用点。
                raise CombatIntentError("you cannot see the creature you are marking")
        self._validate_cantrip_temp_hp_target(instance, combat, intent, actor, spell, effect)
        damage_choices = effect.get("damage_type_choice")
        if damage_choices and intent.get("damage_type") not in damage_choices:
            raise CombatIntentError("spell damage_type choice is required")
        resist_choices = effect.get("resist_choice")
        if resist_choices:
            if intent.get("resist_type") not in resist_choices:
                raise CombatIntentError("spell resist_type choice is required")
        elif intent.get("resist_type") is not None:
            raise CombatIntentError("this spell does not take a resist_type")
        maximise = intent.get("overchannel")
        if maximise is not None and not isinstance(maximise, bool):
            raise CombatIntentError("overchannel must be a boolean")
        if maximise:
            character = self._feature_character(actor)
            if not self.features.has_feature(
                character, OVERCHANNEL_FEATURE_ID, owner=OVERCHANNEL_OWNER,
            ):
                raise CombatIntentError("overchannel is required to maximize damage")
            # 列表归属来自官方目录的 `class_refs`；学派不是门槛。
            if OVERCHANNEL_SPELL_CLASS not in spell["class_refs"]:
                raise CombatIntentError("overchannel only applies to Wizard spells")
            if effect["mode"] not in {"spell_attack", "saving_throw", "automatic_damage"} \
                    or not effect.get("damage"):
                raise CombatIntentError("this spell does not deal damage")
            max_slot = int(self.features.feature_value(
                character, OVERCHANNEL_FEATURE_ID, "maximum_slot_level", 0,
                owner=OVERCHANNEL_OWNER,
            ) or 0)
            if not 1 <= int(slot_level) <= max_slot:
                raise CombatIntentError(
                    f"maximized casting requires a spell slot of level 1-{max_slot}"
                )
            # 坏计数必须在这里炸掉，不能落成"首次免费"。
            try:
                feature_usage_count(
                    character, OVERCHANNEL_OWNER, OVERCHANNEL_FEATURE_ID,
                )
            except ClassFeatureError as exc:
                raise CombatIntentError(str(exc)) from exc
        if effect["mode"] == "teleport":
            distance = intent.get("teleport_distance")
            if isinstance(distance, bool) or not isinstance(distance, int) or distance == 0:
                raise CombatIntentError("teleport distance must be a non-zero integer")
            if abs(distance) > int(effect["range"]):
                raise CombatIntentError("teleport distance exceeds the spell range")
        elif intent.get("teleport_distance") is not None:
            raise CombatIntentError("teleport distance is not valid for this spell")

    def _spell_target_visible(
        self, instance: Any, combat: dict[str, Any],
        actor: dict[str, Any], target: dict[str, Any],
    ) -> bool:
        """Whether the caster can see this creature right now.

        Visibility is not "is it invisible": it is "does its Invisible still
        hide it from *me*".  The registry owns the second half -- the negations
        that cancel ``invisible`` -- so this method asks the same list the
        attack matrix asks, at this distance, instead of inventing a second
        rule for spell targeting.
        """

        target_conditions = target.get("conditions") or {}
        if "invisible" not in target_conditions:
            return True
        negations: set[str] = set()
        for declaration in (
            (self.catalog.conditions.get("invisible") or {}).get("attack_modes") or []
        ):
            negations.update(str(code) for code in declaration.get("negated_by") or [])
        distance = self._distance(
            combat, str(actor["actor_id"]), str(target["actor_id"]),
        )
        return bool(negations & vision_codes(self, actor, distance=distance))

    def _validate_mark_retarget(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], economy: dict[str, Any], effect: dict[str, Any],
    ) -> None:
        """Every precondition of "move the mark" is checked before any event.

        The move is free on purpose: it reuses the instance, so it must not
        look like a second cast to validation, the reducer or the log.  What it
        does cost is the Bonus Action, and what it requires is that the spell
        it moves is still *this caster's* current instance.
        """

        if not effect.get("marks_target"):
            raise CombatIntentError("this spell has no mark to move")
        if not owned_mark(actor):
            raise CombatIntentError("there is no Hunter's Mark to move")
        if int(economy.get("bonus_action", 0) or 0) < 1:
            raise CombatIntentError("the bonus action has already been spent")
        raw = intent.get("target_ids")
        target_ids = [str(item) for item in raw] if isinstance(raw, list) else []
        if len(target_ids) != 1 or not target_ids[0]:
            raise CombatIntentError("the mark moves to exactly one creature")
        new_target_id = target_ids[0]
        current_id = str(owned_mark(actor).get("mark_target_id") or "")
        if not current_id:
            raise CombatIntentError("this Hunter's Mark has no target")
        if new_target_id == current_id:
            raise CombatIntentError("the mark is already on that creature")
        current = self._actor_view(instance, combat, current_id)
        if int(current.get("hp", 0) or 0) > 0:
            raise CombatIntentError(
                "the mark can only move after its target drops to 0 hit points"
            )
        target = self._actor_view(instance, combat, new_target_id)
        if int(target.get("hp", 0) or 0) <= 0:
            raise CombatIntentError("spell target is not active")
        if str(target.get("side") or "") == str(actor.get("side") or ""):
            # 与施放同一条边界：标记属于进攻性法术，不能落在盟友身上。
            raise CombatIntentError("Hunter's Mark marks a hostile creature")
        self._require_range(
            combat, str(actor["actor_id"]), new_target_id,
            int(effect.get("mark_retarget_range") or effect.get("range") or 0),
        )
        if not self._spell_target_visible(instance, combat, actor, target):
            raise CombatIntentError("you cannot see the new target")

    def _validate_cantrip_temp_hp_target(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], spell: dict[str, Any], effect: dict[str, Any],
    ) -> None:
        """Improved Blessed Strikes' temp HP grant, checked before any dice roll.

        Every clause of "when you cast a Cleric cantrip that deals damage, you
        can grant ... to yourself or one creature you can see within 60 feet"
        is a separate gate: the feature, the Potent Spellcasting option being
        the one actually chosen, a damaging cantrip, an ally, and the range.
        Granting on a hostile is refused: temporary hit points are a benefit,
        and "one creature" is never read as "any creature, including the one I
        am shooting".
        """

        raw = intent.get("cantrip_temp_hp_target_id")
        if raw is None:
            return
        if not isinstance(raw, str) or not raw:
            raise CombatIntentError("cantrip_temp_hp_target_id must be an actor id")
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "improved_blessed_strikes"):
            raise CombatIntentError(
                "improved blessed strikes is required to grant temporary hit points"
            )
        if int(spell.get("level", 0) or 0) != 0 or not effect.get("damage"):
            raise CombatIntentError("the temporary hit points require a damaging cantrip")
        if self._potent_spellcasting_owner(actor, spell) != "class:cleric":
            raise CombatIntentError(
                "cleric potent spellcasting is required to grant the temporary hit points"
            )
        target = self._actor_view(instance, combat, raw)
        if str(target.get("side") or "") != str(actor.get("side") or ""):
            raise CombatIntentError(
                "the temporary hit points must go to the caster or an ally"
            )
        if "dead" in (target.get("conditions") or {}):
            raise CombatIntentError("the temporary hit point target is not active")
        if raw != str(actor["actor_id"]):
            self._require_range(
                combat, str(actor["actor_id"]), raw,
                int(self.features.feature_value(
                    character, "improved_blessed_strikes", "temp_hp_range_ft", 0,
                ) or 0),
            )
