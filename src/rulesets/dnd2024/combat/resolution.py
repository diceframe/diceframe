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
from ..character.primitives import derived_save, derived_skill
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
    ABJURE_FOES_DURATION,
    INITIATIVE_CHOICES,
    PERSISTENT_RAGE_DURATION,
    RELENTLESS_RAGE_DECISION,
    SUPERIOR_DEFENSE_CONDITION,
    SUPERIOR_DEFENSE_DURATION,
    DIVINE_SENSE_CONDITION,
    DIVINE_SENSE_DURATION,
    SACRED_WEAPON_CONDITION,
    SACRED_WEAPON_DURATION,
    SMITE_OF_PROTECTION_CONDITION,
    SMITE_OF_PROTECTION_DURATION,
    TURN_UNDEAD_DURATION,
    TURN_UNDEAD_MAX_ROUNDS,
    CombatIntentError,
    RAGE_MAX_ROUNDS,
    UNARMED_STRIKE_REF,
    actor_kind as _actor_kind,
    attack_damage_parts,
    attack_riders,
    canonical as _canonical,
    combat_turn_slots,
    companion_actor as _companion_actor,
    current_turn_slot as _current_turn_slot,
    enemy_actor as _enemy_actor,
    normalize_attack_profile,
    player_actor as _player_actor,
    LEGENDARY_RESISTANCE_DEFAULT_PER_DAY,
    rider_attack_kind,
    rider_condition,
    size_at_most,
    stand_up_declaration,
    trait_ids,
    trait_params,
)
from .check_modes import (
    HUNTERS_MARK_REF,
    condition_check_bonus_rolls,
    condition_flags,
    effective_speed,
    marked_target_id,
    mode_from_sources,
    owned_mark,
)
from src.rulesets.dnd2024.features.resources import resource_state
from .check_windows import CHECK_WINDOW_DECISION, DAMAGE_WINDOW_DECISION
from .cover import cover_axis
from .reaction_spells import COUNTER_SPELL_DECISION, HIT_REACTION_DECISION, effective_armor_class
from .triggered_spells import HIT_SPELL_DECISION
from .weapon_masteries import WEAPON_MASTERY_EFFECTS, WEAPON_MASTERY_FEATURE_ID

# Potent Spellcasting is declared once per casting class with its own
# ``cantrip_source_class``; both declarations have to be asked separately.
POTENT_SPELLCASTING_OWNERS = ("class:cleric", "class:druid")
# Indomitable Might is a passive declaration on the character, but the check
# event has to name the feature that rewrote its total.  The id lives here so
# the audit trail and the catalog cannot drift apart.
INDOMITABLE_MIGHT = "indomitable_might"


def _proficiency_bonus_for_cr(cr: str) -> int:
    """The PB a stat block of this CR carries (SRD table, CR 0-30)."""

    text = str(cr or "0")
    if "/" in text:
        numerator, denominator = text.split("/")[:2]
        value = float(int(numerator) / int(denominator))
    else:
        value = float(text or 0)
    for ceiling, bonus in ((4, 2), (8, 3), (12, 4), (16, 5), (20, 6), (24, 7), (28, 8)):
        if value <= ceiling:
            return bonus
    return 9


def _statblock_derived_checks(
    raw: dict[str, Any], skill_abilities: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Effective saves and skills for a stat block.

    A stat block prints only the numbers that differ from the bare ability
    modifier.  Everything it does not print is still derivable, and the engine
    derives it: a monster's saving throw is computed like a player's, not read
    from whatever happened to be written down.

    Declared values win, but the derived ones stay beside them so an audit can
    tell a stat block typo from a rules difference.
    """

    abilities = {
        str(key): int(value) for key, value in (raw.get("abilities") or {}).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    declared_bonus = raw.get("proficiency_bonus")
    bonus = int(
        declared_bonus
        if isinstance(declared_bonus, int) and not isinstance(declared_bonus, bool)
        else _proficiency_bonus_for_cr(str(raw.get("cr") or ""))
    )
    declared_saves = {
        str(key): int(value) for key, value in (raw.get("saving_throws") or {}).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    derived_saves = {
        ability: derived_save(score, proficiency_bonus=bonus, proficient=False)
        for ability, score in abilities.items()
    }
    saves = {**derived_saves, **declared_saves}
    declared_skills = {
        str(key): int(value) for key, value in (raw.get("skills") or {}).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    # 决策 D-1：默认按六维算，面板印出的值优先覆盖。stat block 只印熟练项，
    # 所以派生侧**永不加 PB**——没印就是没熟练，加了会让所有怪物技能凭空变高。
    skills: dict[str, int] = {}
    for skill_id, ability in (skill_abilities or {}).items():
        if skill_id in declared_skills:
            skills[skill_id] = declared_skills[skill_id]
            continue
        score = abilities.get(ability)
        if score is not None:
            skills[skill_id] = derived_skill(
                score, proficiency_bonus=bonus, proficient=False,
            )
    # 未登记在技能目录里的声明值也要保留（目录可能落后于内容）。
    for skill_id, value in declared_skills.items():
        skills.setdefault(skill_id, value)
    peak = int(raw.get("passive_perception") or 0)
    if not peak:
        # 与 skill_values.perception 自洽：10 + 生效技能值，而不是只看声明值。
        peak = 10 + int(skills.get("perception", 0) or 0)
    return {
        "saving_throws": saves,
        "saving_throws_declared": declared_saves,
        "saving_throws_derived": derived_saves,
        "skills": skills,
        "skills_declared": declared_skills,
        "passive_perception": peak,
    }


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


def _with_climb_speed(
    speeds: dict[str, Any], traits: Any,
) -> dict[str, Any]:
    """S0-MM3d: Spider Climb means climbing costs no extra movement.

    The engine's only expression of that fact is a climb speed equal to the
    walking speed, so it is derived here — once, at load — instead of in every
    reader.  A stat block that prints its own climb speed keeps it.
    """

    if "spider_climb" not in trait_ids(traits):
        return speeds
    if not int(speeds.get("climb", 0) or 0):
        speeds["climb"] = int(speeds.get("walk", 30) or 30)
    return speeds


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
        # 两段伤害的期待值相加：The AI 用这个数决定值不值得花一个反应，
        # 少算第二段会让"火焰附加"的攻击看起来比实际弱。
        return sum(
            average_damage(str(part["dice"]), flat_bonus=flat_bonus if index == 0 else 0.0)
            for index, part in enumerate(attack_damage_parts(attack))
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
        dead_actor_ids: set[str] = set()
        for uid in sorted(instance.players):
            canonical = _canonical(instance.get_character_sheet(uid))
            actor_id = _player_actor(uid)
            if "dead" in (canonical.get("conditions") or {}):
                dead_actor_ids.add(actor_id)
            modifier = int(canonical.get("derived", {}).get("initiative", 0) or 0)
            initiatives.append({
                "actor_id": actor_id, "modifier": modifier, "kind": "player",
                **self._initiative_roll(canonical, modifier, rng),
            })
            positions[actor_id] = int(intent.get("player_positions", {}).get(uid, 0) or 0)
        for companion_id in sorted(companions):
            canonical = companions[companion_id].get("ruleset_character") or {}
            actor_id = _companion_actor(companion_id)
            if "dead" in (canonical.get("conditions") or {}):
                dead_actor_ids.add(actor_id)
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
                "attacks": [
                    normalize_attack_profile(item)
                    for item in raw["attacks"]
                ], "conditions": {},
                # S0-MM3b：动作容器原样落地。Multiattack 的 part 引用同一只
                # 生物的攻击表，因此它必须在同一个 dict 里，别处都读到。
                "actions": deepcopy(raw.get("actions") or []),
                # S0-MM3d：特质是事实声明。未知 id 已在装载期拒绝，所以这里
                # 原样落地即可——消费者各自来查，引擎不替它们改数值。
                "traits": deepcopy(raw.get("traits") or []),
                "creature_type": str(raw.get("creature_type") or ""),
                "counterspell_dc": int(raw.get("counterspell_dc", 0) or 0),
                # S0-MM1：stat block 的事实必须在开局时一次性落账，否则校验
                # 通过了、视图读不到——"看起来进去了，实际没生效"。
                "size": str(raw.get("size") or ""),
                "speeds": _with_climb_speed(
                    deepcopy(
                        raw.get("speeds") or {"walk": int(raw.get("speed", 30) or 30)}
                    ),
                    raw.get("traits"),
                ),
                "senses": deepcopy(raw.get("senses") or {}),
                "resist_damage_types": list(raw.get("resist_damage_types") or []),
                "immune_damage_types": list(raw.get("immune_damage_types") or []),
                "vulnerable_damage_types": list(
                    raw.get("vulnerable_damage_types") or []
                ),
                "condition_immunities": list(raw.get("condition_immunities") or []),
                "cr": str(raw.get("cr") or ""),
                "xp": int(raw.get("xp", 0) or 0),
                # S0-MM2：六维必须真的参与计算。声明值作为覆盖留在
                # ``*_declared`` 里，生效值写进 ``saving_throws`` / ``skills``，
                # 于是所有既有读取点（豁免、隐匿对抗、法术豁免）不必改。
                **_statblock_derived_checks(raw, self._skill_abilities()),
                "languages": list(raw.get("languages") or []),
                "subtype": str(raw.get("subtype") or ""),
                "hit_dice": str(raw.get("hit_dice") or ""),
                "proficiency_bonus": int(
                    raw.get("proficiency_bonus")
                    or _proficiency_bonus_for_cr(str(raw.get("cr") or ""))
                ),
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
        slots = self._turn_slots(instance, initiatives, enemies, companions)
        # Preserve the slots for explicit revival during the encounter.
        first_index = next(i for i, slot in enumerate(slots) if slot["actor_id"] not in dead_actor_ids)
        first = self._actor_view_from_data(instance, enemies, slots[first_index]["actor_id"], companions)
        # Superior Inspiration hooks the real Initiative roll, not a static
        # character value: every bard that rolled here tops up to two uses.
        refills: list[dict[str, Any]] = []
        for uid in sorted(instance.players):
            if _player_actor(uid) in dead_actor_ids:
                continue
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
        opening = self._initiative_opening_events(
            instance, enemies, companions, [actor_id for actor_id in order if actor_id not in dead_actor_ids],
            self._initiative_choices(intent), rng,
        )
        return [{
            "type": "dnd2024.combat.started", "round": 1, "initiative": order,
            "turn_slots": slots, "turn_index": first_index,
            "initiative_rolls": initiatives, "enemies": enemies, "positions": positions,
            "economy": self._fresh_economy(first), "position_mode": "theater",
            "reactions": {actor_id: 1 for actor_id in order},
            "encounter_instance_id": str(intent.get("encounter_instance_id") or ""),
            "encounter_preset_id": str(intent.get("encounter_preset_id") or ""),
            "origin_step_id": str(intent.get("origin_step_id") or ""),
            "mode": str(intent.get("mode") or "sandbox"),
            "adventure_binding": deepcopy(intent.get("adventure_binding")),
            # 骑乘只影响"骑枪是否需要双手"，因此它是遭遇级的最小声明：
            # 开局声明一次，战斗结束随状态丢弃。中途上下坐骑属于骑乘系统，
            # 不在本切片范围内。
            "mounted": {
                _player_actor(str(uid)): True
                for uid in (intent.get("mounted_player_ids") or [])
                if str(uid)
            },
        }, *refills, *opening, *self._turn_start_events(first, rng)]

    @staticmethod
    def _initiative_choices(intent: dict[str, Any]) -> dict[str, dict[str, bool]]:
        """The per-actor declarations made when Initiative was rolled.

        A declaration only says *whether* to use an optional feature.  Every
        amount, target and die comes from the server, so a client cannot
        declare "regain 40 Focus" or a made-up slot; anything that is not a
        known choice name is dropped here and rejected by validation first.
        """

        raw = intent.get("initiative_choices")
        if not isinstance(raw, dict):
            return {}
        choices: dict[str, dict[str, bool]] = {}
        for actor_id, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            chosen = {
                key: value for key, value in entry.items()
                if key in INITIATIVE_CHOICES and value is True
            }
            if chosen:
                choices[str(actor_id)] = chosen
        return choices

    def _initiative_opening_events(
        self, instance: Any, enemies: dict[str, dict[str, Any]],
        companions: dict[str, dict[str, Any]], order: list[str],
        choices: dict[str, dict[str, bool]], rng: Any,
    ) -> list[dict[str, Any]]:
        """Resolve every Initiative-time choice in one stable actor order.

        Uncanny Metabolism and Perfect Focus are resolved per actor, then
        Persistent Rage, so a monk can never both refill everything and take
        the Perfect Focus floor in the same Initiative roll.  Actors are read
        through their own view, which is what keeps a malformed declaration
        from touching another player's sheet.
        """

        events: list[dict[str, Any]] = []
        for actor_id in order:
            if _actor_kind(actor_id)[0] != "player":
                continue
            chosen = choices.get(actor_id) or {}
            view = self._actor_view_from_data(instance, enemies, actor_id, companions)
            character = self._feature_character(view)
            uncanny: list[dict[str, Any]] = []
            if chosen.get("uncanny_metabolism"):
                uncanny = self._uncanny_metabolism_events(view, character, rng)
            events.extend(uncanny)
            if not uncanny:
                events.extend(self._perfect_focus_events(view, character))
            if chosen.get("persistent_rage"):
                events.extend(self._persistent_rage_opening_events(view, character))
        return events

    def _uncanny_metabolism_events(
        self, view: dict[str, Any], character: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Uncanny Metabolism: refill Focus, heal ``level + Martial Arts die``.

        The Long Rest charge is the gate, not the current Focus: a monk at full
        Focus who declares the feature still spends the charge and rolls the
        die, because the rule lets them (and a free "no-op" would be a way to
        keep the charge forever).  ``cannot_regain_hp`` blocks only the Hit
        Points; the refill and the charge still happen.
        """

        if not self.features.has_feature(character, "uncanny_metabolism"):
            return []
        charge = resource_state(character, "uncanny_metabolism")
        if int(charge.get("current", 0) or 0) < 1:
            return []
        # "your Monk level" 读的是 monk 那一行职业等级，不是角色总等级：
        # 多职业角色的 class_state 会是 None，用它会把治疗量算成 0。
        level = declaring_class_level(character, "monk")
        die = str(self.features.unarmed_strike_die(character) or "")
        size = int(die.rsplit("d", 1)[1]) if "d" in die else 0
        roll = int(rng.randint(1, size)) if size > 1 else 0
        actor_id = str(view["actor_id"])
        focus = resource_state(character, "focus_points")
        events: list[dict[str, Any]] = []
        maximum = int(focus.get("maximum", 0) or 0)
        if maximum > 0:
            events.append({
                "type": "dnd2024.class_resource.refilled", "actor_id": actor_id,
                "resource_id": "focus_points", "target": maximum,
            })
        healing = level + roll
        blocked = "cannot_regain_hp" in (view.get("conditions") or {})
        events.append({
            "type": "resource.changed", "resource": "hp", "target_id": actor_id,
            "delta": 0 if blocked else healing, "amount": 0 if blocked else healing,
            "healing": True, "rolls": [roll] if size > 1 else [],
            "feature_id": "uncanny_metabolism", "martial_arts_die": die,
            "roll": roll, "monk_level": level,
            **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
        })
        events.append({
            "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
            "resource_id": "uncanny_metabolism", "amount": 1,
        })
        return events

    def _perfect_focus_events(
        self, view: dict[str, Any], character: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Perfect Focus: a floor of 4, never a gift and never a cost.

        Only reached when Uncanny Metabolism was not used, which is why this
        runs from the caller rather than deciding for itself.
        """

        if not self.features.has_feature(character, "perfect_focus"):
            return []
        floor = self.features.feature_value(character, "perfect_focus", "initiative_focus_floor", 0)
        if isinstance(floor, bool) or not isinstance(floor, int) or floor <= 0:
            return []
        focus = resource_state(character, "focus_points")
        current = int(focus.get("current", 0) or 0)
        maximum = int(focus.get("maximum", 0) or 0)
        if maximum <= 0 or current >= floor or current > 3:
            return []
        return [{
            "type": "dnd2024.class_resource.refilled", "actor_id": str(view["actor_id"]),
            "resource_id": "focus_points", "target": min(floor, maximum),
        }]

    def _persistent_rage_opening_events(
        self, view: dict[str, Any], character: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Persistent Rage: refill every expended Rage use, once per Long Rest."""

        if not self.features.has_feature(character, "persistent_rage"):
            return []
        charge = resource_state(character, "persistent_rage")
        if int(charge.get("current", 0) or 0) < 1:
            return []
        rages = resource_state(character, "rages")
        maximum = int(rages.get("maximum", 0) or 0)
        actor_id = str(view["actor_id"])
        events: list[dict[str, Any]] = []
        if maximum > 0:
            events.append({
                "type": "dnd2024.class_resource.refilled", "actor_id": actor_id,
                "resource_id": "rages", "target": maximum,
            })
        events.append({
            "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
            "resource_id": "persistent_rage", "amount": 1,
        })
        return events

    def _turn_slots(
        self, instance: Any, initiatives: list[dict[str, Any]],
        enemies: dict[str, dict[str, Any]],
        companions: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """The authoritative first-round schedule, one entry per turn.

        ``initiative`` stays a deduplicated actor list because every reader of
        it asks "who is in this combat".  A Thief's two turns are two slots of
        the same actor instead: same identity, same sheet, separate economy and
        separate turn-start.  Slot order uses the same tie-break as Initiative
        so a replay never reorders them.
        """

        kind_by_actor = {str(row["actor_id"]): str(row["kind"]) for row in initiatives}
        slots: list[dict[str, Any]] = [
            {
                "slot_id": f"{row['actor_id']}#1", "actor_id": str(row["actor_id"]),
                "initiative_total": int(row["total"]), "kind": "normal",
            }
            for row in initiatives
        ]
        for row in initiatives:
            actor_id = str(row["actor_id"])
            if kind_by_actor[actor_id] != "player":
                continue
            view = self._actor_view_from_data(instance, enemies, actor_id, companions)
            character = self._feature_character(view)
            offset = self.features.feature_value(
                character, "thiefs_reflexes", "first_round_extra_turn_offset", 0,
            )
            if isinstance(offset, bool) or not isinstance(offset, int) or offset <= 0:
                continue
            slots.append({
                "slot_id": f"{actor_id}#2", "actor_id": actor_id,
                "initiative_total": int(row["total"]) - offset,
                "kind": "thief_reflexes", "rounds": [1],
            })
        slots.sort(key=lambda slot: (
            -int(slot["initiative_total"]),
            kind_by_actor[str(slot["actor_id"])] != "player",
            str(slot["actor_id"]),
            0 if slot["kind"] == "normal" else 1,
        ))
        return slots

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

    def _legendary_resistance_events(
        self, actor: dict[str, Any], actor_id: str, succeeded: bool,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """S0-MM3d: turn a failed save into a success, at the cost of one use.

        The creature's own declaration decides whether the trait exists and how
        many uses a day it has; the uses are counted in the same ``usage`` ledger
        as recharge actions, so a save/load round trip keeps them.  A creature
        with no uses left simply fails: exhaustion must not be papered over.

        Known limitation (registered, not hidden): the trait is applied
        automatically.  "You can choose to succeed" is a decision, and there is
        no GM-facing decline channel for it yet — a window would have to be
        threaded through every place a save is rolled.
        """

        if succeeded:
            return {"used": False, "remaining": 0}
        traits = actor.get("traits")
        if "legendary_resistance" not in trait_ids(traits):
            return {"used": False, "remaining": 0}
        per_day = int(
            trait_params(traits, "legendary_resistance").get(
                "per_day", LEGENDARY_RESISTANCE_DEFAULT_PER_DAY,
            ) or LEGENDARY_RESISTANCE_DEFAULT_PER_DAY
        )
        used = int(((actor.get("usage") or {}).get("trait:legendary_resistance") or {})
                   .get("used", 0) or 0)
        if used >= per_day:
            return {"used": False, "remaining": 0}
        events.append({
            "type": "dnd2024.legendary_resistance.spent",
            "actor_id": actor_id,
            "per_day": per_day,
        })
        return {"used": True, "remaining": per_day - used - 1}

    def _saving_throw_sources(
        self, actor: dict[str, Any], ability: str, *, against_spell: bool = False,
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
        if "next_save_disadvantage" in conditions:
            # Staggering Blow. It is a marker rather than a registry declaration
            # because no registry field can say "the *next* saving throw, then
            # gone": the consumption point is the save itself, not a turn
            # boundary, so the same code that reads it also removes it.
            sources.append({"code": "next_save_disadvantage", "mode": "disadvantage"})
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

    def _strength_total_replacement(
        self, target: dict[str, Any], ability: str, total: int,
    ) -> tuple[int, dict[str, Any]]:
        """Indomitable Might: judge with the Strength score when it is higher.

        The rule replaces the *total*, not the die, so the d20 face and
        ``rolls`` stay exactly what was rolled and the event carries both
        numbers: ``ordinary_total`` is what the roll and every modifier
        produced, ``replacement_score`` is the score that may stand in for it.
        Nothing is rolled and no die is added, so the RNG order of the batch is
        unchanged.

        Only Strength checks and Strength saving throws reach this: every call
        site passes the ability it actually rolled, and anything else comes
        back untouched, so attacks, initiative, damage, Dex/Con saves and death
        saves keep their own rules.  An auto-failed save has no total to
        replace and never reaches here either.
        """

        if ability != "str":
            return total, {}
        if not self.features.strength_total_replacement(
            self._feature_character(target),
        ):
            return total, {}
        score = int((target.get("abilities") or {}).get("str", 10) or 10)
        if total >= score:
            return total, {
                "ordinary_total": total,
                "replacement_score": score,
                "total_replaced": False,
                "feature_id": INDOMITABLE_MIGHT,
            }
        return score, {
            "ordinary_total": total,
            "replacement_score": score,
            "total_replaced": True,
            "feature_id": INDOMITABLE_MIGHT,
        }

    def _consume_save_markers(
        self, target_id: str, target: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Remove the "next saving throw" markers a save just consumed.

        Called at every real saving-throw site with the actor who rolled.  The
        two Staggering halves are independent: this only ever removes the
        saving-throw half, so the Opportunity Attack the other half forbids
        still lasts until the attacker's next turn.
        """

        conditions = target.get("conditions") or {}
        if "next_save_disadvantage" not in conditions:
            return []
        return [{
            "type": "condition.removed", "target_id": str(target_id),
            "condition": "next_save_disadvantage", "reason": "save_resolved",
        }]

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
        use_grant: bool = True,
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

        if bool(intent.get("brutal_strike")) and self.features.has_feature(
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

    def _escape_events(
        self, combat: dict[str, Any], intent: dict[str, Any], actor: dict[str, Any],
        rng: Any,
    ) -> list[dict[str, Any]]:
        """S0-MM3e: one ability check, and the condition ends on a success.

        The requirement is read, not declared: the DC comes from the applied
        instance, the allowed abilities and the cost from the condition type.
        A failed attempt still spends the action — that is the rule, and paying
        only on success would make escaping free to retry forever.
        """

        actor_id = str(actor["actor_id"])
        condition_id = str(intent["condition"])
        carried = (actor.get("conditions") or {}).get(condition_id) or {}
        declared = (self.catalog.conditions.get(condition_id) or {}).get("escape") or {}
        chosen = str(intent.get("check") or "")
        rows = [
            row for row in declared.get("checks") or []
            if isinstance(row, dict) and str(row.get("ability")) == chosen
        ]
        skill = str(rows[0].get("skill") or "") if rows else ""
        bonus = (
            int((actor.get("skill_values") or {}).get(skill, 0) or 0)
            if skill else ability_modifier(int((actor.get("abilities") or {}).get(chosen, 10)))
        )
        dc = int(carried.get("escape_dc") or 0)
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.action.spent", "actor_id": actor_id,
            "resource": str(declared.get("cost") or "action"), "amount": 1,
        }]
        roll = int(rng.randint(1, 20))
        total = roll + bonus
        events.append({
            "type": "check.resolved", "kind": "escape", "actor_id": actor_id,
            "ability": chosen, "skill": skill, "rolls": [roll], "natural": roll,
            "modifier": bonus, "total": total, "target": dc,
            "success": total >= dc,
            "condition": condition_id,
            "source_actor_id": str(carried.get("source_actor_id") or ""),
            "roll_id": self._roll_id(events, "escape", actor_id),
        })
        if total >= dc:
            events.append({
                "type": "condition.removed", "target_id": actor_id,
                "condition": condition_id,
            })
        return events

    def _stand_up_events(
        self, actor: dict[str, Any], intent: dict[str, Any], *, speed: int | None = None,
    ) -> list[dict[str, Any]]:
        """S0-MM3e: standing up spends movement, and the position does not change.

        It is emitted as ``position.changed`` with a zero distance and a
        movement cost so the movement budget is charged by the same reducer
        branch every other movement uses — no second accounting path.
        """

        actor_id = str(actor["actor_id"])
        declared = stand_up_declaration(self.catalog.conditions, actor.get("conditions") or {})
        if declared is None:  # pragma: no cover - validation guards this
            raise CombatIntentError("the actor cannot stand up")
        cost = self.stand_up_cost(actor, declared, speed=speed)
        return [
            {
                "type": "dnd2024.position.changed", "actor_id": actor_id,
                "distance": 0, "movement_cost": cost, "forced": False,
                "reason": "stand_up",
            },
            {
                "type": "condition.removed", "target_id": actor_id,
                "condition": str(declared["condition"]), "reason": "stand_up",
            },
        ]

    def _save_action_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """S0-MM3c: one save, then one of two declared outcomes.

        The save is the target's, rolled by the server; the damage comes from
        the branch the result selects.  Each part of that branch is its own
        resisted instance, so a fire breath that also dealt bludgeoning would
        not be halved as a lump.  ``usage_limits`` is settled in the same batch
        as the effect: an action that was spent must read as spent even if the
        target saved.
        """

        actor_id = str(intent["actor_id"])
        target_id = str(intent["target_id"])
        actor = self._actor_view(instance, combat, actor_id)
        action = self._declared_action(actor, intent)
        if action is None:  # pragma: no cover - validation guards this
            raise CombatIntentError("save action profile is not available to the actor")
        target = self._actor_view(instance, combat, target_id)
        action_id = str(action.get("id") or "")
        events: list[dict[str, Any]] = [
            {
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": self._spend_action_resource(combat), "amount": 1,
            },
            {
                "type": "dnd2024.action.used", "actor_id": actor_id,
                "action_id": action_id,
                "usage_limits": deepcopy(action.get("usage_limits")),
            },
        ]
        check = self._saving_throw_check(
            instance, combat, target, target_id,
            str(action.get("ability") or ""), int(action.get("dc") or 0),
            actor_id, events, rng,
        )
        check["source_action"] = action_id
        events.append(check)
        branch = action.get("on_fail") if not check["success"] else action.get("on_success")
        for row in branch or []:
            if not isinstance(row, dict):
                continue
            for part in row.get("damage") or []:
                if not isinstance(part, dict) or not part.get("dice"):
                    continue
                event, _amount = self._damage_instance_event(
                    str(part["dice"]), str(part.get("type") or ""), target,
                    False, rng, half=bool(part.get("half")),
                )
                if event is not None:
                    events.append(event)
        return events

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
            # 属性与射程劣势都由共用函数判定：validation、掷骰后的预演和这里
            # 读的是同一份规则，否则"面板显示敏捷、结算按力量"这种裂缝永远
            # 只在某个分支上出现。
            is_ranged_weapon = bool(weapon.get("ranged"))
            ability = self.attack_ability(actor, weapon)
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
            if not sacred_bonus and intent.get("activate_sacred_weapon"):
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
                resource="bonus_action" if intent.get("light_extra_attack") else "",
                weapon=weapon,
                # Multiattack 的第一次攻击为"这一次动作"开一张总额度，后续
                # 每一击都从同一份额度里扣：动作只付一次，次数不重复付。
                attacks_per_action=attacks_in_action,
            )] if spend_attack_action else []),
        ]
        events.extend(self._attack_declaration_events(
            combat, actor_id, target_id, declaration,
        ))
        if intent.get("activate_sacred_weapon"):
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
            intent.get("light_extra_attack")
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
        if sneak_dice:
            # 延迟提交：只有这次命中伤害最终落地才记一次偷袭使用。
            # Shield 改判落空时不会走到提交辅助方法，因此不会记账。
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
            actor, weapon, intent, combat, damage_type=damage_type, check=check,
        )
        if riders:
            # 声明先挂在伤害事件上，掷骰留到真正提交时：Shield/Uncanny Dodge
            # 把命中改判落空时，不给这些骰子白掷一次的机会。
            damage_event["hit_riders"] = riders
        conditions = self._hit_condition_declarations(
            actor, weapon, intent, combat, granted_by,
        )
        if conditions:
            damage_event["hit_condition_effects"] = conditions
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

    def _mark_duration_hours(self, effect: dict[str, Any], slot_level: int) -> int:
        """How long this cast's instance may last, from the slot it used.

        There is no clock in the combat state, so this is the declared number
        carried by the instance rather than an expiry that gets enforced -- the
        same honesty the rest of the concentration model applies.
        """

        base = int(effect.get("mark_duration_hours", 1) or 1)
        thresholds = effect.get("mark_duration_hours_by_slot") or {}
        best = base
        for raw_level, hours in thresholds.items():
            level = int(raw_level)
            if int(slot_level) >= level and int(hours) > best:
                best = int(hours)
        return best

    def _mark_retarget_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        effect: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Move the mark to a new creature without casting anything.

        Validation already proved every precondition (own instance, the marked
        creature at 0 HP, the new one visible and in range, a Bonus Action to
        spend); this method only builds the events, and it fails closed if the
        instance went away between the two steps.

        No ``spell_cast`` trigger fires and no cost is paid: the spell instance
        keeps its identity, so its remaining duration is untouched.
        """

        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        mark = owned_mark(actor)
        if not mark:
            raise CombatIntentError("there is no Hunter's Mark to move")
        target_ids = intent.get("target_ids")
        target_id = str(target_ids[0]) if isinstance(target_ids, list) and target_ids else ""
        if not target_id:
            raise CombatIntentError("the mark needs a new target")
        if not effect.get("marks_target"):  # pragma: no cover - catalog guard
            raise CombatIntentError("only a mark instance can be moved")
        return [
            {
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": "bonus_action", "amount": 1,
            },
            {
                "type": "dnd2024.concentration.retargeted",
                "actor_id": actor_id, "spell_ref": HUNTERS_MARK_REF,
                "target_id": target_id,
                "previous_target_id": str(mark.get("mark_target_id") or ""),
            },
        ]

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
        # Monk feature save DC: 8 + Proficiency Bonus + Wisdom modifier.  It is
        # not the spell save DC (a Monk may have none) and never a number the
        # client supplies.
        dc = (
            8 + int(attacker.get("proficiency_bonus", 0) or 0)
            + ability_modifier(attacker["abilities"].get("wis", 10))
        )
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
        }]

    def _resolve_committed_hit(
        self, instance: Any, combat: dict[str, Any], attacker_id: str,
        target: dict[str, Any], damage_event: dict[str, Any], damage: int, rng: Any,
    ) -> list[dict[str, Any]]:
        """Submit a final hit's damage and run its trailing consequences."""

        consequences, extra_damage = self._hit_tail_events(
            instance, combat, attacker_id, target, damage_event, rng,
        )
        events = list(self._submit_hit_damage(damage_event))
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
        events.extend(self._enemy_zero_hp_feature_events(
            instance, combat, attacker_id, target, total_damage,
        ))
        if self._last_hostile_defeated(combat, target, total_damage):
            events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
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

    def _maybe_overchannel(self, intent: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
        """门控已由 _validate_spell 完成（fail closed）；此处只做变换。"""

        if not intent.get("overchannel"):
            return node
        return maximize_dice_nodes(node)

    def _obscuring_zone_event(
        self, combat: dict[str, Any], intent: dict[str, Any], effect: dict[str, Any],
        actor_id: str, spell_ref: str, slot_level: int, target_ids: list[str],
    ) -> dict[str, Any]:
        """The zone a cast leaves behind: a fact on the battlefield, no targets.

        ``origin`` is a coordinate on the same one-dimensional axis positions
        already use, snapshotted at cast time from the first target/caster --
        that is the whole spatial model this slice gets, and it is enough for
        "walk in, walk out" without pretending to be a map.
        """

        anchor_id = target_ids[0] if target_ids else actor_id
        origin = self._position(combat, anchor_id)
        zone_id = f"{spell_ref}:{actor_id}:{intent.get('intent_id', '')}"
        return {
            "type": "dnd2024.zone.created",
            "zone_id": zone_id,
            "kind": "sphere",
            "level": str(effect.get("level") or "heavily"),
            "origin": origin,
            "radius_ft": int(effect.get("radius_ft", 0) or 0),
            "magical": bool(effect.get("magical")),
            "spell_ref": spell_ref,
            "caster_id": actor_id,
            "concentration": bool(effect.get("concentration")),
            "slot_level": slot_level,
            "rounds_left": int(effect.get("duration_rounds", 10) or 10),
        }

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
        if intent.get("retarget_mark"):
            # 转移标记不是再施一次法：它是同一个法术实例换了个目标，所以
            # 不扣任何施法资源，也不重置剩余时长。
            return self._mark_retarget_events(instance, combat, intent, effect)
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
            started: dict[str, Any] = {
                "type": "dnd2024.concentration.started", "actor_id": actor_id,
                "spell_ref": spell_ref, "target_ids": target_ids,
            }
            if effect.get("marks_target"):
                # 唯一当前目标与剩余时长随实例一起落地：走这条唯一写入点，
                # 才能保证"一个施法者同时只有一个标记"是数据形状而非约定。
                started["mark_target_id"] = target_ids[0]
                started["mark_expires_hours"] = self._mark_duration_hours(
                    effect, slot_level,
                )
            events.append(started)
        cast_event = {
            "type": "dnd2024.spell.cast", "actor_id": actor_id, "spell_ref": spell_ref,
            "slot_level": slot_level, "target_ids": target_ids,
        }
        if overchannel:
            cast_event["overchannel"] = True
        events.append(cast_event)
        if str(effect["mode"]) == "obscuring_zone":
            # 区域法术没有目标结算：落一个区域事实就结束。区域从"目标所在的
            # 那一点"展开并在施放瞬间定死，之后不随生物移动。
            events.append(self._obscuring_zone_event(
                combat, intent, effect, actor_id, spell_ref, slot_level, target_ids,
            ))
            return events
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
        # 真正恢复了 HP 的目标（被 ``cannot_regain_hp`` 拦住的不算）：法术
        # 治疗的两个 rider 都以"实际恢复"为前提，所以这份名单由结算过程
        # 自己记，而不是事后从事件猜。
        healed_targets: list[str] = []
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
                # 破甲一击的 +5 说的是"另一次攻击掷骰"，不区分武器还是法术：
                # 只给武器路径加会让友军的 Fire Bolt 白打，而条件挂到自然过期。
                sundering_bonus, sundering_condition = self._sundering_bonus(
                    visible_target, actor_id,
                )
                # 法术攻击同样对着被掩蔽的 AC：掩蔽是目标的位置事实，不是
                # 武器攻击的特例。
                cover = self._cover_record(instance, combat, target)
                total = roll + bonus + check_bonus + sundering_bonus
                target_ac = effective_armor_class(target) + int(
                    cover["armor_class_bonus"]
                )
                succeeded = roll != 1 and (roll == 20 or total >= target_ac)
                critical = roll == 20
                if (
                    succeeded and not critical
                    and condition_flags(self.catalog.conditions, target["conditions"])["crit_on_hit_within_5"]
                    and self._distance(combat, actor_id, target_id) <= 5
                ):
                    # 法术攻击也是攻击检定：5 尺内命中麻痹/昏迷目标自动重击。
                    critical = True
                if sundering_bonus and sundering_condition:
                    # 消费时机与武器路径一致：+5 已经加进这一掷，命中与否都算用过。
                    events.append({
                        "type": "condition.removed", "target_id": target_id,
                        "condition": sundering_condition,
                        "reason": "sundering_consumed",
                    })
                    consumed_conditions.add((target_id, sundering_condition))
                events.append({
                    "type": "check.resolved", "kind": "spell_attack", "actor_id": actor_id,
                    "target_id": target_id, "rolls": rolls, "natural": roll,
                    "advantage_mode": check_mode["mode"], "advantage_sources": check_mode["sources"],
                    "modifier": bonus,
                    "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
                    "total": total,
                    **({"sundering_bonus": sundering_bonus} if sundering_bonus else {}),
                    "target": target_ac, "success": succeeded, "critical": critical,
                    **(
                        {
                            "cover_bonus": int(cover["armor_class_bonus"]),
                            "cover_sources": cover["sources"],
                        }
                        if int(cover["armor_class_bonus"]) else {}
                    ),
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
                    # Hunter's Mark 认的是"任意攻击检定命中"，法术攻击也在内：
                    # 独立实例、独立抗性，但只算一次专注豁免。
                    mark_row = self._mark_rider_row(actor, target_id)
                    mark_event, mark_damage = (
                        self._rider_damage_event(mark_row, target, critical, rng)
                        if mark_row is not None else (None, 0)
                    )
                    if mark_event is not None:
                        events.append(mark_event)
                    events.extend(self._concentration_events(
                        instance, combat, target, damage + mark_damage, rng,
                    ))
                    dealt[target_id] = dealt.get(target_id, 0) + damage + mark_damage
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
                    # Magic Resistance 只对抗法术的豁免：这一处是唯一由法术
                    # 产出的豁免，其余的（呼吸、rider）不享受它。
                    against_spell=True,
                )
                if not check_event.get("auto_failed"):
                    # 掩蔽只帮敏捷豁免，Indomitable Might 用力量值替换总值；
                    # 两者都只改总数，掷骰面与 check_bonus 保持原样。自动失败
                    # 的豁免没有掷骰，两者都不该凭空给它一个总数。
                    cover = self._cover_save_bonus(
                        instance, combat, target, save_ability,
                        # 效应自己声明"水平掩体与我无关"时不读等级。
                        axis=cover_axis(effect),
                    )
                    total = int(check_event["total"]) + int(cover["bonus"])
                    total, replacement = self._strength_total_replacement(
                        target, save_ability, total,
                    )
                    if int(cover["bonus"]):
                        check_event["cover_bonus"] = int(cover["bonus"])
                        check_event["cover_sources"] = cover["sources"]
                    check_event["cover_level"] = str(cover.get("level") or "none")
                    check_event["cover_interaction"] = cover_axis(effect)[0]
                    check_event.update(replacement)
                    check_event["total"] = total
                    check_event["success"] = total >= int(actor["spell_save_dc"])
                succeeded = check_event["success"]
                save_check_index = len(events)
                events.append(check_event)
                # 无论成功、失败还是自动失败，这次豁免都算"用掉了"那份
                # "下一次豁免有劣势"：消费点就是豁免本身，不是回合边界。
                events.extend(self._consume_save_markers(target_id, target))
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
                if not blocked and healing > 0:
                    healed_targets.append(target_id)
                    events.extend(self._healing_rider_events(
                        actor, target_id, target, slot_level, spell, actor_id,
                    ))
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
                # 标记类法术也走这条分支：它们不造成伤害、不施加条件，只把
                # "谁是我的目标"写进施法者的专注实例（已在上方落地）。
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
        # Blessed Healer 按"这次施法"结算一次，所以落在目标循环之外：三个
        # 目标吃到的是一次治疗后的 2+环阶，而不是三份。
        if slot_level > 0 and any(
            target_id != actor_id for target_id in healed_targets
        ):
            events.extend(self._blessed_healer_events(
                actor, actor_id, slot_level, spell,
            ))
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
            # 不构成威胁，也不能为一次无法结算的攻击消耗反应。"不能借机"是
            # 比"不能反应"更窄的一条（Staggering / Shocking Grasp），只有
            # 这条路径读它。
            if not self._reaction_eligible(combat, threat, opportunity=True):
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
        if "flyby" in trait_ids(actor.get("traits")):
            # Flyby：这只生物飞离触及范围不引发借机攻击，因此威胁表直接清空。
            # 规则原文的前提是"飞行中"，而本引擎没有飞行状态——这个特质只
            # 挂在靠飞行移动的 stat block 上，登记为已知简化。
            adjacent_hostiles = []
        disengaged = bool(economy.get("disengaged")) or "disengaged" in actor["conditions"]
        # Remarkable Athlete 的暴击后移动：这份额度自身免借机攻击，且只免
        # 这一次移动——阈值是额度（英尺），不是"永久免疫"。
        crit_budget = int(economy.get("crit_move", 0) or 0)
        covered_by_crit = bool(crit_budget) and abs(distance) <= crit_budget
        # Forceful Blow 的跟步额度：同样是"英尺预算 + 方向"。方向是"朝被推开
        # 的那个目标"，所以只有当这一步真的朝它走、而且没超额度时才免借机攻击；
        # 朝别处走只是普通移动，照常开窗。
        follow = economy.get("brutal_move")
        covered_by_brutal = False
        if isinstance(follow, dict):
            budget = int(follow.get("amount", 0) or 0)
            follow_target = str(follow.get("target_id") or "")
            toward = (
                self._position(combat, follow_target)
                - self._position(combat, actor_id)
                if follow_target else 0
            )
            covered_by_brutal = bool(
                budget and toward and abs(distance) <= budget
                and (distance > 0) == (toward > 0)
            )
        if adjacent_hostiles and not disengaged and not covered_by_crit and not covered_by_brutal:
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
            **({"brutal_move": True} if covered_by_brutal else {}),
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
        # 第二段伤害与 stat block rider 由 ``_resolve_committed_hit`` 结算：
        # 它们是独立事件，不能混进主伤害的线上形状。
        event.pop("extra_damage_parts", None)
        event.pop("statblock_riders", None)
        events: list[dict[str, Any]] = [event]
        if isinstance(feature_use, dict):
            events.append({"type": "dnd2024.feature.used", **feature_use})
        if isinstance(extra_feature_uses, list):
            for row in extra_feature_uses:
                if isinstance(row, dict) and row.get("feature_id"):
                    events.append({"type": "dnd2024.feature.used", **row})
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

    ABILITY_CHECK_COSTS = frozenset({"action", "bonus_action", "none"})

    def _ability_check_events(
        self, combat: dict[str, Any], intent: dict[str, Any], actor: dict[str, Any],
        rng: Any,
    ) -> list[dict[str, Any]]:
        """A bare ability check, at whatever cost the caller declared.

        The arithmetic is the same one players already use (``ability_modifier``);
        what varies is only what the check costs.  Fighting this into ``hide`` or
        ``stabilize`` would have meant a third copy of the roll, which is exactly
        the duplication GAP-3 records.
        """

        ability = str(intent.get("ability") or "")
        dc = int(intent.get("dc") or 0)
        cost = str(intent.get("check_cost") or "action")
        actor_id = str(actor["actor_id"])
        events: list[dict[str, Any]] = []
        if cost != "none":
            events.append({
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": cost, "amount": 1,
            })
        modifier = ability_modifier(int((actor.get("abilities") or {}).get(ability, 10)))
        roll = int(rng.randint(1, 20))
        total = roll + modifier
        events.append({
            "type": "check.resolved", "kind": "ability_check", "actor_id": actor_id,
            "ability": ability, "rolls": [roll], "natural": roll,
            "modifier": modifier, "total": total, "target": dc,
            "success": total >= dc,
            "roll_id": self._roll_id(events, "ability_check", actor_id),
        })
        return events

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

    def _skill_check_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any], rng: Any,
        *, skill: str, dc: int, kind: str, events: list[dict[str, Any]],
        sources: list[dict[str, Any]] | None = None, intent_id: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """One d20 skill check: the only place a skill is rolled in combat.

        ``kind`` doubles as the event kind and the window settlement kind, which
        is what the pre-roll bonus windows key off (``"hide"``,
        ``"peerless_skill"``...).  Returns the events so far -- possibly ending
        in a pending window -- plus the check row, or ``None`` when a window now
        owns the outcome.

        The DC belongs to the caller: Hide passes a placeholder derived from the
        enemies' Wisdom, which is a registered gap rather than an accident here.
        """

        actor_id = str(actor["actor_id"])
        bonus = int(actor["skill_values"].get(skill, 0) or 0)
        declared = list(sources or ())
        mode = mode_from_sources(declared)
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if mode == "disadvantage" else None
        rolls = [first] if second is None else [first, second]
        natural = min(rolls) if mode == "disadvantage" else first
        floor, floor_sources = self._skill_check_floor(actor, skill)
        effective = max(natural, floor) if floor else natural
        total = effective + bonus
        success = total >= dc
        event: dict[str, Any] = {
            "type": "check.resolved", "kind": kind, "actor_id": actor_id,
            "ability": skill, "rolls": rolls, "natural": effective,
            "modifier": bonus, "total": total, "target": dc, "success": success,
            "roll_id": self._roll_id(events, kind, actor_id),
        }
        if floor:
            event["raw_d20"] = natural
            event["effective_d20"] = effective
            event["d20_floor"] = floor
            event["d20_floor_sources"] = floor_sources
        if mode:
            event["advantage_mode"] = mode
            event["advantage_sources"] = declared
        window = self._ability_check_window_offer(
            instance, combat, actor, event,
            {"kind": kind, "intent_id": intent_id},
        )
        if window is not None:
            events.append({"type": "dnd2024.decision.pending", **window})
            return events, None
        return events, event

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
        # 两个入口（基本动作与 ``cunning_hide`` 能力）共用这一处复核：默认拒绝，
        # 只有重度遮蔽或 ¾/Total 掩护才允许尝试。
        gate = self.hide_preconditions(instance, combat, actor_id)
        if not gate["allowed"]:
            raise CombatIntentError(str(gate["reason"]))
        actor = self._actor_view(instance, combat, actor_id)
        events: list[dict[str, Any]] = []
        if spend_action:
            events.append({
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": self._spend_action_resource(combat), "amount": 1,
            })
        alive_wisdom = [
            int((self._actor_view(instance, combat, f"enemy:{enemy_id}")["saving_throws"]
                or {}).get("wis", 0) or 0)
            for enemy_id, enemy in (combat.get("enemies") or {}).items()
            if int(enemy.get("hp", 0) or 0) > 0
        ]
        dc = 10 + max(alive_wisdom, default=0)
        sources: list[dict[str, Any]] = []
        if bool((actor.get("armor_training") or {}).get("stealth_disadvantage")):
            sources.append({"code": "stealth_disadvantage", "mode": "disadvantage"})
        events, check = self._skill_check_events(
            instance, combat, actor, rng,
            skill="stealth", dc=dc, kind="hide", events=events, sources=sources,
            intent_id=str(intent.get("intent_id") or ""),
        )
        if check is None:
            return events
        events.append(check)
        if check["success"]:
            events.append({
                "type": "condition.applied", "target_id": actor_id,
                "condition": "invisible", "duration": "combat",
                "source_actor_id": actor_id,
                "ends_on": ["attack_roll", "spell_cast"],
            })
        return events

    def _sacred_weapon_binding(self, actor: dict[str, Any]) -> dict[str, Any]:
        """The weapon this Paladin has imbued right now, or ``{}``.

        The binding is one condition on the holder, never a flag on the weapon:
        a second activation replaces it, and ending it is a single removal.
        """

        meta = (actor.get("conditions") or {}).get(SACRED_WEAPON_CONDITION)
        return meta if isinstance(meta, dict) else {}

    def _sacred_weapon_bonus(
        self, actor: dict[str, Any], weapon_ref: str,
    ) -> int:
        """The attack bonus this weapon_ref carries, or zero.

        Matching is by canonical ref: the same weapon *type* in someone else's
        hands, or a second copy of it in this one, is not the imbued weapon.
        """

        binding = self._sacred_weapon_binding(actor)
        if not binding:
            return 0
        if str(binding.get("weapon_ref") or "") != str(weapon_ref or ""):
            return 0
        return int(binding.get("bonus", 0) or 0)

    def _sacred_weapon_bonus_value(
        self, actor: dict[str, Any],
    ) -> tuple[int, str]:
        """The bonus this Paladin's Sacred Weapon grants, and its ability.

        Read from the sheet every time instead of from the stored binding: the
        activation attack itself is answered before the condition exists, and
        both answers must be the same number.
        """

        character = self._feature_character(actor)
        ability = str(self.features.feature_value(
            character, "sacred_weapon", "weapon_attack_bonus_ability", "cha",
        ) or "cha")
        minimum = int(self.features.feature_value(
            character, "sacred_weapon", "weapon_attack_bonus_minimum", 1,
        ) or 1)
        return (
            max(
                minimum,
                ability_modifier(
                    int((actor.get("abilities") or {}).get(ability, 10) or 10)
                ),
            ),
            ability,
        )

    def _sacred_weapon_events(
        self, instance: Any, actor: dict[str, Any], weapon_ref: str,
    ) -> list[dict[str, Any]]:
        """Imbue one held Melee weapon: pay a Channel use, then record the bond.

        Order matters for audit: the old binding (if any) is ended *before* the
        new one exists, so no reader ever sees two bounds at once.
        """

        actor_id = str(actor["actor_id"])
        character = self._feature_character(actor)
        feature_id = "sacred_weapon"
        bonus, ability = self._sacred_weapon_bonus_value(actor)
        minutes = int(self.features.feature_value(
            character, feature_id, "weapon_binding_minutes", 10,
        ) or 10)
        choices = [
            str(value) for value in (
                self.features.feature_value(
                    character, feature_id, "weapon_damage_type_choices",
                    ["normal", "radiant"],
                ) or []
            ) if str(value)
        ]
        events: list[dict[str, Any]] = []
        if self._sacred_weapon_binding(actor):
            events.append({
                "type": "condition.removed", "target_id": actor_id,
                "condition": SACRED_WEAPON_CONDITION, "reason": "sacred_weapon_recast",
            })
        events.append({
            "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
            "resource_id": str(self.features.feature_value(
                character, feature_id, "channel_resource_id", "channel_divinity",
            ) or "channel_divinity"),
            "amount": 1,
            "feature_id": feature_id,
        })
        events.append({
            "type": "condition.applied", "target_id": actor_id,
            "condition": SACRED_WEAPON_CONDITION,
            "duration": SACRED_WEAPON_DURATION,
            "duration_minutes": minutes,
            "expires_at_minute": self._logical_minutes(instance) + minutes,
            "weapon_ref": weapon_ref, "bonus": bonus, "ability": ability,
            "damage_type_choices": choices,
            # 光照只是可审计事实：没有光照系统时它不参与 AC、攻击、Hide 或
            # 感知，所以这里显式写 guided，而不是假装已经自动判定。
            "bright_light_radius": int(self.features.feature_value(
                character, feature_id, "bright_light_radius", 20,
            ) or 20),
            "dim_light_beyond": int(self.features.feature_value(
                character, feature_id, "dim_light_beyond", 20,
            ) or 20),
            "light_automation": str(self.features.feature_value(
                character, feature_id, "light_automation", "guided",
            ) or "guided"),
            "source_actor_id": actor_id, "feature_id": feature_id,
        })
        return events

    #: Bonus Action 附带移动的声明表。今天只有两项消费者，所以先写成代码表；
    #: 第三个出现时再搬进 catalog（与 ``unavailable_reason`` 同一条纪律）。
    RIDER_MOVES = {
        "rage": {"feature_id": "instinctive_pounce", "provokes_opportunity": False},
        "second_wind": {"feature_id": "tactical_shift", "provokes_opportunity": False},
    }

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
        if rider is None or raw in (None, 0):
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

    def _skill_abilities(self) -> dict[str, str]:
        """skill id -> ability id, read from the bundle once per batch.

        The mapping lives in content (one entity per skill), so the engine never
        hard-codes "Perception is Wisdom".
        """

        try:
            rows = self.bundle.list("skill")
        except Exception:  # pragma: no cover - bundle always exposes list()
            return {}
        mapped: dict[str, str] = {}
        for row in rows:
            skill_id = str(row.get("id") or "")
            ability = str(row.get("ability_ref") or "").removeprefix("ability:")
            if skill_id and ability:
                mapped[skill_id] = ability
        return mapped

    def _fleet_step_grant_events(
        self, actor: dict[str, Any], capability: Any,
    ) -> list[dict[str, Any]]:
        """Fleet Step: a non-Step Bonus Action leaves one free Step behind.

        旗标每回合只由这一个来源写入；Step 自己不再授予（否则链式无限），
        所以判断的是"这个能力是不是 Step of the Wind"而不是"花了附赠动作没有"。
        """

        if capability.feature_id == "step_of_the_wind":
            return []
        if capability.cost.action != "bonus_action":
            return []
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "fleet_step"):
            return []
        return [{
            "type": "dnd2024.economy.granted", "actor_id": str(actor["actor_id"]),
            "grants": {}, "flags": {"fleet_step_available": True},
        }]

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
        via_fleet_step = bool(intent.get("via_fleet_step"))
        if capability.cost.action and not via_fleet_step:
            events.append({
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": capability.cost.action, "amount": 1,
            })
        if via_fleet_step:
            events.append({
                "type": "dnd2024.economy.granted", "actor_id": actor_id,
                "grants": {}, "flags": {"fleet_step_available": False},
            })
        for resource_cost in capability.cost.resources:
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
                "resource_id": resource_cost.resource_id, "amount": resource_cost.amount,
            })
        events.extend(self._fleet_step_grant_events(actor, capability))
        target_id = str(intent.get("target_id") or "")
        # Reuse the actual reducer on an isolated aggregate, not a second
        # approximation of HP, positions, conditions or per-turn usage.
        staged = fork_domain_state(instance)
        simulated = 0
        for action in capability.actions:
            if action.kind == "unarmed_strike":
                strike_count = self._strike_count(actor, capability, action.count)
                for _ in range(strike_count):
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
            elif action.kind == "divine_spark":
                events.extend(self._divine_spark_events(
                    instance, combat, actor_id, actor, capability, intent, rng,
                ))
            elif action.kind == "turn_undead":
                events.extend(self._turn_undead_events(
                    instance, combat, actor_id, actor, capability, intent, rng,
                ))
            elif action.kind == "abjure_foes":
                events.extend(self._abjure_foes_events(
                    instance, combat, actor_id, actor, capability, intent, rng,
                ))
            elif action.kind == "preserve_life":
                events.extend(self._preserve_life_events(
                    actor, capability, intent,
                ))
            elif action.kind == "divine_sense":
                events.extend(self._divine_sense_events(
                    instance, actor, capability,
                ))
            elif action.kind == "lay_on_hands":
                events.extend(self._lay_on_hands_events(
                    instance, combat, actor_id, capability, intent,
                ))
            elif action.kind == "heal":
                # 特性声明的治疗骰 + 等级加值；事件形状与法术治疗分支一致。
                character = self._feature_character(actor)
                dice = str(self.features.feature_value(
                    character, capability.feature_id, "heal_dice", "",
                ) or "")
                if dice == "martial_arts":
                    # Wholeness of Body 掷的是"当前等级轨道的一个 Martial Arts
                    # die"，不是固定骰：6/11/17 级分别是 d8/d10/d12，写成常量就
                    # 会在升级后停在旧骰面上。
                    dice = self.features.unarmed_strike_die(character)
                bonus = int(self.features.feature_value(
                    character, capability.feature_id, "heal_bonus", 0,
                ) or 0)
                ability = str(self.features.feature_value(
                    character, capability.feature_id, "heal_ability", "",
                ) or "")
                if ability:
                    bonus += ability_modifier(
                        int((actor.get("abilities") or {}).get(ability, 10) or 10)
                    )
                minimum = int(self.features.feature_value(
                    character, capability.feature_id, "heal_minimum", 1,
                ) or 1)
                healing_node = formula_node_from_dice_formula(dice)
                if bonus:
                    healing_node = {"op": "add", "args": [
                        healing_node, {"op": "constant", "value": bonus},
                    ]}
                healing, rolls = roll_damage_node(healing_node, rng)
                # 最低值作用在"最终治疗量"上：Wis 为负时 roll+mod 可以是负数，
                # 但它仍是一次成功的治疗，不能变成扣血。
                healing = max(minimum, healing)
                blocked = "cannot_regain_hp" in actor["conditions"]
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": actor_id,
                    "delta": 0 if blocked else healing, "amount": 0 if blocked else healing,
                    "healing": True, "rolls": rolls,
                    "feature_id": capability.feature_id,
                    "heal_die": dice, "heal_bonus": bonus,
                    **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
                })
                events.extend(
                    self._rider_move_events(combat, actor, capability_id, intent)
                )
            elif action.kind == "steady_aim":
                events.append({
                    "type": "condition.applied", "target_id": actor_id,
                    "condition": "next_attack_advantage", "duration": "combat",
                })
                events.append({
                    "type": "condition.applied", "target_id": actor_id,
                    "condition": "steady_aim_slow", "duration": "caster_turn_end",
                    "source_actor_id": actor_id,
                })
            elif action.kind == "temp_hp":
                events.extend(self._temp_hp_grant_events(
                    actor, capability.feature_id, rng,
                ))
            elif action.kind == "invisible":
                events.extend(self._self_invisible_events(actor, capability.feature_id))
            elif action.kind == "resistance_all":
                events.extend(self._resistance_all_events(
                    instance, combat, actor, capability.feature_id,
                ))
            elif action.kind == "rage":
                # 狂暴不能维持专注：进入狂暴即结束正在维持的专注法术。
                if actor.get("concentration"):
                    events.append({
                        "type": "dnd2024.concentration.ended", "actor_id": actor_id,
                        "reason": "rage",
                    })
                events.append({
                    "type": "condition.applied", "target_id": actor_id,
                    "condition": "rage",
                    "duration": (
                        PERSISTENT_RAGE_DURATION
                        if self.features.has_feature(
                            self._feature_character(actor), "persistent_rage",
                        )
                        else "rage"
                    ),
                    "turns_remaining": RAGE_MAX_ROUNDS,
                    "sustained_this_turn": False, "established": False,
                    "source_actor_id": actor_id,
                })
                # 进入狂暴时"已有的条件直接结束"，与"之后的免疫"是两件事：
                # 免疫只拦新增，已经挂上的条目要真的删除并留下可审计事件。
                events.extend(
                    self._condition_gain_cleanup_events(instance, actor, "rage")
                )
                events.extend(
                    self._rider_move_events(combat, actor, "rage", intent)
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

    def _temp_hp_grant_events(
        self, actor: dict[str, Any], feature_id: str, rng: Any,
    ) -> list[dict[str, Any]]:
        """A self-only Temporary Hit Point grant paid by an action.

        Tireless rolls its own die and adds the ability modifier.  The reducer
        already owns the one rule that matters here -- keep the higher of the
        old and the new pool, never touch Hit Points, Unconscious or stable --
        so this method produces only the roll and the amount.  A lower result
        therefore still costs the action and the charge, which is what the rule
        says: the die was rolled, not negotiated.
        """

        character = self._feature_character(actor)
        dice = str(self.features.feature_value(
            character, feature_id, "temp_hp_dice", "",
        ) or "")
        ability = str(self.features.feature_value(
            character, feature_id, "temp_hp_dice_ability", "",
        ) or "")
        minimum = int(self.features.feature_value(
            character, feature_id, "temp_hp_dice_minimum", 1,
        ) or 1)
        node = formula_node_from_dice_formula(dice)
        modifier = (
            ability_modifier(int((actor.get("abilities") or {}).get(ability, 10) or 10))
            if ability else 0
        )
        rolled, rolls = roll_damage_node(node, rng)
        amount = max(minimum, rolled + modifier)
        actor_id = str(actor["actor_id"])
        return [{
            "type": "resource.changed", "resource": "temp_hp",
            "target_id": actor_id, "source_actor_id": actor_id,
            "delta": amount, "amount": amount, "rolls": rolls,
            "feature_id": feature_id, "ability": ability,
            "ability_modifier": modifier, "rolled": rolled,
        }]

    def _self_invisible_events(
        self, actor: dict[str, Any], feature_id: str,
    ) -> list[dict[str, Any]]:
        """Nature's Veil: Invisible until the end of the actor's next turn.

        ``caster_next_turn_end`` with two ticks is exactly that span: the
        actor's own turn end takes it to one, the next one takes it to zero.
        Nothing else ends it -- attacking or being attacked does not, because
        the SRD text grants a fixed span and the generic visibility rules
        already answer who can see whom.  Re-applying writes the same
        condition again rather than stacking a second one.
        """

        character = self._feature_character(actor)
        condition = str(self.features.feature_value(
            character, feature_id, "condition", "invisible",
        ) or "invisible")
        duration = str(self.features.feature_value(
            character, feature_id, "condition_duration", "caster_next_turn_end",
        ) or "caster_next_turn_end")
        turns = int(self.features.feature_value(
            character, feature_id, "condition_turns", 2,
        ) or 2)
        actor_id = str(actor["actor_id"])
        return [{
            "type": "condition.applied", "target_id": actor_id,
            "condition": condition, "duration": duration,
            "turns_remaining": turns, "source_actor_id": actor_id,
            "feature_id": feature_id,
        }]

    def _resistance_all_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        feature_id: str,
    ) -> list[dict[str, Any]]:
        """Superior Defense: Resistance to every damage type except a named few.

        The condition carries the *exclusion* instead of an enumerated list of
        every type: a damage type added to the catalog later must be resisted by
        default, and a copied list would silently be one entry short.  Both time
        limits are recorded -- one real minute on the world clock and ten combat
        rounds -- because a fight does not advance the clock and a rest does not
        advance the round counter; whichever runs out first ends the effect.
        """

        character = self._feature_character(actor)
        minutes = int(self.features.feature_value(
            character, feature_id, "duration_minutes", 1,
        ) or 1)
        rounds = int(self.features.feature_value(
            character, feature_id, "duration_rounds", 10,
        ) or 10)
        excepted = self.features.feature_value(
            character, feature_id, "resist_all_except", ["force"],
        ) or []
        now = self._logical_minutes(instance)
        actor_id = str(actor["actor_id"])
        return [{
            "type": "condition.applied", "target_id": actor_id,
            "condition": SUPERIOR_DEFENSE_CONDITION,
            "duration": SUPERIOR_DEFENSE_DURATION,
            "duration_minutes": minutes, "duration_rounds": rounds,
            "expires_at_minute": now + minutes,
            "expires_at_round": int(combat.get("round", 1) or 1) + rounds,
            "resist_all_except": [
                str(kind) for kind in excepted if isinstance(kind, str) and kind
            ],
            "ends_on_incapacitated": bool(self.features.feature_value(
                character, feature_id, "ends_on_incapacitated", False,
            )),
            "source_actor_id": actor_id, "feature_id": feature_id,
        }]

    def _healing_rider_events(
        self, actor: dict[str, Any], target_id: str, target: dict[str, Any],
        slot_level: int, spell: dict[str, Any], actor_id: str,
    ) -> list[dict[str, Any]]:
        """Disciple of Life: 2 plus the slot's level, per healed creature.

        The feature's sentence is "a spell you cast with a spell slot", so a
        free cast, a Channel Divinity option and Lay On Hands are all outside
        it -- those restore Hit Points without spending a slot.  ``slot_level``
        is therefore the whole test, and it is the *real* level of the slot
        that was spent, so an upcast Cure Wounds pays 2 + the higher level.
        """

        if slot_level <= 0:
            return []
        character = self._feature_character(actor)
        flat = self.features.feature_value(
            character, "disciple_of_life", "rider_flat", 0,
        )
        if not flat:
            return []
        per_level = int(self.features.feature_value(
            character, "disciple_of_life", "rider_per_slot_level", 1,
        ) or 0)
        bonus = int(flat) + per_level * slot_level
        blocked = "cannot_regain_hp" in (target.get("conditions") or {})
        return [{
            "type": "resource.changed", "resource": "hp", "target_id": target_id,
            "delta": 0 if blocked else bonus,
            "amount": 0 if blocked else bonus,
            "healing": True, "rolls": [],
            "feature_id": "disciple_of_life", "rider": "disciple_of_life",
            "source_spell_ref": f"spell:{spell['id']}",
            "slot_level": slot_level, "rider_target_id": target_id,
            **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
        }]

    def _blessed_healer_events(
        self, actor: dict[str, Any], actor_id: str, slot_level: int,
        spell: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Blessed Healer: heal yourself once per qualifying cast.

        "One or more creatures other than yourself" is a property of the whole
        cast, so the caller decides whether the condition held; this method
        only prices the once-per-cast amount.
        """

        if slot_level <= 0:
            return []
        character = self._feature_character(actor)
        flat = self.features.feature_value(
            character, "blessed_healer", "rider_flat", 0,
        )
        if not flat:
            return []
        per_level = int(self.features.feature_value(
            character, "blessed_healer", "rider_per_slot_level", 1,
        ) or 0)
        bonus = int(flat) + per_level * slot_level
        blocked = "cannot_regain_hp" in (actor.get("conditions") or {})
        return [{
            "type": "resource.changed", "resource": "hp", "target_id": actor_id,
            "delta": 0 if blocked else bonus,
            "amount": 0 if blocked else bonus,
            "healing": True, "rolls": [],
            "feature_id": "blessed_healer", "rider": "blessed_healer",
            "source_spell_ref": f"spell:{spell['id']}",
            "slot_level": slot_level, "rider_target_id": actor_id,
            **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
        }]

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
        events: list[dict[str, Any]], rng: Any, *, against_spell: bool = False,
    ) -> dict[str, Any]:
        """Shared save authority for spells, repeated saves and hit riders.

        Two stat-block traits attach here because this is the only place that
        knows a save is being rolled at all: Magic Resistance (Advantage when
        the effect is a spell) and Legendary Resistance (a failed save becomes
        a success, at the cost of one use).
        """

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
        save_sources = self._saving_throw_sources(
            actor, ability, against_spell=against_spell,
        )
        traits = actor.get("traits")
        if against_spell and "magic_resistance" in trait_ids(traits):
            save_sources.append({"code": "magic_resistance", "mode": "advantage"})
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
        cover = self._cover_save_bonus(instance, combat, actor, ability)
        total = (
            roll + modifier + check_bonus
            + int(aura["bonus"]) + int(cover["bonus"])
        )
        total, replacement = self._strength_total_replacement(actor, ability, total)
        legendary = self._legendary_resistance_events(
            actor, actor_id, total >= dc, events,
        )
        check_event = {
            "type": "check.resolved", "kind": "saving_throw",
            "actor_id": actor_id,
            "source_actor_id": source_actor_id,
            "ability": ability,
            "rolls": [value for value in (first, second) if value is not None],
            "natural": roll, "modifier": modifier,
            "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
            "aura_bonus": int(aura["bonus"]), "aura_sources": aura["sources"],
            "total": total, "target": dc,
            "success": total >= dc or legendary["used"],
            "roll_id": self._roll_id(events, "saving_throw", actor_id),
            **replacement,
        }
        if legendary["used"]:
            # 掷骰结果原样留着：日志要能看出"这一掷本来是失败的"。
            check_event["legendary_resistance"] = legendary["remaining"]
        if int(cover["bonus"]):
            check_event["cover_bonus"] = int(cover["bonus"])
            check_event["cover_sources"] = cover["sources"]
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
            events.extend(self._consume_save_markers(actor_id, actor))
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
        intent: dict[str, Any] | None = None, *, settled_condition: str = "",
    ) -> list[dict[str, Any]]:
        order = list(combat["initiative"])
        if not any(
            int(enemy.get("hp", 0) or 0) > 0
            for enemy in combat.get("enemies", {}).values()
        ):
            return [{"type": "dnd2024.combat.ended", "reason": "victory"}]
        previous_actor = self._current_actor(combat)
        player_conditions: dict[str, set[str]] = {}
        for actor_id in order:
            if actor_id.startswith("player:"):
                conditions = set(self._actor_view(instance, combat, actor_id)["conditions"])
                if actor_id == previous_actor and settled_condition:
                    conditions.add(settled_condition)
                player_conditions[actor_id] = conditions
        if not any(
            "dead" not in conditions and "stable" not in conditions
            for conditions in player_conditions.values()
        ):
            reason = (
                "party_incapacitated"
                if any("stable" in conditions and "dead" not in conditions
                       for conditions in player_conditions.values())
                else "party_defeated"
            )
            return [{"type": "dnd2024.combat.ended", "reason": reason}]
        previous_index = int(combat["turn_index"])
        # 回合按 slot 推进：同一角色在首轮可以有两个 slot（Thief's Reflexes），
        # 它们共享 actor identity，但各自刷新经济、反应与 turn-start。
        slots = self._combat_slots(combat)
        # 重复豁免 tick 发生在"目标自己回合结束"，排在推进回合之前。
        events = []
        if "dead" not in player_conditions.get(previous_actor, ()):
            events = self._save_repeat_events(instance, combat, previous_actor, rng)
            # Self-Restoration 与重复豁免读的是同一个时刻：若那条条件已在本次
            # tick 里被豁免掉，选定移除就不再产生第二条事件。
            ender = self._actor_view(instance, combat, previous_actor)
            events.extend(self._self_restoration_removal_events(ender, intent, events))
        next_index = previous_index
        wraps = 0
        for _ in range(len(slots)):
            next_index = (next_index + 1) % len(slots)
            if next_index == 0:
                wraps += 1
            slot = slots[next_index]
            # 只在前一轮有效的 slot（Thief 的第二回合）在第二轮直接跳过，
            # 不能让它把轮次预算或先攻顺序改掉。
            rounds = slot.get("rounds")
            if isinstance(rounds, list) and int(combat["round"]) + wraps not in rounds:
                continue
            candidate = self._actor_view(instance, combat, str(slot["actor_id"]))
            # 失能单位跳过回合：敌人/队友 HP 归零即跳过；玩家 0 HP 走死亡豁免。
            if candidate["kind"] in {"enemy", "companion"} and candidate["hp"] <= 0:
                continue
            conditions = player_conditions.get(candidate["actor_id"], candidate["conditions"])
            if "dead" in conditions or "stable" in conditions:
                continue
            break
        next_round = int(combat["round"]) + wraps
        next_actor = str(slots[next_index]["actor_id"])
        next_view = self._actor_view(instance, combat, next_actor)
        return [
            *events,
            # 卓越防御按"一分钟"计时，不是按持有者的回合数：每当局势推进就
            # 用两个上限各查一次，谁先到就结束谁。
            *self._superior_defense_expiry_events(instance, combat, next_round),
            {
                "type": "dnd2024.turn.advanced", "round": next_round,
                "turn_index": next_index, "actor_id": next_actor,
                "slot_id": str(slots[next_index]["slot_id"]),
                "previous_actor_id": previous_actor,
                "economy": self._fresh_economy(next_view),
            },
            *self._turn_start_events(next_view, rng),
        ]

    def _superior_defense_expiry_events(
        self, instance: Any, combat: dict[str, Any], round_number: int | None = None,
    ) -> list[dict[str, Any]]:
        """End Superior Defense when either of its two limits has passed.

        A combat round is six seconds, so ten rounds are one minute on the
        battlefield; the world clock is the only thing that measures time
        outside it.  Both limits are read and whichever expired first ends the
        effect, so it never outlives its minute by hiding behind ``combat.ended``.

        ``round_number`` is supplied only when a turn just advanced.  Every
        combat intent sweeps the clock limit too, which is what removes a
        leftover buff carried out of an earlier fight without inventing a
        second expiry mechanism.  An actor whose view cannot be built any more
        is simply skipped: it left the fight, and its conditions went with it.
        """

        now = self._logical_minutes(instance)
        events: list[dict[str, Any]] = []
        for actor_id in combat.get("initiative") or []:
            identifier = str(actor_id)
            try:
                view = self._actor_view(instance, combat, identifier)
            except CombatIntentError:
                continue
            condition = (view.get("conditions") or {}).get(SUPERIOR_DEFENSE_CONDITION)
            if not isinstance(condition, dict):
                continue
            expires_round = condition.get("expires_at_round")
            expired = (
                round_number is not None
                and isinstance(expires_round, int) and not isinstance(expires_round, bool)
                and round_number > expires_round
            )
            if not expired:
                expires_at = condition.get("expires_at_minute")
                expired = (
                    isinstance(expires_at, int) and not isinstance(expires_at, bool)
                    and now >= expires_at
                )
            if not expired:
                continue
            events.append({
                "type": "condition.removed", "target_id": identifier,
                "condition": SUPERIOR_DEFENSE_CONDITION,
                "reason": "one minute passed",
            })
        return events

    @staticmethod
    def _combat_slots(combat: dict[str, Any]) -> list[dict[str, Any]]:
        """Turn slots for this combat, defaulting to one slot per actor.

        Old saves (and any combat started before this slice) carry no
        ``turn_slots``; the shared primitive falls back to the deduplicated
        Initiative list so their schedule stays byte-identical instead of
        migrating state on read.
        """

        return combat_turn_slots(combat)

    def _turn_start_events(
        self, actor: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Everything a turn beginning does, in registration order.

        Survivor's regeneration was registered first and keeps its position;
        Heroic Warrior appends after it.  Every entry point -- the first actor
        of a combat, a normal turn, and a Thief's second turn -- comes through
        here, so no path can grant twice or skip one.
        """

        return [
            *self._turn_start_regen_events(actor),
            *self._turn_start_heroic_events(actor),
            *self._turn_start_recharge_events(actor, rng),
        ]

    @staticmethod
    def _turn_start_recharge_events(
        actor: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """S0-MM3c: roll each spent recharge action back at the owner's turn start.

        The roll happens here rather than at intent time because the rule is
        "at the start of its turn, roll a d6": doing it lazily would let a
        creature decide after seeing whether it wants the action.  A recharge
        action that is still available rolls nothing.
        """

        usage = actor.get("usage")
        if not isinstance(usage, dict):
            return []
        events: list[dict[str, Any]] = []
        for action in actor.get("actions") or []:
            if not isinstance(action, dict):
                continue
            limits = action.get("usage_limits")
            if not isinstance(limits, dict) or "recharge" not in limits:
                continue
            row = usage.get(str(action.get("id") or ""))
            if not isinstance(row, dict) or row.get("available", True):
                continue
            window = limits.get("recharge") or {}
            events.append({
                "type": "dnd2024.action.recharge_rolled",
                "actor_id": str(actor["actor_id"]),
                "action_id": str(action.get("id") or ""),
                "roll": int(rng.randint(1, 6)),
                "from": int(window.get("from", 6)),
                "to": int(window.get("to", 6)),
            })
        return events

    def _turn_start_heroic_events(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        """Heroic Warrior: a self-grant at turn start, only when lacking it.

        Heroic Inspiration is a state, not an Advantage: this emits the grant
        and nothing else.  Spending it rerolls *any* die, which this slice does
        not model -- the sheet flag is therefore real but currently
        unconsumable, and the execution report says so.
        """

        if actor.get("kind") != "player":
            return []
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "heroic_warrior"):
            return []
        current = character.get("heroic_inspiration")
        if isinstance(current, dict) and current.get("granted") is True:
            return []
        return [{
            "type": "dnd2024.heroic_inspiration.changed",
            "actor_id": str(actor["actor_id"]), "granted": True,
            "feature_id": "heroic_warrior",
        }]

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
        # 死亡豁免同样是豁免：劣势必在这里被用掉，不能留到"复活后的第一次豁免"。
        events.extend(self._consume_save_markers(actor_id, actor))
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
        settled_condition = "dead" if dead else "stable" if stable else ""
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
            *([] if hp else self._end_turn_events(
                instance, combat, rng, settled_condition=settled_condition,
            )),
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
        events.extend(self._consume_save_markers(actor_id, actor))
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
            if self._reaction_eligible(combat, threat, opportunity=True):
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
