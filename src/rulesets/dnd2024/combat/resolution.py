"""Deterministic event resolution stage for D&D 2024 combat.

Two of the settlement pipelines live in sibling mixins:
``combat/spell_resolution.py`` (casting a spell) and ``combat/hit_resolution.py``
(what a committed hit does).  This module keeps the intent dispatch that fans out
to them, plus attacks, saving throws and checks, combat start and initiative,
turn and movement events, item activation, and the shared readers they all use.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.features.resources import (
    USAGE_SCOPE_LONG_REST,
    feature_usage_count,
)
from ..character.attunement import AttunementError, canonical_item_ref
from ..character.primitives import derived_save, derived_skill
from ..forms import wild_shape_state
from ..items.activation import (
    ItemActivationError,
    build_item_healing_event,
    validate_activation_declaration,
)
from ..items.charges import (
    ChargesError,
    charge_waiver_roll,
    charges_of,
    destroy_check_events,
    validate_charges_declaration,
)
from ..spells.font_of_magic import declaring_class_level
from .obscurement import SANCTUARY_DURATION_ROUNDS, sanctuary_zone_id, zone_members
from . import spatial
from .action_adapter import (
    attack_damage_node,
    average_damage,
    formula_node_from_dice_formula,
    roll_damage_node,
)
from ..features.resources import (
    feature_usage_count,
    long_effect_active,
)
from ..character.derivation import proficiency_bonus
from .primitives import (
    world_minutes,
    INITIATIVE_CHOICES,
    RELENTLESS_RAGE_DECISION,
    CombatIntentError,
    actor_kind as _actor_kind,
    attack_damage_parts,
    canonical as _canonical,
    combat_turn_slots,
    companion_actor as _companion_actor,
    current_turn_slot as _current_turn_slot,
    enemy_actor as _enemy_actor,
    normalize_attack_profile,
    player_actor as _player_actor,
    LEGENDARY_RESISTANCE_DEFAULT_PER_DAY,
    stand_up_declaration,
    trait_ids,
    trait_params,
)
from .check_modes import (
    HUNTERS_MARK_REF,
    condition_check_bonus_rolls,
    condition_flags,
    effective_speed,
    mode_from_sources,
)
from src.rulesets.dnd2024.features.attack_declarations import (
    BRUTAL_STRIKE,
    LIGHT_EXTRA_ATTACK,
    SACRED_WEAPON,
    declaration_value,
)
from src.rulesets.dnd2024.features.kinds import KIND_HANDLERS, KindContext
from src.rulesets.dnd2024.features.models import FeatureCapability, SpellSlotCost
from src.rulesets.dnd2024.features.resources import resource_state
from .check_windows import CHECK_WINDOW_DECISION, DAMAGE_WINDOW_DECISION
from .reaction_spells import COUNTER_SPELL_DECISION, HIT_REACTION_DECISION, effective_armor_class
from .attack_resolution import CombatAttackResolutionMixin
from .attack_riders import CombatAttackRidersMixin
from .saving_throws import CombatSavingThrowsMixin
from .triggered_spells import HIT_SPELL_DECISION
# Indomitable Might is a passive declaration on the character, but the check
# event has to name the feature that rewrote its total.  The id lives here so
# the audit trail and the catalog cannot drift apart.

def _equipment_modes(intent: dict[str, Any]) -> dict[str, str]:
    """逐件模式：意图里给 `equipment_modes`，否则退回单个 `equipment_mode`。"""

    raw = intent.get("equipment_modes")
    if isinstance(raw, dict) and raw:
        return {str(ref): str(mode) for ref, mode in raw.items()}
    return {"*": str(intent.get("equipment_mode") or "merge")}


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


class CombatResolutionMixin(
    CombatAttackResolutionMixin, CombatAttackRidersMixin, CombatSavingThrowsMixin,
):
    """Turn validated intents and server RNG into immutable event batches.

    Composition root: the attack-flow, attack-rider and saving-throw mixins are
    assembled here so callers keep one ``CombatResolutionMixin`` name."""

    __slots__ = ()

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
        # 实际扣费：Use Magic Device 掷出阈值时本次为 0。后续的事件与销毁判定
        # 都读这个值，否则会出现"事件说免收、销毁检查照扣"的两套账。
        spent: int | None = None
        if maximum is not None and charge_cost is not None:
            declared = int(charge_cost)
            spent = declared
            waiver = charge_waiver_roll(
                self._feature_character(actor), self.features, rng,
            )
            if waiver is not None:
                roll, threshold = waiver
                waived = roll >= threshold
                if waived:
                    spent = 0
                # 掷了就要留骰迹：1–5 与 6 的区别必须能从事件流里读出来，
                # 否则"每次只掷一次"和"根本没掷"无法区分。
                events.append({
                    "type": "dnd2024.item.charge_roll",
                    "actor_id": actor_id,
                    "item_ref": item_ref,
                    "declared": declared,
                    "roll": roll,
                    "threshold": threshold,
                    "waived": waived,
                })
            if spent:
                events.append({
                    "type": "dnd2024.item.charge_spent",
                    "actor_id": actor_id,
                    "item_ref": item_ref,
                    "amount": spent,
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
            instance, actor_id, item_ref, item, charge_cost=spent, rng=rng,
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
            # 形态会跨战斗保留：带着狼形态进战斗，先攻就用狼的敏捷。
            modifier = self._initiative_modifier(
                canonical, self._initiative_view(instance, uid, canonical),
            )
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
        started = [{
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
        }]
        grid_input = spatial.start_input(intent)
        if grid_input is not None:
            board, grid_positions = grid_input
            missing = [actor for actor in order if actor not in grid_positions]
            if missing:
                raise CombatIntentError(
                    "GRID_START_MISSING_POSITION: no bound token position for "
                    + ", ".join(sorted(missing))
                )
            started[0].update({
                "position_mode": spatial.POSITION_MODE_GRID,
                "board": board,
                "spatial_position": {
                    actor: grid_positions[actor] for actor in order
                },
            })
        return [*started, *refills, *opening, *self._turn_start_events(instance, None, first, rng)]

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
            events.extend(self._evergreen_wild_shape_events(character, actor_id))
        return events

    def _evergreen_wild_shape_events(
        self, character: dict[str, Any], actor_id: str,
    ) -> list[dict[str, Any]]:
        """SRD p43：掷先攻且已无 Wild Shape 次数时恢复一次。

        无骰、无动作、不受长休限制；`current > 0` 时什么都不做——所以每场战斗
        最多补 1 次，不能靠连续开战刷次数。
        """

        if not self.features.has_feature(character, "archdruid"):
            return []
        state = (character.get("resources") or {}).get("class", {}).get("wild_shape")
        if not isinstance(state, dict):
            return []
        if int(state.get("current", 0) or 0) != 0:
            return []
        return [{
            "type": "dnd2024.class_resource.restored", "actor_id": actor_id,
            "resource_id": "wild_shape", "amount": 1,
        }]

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

    def _natures_ward_source(self, target: dict[str, Any], damage_type: str) -> str:
        """Nature's Ward, on the owner or shared inside their Sanctuary.

        Resistance never stacks, so this is consulted after every condition
        source declined and returns at most one source id.  Zone sharing is
        positional: walking out of the Cube drops it on the next hit.
        """

        if target.get("kind") not in {"player", "companion"}:
            return ""
        ward_type = str(target.get("ward_resistance_type") or "")
        if ward_type and ward_type == damage_type:
            return "natures_ward"
        return ""

    def _fiendish_resilience_type(self, target: dict[str, Any]) -> str:
        """The damage type this character re-chose at its last rest, or ``""``."""

        if target.get("kind") not in {"player", "companion"}:
            return ""
        character = self._feature_character(target)
        stored = (character.get("features") or {}).get("fiendish_resilience")
        if not isinstance(stored, dict):
            return ""
        return str(stored.get("damage_type") or "")

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

    def _movement_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        actor_id = str(intent["actor_id"])
        grid = spatial.is_grid(combat)
        target_cell = spatial.require_target_cell(intent) if grid else None
        distance = (
            spatial.move_cost_ft(combat, actor_id, target_cell)
            if grid else int(intent["distance"])
        )
        economy = combat.get("economy") or {}
        adjacent_hostiles: list[dict[str, Any]] = []
        for target in self._hostile_targets(instance, combat, actor_id):
            if self._distance(combat, actor_id, target["actor_id"]) > 5:
                continue
            if grid:
                # Chebyshev, same metric every other grid range reader uses.
                after = spatial.distance_ft(
                    combat["board"], target_cell,
                    spatial.position_of(combat, target["actor_id"]),
                )
                if after <= 5:
                    continue
            elif self._distance_after_move(combat, actor_id, target["actor_id"], distance) <= 5:
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
        # 三种一次性额度（暴击后移动 / Forceful Blow 的跟步 / 诡诈打击的撤步）
        # 形状相同：英尺预算 + 可选的"只能朝某个目标走"。它们共用一个键。
        covered = self._covered_by_move_allowance(combat, actor_id, distance, economy)
        if adjacent_hostiles and not disengaged and not covered:
            decision = {
                "decision_id": f"decision:{intent['intent_id']}",
                "kind": "opportunity_attack",
                "assigned_to": str(getattr(instance, "gm_uid", "") or ""),
                "options": ["resolve", "decline"],
                "threat_actor_ids": [item["actor_id"] for item in adjacent_hostiles],
                "movement": {
                    "actor_id": actor_id,
                    **({"target_cell": target_cell} if grid else {"distance": distance}),
                },
            }
            return decision, [{"type": "dnd2024.decision.pending", **decision}]
        return None, [{
            "type": "dnd2024.position.changed", "actor_id": actor_id,
            "distance": 0 if grid else distance,
            "movement_cost": distance if grid else abs(distance), "forced": False,
            **({"target_cell": target_cell} if grid else {}),
            **({"move_allowance": True} if covered else {}),
        }]

    def _covered_by_move_allowance(
        self, combat: dict[str, Any], actor_id: str, distance: int,
        economy: dict[str, Any],
    ) -> bool:
        """Whether this one move is paid for by a provoke-free allowance.

        A target-bound allowance (Forceful Blow) additionally requires the
        move to point at that creature; walking anywhere else is ordinary
        movement and still opens the window.  Older saves may still carry
        ``crit_move`` / ``brutal_move``: they are read here and nowhere else.
        """

        allowance = economy.get("move_allowance")
        if isinstance(allowance, dict):
            budget = int(allowance.get("amount", 0) or 0)
            target_id = str(allowance.get("target_id") or "")
            if not budget or abs(distance) > budget:
                return False
            if not target_id:
                return True
            toward = self._position(combat, target_id) - self._position(combat, actor_id)
            return bool(toward) and (distance > 0) == (toward > 0)
        crit = int(economy.get("crit_move", 0) or 0)
        if crit and abs(distance) <= crit:
            return True
        follow = economy.get("brutal_move")
        if isinstance(follow, dict):
            budget = int(follow.get("amount", 0) or 0)
            target_id = str(follow.get("target_id") or "")
            toward = (
                self._position(combat, target_id) - self._position(combat, actor_id)
                if target_id else 0
            )
            return (
                bool(budget and toward) and abs(distance) <= budget
                and (distance > 0) == (toward > 0)
            )
        return False
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

    ABILITY_CHECK_COSTS = frozenset({"action", "bonus_action", "none"})

    #: Wild Resurgence 的两种方向。
    WILD_RESURGENCE_DIRECTIONS = ("slot_to_use", "use_to_slot")

    LAND_WARD_DAMAGE = {
        "arid": "fire", "polar": "cold", "temperate": "lightning", "tropical": "poison",
    }
    LAND_AID_DICE = ((10, 2), (14, 3), (99, 4))

    def _land_terrain(self, character: dict[str, Any]) -> str:
        """The current land, from the slot F4 already writes."""

        try:
            stored = self.features.spell_grant_choices(character)
        except Exception:  # pragma: no cover - resolver already validated
            return ""
        for block in (stored or {}).values():
            if isinstance(block, dict) and block.get("terrain"):
                return str(block["terrain"])
        return ""

    def _land_aid_dice(self, character: dict[str, Any]) -> int:
        level = declaring_class_level(character, "druid")
        for ceiling, count in self.LAND_AID_DICE:
            if level < ceiling:
                return count
        return 4
    def _land_aid_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Land's Aid: one damage roll, one save per target, one healing roll."""

        actor_id = str(intent["actor_id"])
        character = self._canonical_for_actor(instance, actor_id)
        actor = self._actor_view(instance, combat, actor_id)
        origin = int(intent.get("origin", 0) or 0)
        inside = set(zone_members(
            combat, {"shape": "sphere", "size_ft": 10, "origin": origin},
            combat.get("positions") or {}, None,
        ))
        # 校验层已拒绝重复目标；这里再去一次重，"一个目标最多一段伤害"不依赖调用方。
        targets = list(dict.fromkeys(
            str(ref) for ref in intent.get("damage_targets") or [] if str(ref) in inside
        ))
        heal_target = str(intent.get("heal_target") or "")
        events: list[dict[str, Any]] = [
            {"type": "dnd2024.action.spent", "actor_id": actor_id,
             "resource": "action", "amount": 1},
            {"type": "dnd2024.class_resource.spent", "actor_id": actor_id,
             "resource_id": "wild_shape", "amount": 1},
        ]
        count = self._land_aid_dice(character)
        dc = int(actor.get("spell_save_dc", 0) or 0)
        if targets:
            dice = [int(rng.randint(1, 6)) for _ in range(count)]
            total = sum(dice)
            for target_id in targets:
                target = self._actor_view(instance, combat, target_id)
                save = self._saving_throw_check(
                    instance, combat, target, target_id, "con", dc, actor_id, events, rng,
                )
                events.append(save)
                damage = total if not save["success"] else max(0, total // 2)
                damage, resisted = self._resist_damage(target, damage, "necrotic")
                extra = {"resisted": True, "resistance": resisted} if resisted else {}
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": target_id,
                    "delta": -damage, "amount": damage, "damage_type": "necrotic",
                    "rolls": dice, **extra,
                })
        if heal_target:
            heal_dice = [int(rng.randint(1, 6)) for _ in range(count)]
            total_heal = sum(heal_dice)
            heal_view = self._actor_view(instance, combat, heal_target)
            blocked = "cannot_regain_hp" in (heal_view.get("conditions") or {})
            events.append({
                "type": "resource.changed", "resource": "hp", "target_id": heal_target,
                "delta": 0 if blocked else total_heal,
                "amount": 0 if blocked else total_heal,
                "healing": True, "rolls": heal_dice, "source": "lands_aid",
                **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
            })
        return events

    def _land_ward_type(self, character: dict[str, Any]) -> str:
        land = self._land_terrain(character)
        return self.LAND_WARD_DAMAGE.get(land, "")

    def _nature_sanctuary_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Nature's Sanctuary: place a 15-foot Cube, or move it as a Bonus Action."""

        actor_id = str(intent["actor_id"])
        # 区域 id 由服务端派生，不读意图里的值：reducer 按 id 存区域，客户端自报
        # 的 id 会覆盖别人的区域（另一位德鲁伊的 Sanctuary、法术的遮蔽区）。
        zone_id = sanctuary_zone_id(actor_id)
        mode = str(intent.get("mode") or "create")
        if mode == "move":
            return [
                {"type": "dnd2024.action.spent", "actor_id": actor_id,
                 "resource": "bonus_action", "amount": 1},
                {"type": "dnd2024.zone.moved",
                 "zone_id": zone_id,
                 "origin": int(intent.get("origin", 0) or 0)},
            ]
        character = self._canonical_for_actor(instance, actor_id)
        return [
            {"type": "dnd2024.action.spent", "actor_id": actor_id,
             "resource": "action", "amount": 1},
            {"type": "dnd2024.class_resource.spent", "actor_id": actor_id,
             "resource_id": "wild_shape", "amount": 1},
            {
                "type": "dnd2024.zone.created", "zone_id": zone_id,
                "kind": "sanctuary", "shape": "cube", "size_ft": 15,
                "level": "none", "origin": int(intent.get("origin", 0) or 0),
                "radius_ft": 7, "magical": False, "concentration": False,
                "rounds_left": 0,
                "owner_id": actor_id, "caster_id": actor_id,
                "beneficiaries": "allies", "half_cover": True,
                "resistance_from_owner": True,
                "expires_at_minute": self._logical_minutes(instance) + 1,
                # 一分钟也是十个战斗回合：战斗不推进世界时钟，只记分钟的话
                # 这个 Cube 会在整场战斗里一直在。
                "expires_at_round": int(combat.get("round", 0) or 0) + SANCTUARY_DURATION_ROUNDS,
                "ends_on_owner_incapacitated": True,
                "feature_id": "natures_sanctuary",
                "projection": "theater_1d",
                # 一分钟内不会发生长休换 land，所以把建立时的类型写在区域上，
                # 盟友在区域内按它取抗性；离开即失去。
                "ward_type": self._land_ward_type(character),
            },
        ]

    def _wild_resurgence_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """SRD p42：法术位 ↔ Wild Shape 使用次数的两个单向换算。

        两个方向各自记账：`slot_to_use` 每回合一次（economy 里的标记），
        `use_to_slot` 两次长休之间一次（`wild_resurgence` 资源）。**不允许**
        在同一声明里连续做两次——那不是换算，是免费资源。
        """

        actor_id = str(intent["actor_id"])
        direction = str(intent.get("direction") or "")
        if direction == "slot_to_use":
            slot_level = int(intent.get("slot_level") or 0)
            return [
                # exchange=True：这次消耗法术位不算"这一回合已经用槽施法"，
                # 否则 Wild Resurgence 会顺带吃掉本回合的施法额度。
                {"type": "dnd2024.spell.slot_spent", "actor_id": actor_id,
                 "slot_level": slot_level, "exchange": True},
                {"type": "dnd2024.class_resource.restored", "actor_id": actor_id,
                 "resource_id": "wild_shape", "amount": 1},
                {"type": "dnd2024.wild_resurgence.used", "actor_id": actor_id,
                 "direction": direction, "turn_limited": True},
            ]
        return [
            {"type": "dnd2024.class_resource.spent", "actor_id": actor_id,
             "resource_id": "wild_shape", "amount": 1},
            # 两次长休之间一次：必须真扣，否则这个方向可以无限续。
            {"type": "dnd2024.class_resource.spent", "actor_id": actor_id,
             "resource_id": "wild_resurgence", "amount": 1},
            {"type": "dnd2024.spell.font_slot_created", "actor_id": actor_id,
             "slot_level": 1, "source": "wild_resurgence"},
            {"type": "dnd2024.wild_resurgence.used", "actor_id": actor_id,
             "direction": direction, "turn_limited": False},
        ]

    def _nature_magician_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """SRD p43：消耗 N 次 Wild Shape，换一个 2N 级的临时法术位。"""

        actor_id = str(intent["actor_id"])
        uses = int(intent.get("uses") or 0)
        return [
            {"type": "dnd2024.class_resource.spent", "actor_id": actor_id,
             "resource_id": "wild_shape", "amount": uses},
            # 两次长休之间一次：与 Wild Resurgence 的 use_to_slot 分账。
            {"type": "dnd2024.class_resource.spent", "actor_id": actor_id,
             "resource_id": "archdruid", "amount": 1},
            {"type": "dnd2024.spell.font_slot_created", "actor_id": actor_id,
             "slot_level": 2 * uses, "source": "archdruid_nature_magician"},
        ]

    def _form_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """变成某个形态，或变回来。

        面板怎么合成不在这里——那是 ``forms.panel`` 的策略表。这里只做变形这个
        动作本身的三件事：付附赠动作、扣一次 Wild Shape、按德鲁伊等级给临时 HP。
        """

        actor_id = str(intent["actor_id"])
        character = self._canonical_for_actor(instance, actor_id)
        level = declaring_class_level(character, "druid")
        if str(intent.get("type")) == "form.revert":
            return [
                {"type": "dnd2024.action.spent", "actor_id": actor_id,
                 "resource": "bonus_action", "amount": 1},
                # 原因由引擎决定：主动变回就是 voluntary。存客户端给的字符串等于
                # 让它把"死亡""失能"写进权威事件流。
                {"type": "dnd2024.form.reverted", "actor_id": actor_id,
                 "reason": "voluntary"},
            ]
        entry = self._statblock_by_ref(str(intent.get("form_ref") or ""))
        if entry is None:  # pragma: no cover - validation guards this
            raise CombatIntentError("form reference is not available")
        state = wild_shape_state(
            entry,
            druid_level=level,
            beast_spells=self.features.has_feature(character, "beast_spells"),
            equipment_modes=_equipment_modes(intent),
            assumed_at_turn=int(combat.get("round", 0) or 0),
            assumed_at_minute=self._logical_minutes(instance),
        )
        events: list[dict[str, Any]] = []
        if isinstance(character.get("forms"), dict) and character["forms"].get("active"):
            # 再次变形先原子结束旧形态：不结束就换面板等于"免费续时"。
            events.append({"type": "dnd2024.form.reverted", "actor_id": actor_id,
                           "reason": "reshaped"})
        events.extend([
            {"type": "dnd2024.action.spent", "actor_id": actor_id,
             "resource": "bonus_action", "amount": 1},
            {"type": "dnd2024.class_resource.spent", "actor_id": actor_id,
             "resource_id": "wild_shape", "amount": 1},
            # Temporary HP 等于德鲁伊等级（SRD p43）；"保留较高值"由 reducer 决定，
            # 这里只声明这次变形给了多少。
            {"type": "resource.changed", "target_id": actor_id,
             "resource": "temp_hp", "amount": max(0, int(level))},
            {"type": "dnd2024.form.assumed", "actor_id": actor_id,
             "form": state.as_payload()},
        ])
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

    def _pay_cost(
        self, actor_id: str, capability: FeatureCapability, *, via_fleet_step: bool = False,
    ) -> list[dict[str, Any]]:
        """Charge one capability's declared price: its slot, then its resources.

        This is the only place a capability cost is paid.  Availability is a
        separate question answered before resolution from the same
        declaration, so an actor is never refused a price it would not pay and
        never charged one it was not asked for.  A Bonus Action paid with Fleet
        Step's free step spends that step instead of the slot.
        """

        events: list[dict[str, Any]] = []
        if capability.cost.slot and not via_fleet_step:
            events.append({
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": capability.cost.slot, "amount": 1,
            })
        if via_fleet_step:
            events.append({
                "type": "dnd2024.economy.granted", "actor_id": actor_id,
                "grants": {}, "flags": {"fleet_step_available": False},
            })
        for resource_cost in capability.cost.resources:
            if isinstance(resource_cost, SpellSlotCost):
                # This pays for a capability, not a spell cast.
                events.append({
                    "type": "dnd2024.spell.slot_spent", "actor_id": actor_id,
                    "slot_level": resource_cost.level, "exchange": True,
                })
                continue
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
                "resource_id": resource_cost.resource_id, "amount": resource_cost.amount,
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
        # 特性自身的使用次数（每长休）。资源条表达不了"这个特性还剩几次"：
        # 账记在 feature_usage，长休由 resting 统一清零。
        if capability.uses_per_long_rest:
            character = self._feature_character(actor)
            before = feature_usage_count(
                character, capability.feature_id, capability.feature_id,
                USAGE_SCOPE_LONG_REST,
            )
            if before >= capability.uses_per_long_rest:
                raise CombatIntentError(
                    "this feature has no use left until a long rest"
                )
            events.append({
                "type": "dnd2024.feature_usage.changed",
                "actor_id": actor_id,
                "owner": capability.feature_id,
                "feature_id": capability.feature_id,
                "scope": USAGE_SCOPE_LONG_REST,
                "before_count": before,
                "after_count": before + 1,
                "use_index": before + 1,
                "feature_kind": capability.feature_id,
            })
        via_fleet_step = bool(intent.get("via_fleet_step"))
        events.extend(
            self._pay_cost(actor_id, capability, via_fleet_step=via_fleet_step)
        )
        events.extend(self._fleet_step_grant_events(actor, capability))
        for action in capability.actions:
            context = KindContext(self, instance, combat, actor, intent, tuple(events))
            effect_events = KIND_HANDLERS[action.kind].resolve(capability, action, context, rng)
            events.extend(effect_events)
            if any(event["type"] == "dnd2024.combat.ended" for event in effect_events):
                break
        return events

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
            *self._turn_start_events(instance, combat, next_view, rng),
        ]

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
        self, instance: Any, combat: dict[str, Any],
        actor: dict[str, Any], rng: Any,
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
            *self._turn_start_aura_damage_events(instance, combat, actor),
        ]

    def _turn_start_aura_damage_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Holy Nimbus: an enemy that starts its turn inside takes radiant damage.

        只在敌人的回合开始、只在光环内、每个敌人每回合一次。友军与持有者
        自己不触发；伤害量是持有者的魅力调整 + 熟练，来源记在事件里，
        重放不会重复结算。
        """

        # 战斗开始的那一刻还没有 combat 聚合：首个角色的回合开始不结算
        # 光环伤害，等第一次 advance 之后才有位置与回合。
        if combat is None or str(actor.get("side") or "") != "enemy":
            return []
        events: list[dict[str, Any]] = []
        for row in self._projected_auras(instance, combat):
            definition = row["definition"]
            damage_type = str(
                definition.parameters.get("turn_start_aura_damage") or ""
            )
            if not damage_type:
                continue
            holder_id = str(row["holder_id"])
            if self._distance(combat, holder_id, str(actor["actor_id"])) > int(
                row["range_ft"]
            ):
                continue
            character = row["character"]
            ability = str(definition.parameters.get("aura_damage_ability") or "cha")
            abilities = character.get("abilities") or {}
            amount = (int(abilities.get(ability, 10) or 10) - 10) // 2
            # ``_feature_character`` 的切片不含 derived：等级在 build 里，
            # 熟练加值从等级算，别去读一个不存在的字段。
            level = int((character.get("build") or {}).get("level", 1) or 1)
            amount += proficiency_bonus(level)
            if amount <= 0:
                continue
            # 固定值的伤害同样要过抗性：抗性判定只有 _resist_damage 一份。
            damage, resistance = self._resist_damage(actor, amount, damage_type)
            event: dict[str, Any] = {
                "type": "resource.changed",
                "resource": "hp",
                "target_id": str(actor["actor_id"]),
                "delta": -damage,
                "amount": damage,
                "damage_type": damage_type,
                "feature_id": definition.id,
                "source_actor_id": holder_id,
                "reason": "holy_nimbus",
            }
            if resistance:
                event["resisted"] = True
                event["resistance"] = resistance
            events.append(event)
        return events

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
                    # 借机攻击会在结算后被改写成 reaction.spent；掷骰侧的准入
                    # （Escape the Horde）要能分辨这一击是不是借机攻击，所以
                    # 这个事实作为参数进结算链，不写成可被客户端申报的意图键。
                    opportunity = self._attack_events(
                        instance, combat, attack_intent, rng, opportunity=True,
                    )
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
            **(
                {
                    "distance": 0,
                    "movement_cost": spatial.move_cost_ft(
                        combat, str(movement["actor_id"]), movement["target_cell"],
                    ),
                    "target_cell": movement["target_cell"],
                }
                if "target_cell" in movement
                else {
                    "distance": int(movement["distance"]),
                    "movement_cost": abs(int(movement["distance"])),
                }
            ),
            "forced": False,
        })
        return events
