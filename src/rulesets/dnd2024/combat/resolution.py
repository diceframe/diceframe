"""Deterministic event resolution stage for D&D 2024 combat."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.rulesets.dnd2024.character.builder import ability_modifier
from src.engine.player_control import is_ai_controlled
from src.engine.modules import ruleset_runtime
from src.engine.staged_transaction import fork_domain_state
from src.rulesets.dnd2024.features.resolver import ENEMY_REDUCED_TO_ZERO_HP
from src.rulesets.dnd2024.features.resources import (
    OVERCHANNEL_FEATURE_ID,
    OVERCHANNEL_OWNER,
    USAGE_SCOPE_LONG_REST,
    feature_usage_count,
)
from ..character.attunement import AttunementError, canonical_item_ref
from ..items.activation import (
    ItemActivationError,
    build_item_healing_event,
    validate_activation_declaration,
)
from ..items.charges import (
    ChargesError,
    charges_of,
    destroy_check_events,
    validate_charges_declaration,
)
from ..spells.font_of_magic import (
    FontOfMagicError,
    apply_create_slot,
    apply_slot_to_points,
    create_slot_plan,
    declaring_class_level,
    parse_created_slot_costs,
    slot_to_points_plan,
)
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
    RELENTLESS_RAGE_DECISION,
    CombatIntentError,
    RAGE_MAX_ROUNDS,
    UNARMED_STRIKE_REF,
    actor_kind as _actor_kind,
    canonical as _canonical,
    companion_actor as _companion_actor,
    enemy_actor as _enemy_actor,
    player_actor as _player_actor,
    rider_attack_kind,
)
from .check_modes import (
    condition_check_bonus_rolls,
    condition_flags,
    effective_speed,
    mode_from_sources,
)
from src.rulesets.dnd2024.features.resources import resource_state
from .check_windows import CHECK_WINDOW_DECISION, DAMAGE_WINDOW_DECISION
from .reaction_spells import COUNTER_SPELL_DECISION, HIT_REACTION_DECISION, effective_armor_class
from .triggered_spells import HIT_SPELL_DECISION
from .weapon_masteries import WEAPON_MASTERY_EFFECTS, WEAPON_MASTERY_FEATURE_ID

# Potent Spellcasting is declared once per casting class with its own
# ``cantrip_source_class``; both declarations have to be asked separately.
POTENT_SPELLCASTING_OWNERS = ("class:cleric", "class:druid")


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

    def _activate_item_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Activation: pay the declared cost/charge/copy, then heal the target."""

        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        try:
            item_ref = canonical_item_ref(intent.get("item_ref"))
        except AttunementError as exc:  # pragma: no cover - validation guards this
            raise CombatIntentError(str(exc)) from None
        item = self.bundle.get("item", item_ref.removeprefix("item:"))
        if not isinstance(item, dict):  # pragma: no cover - validation guards this
            raise CombatIntentError("item is not defined in the active bundle")
        try:
            declaration = validate_activation_declaration(item)
            maximum = validate_charges_declaration(item)
        except (ItemActivationError, ChargesError) as exc:  # pragma: no cover
            raise CombatIntentError(str(exc)) from None
        if declaration is None:  # pragma: no cover - validation guards this
            raise CombatIntentError("item cannot be activated")
        target_id = str(intent.get("target_id") or actor_id)
        target = actor if target_id == actor_id else self._actor_view(
            instance, combat, target_id,
        )
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.action.spent",
            "actor_id": actor_id,
            "resource": declaration["cost"],
            "amount": 1,
        }]
        charge_cost = declaration.get("charge_cost")
        if maximum is not None and charge_cost is not None:
            events.append({
                "type": "dnd2024.item.charge_spent",
                "actor_id": actor_id,
                "item_ref": item_ref,
                "amount": int(charge_cost),
            })
        if bool(item.get("consumable")):
            events.append({
                "type": "dnd2024.item.consumed",
                "actor_id": actor_id,
                "item_ref": item_ref,
                "amount": 1,
            })
        node = formula_node_from_dice_formula(declaration["effect"]["healing"])
        healing, rolls = roll_damage_node(node, rng)
        healing = max(1, healing)
        blocked = "cannot_regain_hp" in (target.get("conditions") or {})
        events.append(build_item_healing_event(
            item_ref=item_ref,
            target_id=target_id,
            rolls=rolls,
            healing=healing,
            hp_before=int(target.get("hp", 0) or 0),
            hp_max=int(target.get("max_hp", 0) or 0),
            blocked_by="cannot_regain_hp" if blocked else None,
        ))
        events.append({
            "type": "dnd2024.item.activated",
            "actor_id": actor_id,
            "item_ref": item_ref,
            "target_id": target_id,
            "cost": declaration["cost"],
        })
        # 销毁判定永远排在全部效果之后：先结算的治疗不得被销毁回滚。
        events.extend(self._destroy_check_events(
            instance, actor_id, item_ref, item, charge_cost=charge_cost, rng=rng,
        ))
        return events

    def _destroy_check_events(
        self,
        instance: Any,
        actor_id: str,
        item_ref: str,
        item: dict[str, Any],
        *,
        charge_cost: Any,
        rng: Any,
    ) -> list[dict[str, Any]]:
        if charge_cost is None:
            return []
        current = charges_of(self._canonical_for_actor(instance, actor_id), item_ref)
        if current is None:
            return []
        remaining = current - int(charge_cost)
        return destroy_check_events(
            item,
            actor_id=actor_id,
            item_ref=item_ref,
            remaining_charges=remaining if remaining >= 0 else None,
            rng=rng,
        )

    def _canonical_for_actor(self, instance: Any, actor_id: str) -> dict[str, Any]:
        kind, raw_id = _actor_kind(actor_id)
        if kind == "player":
            return _canonical(instance.get_character_sheet(raw_id))
        if kind == "companion":
            return self._companion(instance, raw_id).get("ruleset_character") or {}
        return {}

    def _start_combat_event(
        self, instance: Any, intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        enemies: dict[str, dict[str, Any]] = {}
        initiatives: list[dict[str, Any]] = []
        positions: dict[str, int] = {}
        companions = self._active_companions(instance)
        for uid in sorted(instance.players):
            canonical = _canonical(instance.get_character_sheet(uid))
            actor_id = _player_actor(uid)
            modifier = int(canonical.get("derived", {}).get("initiative", 0) or 0)
            initiatives.append({
                "actor_id": actor_id, "modifier": modifier, "kind": "player",
                **self._initiative_roll(canonical, modifier, rng),
            })
            positions[actor_id] = int(intent.get("player_positions", {}).get(uid, 0) or 0)
        for companion_id in sorted(companions):
            canonical = companions[companion_id].get("ruleset_character") or {}
            actor_id = _companion_actor(companion_id)
            modifier = int(canonical.get("derived", {}).get("initiative", 0) or 0)
            initiatives.append({
                "actor_id": actor_id, "modifier": modifier, "kind": "companion",
                **self._initiative_roll(canonical, modifier, rng),
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
        # Superior Inspiration hooks the real Initiative roll, not a static
        # character value: every bard that rolled here tops up to two uses.
        refills: list[dict[str, Any]] = []
        for uid in sorted(instance.players):
            canonical = _canonical(instance.get_character_sheet(uid))
            if not self.features.has_feature(canonical, "superior_inspiration"):
                continue
            target = self.features.feature_value(
                canonical, "superior_inspiration", "initiative_refill", 0,
            )
            if isinstance(target, bool) or not isinstance(target, int) or target <= 0:
                continue
            refills.append({
                "type": "dnd2024.class_resource.refilled",
                "actor_id": _player_actor(uid),
                "resource_id": "bardic_inspiration",
                "target": target,
            })
        # 战斗开始的第一回合也是"自己的回合开始"：首行动者与后续回合共用
        # 同一个 turn-start 辅助，不能因为批次里没有 turn.advanced 就跳过。
        return [{
            "type": "dnd2024.combat.started", "round": 1, "initiative": order,
            "initiative_rolls": initiatives, "enemies": enemies, "positions": positions,
            "economy": self._fresh_economy(first), "position_mode": "theater",
            "reactions": {actor_id: 1 for actor_id in order},
            "encounter_instance_id": str(intent.get("encounter_instance_id") or ""),
            "encounter_preset_id": str(intent.get("encounter_preset_id") or ""),
            "origin_step_id": str(intent.get("origin_step_id") or ""),
            "mode": str(intent.get("mode") or "sandbox"),
            "adventure_binding": deepcopy(intent.get("adventure_binding")),
        }, *refills, *self._turn_start_regen_events(first)]

    def _initiative_roll(
        self, character: dict[str, Any], modifier: int, rng: Any,
    ) -> dict[str, Any]:
        """One Initiative roll, with every declared Advantage source recorded.

        Initiative is a Dexterity ability check, so Advantage applies exactly as
        it does to any other d20 test: two dice and keep the higher.  Multiple
        sources never add dice -- ``mode_from_sources`` collapses them into one
        mode and the event lists every source, so "Remarkable Athlete and
        Invisible" is still two d20s, not three.

        Advantage on Initiative is read from two places, and both are
        declarations rather than code: a class feature's
        ``initiative_advantage`` flag, and a condition's ``initiative_advantage``
        entry in the combat catalog (the Invisible condition grants it).
        """

        sources: list[dict[str, Any]] = [
            {**row, "mode": "advantage"}
            for row in self.features.initiative_advantage_sources(character)
        ]
        conditions = character.get("conditions")
        conditions = conditions if isinstance(conditions, dict) else {}
        for condition_id, entry in (self.catalog.conditions or {}).items():
            if condition_id not in conditions:
                continue
            if entry.get("initiative_advantage") is True:
                sources.append({"code": condition_id, "mode": "advantage"})
        mode = mode_from_sources(sources)
        rolls = [int(rng.randint(1, 20))]
        if mode:
            rolls.append(int(rng.randint(1, 20)))
        natural = rolls[0]
        if mode == "advantage":
            natural = max(rolls)
        elif mode == "disadvantage":
            natural = min(rolls)
        payload: dict[str, Any] = {
            "roll": natural, "total": natural + int(modifier),
        }
        if mode:
            payload["rolls"] = rolls
            payload["advantage_mode"] = mode
            payload["advantage_sources"] = sources
        return payload

    def _skill_check_floor(
        self, actor: dict[str, Any], skill_id: str,
    ) -> tuple[int, list[dict[str, str]]]:
        """The d20 floor for one skill check, gated on real proficiency.

        Reliable Talent applies only to a check that "uses one of your skill or
        tool proficiencies".  The gate is read from the actor's ``skill_refs``,
        not from the derived number: ``+2`` cannot say whether it came from
        proficiency or from the ability modifier, and a floor on an untrained
        skill would silently turn every rogue into a specialist.
        """

        refs = {str(ref) for ref in actor.get("skill_refs") or []}
        if f"skill:{skill_id}" not in refs:
            return 0, []
        return self.features.ability_check_floor(self._feature_character(actor))

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

    def _saving_throw_sources(
        self, actor: dict[str, Any], ability: str,
    ) -> list[dict[str, Any]]:
        """Every declared Advantage/Disadvantage source on one saving throw.

        Rage's Strength save and untrained armor are code facts of the combat
        authority; Danger Sense is read from its own declaration so a second
        ability would never need a new branch.  The aggregation stays in one
        place: two sources of Advantage still roll two dice, and Advantage plus
        Disadvantage collapses to a straight roll.
        """

        conditions = actor.get("conditions") or {}
        flags = condition_flags(self.catalog.conditions, conditions)
        sources: list[dict[str, Any]] = []
        if "rage" in conditions and ability == "str":
            sources.append({"code": "rage", "mode": "advantage"})
        if bool(
            (actor.get("armor_training") or {}).get("untrained_armor_worn")
        ) and ability in {"str", "dex"}:
            sources.append({"code": "untrained_armor", "mode": "disadvantage"})
        if not flags["incapacitated"]:
            character = self._feature_character(actor)
            declared = self.features.feature_value(
                character, "danger_sense", "save_advantage_abilities", [],
            )
            if isinstance(declared, list) and ability in {str(item) for item in declared}:
                sources.append({"code": "danger_sense", "mode": "advantage"})
        return sources

    def _evasion_applies(self, target: dict[str, Any], ability: str) -> bool:
        """Whether Evasion rewrites this effect's damage branches.

        Evasion only matches the effect the declaration names -- a Dexterity
        save that already allows half damage on a success.  It is not a global
        halving of Dexterity saves, and an Incapacitated holder gets nothing.
        """

        if target.get("kind") not in {"player", "companion"}:
            return False
        flags = condition_flags(self.catalog.conditions, target.get("conditions") or {})
        if flags["incapacitated"]:
            return False
        character = self._feature_character(target)
        declared = self.features.feature_value(
            character, "evasion", "half_on_success_save", "",
        )
        return str(declared) == ability

    def _relentless_rage_marker(
        self, instance: Any, target: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The state a damage event needs so the reducer can offer Relentless Rage.

        The declaration answers ``what`` (DC ladder, ability, HP formula); the
        target's own feature state answers ``which DC``.  Nothing is rolled and
        no decision is opened here: only a damage batch that would actually
        reach 0 HP (temp HP already absorbed) may open the window, and that is
        the reducer's boundary.
        """

        if target.get("kind") not in {"player", "companion"}:
            return None
        if "rage" not in (target.get("conditions") or {}):
            return None
        character = self._feature_character(target)
        if not self.features.has_feature(character, "relentless_rage"):
            return None
        base_dc = self.features.feature_value(
            character, "relentless_rage", "base_dc", 10,
        )
        step = self.features.feature_value(
            character, "relentless_rage", "dc_step", 5,
        )
        multiplier = self.features.feature_value(
            character, "relentless_rage", "hp_multiplier", 2,
        )
        ability = str(self.features.feature_value(
            character, "relentless_rage", "save_ability", "con",
        ) or "con")
        stored = (character.get("features") or {}).get("relentless_rage")
        stored_dc = stored.get("next_dc") if isinstance(stored, dict) else None
        dc = int(base_dc)
        if isinstance(stored_dc, int) and not isinstance(stored_dc, bool):
            dc = stored_dc
        class_level = 0
        rows = (character.get("build") or {}).get("class_levels") or []
        for row in rows:
            if isinstance(row, dict) and str(row.get("class_ref")) == "class:barbarian":
                raw_level = row.get("level")
                if isinstance(raw_level, int) and not isinstance(raw_level, bool):
                    class_level = raw_level
                break
        if class_level <= 0:
            return None
        kind, raw_id = _actor_kind(str(target.get("actor_id") or ""))
        human = kind == "player" and not is_ai_controlled(instance, raw_id)
        return {
            "dc": dc,
            "dc_step": int(step) if isinstance(step, int) else 5,
            "hp_amount": int(multiplier) * class_level,
            "save_ability": ability,
            "assigned_to": raw_id if human else str(getattr(instance, "gm_uid", "") or ""),
        }

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
        if self._fiendish_resilience_type(target) == damage_type:
            return amount // 2, "fiendish_resilience"
        return amount, ""

    def _fiendish_resilience_type(self, target: dict[str, Any]) -> str:
        """The damage type this character re-chose at its last rest, or ``""``."""

        if target.get("kind") not in {"player", "companion"}:
            return ""
        character = self._feature_character(target)
        stored = (character.get("features") or {}).get("fiendish_resilience")
        if not isinstance(stored, dict):
            return ""
        return str(stored.get("damage_type") or "")

    def _attack_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
        *, spend_attack_action: bool = True,
        actor_view: dict[str, Any] | None = None,
        target_view: dict[str, Any] | None = None,
        granted_by: str = "",
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
            if intent.get("two_handed"):
                # 两用武器双手持用换用更大的骰式；validation 已保证只有声明
                # damage_two_handed 的武器才允许声明该意图。
                damage_formula = str(weapon.get("damage_two_handed") or damage_formula)
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
            combat=combat,
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
            self.catalog.conditions,
            self._effective_conditions(instance, combat, actor), "attack", rng,
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
        if crit_on_hit:
            # 这是"命中之后"才升级的重击，不是 d20 本身掷出的重击。标记出来，
            # 掷后窗口才知道扣减总数可以把它退回普通命中甚至失手；不标记的话
            # ``critical`` 会被当成必中，Cutting Words 就永远改不成 miss。
            check_event["critical_upgrade"] = True
        rage_sustain = bool(
            target["kind"] == "enemy" and "rage" in actor["conditions"]
            and str((combat.get("economy") or {}).get("actor_id") or "") == str(actor_id)
        )
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
            events.extend(self._hit_settlement_events(
                instance, combat, actor, target, weapon, check_event, intent, rng,
                ability=ability, modifier=modifier,
                damage_formula=damage_formula, damage_type=damage_type,
                crit_on_hit=crit_on_hit, granted_by=granted_by,
            ))
        else:
            events.extend(self._miss_mastery_events(
                instance, combat, actor, target, weapon, modifier, damage_type, rng,
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
        relentless = self._relentless_rage_marker(instance, target)
        if relentless:
            damage_event["relentless"] = relentless
        if sneak_dice:
            # 延迟提交：只有这次命中伤害最终落地才记一次偷袭使用。
            # Shield 改判落空时不会走到提交辅助方法，因此不会记账。
            damage_event["feature_use"] = {
                "actor_id": actor_id,
                "feature_id": "sneak_attack",
                "round": int(combat.get("round", 0)),
                "turn_index": int(combat.get("turn_index", 0)),
            }
        riders = self._hit_rider_declarations(actor, weapon, intent, combat)
        if riders:
            # 声明先挂在伤害事件上，掷骰留到真正提交时：Shield/Uncanny Dodge
            # 把命中改判落空时，不给这些骰子白掷一次的机会。
            damage_event["hit_riders"] = riders
        conditions = self._hit_condition_declarations(
            actor, weapon, intent, combat, granted_by,
        )
        if conditions:
            damage_event["hit_condition_effects"] = conditions
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

    def _hit_rider_declarations(
        self, actor: dict[str, Any], weapon: dict[str, Any],
        intent: dict[str, Any], combat: dict[str, Any],
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
        declarations: list[dict[str, Any]] = []

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
        return declarations

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

    def _potent_spellcasting_owner(
        self, actor: dict[str, Any], spell: dict[str, Any],
    ) -> str:
        """Which class's Potent Spellcasting option covers this spell, if any.

        The id is declared twice -- ``class:cleric`` and ``class:druid`` -- with
        different ``cantrip_source_class`` values, so ownership has to be
        resolved per declaration: a Cleric's option never boosts a Druid cantrip
        even though both are called Potent Spellcasting, and a character holding
        both options must not have one silently answer for the other.
        """

        if int(spell.get("level", 0) or 0) != 0:
            return ""
        refs = spell.get("class_refs") or []
        character = self._feature_character(actor)
        for owner in POTENT_SPELLCASTING_OWNERS:
            if not self.features.has_feature(
                character, "potent_spellcasting", owner=owner,
            ):
                continue
            source = str(self.features.feature_value(
                character, "potent_spellcasting", "cantrip_source_class", "",
                owner=owner,
            ) or "")
            if source and source in refs:
                return owner
        return ""

    def _potent_spellcasting_bonus(
        self, actor: dict[str, Any], spell: dict[str, Any],
    ) -> int:
        """The Wisdom modifier a Potent Spellcasting option adds to this cantrip."""

        owner = self._potent_spellcasting_owner(actor, spell)
        if not owner:
            return 0
        character = self._feature_character(actor)
        ability = str(
            self.features.feature_value(
                character, "potent_spellcasting", "ability", "", owner=owner,
            ) or ""
        )
        if not ability:
            return 0
        return ability_modifier(actor["abilities"].get(ability, 10))

    def _spell_effective_range(
        self, actor: dict[str, Any], spell: dict[str, Any], effect: dict[str, Any],
    ) -> int:
        """``effect["range"]`` after the Druid 15 Elemental Fury upgrade.

        Only the Druid side grows: Improved Elemental Fury extends Druid cantrips
        whose base range is already 10 feet or more by 300 feet, so a Touch or
        Self cantrip keeps its range.  The upgrade is a property of the option
        the character chose, not of the class row, hence the same owner-scoped
        question Potent Spellcasting damage asks.
        """

        base = int(effect["range"])
        if self._potent_spellcasting_owner(actor, spell) != "class:druid":
            return base
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "improved_elemental_fury"):
            return base
        bonus = int(self.features.feature_value(
            character, "improved_elemental_fury", "range_bonus_ft", 0,
        ) or 0)
        minimum = int(self.features.feature_value(
            character, "improved_elemental_fury", "range_minimum_ft", 0,
        ) or 0)
        if not bonus or base < minimum:
            return base
        return base + bonus

    def _add_potent_spellcasting(
        self, actor: dict[str, Any], spell: dict[str, Any], node: dict[str, Any],
    ) -> dict[str, Any]:
        """Add the flat Wisdom bonus as a constant, so crits never double it."""

        bonus = self._potent_spellcasting_bonus(actor, spell)
        if not bonus:
            return node
        return {
            "op": "add",
            "args": [node, {"op": "constant", "value": bonus}],
        }

    def _cantrip_temp_hp_events(
        self, actor: dict[str, Any], actor_id: str, spell: dict[str, Any],
        effect: dict[str, Any], intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Cleric 14's Improved Blessed Strikes: temp HP after a damaging cantrip.

        The wording gates on the spell ("when you cast a Cleric cantrip that
        deals damage"), not on the outcome, so a target that saved against the
        cantrip still enables the grant.  The amount is twice the Wisdom
        modifier and lands once per casting, on the caster or on one creature
        within 60 feet -- ``_validate_spell`` has already proved both.
        """

        target_id = str(intent.get("cantrip_temp_hp_target_id") or "")
        if not target_id:
            return []
        if int(spell.get("level", 0) or 0) != 0 or not effect.get("damage"):
            return []
        if self._potent_spellcasting_owner(actor, spell) != "class:cleric":
            return []
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "improved_blessed_strikes"):
            return []
        multiplier = int(self.features.feature_value(
            character, "improved_blessed_strikes", "temp_hp_ability_multiplier", 0,
        ) or 0)
        ability = str(self.features.feature_value(
            character, "potent_spellcasting", "ability", "", owner="class:cleric",
        ) or "")
        if not multiplier or not ability:
            return []
        amount = multiplier * ability_modifier(actor["abilities"].get(ability, 10))
        if amount <= 0:
            # 负的 Wisdom 修正不会产生"负临时 HP"：不授予，而不是扣血。
            return []
        return [{
            "type": "resource.changed", "resource": "temp_hp",
            "target_id": target_id, "delta": amount, "amount": amount,
            "source_actor_id": actor_id, "rolls": [],
            "granted_by_feature": "improved_blessed_strikes",
        }]

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
            node = attack_damage_node(
                damage_formula=str(row["dice"]), modifier=0,
                critical=critical, extra_dice_formulas=[],
            )
            amount, rolls = roll_damage_node(node, rng)
            amount = max(0, amount)
            if amount <= 0:
                continue
            damage_type = str(row.get("damage_type") or "")
            damage, resistance = self._resist_damage(target, amount, damage_type)
            event: dict[str, Any] = {
                "type": "resource.changed", "resource": "hp",
                "target_id": str(target["actor_id"]),
                "delta": -damage, "amount": damage, "damage_type": damage_type,
                "critical": critical, "rolls": rolls,
                "feature_id": str(row.get("feature_id") or ""),
                "owner_ref": str(row.get("owner") or ""),
            }
            if resistance:
                event["resisted"] = True
                event["resistance"] = resistance
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

    def _hit_condition_declarations(
        self, actor: dict[str, Any], weapon: dict[str, Any],
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

        if not intent.get("stunning_strike"):
            return declarations
        if not self.features.has_feature(character, "stunning_strike"):
            return declarations
        actor_id = str(actor["actor_id"])
        if self._feature_used_this_turn(combat, actor_id, "stunning_strike"):
            return declarations
        unarmed = bool(weapon.get("unarmed"))
        # Martial Arts 已经把"力量或敏捷"投到它覆盖的武器上（徒手与 Monk
        # weapon）。那个投影就是判定依据，不再按武器名重猜一次。
        monk_weapon = bool(weapon.get("ability_choice"))
        if not (unarmed or monk_weapon):
            # 只有 Monk weapon 与 Unarmed Strike 能触发；普通武器命中不算。
            return declarations

        save_ability = str(parameter("stunning_strike", "save_ability") or "")
        on_fail = str(parameter("stunning_strike", "on_fail_condition") or "")
        on_success = str(parameter("stunning_strike", "on_success_condition") or "")
        duration = str(parameter("stunning_strike", "condition_duration") or "")
        if not (save_ability and on_fail and on_success and duration):
            return declarations
        declarations.append({
            "kind": "stunning_strike",
            "feature_id": "stunning_strike",
            "save_ability": save_ability,
            "focus_cost": int(parameter("stunning_strike", "focus_cost") or 1),
            "on_fail_condition": on_fail,
            "on_success_condition": on_success,
            "on_success_attack_condition": str(
                parameter("stunning_strike", "on_success_attack_condition") or ""
            ),
            "duration": duration,
        })
        return declarations

    def _hit_condition_events(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], damage_event: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Resolve the declared hit conditions after their damage landed."""

        declarations = damage_event.get("hit_condition_effects")
        if not isinstance(declarations, list) or not declarations:
            return []
        attacker = self._actor_view(instance, combat, attacker_id)
        # Monk feature save DC: 8 + Proficiency Bonus + Wisdom modifier.  It is
        # not the spell save DC (a Monk may have none) and never a number the
        # client supplies.
        dc = (
            8 + int(attacker.get("proficiency_bonus", 0) or 0)
            + ability_modifier(attacker["abilities"].get("wis", 10))
        )
        events: list[dict[str, Any]] = []
        for row in declarations:
            if not isinstance(row, dict):
                continue
            if row.get("kind") == "open_hand":
                events.extend(self._open_hand_events(
                    instance, combat, attacker_id, target, row, dc, rng,
                ))
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
            events.append(check)
            succeeded = check["success"]
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
        events.append(check)
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

        order = [str(item) for item in combat.get("initiative") or []]
        index = combat.get("turn_index", -1)
        if not (0 <= index < len(order) and order[index] == actor_id):
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
        }]

    def _resolve_committed_hit(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], damage_event: dict[str, Any], damage: int, rng: Any,
    ) -> list[dict[str, Any]]:
        """Submit a final hit's damage and run its trailing consequences."""

        rider_events, rider_damage = self._hit_rider_events(
            combat, target, damage_event, rng, attacker_id=attacker_id,
        )
        events = list(self._submit_hit_damage(damage_event))
        events.extend(rider_events)
        if damage_event.get("critical"):
            events.extend(
                self._critical_hit_move_events(instance, combat, attacker_id)
            )
        # 一次命中只有一个专注 DC：额外伤害实例与主伤害合并计算，而不是每个
        # 实例各掷一次豁免。
        total_damage = damage + rider_damage
        events.extend(
            self._concentration_with_dool(instance, combat, target, total_damage, rng)
        )
        events.extend(self._hit_condition_events(
            instance, combat, attacker_id, target, damage_event, rng,
        ))
        events.extend(self._enemy_zero_hp_feature_events(
            instance, combat, attacker_id, target, total_damage,
        ))
        if self._last_hostile_defeated(combat, target, total_damage):
            events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        return events

    def _maybe_overchannel(self, intent: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
        """门控已由 _validate_spell 完成（fail closed）；此处只做变换。"""

        if not intent.get("overchannel"):
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
        # 一次施法只有一个启用状态：所有目标、所有射线共享它。
        overchannel = bool(intent.get("overchannel"))
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
        cast_event = {
            "type": "dnd2024.spell.cast", "actor_id": actor_id, "spell_ref": spell_ref,
            "slot_level": slot_level, "target_ids": target_ids,
        }
        if overchannel:
            cast_event["overchannel"] = True
        events.append(cast_event)
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
                    extra_sources=extra_sources, combat=combat,
                )
                rolls = [int(rng.randint(1, 20))]
                if check_mode["mode"]:
                    rolls.append(int(rng.randint(1, 20)))
                roll = max(rolls) if check_mode["mode"] == "advantage" else min(rolls)
                bonus = int(actor["spell_attack_bonus"])
                check_bonus_rolls = condition_check_bonus_rolls(
                    self.catalog.conditions,
                    self._effective_conditions(instance, combat, visible_actor),
                    "attack", rng,
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
                    # Potent Spellcasting 是加值不是骰：先翻倍骰数再追加，
                    # 否则重击会把这个固定加值也一起翻倍。
                    damage_node = self._add_potent_spellcasting(actor, spell, damage_node)
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
                    if overchannel:
                        damage_event["overchannel"] = True
                    if resistance:
                        damage_event["resisted"] = True
                        damage_event["resistance"] = resistance
                    relentless = self._relentless_rage_marker(instance, target)
                    if relentless:
                        damage_event["relentless"] = relentless
                    events.append(damage_event)
                    events.extend(self._concentration_events(instance, combat, target, damage, rng))
                    dealt[target_id] = dealt.get(target_id, 0) + damage
                    total_dealt = dealt[target_id]
                    if (
                        target["kind"] == "enemy"
                        and target["hp"] - total_dealt <= 0
                        and target_id not in defeated_targets
                    ):
                        # 只有首次跨过 0 HP 的那一束触发；同批次后续射线照旧
                        # 产生伤害事件，但不再授予第二次临时 HP。
                        defeated_targets.add(target_id)
                        events.extend(self._enemy_zero_hp_feature_events(
                            instance, combat, actor_id, target, total_dealt,
                        ))
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
                check_event = self._saving_throw_check(
                    instance, combat, target, target_id, save_ability,
                    int(actor["spell_save_dc"]), actor_id, events, rng,
                )
                succeeded = check_event["success"]
                save_check_index = len(events)
                events.append(check_event)
                if effect.get("damage"):
                    damage_node = spell_formula_node(
                        str(effect["damage"]), effect.get("upcast_damage"),
                        spell, slot_level, actor,
                    )
                    # 加值先并入骰式，再走豁免减半与抗性：三者都只作用于
                    # 同一个总数，顺序不能倒。
                    damage_node = self._add_potent_spellcasting(actor, spell, damage_node)
                    damage_node = self._maybe_overchannel(intent, damage_node)
                    damage, rolls = roll_damage_node(damage_node, rng)
                    half_on_success = bool(effect.get("half_on_success"))
                    if half_on_success and self._evasion_applies(target, save_ability):
                        # Evasion 改写的是"这条允许敏捷豁免半伤"的效果分支，不是
                        # 所有敏捷豁免：成功 0、失败 floor(amount/2)，之后抗性
                        # 仍只应用一次（顺序固定，不能重复减半）。
                        damage = 0 if succeeded else damage // 2
                    elif succeeded:
                        damage = damage // 2 if half_on_success else 0
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
                        if overchannel:
                            damage_event["overchannel"] = True
                        if resistance:
                            damage_event["resisted"] = True
                            damage_event["resistance"] = resistance
                        relentless = self._relentless_rage_marker(instance, target)
                        if relentless:
                            damage_event["relentless"] = relentless
                        events.append(damage_event)
                        events.extend(self._concentration_events(instance, combat, target, damage, rng))
                        dealt[target_id] = dealt.get(target_id, 0) + damage
                        total_dealt = dealt[target_id]
                        if (
                            target["kind"] == "enemy"
                            and target["hp"] - total_dealt <= 0
                            and target_id not in defeated_targets
                        ):
                            defeated_targets.add(target_id)
                            events.extend(self._enemy_zero_hp_feature_events(
                                instance, combat, actor_id, target, total_dealt,
                            ))
            elif mode == "automatic_damage":
                damage_node = spell_formula_node(
                    str(effect["damage"]), effect.get("upcast_damage"),
                    spell, slot_level, actor,
                )
                damage_node = self._add_potent_spellcasting(actor, spell, damage_node)
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
                if overchannel:
                    damage_event["overchannel"] = True
                if resistance:
                    damage_event["resisted"] = True
                    damage_event["resistance"] = resistance
                relentless = self._relentless_rage_marker(instance, target)
                if relentless:
                    damage_event["relentless"] = relentless
                events.append(damage_event)
                events.extend(self._concentration_events(instance, combat, target, damage, rng))
                dealt[target_id] = dealt.get(target_id, 0) + damage
                total_dealt = dealt[target_id]
                if (
                    target["kind"] == "enemy"
                    and target["hp"] - total_dealt <= 0
                    and target_id not in defeated_targets
                ):
                    defeated_targets.add(target_id)
                    events.extend(self._enemy_zero_hp_feature_events(
                        instance, combat, actor_id, target, total_dealt,
                    ))
                succeeded = True
            elif mode == "healing":
                healing_node = spell_formula_node(
                    str(effect["healing"]), effect.get("upcast_healing"),
                    spell, slot_level, actor,
                )
                if effect.get("add_spell_ability"):
                    healing_node = heal_ability_modifier_node(healing_node, actor)
                # Owner-scoped on purpose: the short id alone would let any
                # future class that declares a same-named feature turn this
                # heal into a maximized one.
                maximized = self.features.has_feature(
                    self._feature_character(actor),
                    "supreme_healing",
                    owner="subclass:life_domain",
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
            # 条件免疫：命中则根本不产生 condition.applied，并把拦截来源写回
            # 本次豁免事件（没有 check event 时改用一条 message 留痕）。
            # 豁免本身仍然是失败——被免疫的是条件的落地，不是掷骰结果。
            # 两个字段名区分"站在谁的灵光里"与"我自己带着免疫"：玩家看到的
            # 拦截原因不能只有一句"没生效"。
            blocked: dict[str, str] | None = None
            blocked_field = ""
            if condition and condition_applies:
                blocker = self._condition_immunity_blocker(
                    instance, combat, target, condition,
                )
                if blocker is not None:
                    scope = str(blocker.get("scope") or "")
                    blocked = {
                        key: value for key, value in blocker.items() if key != "scope"
                    }
                    blocked_field = (
                        "blocked_by_aura" if scope == "aura" else "blocked_by_feature"
                    )
            if blocked:
                if save_check_index is not None:
                    events[save_check_index]["blocked_condition"] = condition
                    events[save_check_index][blocked_field] = blocked
                else:
                    # 免疫不依赖豁免：buff/debuff/自动伤害同样经过这里。这些
                    # 路径没有 check event 可挂，但被吞掉的条件必须留痕，否则
                    # 只能靠怀疑；用既有 message 类型，不新增事件类型。
                    events.append({
                        "type": "dnd2024.combat.message", "target_id": target_id,
                        "text": f"{condition} has no effect on this creature",
                        "condition": condition,
                        "blocked_condition": condition,
                        blocked_field: blocked,
                    })
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
        # 使用计数与自伤都落在法术效果之后、胜利判定之前：计数是这次施法的
        # 一部分，自伤是"施法后立即"的代价，二者都不该被战斗结束顺序吞掉。
        if overchannel:
            character = self._feature_character(actor)
            before = feature_usage_count(
                character, OVERCHANNEL_OWNER, OVERCHANNEL_FEATURE_ID,
                USAGE_SCOPE_LONG_REST,
            )
            use_index = before + 1
            events.append({
                "type": "dnd2024.feature_usage.changed",
                "actor_id": actor_id,
                "owner": OVERCHANNEL_OWNER,
                "feature_id": OVERCHANNEL_FEATURE_ID,
                "scope": USAGE_SCOPE_LONG_REST,
                "spell_ref": spell_ref,
                "slot_level": slot_level,
                "before_count": before,
                "after_count": use_index,
                "use_index": use_index,
            })
            if use_index >= 2:
                # 第 N 次（N ≥ 2）：每环阶掷 N 个 d12 黯蚀。声明式基数 2 加上
                # "每再用一次 +1d12"，与 SRD 原文的递增表述一一对应。
                base = int(self.features.feature_value(
                    character, OVERCHANNEL_FEATURE_ID, "initial_self_damage_dice", 2,
                    owner=OVERCHANNEL_OWNER,
                ) or 0)
                sides = int(self.features.feature_value(
                    character, OVERCHANNEL_FEATURE_ID, "self_damage_die", 12,
                    owner=OVERCHANNEL_OWNER,
                ) or 0)
                per_level = base + (use_index - 2)
                cost_node = formula_node_from_dice_formula(
                    f"{per_level * slot_level}d{sides}"
                )
                cost, cost_rolls = roll_damage_node(cost_node, rng)
                # 无视抗性与免疫：不走 _resist_damage；临时 HP 仍由 reducer 先吸收。
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": actor_id,
                    "delta": -cost, "amount": cost, "damage_type": "necrotic",
                    "overchannel": True, "overchannel_cost": True,
                    "ignore_resistance": True, "ignore_immunity": True,
                    "rolls": cost_rolls,
                })
                events.extend(self._concentration_events(instance, combat, actor, cost, rng))
        # 提升的祝福打击落在本次施法末：临时 HP 不是"命中后的效果"，它与
        # 目标是否真的掉血无关，所以不挂在任何单个目标的结算链上。
        events.extend(self._cantrip_temp_hp_events(actor, actor_id, spell, effect, intent))
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
        # Remarkable Athlete 的暴击后移动：这份额度自身免借机攻击，且只免
        # 这一次移动——阈值是额度（英尺），不是"永久免疫"。
        crit_budget = int(economy.get("crit_move", 0) or 0)
        covered_by_crit = bool(crit_budget) and abs(distance) <= crit_budget
        if adjacent_hostiles and not disengaged and not covered_by_crit:
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
            **({"crit_move": True} if covered_by_crit else {}),
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
        # Hit riders (Radiant/Divine/Primal strikes) are settled by
        # ``_hit_rider_events``: their dice are a separate damage instance, so
        # they can neither be merged into this event nor ride along on the wire.
        event.pop("hit_riders", None)
        event.pop("hit_condition_effects", None)
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
        # 下限在加值之前、在掷后窗口之前作用：被替换的是 d20 面值本身，
        # 所以它必须发生在 total 计算之前，并且不能再掷一次骰。
        floor, floor_sources = self._skill_check_floor(actor, "stealth")
        effective = max(natural, floor) if floor else natural
        total = effective + stealth
        success = total >= dc
        event: dict[str, Any] = {
            "type": "check.resolved", "kind": "hide", "actor_id": actor_id,
            "ability": "stealth", "rolls": rolls, "natural": effective,
            "modifier": stealth, "total": total, "target": dc, "success": success,
            "roll_id": self._roll_id(events, "hide", actor_id),
        }
        if floor:
            event["raw_d20"] = natural
            event["effective_d20"] = effective
            event["d20_floor"] = floor
            event["d20_floor_sources"] = floor_sources
        if mode:
            event["advantage_mode"] = mode
            event["advantage_sources"] = sources
        window = self._ability_check_window_offer(
            instance, combat, actor, event,
            {"kind": "hide", "intent_id": str(intent.get("intent_id") or "")},
        )
        if window is not None:
            # The check is on the table; the invisible condition (or the
            # bardic die) waits for the window owner.
            events.append({"type": "dnd2024.decision.pending", **window})
            return events
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
        # Reuse the actual reducer on an isolated aggregate, not a second
        # approximation of HP, positions, conditions or per-turn usage.
        staged = fork_domain_state(instance)
        simulated = 0
        for action in capability.actions:
            if action.kind == "unarmed_strike":
                for _ in range(action.count):
                    self._simulate_capability_events(staged, events[simulated:])
                    simulated = len(events)
                    current_combat = ruleset_runtime.state(staged)["combat"]
                    current_actor = self._actor_view(staged, current_combat, actor_id)
                    current_target = self._actor_view(staged, current_combat, target_id)
                    strike_intent = {
                        "actor_id": actor_id, "target_id": target_id,
                        "weapon_ref": UNARMED_STRIKE_REF,
                        **{key: intent[key] for key in (
                            "unarmed_damage_type", "rider_damage_type", "stunning_strike",
                        ) if key in intent},
                    }
                    # The choice applies to the first eligible hit, never twice
                    # in the same turn (a miss leaves it available).
                    if self._feature_used_this_turn(current_combat, actor_id, "stunning_strike"):
                        strike_intent.pop("stunning_strike", None)
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
                    strike_events = self._attack_events(
                        staged, current_combat, strike_intent, rng,
                        spend_attack_action=False,
                        granted_by=str(capability.feature_id),
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
                # 进入狂暴时"已有的条件直接结束"，与"之后的免疫"是两件事：
                # 免疫只拦新增，已经挂上的条目要真的删除并留下可审计事件。
                events.extend(
                    self._condition_gain_cleanup_events(instance, actor, "rage")
                )
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
            elif action.kind == "bardic_grant":
                events.extend(self._bardic_grant_events(instance, combat, intent, rng))
            elif action.kind == "inspiration_slot_exchange":
                events.extend(self._font_slot_exchange_events(instance, actor, intent))
            elif action.kind in {"font_slot_to_points", "font_create_slot"}:
                events.extend(self._font_of_magic_events(
                    instance, actor, intent, action.kind, capability.feature_id,
                ))
            else:  # pragma: no cover - catalog validation guards this
                raise CombatIntentError("unsupported class capability action")
        return events

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

    def _font_of_magic_events(
        self, instance: Any, actor: dict[str, Any], intent: dict[str, Any],
        kind: str, feature_id: str,
    ) -> list[dict[str, Any]]:
        """Sorcery Points <-> spell slots, both directions in one batch.

        The plans are recomputed here rather than trusted from validation: the
        two stages read the same authoritative sheet, and a settle-time refusal
        is what keeps a forged intent from moving points it never owned.  Only
        the *events* survive -- the reducer is the writer, so a copy of the
        sheet is enough to derive the balances the log records.
        """

        actor_id = str(actor["actor_id"])
        character = deepcopy(self._canonical_actor_character(instance, actor_id))
        try:
            if kind == "font_slot_to_points":
                plan = slot_to_points_plan(character, intent.get("slot_level"))
                events = apply_slot_to_points(character, plan)
            else:
                owner = self.features.feature_definition(str(feature_id))
                class_id = str(getattr(owner, "class_id", "") or "")
                costs = parse_created_slot_costs(self.features.feature_value(
                    character, feature_id, "created_slot_costs", (),
                ))
                plan = create_slot_plan(
                    character, intent.get("slot_level"), costs,
                    declaring_class_level(character, class_id),
                )
                events = apply_create_slot(character, plan)
        except FontOfMagicError as exc:
            raise CombatIntentError(str(exc)) from exc
        for event in events:
            event["actor_id"] = actor_id
        return events

    def _font_slot_exchange_events(
        self, instance: Any, actor: dict[str, Any], intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Spend one real spell slot to regain one Bardic Inspiration use.

        "No action required" means no economy is spent and the exchange does
        not count as casting a spell, so the slot event carries ``exchange``
        and the reducer leaves ``slot_spell_cast`` alone.  Both halves land in
        one batch: the slot only leaves if the use actually comes back, and
        the use can never exceed the Charisma-derived maximum.
        """

        actor_id = str(actor["actor_id"])
        character = self._canonical_actor_character(instance, actor_id)
        pool = dict(resource_state(character, "bardic_inspiration"))
        if not pool:
            raise CombatIntentError("Bardic Inspiration is not initialized")
        maximum = int(pool.get("maximum", 0) or 0)
        current = int(pool.get("current", 0) or 0)
        if current >= maximum:
            raise CombatIntentError("no expended Bardic Inspiration use to regain")
        slots = dict((character.get("spellcasting") or {}).get("class", {}).get("slots_current") or {})
        slot_level = intent.get("slot_level")
        if isinstance(slot_level, bool) or not isinstance(slot_level, int) or slot_level < 1:
            raise CombatIntentError("a spell slot level is required")
        if int(slots.get(str(slot_level), 0) or 0) < 1:
            raise CombatIntentError("the spell slot is already spent")
        return [
            {
                "type": "dnd2024.spell.slot_spent", "actor_id": actor_id,
                "slot_level": slot_level, "exchange": True,
            },
            {
                "type": "dnd2024.class_resource.refunded", "actor_id": actor_id,
                "resource_id": "bardic_inspiration", "amount": 1,
                "reason": "font_of_inspiration",
            },
        ]

    def _bardic_grant_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Grant one Bardic Inspiration die to a party creature.

        RAW re-checks at settle time what validation already checked: another
        creature within 60 feet that can see or hear the bard, not already
        holding an unexpired die.  The die face is fixed at grant time from
        the bard's progression track, and the hour lives on the authoritative
        world clock — no wall time, no background timer.  The die *instance*
        (not a second resource table) is the state of record; the use was
        already spent by the capability cost, so this handler only validates
        and records.
        """

        actor_id = str(intent["actor_id"])
        holder_id = str(intent.get("target_id") or "")
        if not holder_id:
            raise CombatIntentError("a Bardic Inspiration grant needs a target")
        if holder_id == actor_id:
            raise CombatIntentError("you cannot grant Bardic Inspiration to yourself")
        bard = self._actor_view(instance, combat, actor_id)
        holder = self._actor_view(instance, combat, holder_id)
        if holder.get("side") != bard.get("side"):
            raise CombatIntentError("Bardic Inspiration targets a creature on your side")
        if int(holder.get("hp", 0) or 0) <= 0:
            raise CombatIntentError("Bardic Inspiration targets a living creature")
        die_sides = self._bardic_die_sides(bard)
        if die_sides == 0:
            raise CombatIntentError("no Bardic Inspiration die is available at this level")
        range_ft = self.features.feature_value(
            self._feature_character(bard), "bardic_inspiration", "range_ft", 0,
        )
        if not isinstance(range_ft, int) or range_ft <= 0:
            raise CombatIntentError("no Bardic Inspiration range is declared")
        if self._distance(combat, actor_id, holder_id) > range_ft:
            raise CombatIntentError("the target is out of Bardic Inspiration range")
        # "can see or hear you": with the condition vocabulary the engine has,
        # the only pair that cuts both channels is an invisible bard seen by a
        # blinded holder; deafened is not modeled, so it never blocks.
        bard_conditions = bard.get("conditions") or {}
        holder_conditions = holder.get("conditions") or {}
        if "invisible" in bard_conditions and "blinded" in holder_conditions:
            raise CombatIntentError("the target can neither see nor hear you")
        if self._active_bardic_instance(instance, combat, holder_id) is not None:
            raise CombatIntentError("the target already holds a Bardic Inspiration die")
        granted_at = self._logical_minutes(instance)
        return [{
            "type": "dnd2024.bardic.granted", "actor_id": actor_id,
            "holder_id": holder_id,
            "die_instance_id": f"bardic:{intent.get('intent_id', '')}:{holder_id}",
            "die_sides": die_sides,
            "granted_at_minute": granted_at,
            "expires_at_minute": granted_at + 60,
            "source_feature": "bardic_inspiration",
        }]

    def _repeat_save_rows(
        self, actor: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        """The conditions that repeat a save at the end of this actor's turn."""

        rows: list[tuple[str, dict[str, Any]]] = []
        for condition_id, meta in (actor.get("conditions") or {}).items():
            if not isinstance(meta, dict):
                continue
            repeat = meta.get("save_repeat")
            if not isinstance(repeat, dict):
                continue
            if not str(repeat.get("ability") or "") or int(repeat.get("dc", 0) or 0) <= 0:
                continue
            rows.append((str(condition_id), deepcopy(meta)))
        return rows

    def _repeat_save_check(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        actor_id: str, meta: dict[str, Any], events: list[dict[str, Any]], rng: Any,
    ) -> dict[str, Any]:
        """Roll one repeated save, without deciding what it does."""

        repeat = meta.get("save_repeat") or {}
        check = self._saving_throw_check(
            instance, combat, actor, actor_id, str(repeat.get("ability") or ""),
            int(repeat.get("dc", 0) or 0), str(meta.get("source_actor_id") or ""), events, rng,
        )
        check["repeat_save"] = True
        return check

    def _saving_throw_check(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        actor_id: str, ability: str, dc: int, source_actor_id: str,
        events: list[dict[str, Any]], rng: Any,
    ) -> dict[str, Any]:
        """Shared save authority for spells, repeated saves and hit riders."""

        modifier = int(actor["saving_throws"].get(ability, 0) or 0)
        auto_fail = ability in condition_flags(
            self.catalog.conditions, actor["conditions"],
        )["auto_fail_saves"]
        if auto_fail:
            return {
                "type": "check.resolved", "kind": "saving_throw",
                "actor_id": actor_id,
                "source_actor_id": source_actor_id,
                "ability": ability, "rolls": [], "natural": 0,
                "modifier": modifier, "check_bonus": 0, "check_bonus_rolls": [],
                "aura_bonus": 0, "aura_sources": [],
                "total": 0,
                "target": dc, "success": False, "auto_failed": True,
                "roll_id": self._roll_id(events, "saving_throw", actor_id),
            }
        save_sources = self._saving_throw_sources(actor, ability)
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
        # 光环按本次回合末结算时的位置与失能状态求值，不用施放时快照；
        # 快照里只有 DC 和豁免属性，这两项不随移动变化。
        aura = self._saving_throw_aura_bonus(instance, combat, actor)
        total = roll + modifier + check_bonus + int(aura["bonus"])
        check_event = {
            "type": "check.resolved", "kind": "saving_throw",
            "actor_id": actor_id,
            "source_actor_id": source_actor_id,
            "ability": ability,
            "rolls": [value for value in (first, second) if value is not None],
            "natural": roll, "modifier": modifier,
            "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
            "aura_bonus": int(aura["bonus"]), "aura_sources": aura["sources"],
            "total": total, "target": dc, "success": total >= dc,
            "roll_id": self._roll_id(events, "saving_throw", actor_id),
        }
        if save_sources:
            check_event["advantage_mode"] = save_mode
            check_event["advantage_sources"] = save_sources
        return check_event

    @staticmethod
    def _roll_id(
        events: list[dict[str, Any]], kind: str, actor_id: str,
    ) -> str:
        """A stable id for one d20 roll inside this batch.

        The sequence is the number of checks already resolved in the batch, so
        the same replay produces the same id without any clock, counter in the
        state, or client input.  A reroll derives ``<old>-r1`` from it.
        """

        seq = sum(1 for event in events if event.get("type") == "check.resolved")
        return f"{kind}:{actor_id}:{seq}"

    def _save_repeat_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str, rng: Any,
        *, pending: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        """Repeat a condition's save at the end of the target's own turn.

        Only conditions whose metadata carries a ``save_repeat`` snapshot
        participate: the ability and DC were frozen when the spell landed. A
        success removes just this target's condition; it never ends the caster's
        concentration, so other targets of an upcast spell keep theirs.

        A window pauses this loop rather than the whole turn: the rows it has
        not reached yet travel in the settlement, and the window finisher
        resumes from exactly there.
        """

        actor = self._actor_view(instance, combat, actor_id)
        rows = list(pending) if pending is not None else self._repeat_save_rows(actor)
        events: list[dict[str, Any]] = []
        while rows:
            condition_id, meta = rows.pop(0)
            check = self._repeat_save_check(
                instance, combat, actor, actor_id, meta, events, rng,
            )
            window = self._save_window_offer(
                instance, combat, actor, check,
                {
                    "surface": "repeat_save", "condition": condition_id,
                    "remaining": [list(row) for row in rows],
                },
                "save", source=str(meta.get("source_actor_id") or actor_id),
                target_id=actor_id,
            )
            if window is not None:
                events.append({"type": "dnd2024.decision.pending", **window})
                return events
            events.append(check)
            if check["success"]:
                events.append({
                    "type": "condition.removed", "target_id": actor_id,
                    "condition": condition_id, "reason": "save_repeat",
                })
        return events

    def _condition_gain_cleanup_events(
        self, instance: Any, actor: dict[str, Any], gained: str,
    ) -> list[dict[str, Any]]:
        """Conditions that end because ``gained`` just started.

        Mindless Rage is two rules wearing one name: while raging you are
        immune, and entering the rage *ends* what you already had.  The second
        half cannot be expressed as immunity -- an immune creature never loses
        the entry, and the entry carries a duration, a source and a repeat save
        that must all go away with it.  Each declared condition gets its own
        removal event so the feed can show which one ended and why.
        """

        conditions = actor.get("conditions") or {}
        events: list[dict[str, Any]] = []
        for definition in self.features.active_features_for(
            self._feature_character(actor)
        ):
            if (
                definition.parameters.get("removes_conditions_on_gaining_condition")
                != gained
            ):
                continue
            declared = definition.parameters.get("removes_conditions_on_gaining")
            if not isinstance(declared, list):
                continue
            for code in declared:
                if not isinstance(code, str) or code not in conditions:
                    continue
                events.append({
                    "type": "condition.removed", "target_id": actor["actor_id"],
                    "condition": code, "reason": f"feature:{definition.id}",
                    "feature_id": definition.id,
                    "source_actor_id": actor["actor_id"],
                })
        return events

    def _self_restoration_removal_events(
        self, actor: dict[str, Any], intent: dict[str, Any] | None,
        already: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """At most one condition the actor chose to end at its own turn end.

        Validation has already proved the choice is legal; this repeats the
        three facts that matter (the feature exists, the condition is on the
        whitelist, the actor actually has it) because the reducer must never be
        the first place an illegal removal is noticed.  Missing intent or no
        choice means the feature simply is not used.
        """

        if not isinstance(intent, dict):
            return []
        requested = intent.get("self_restoration_condition")
        if not isinstance(requested, str) or not requested:
            return []
        chosen = self._self_restoration_choice(actor)
        if chosen is None:
            return []
        feature_id, allowed = chosen
        conditions = actor.get("conditions") or {}
        if requested not in allowed or requested not in conditions:
            return []
        target_id = str(actor["actor_id"])
        if any(
            event.get("type") == "condition.removed"
            and event.get("condition") == requested
            and event.get("target_id") == target_id
            for event in already
        ):
            return []
        return [{
            "type": "condition.removed", "target_id": target_id,
            "condition": requested, "reason": f"feature:{feature_id}",
            "feature_id": feature_id, "source_actor_id": target_id,
        }]

    def _end_turn_events(
        self, instance: Any, combat: dict[str, Any], rng: Any,
        intent: dict[str, Any] | None = None,
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
        # Self-Restoration 与重复豁免读的是同一个时刻：若那条条件已在本次
        # tick 里被豁免掉，选定移除就不再产生第二条事件。
        ender = self._actor_view(instance, combat, order[previous_index])
        events.extend(
            self._self_restoration_removal_events(ender, intent, events)
        )
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
        dead, stable, full or merely wounded actor never ticks.  Each owned
        declaration is evaluated on its own and emits its own event, so two
        future features never collapse into one unattributable number.  The
        amount is fixed by the declaration (``base + ability modifier``), never
        rolled.  ``cannot_regain_hp`` blocks the healing with a zero-delta
        event so the reducer stays purely mechanical.
        """

        if actor.get("kind") not in {"player", "companion"}:
            # Enemies carry no class features, and the start-of-combat view for
            # them is built from raw data without a character slice.
            return []
        hp = int(actor["hp"])
        maximum = int(actor.get("max_hp", 0) or 0)
        if hp < 1 or maximum <= 0 or hp > maximum // 2:
            return []
        character = self._feature_character(actor)
        # A condition whose duration ends at this very turn start is already
        # gone by the time the reducer applies ``turn.advanced``; reading the
        # pre-expiry view would block the heal one round too many.
        live_conditions = {
            condition_id: value
            for condition_id, value in (actor.get("conditions") or {}).items()
            if not (
                isinstance(value, dict)
                and value.get("duration") in {"actor_turn_start", "target_turn_start"}
            )
        }
        blocked = "cannot_regain_hp" in live_conditions
        events: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for definition in self.features.active_features_for(character):
            base = definition.parameters.get("turn_start_regen_base")
            ability = definition.parameters.get("turn_start_regen_ability")
            if base is None or not isinstance(ability, str):
                continue
            identity = (definition.owner, definition.id)
            if identity in seen:
                continue
            seen.add(identity)
            modifier = ability_modifier(
                int(character["abilities"].get(ability, 10) or 0)
            )
            amount = int(base) + modifier
            if amount <= 0:
                continue
            events.append({
                "type": "resource.changed", "resource": "hp",
                "target_id": actor["actor_id"],
                "delta": 0 if blocked else amount, "amount": 0 if blocked else amount,
                "healing": True, "rolls": [],
                "feature_id": definition.id, "owner": definition.owner,
                "trigger": "turn_start", "base": int(base),
                "ability": ability, "ability_modifier": modifier,
                **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
            })
        return events

    def _death_save_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        character = self._feature_character(actor)
        advantage_sources = list(
            self.features.feature_flag_source_rows(character, "death_save_advantage")
        )
        crit_threshold = int(self.features.min_feature_value(
            character, "death_save_crit_threshold", 20,
        ))
        # Lucky's advantage face is declared before the roll and consumed
        # unconditionally; validation proves the pool exists and has a point.
        spend_luck = bool(intent.get("spend_luck"))
        if spend_luck:
            # Lucky is a feat, not a class feature: it has no class owner to
            # report, and the pre-existing attack-roll provenance says the same.
            advantage_sources.append({"code": "lucky", "mode": "advantage"})
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if advantage_sources else None
        roll = max(first, second) if second is not None else first
        # A death saving throw is still a saving throw, so conditional d4s
        # (Bless) apply after the d20 faces and before the outcome is read.
        check_bonus_rolls = condition_check_bonus_rolls(
            self.catalog.conditions, actor["conditions"], "saving_throw", rng,
        )
        check_bonus = sum(row["roll"] for row in check_bonus_rolls)
        # Aura of Protection covers death saves too.  The bonus is itemised
        # rather than folded into ``modifier`` so the event still shows a bare
        # d20 plus separately-attributable sources.
        aura = self._saving_throw_aura_bonus(instance, combat, actor)
        modifier = (
            int(actor["proficiency_bonus"])
            if self.features.saving_throw_proficiency_all(character) else 0
        )
        total = roll + modifier + check_bonus + int(aura["bonus"])
        check_event = {
            "type": "check.resolved", "kind": "death_save", "actor_id": actor_id,
            "rolls": [value for value in (first, second) if value is not None],
            "natural": roll, "modifier": modifier,
            "check_bonus": check_bonus,
            "check_bonus_rolls": check_bonus_rolls, "bless_bonus": check_bonus,
            "aura_bonus": int(aura["bonus"]), "aura_sources": aura["sources"],
            "total": total,
            "target": 10,
            "success": roll != 1 and (roll >= crit_threshold or total >= 10),
            "roll_id": f"death_save:{actor_id}:1",
        }
        if advantage_sources:
            check_event["advantage_mode"] = "advantage"
            check_event["advantage_sources"] = [
                {**source, "mode": "advantage"} for source in advantage_sources
            ]
        if crit_threshold != 20:
            # Named for this save on purpose: ``crit_threshold`` already means
            # the attack crit range elsewhere in the same event shape.
            check_event["death_save_crit_threshold"] = crit_threshold
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
        crit_threshold = int(check.get("death_save_crit_threshold", 20) or 20)
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
                # §4.5.4 asks for the natural face, but
                # test_dark_ones_own_luck_adds_a_d10_to_a_failed_death_save
                # pins the window-boosted total here and sits outside this
                # slice's file whitelist.  Recorded as a deviation instead of
                # changing another slice's test.
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
        floor, floor_sources = self._skill_check_floor(actor, "medicine")
        effective = max(roll, floor) if floor else roll
        success = effective + medicine >= 10
        check: dict[str, Any] = {
            "type": "check.resolved", "kind": "medicine", "actor_id": actor["actor_id"],
            "target_id": target_id, "rolls": [roll], "natural": effective,
            "modifier": medicine, "total": effective + medicine, "target": 10,
            "success": success,
        }
        if floor:
            check["raw_d20"] = roll
            check["effective_d20"] = effective
            check["d20_floor"] = floor
            check["d20_floor_sources"] = floor_sources
        events = [{
            "type": "dnd2024.action.spent", "actor_id": actor["actor_id"],
            "resource": self._spend_action_resource(combat), "amount": 1,
        }, check]
        if success:
            events.append({
                "type": "condition.applied", "target_id": target_id,
                "condition": "stable", "duration": "until_healed",
                "source_actor_id": actor["actor_id"],
            })
        return events

    def _finish_relentless_rage(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Resolve the zero-HP survival window.

        Declining or failing submits the held damage, so the ordinary 0 HP
        consequences run exactly once.  A success replaces those consequences
        with ``hp = 2 x Barbarian level``; the Rage never ended, so nothing has
        to be restored afterwards and no healing trigger fires.  Every actual
        use -- success or failure -- raises the next DC; a decline does not.
        """

        actor_id = str(decision["actor_id"])
        damage_event = deepcopy(decision["damage_event"])
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        if intent["option"] != "use":
            events.append(damage_event)
            return events
        actor = self._actor_view(instance, combat, actor_id)
        ability = str(decision.get("save_ability") or "con")
        dc = int(decision["dc"])
        modifier = int(actor["saving_throws"].get(ability, 0) or 0)
        sources = self._saving_throw_sources(actor, ability)
        mode = mode_from_sources(sources)
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if mode else None
        if mode == "advantage":
            natural = max(first, second)
        elif mode == "disadvantage":
            natural = min(first, second)
        else:
            natural = first
        bonus_rolls = condition_check_bonus_rolls(
            self.catalog.conditions, actor["conditions"], "saving_throw", rng,
        )
        bonus = sum(row["roll"] for row in bonus_rolls)
        aura = self._saving_throw_aura_bonus(instance, combat, actor)
        total = natural + modifier + bonus + int(aura["bonus"])
        success = total >= dc
        check: dict[str, Any] = {
            "type": "check.resolved", "kind": "saving_throw", "actor_id": actor_id,
            "ability": ability,
            "rolls": [value for value in (first, second) if value is not None],
            "natural": natural, "modifier": modifier,
            "check_bonus": bonus, "check_bonus_rolls": bonus_rolls,
            "aura_bonus": int(aura["bonus"]), "aura_sources": aura["sources"],
            "total": total, "target": dc, "success": success,
            "feature_id": "relentless_rage", "trigger": "zero_hp",
        }
        if sources:
            check["advantage_mode"] = mode
            check["advantage_sources"] = sources
        events.append(check)
        # DC 递增写在 save 之后、结果之前：失败也是一次实际使用。
        events.append({
            "type": "dnd2024.feature_state.set", "actor_id": actor_id,
            "feature_id": "relentless_rage", "owner": "class:barbarian",
            "values": {"next_dc": dc + int(decision.get("dc_step") or 5)},
        })
        if not success:
            events.append(damage_event)
            return events
        maximum = int(actor.get("max_hp", 0) or 0)
        target_hp = int(decision.get("hp_amount", 0) or 0)
        if maximum > 0:
            target_hp = min(maximum, target_hp)
        events.append({
            "type": "resource.changed", "resource": "hp", "target_id": actor_id,
            "delta": target_hp - int(actor.get("hp", 0) or 0), "amount": 0,
            # ``healing`` 只用于拒绝临时 HP 吸收：这是"HP 改为某值"，不是伤害。
            "healing": True,
            "relentless_rage": True, "save_total": total, "dc": dc,
            "hp_after": target_hp,
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
        if decision.get("kind") == RELENTLESS_RAGE_DECISION:
            return self._finish_relentless_rage(instance, combat, decision, intent, rng)
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
