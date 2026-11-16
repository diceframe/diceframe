"""Intent validation stage for D&D 2024 combat."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from src.engine.player_control import is_ai_controlled
from src.rulesets.dnd2024.content.contracts import CREATURE_TYPES

from ..character.attunement import (
    AttunementError,
    attuned_refs,
    canonical_item_ref,
    inventory_ref_counts,
    item_requires_attunement,
    owned_ref_counts,
)
from ..features.models import ClassFeatureError
from ..features.resources import (
    OVERCHANNEL_FEATURE_ID,
    OVERCHANNEL_OWNER,
    OVERCHANNEL_SPELL_CLASS,
    feature_usage_count,
    resource_state,
)
from ..items.activation import (
    ItemActivationError,
    validate_activation_declaration,
    validate_consumable_declaration,
)
from ..items.charges import (
    ChargesError,
    charges_of,
    validate_charges_declaration,
    validate_destroy_declaration,
)
from ..spells.font_of_magic import (
    FontOfMagicError,
    create_slot_plan,
    declaring_class_level,
    parse_created_slot_costs,
    slot_to_points_plan,
)
from .primitives import (
    DICE_RE,
    INITIATIVE_CHOICES,
    INTENT_TYPES,
    RELENTLESS_RAGE_DECISION,
    UNARMED_STRIKE_REF,
    CombatIntentError,
    actor_kind as _actor_kind,
    canonical as _canonical,
    rider_attack_kind,
)
from .check_modes import condition_flags, owned_mark, vision_codes
from .check_windows import (
    CHECK_WINDOW_DECISION,
    DAMAGE_WINDOW_DECISION,
    LUCKY_RESOURCE,
)
from .readiness import required_encounter_player_ids
from .reaction_spells import COUNTER_SPELL_DECISION, HIT_REACTION_DECISION
from .triggered_spells import HIT_SPELL_DECISION, free_cast_resource, hit_spell_decision


def validate_enemy_profiles(enemies: Any) -> None:
    """Public enemy profile validation shared by combat.start, preset
    loading, and AI temporary-encounter generation.

    Only one implementation of the safety boundary exists; callers must not
    copy or relax it.
    """

    if not isinstance(enemies, list) or not 1 <= len(enemies) <= 50:
        raise CombatIntentError("combat requires 1 to 50 enemies")
    seen: set[str] = set()
    for enemy in enemies:
        if not isinstance(enemy, dict):
            raise CombatIntentError("enemy must be an object")
        enemy_id = str(enemy.get("id") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", enemy_id) or enemy_id in seen:
            raise CombatIntentError("enemy id is invalid or duplicated")
        seen.add(enemy_id)
        if len(str(enemy.get("name") or enemy_id)) > 120:
            raise CombatIntentError("enemy name is too long")
        if not isinstance(enemy.get("creature_type", ""), str) or (enemy.get("creature_type") and enemy["creature_type"] not in CREATURE_TYPES):
            raise CombatIntentError("enemy creature_type must be a canonical creature type")
        for field_name, minimum, maximum in (
            ("hp", 1, 10000), ("armor_class", 1, 40),
            ("speed", 0, 200), ("position", -10000, 10000),
            ("initiative_modifier", -20, 30),
            # 反击法术能力：0 表示没有该项能力，缺省即 0。
            ("counterspell_dc", 0, 30),
        ):
            default = (
                30 if field_name in {"speed", "position"}
                else 0 if field_name in {"initiative_modifier", "counterspell_dc"} else None
            )
            value = enemy.get(field_name, default)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise CombatIntentError(f"enemy {field_name} is invalid")
        attacks = enemy.get("attacks")
        if not isinstance(attacks, list) or not 1 <= len(attacks) <= 20:
            raise CombatIntentError("enemy requires 1 to 20 attacks")
        attack_ids: set[str] = set()
        for attack in attacks:
            if not isinstance(attack, dict):
                raise CombatIntentError("enemy attack profile is invalid")
            attack_id = str(attack.get("id") or "")
            formula = str(attack.get("damage") or "")
            dice_match = DICE_RE.fullmatch(formula)
            attack_bonus = attack.get("attack_bonus")
            normal_range = attack.get("range", 5)
            long_range = attack.get("long_range", normal_range)
            if (
                not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", attack_id)
                or attack_id in attack_ids
                or dice_match is None
                or isinstance(attack_bonus, bool)
                or not isinstance(attack_bonus, int)
                or not -20 <= attack_bonus <= 30
                or isinstance(normal_range, bool)
                or not isinstance(normal_range, int)
                or not 1 <= normal_range <= 1000
                or isinstance(long_range, bool)
                or not isinstance(long_range, int)
                or long_range < normal_range
                or long_range > 2000
            ):
                raise CombatIntentError("enemy attack profile is invalid")
            dice_count, dice_sides = int(dice_match.group(1)), int(dice_match.group(2))
            if not 1 <= dice_count <= 100 or not 2 <= dice_sides <= 1000:
                raise CombatIntentError("enemy attack damage dice are invalid")
            attack_ids.add(attack_id)


class CombatValidationMixin:
    """Validate combat declarations without resolving dice or changing state."""

    __slots__ = ()

    @staticmethod
    def _validate_enemies(enemies: Any) -> None:
        validate_enemy_profiles(enemies)

    @staticmethod
    def _validate_temporary_encounter_enemies(instance: Any, enemies: Any) -> None:
        # 延迟导入：director 层复用本模块的 validate_enemy_profiles，
        # 顶层导入会形成循环依赖。
        from src.rulesets.dnd2024.director.temporary_encounter import (
            validate_final_temporary_enemies,
        )
        try:
            validate_final_temporary_enemies(instance, enemies)
        except ValueError as exc:
            raise CombatIntentError(str(exc)) from None

    def _validate(self, instance: Any, intent: dict[str, Any]) -> None:
        if not isinstance(intent, dict):
            raise CombatIntentError("intent must be an object")
        intent_id = str(intent.get("intent_id") or "").strip()
        if not intent_id or len(intent_id) > 120:
            raise CombatIntentError("intent_id is required and must be at most 120 characters")
        intent_type = str(intent.get("type") or "")
        if intent_type not in INTENT_TYPES:
            raise CombatIntentError("intent type is not supported")
        state = self.initialize_state(instance)
        expected = intent.get("expected_version")
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise CombatIntentError("expected_version must be an integer")
        if expected != state["version"]:
            raise CombatIntentError(
                f"state version conflict: expected {expected}, current {state['version']}"
            )
        combat = state["combat"]
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        submitted_by = str(intent.get("submitted_by") or "")
        if intent_type in {"encounter.ready", "encounter.unready"}:
            request = state.get("encounter_request")
            if combat.get("status") == "active":
                raise CombatIntentError("combat is already active")
            if not isinstance(request, dict) or request.get("status") != "pending":
                raise CombatIntentError("there is no pending encounter to prepare for")
            if submitted_by not in instance.players or submitted_by == gm_uid:
                raise CombatIntentError("only a non-GM party member can change readiness")
            if submitted_by not in required_encounter_player_ids(instance):
                raise CombatIntentError("only an active human party member can change readiness")
            ready_ids = {
                str(item) for item in request.get("ready_player_ids") or [] if str(item)
            }
            if intent_type == "encounter.ready" and submitted_by in ready_ids:
                raise CombatIntentError("the player is already ready")
            if intent_type == "encounter.unready" and submitted_by not in ready_ids:
                raise CombatIntentError("the player is not ready")
            return
        if intent_type == "combat.start":
            if submitted_by != gm_uid:
                raise CombatIntentError("only the GM can start combat")
            if combat.get("status") == "active":
                raise CombatIntentError("combat is already active")
            if self.encounter_access.unprepared:
                # 活动冒险包内没有合法绑定：这是可解释的“尚未准备遭遇”，
                # 而不是让 GM 在通用目录里随手挑一个顶上。
                raise CombatIntentError(
                    "the active adventure has no bound encounter; declare a sandbox encounter explicitly"
                )
            if not self.encounter_access.can_start:
                raise CombatIntentError("the current story does not allow an encounter to start")
            declared_mode = str(intent.get("mode") or "")
            if declared_mode not in {"", "sandbox"}:
                # 客户端只能声明“这是 GM 主动准备的一场自由遭遇”，
                # 不能自己声明剧情模式或其它模式。
                raise CombatIntentError("encounter mode is invalid")
            preset_id = str(intent.get("encounter_preset_id") or "")
            guided_preset_id = (
                self.encounter_access.encounter_preset_id
                if self.encounter_access.mode == "story"
                else ""
            )
            if guided_preset_id:
                # 剧情已绑定遭遇：不接受在战斗开始页改用通用目录，也不接受
                # 用 "sandbox" 声明把这场战斗从冒险包里摘出去。
                if declared_mode == "sandbox":
                    raise CombatIntentError(
                        "the current story requires its assigned encounter preset"
                    )
                if preset_id != guided_preset_id:
                    raise CombatIntentError(
                        "the current story requires its assigned encounter preset"
                    )
                requested_instance_id = str(intent.get("encounter_instance_id") or "")
                if requested_instance_id and requested_instance_id != self.encounter_access.encounter_instance_id:
                    raise CombatIntentError("the story encounter identity is stale")
            elif preset_id and self._preset(preset_id) is None:
                raise CombatIntentError("encounter preset is not available")
            if not preset_id:
                self._validate_enemies(intent.get("enemies"))
                if bool(intent.get("temporary_encounter")):
                    # AI 临时遭遇确认：编辑并展开后的最终 payload 在权威入口
                    # 重走生成期同一套收紧范围与队伍安全上限。该标记只追加
                    # 校验，不存在“跳过校验”的语义，也不放宽既有边界。
                    self._validate_temporary_encounter_enemies(
                        instance, intent.get("enemies"),
                    )
            # With a preset, the resolver replaces any submitted enemy list
            # with the catalog entry. Enemy data is intentionally optional so
            # clients cannot smuggle a forged stat block into combat.
            self._validate_player_positions(instance, intent.get("player_positions"))
            self._validate_initiative_choices(instance, intent)
            return
        if combat.get("status") != "active":
            raise CombatIntentError("combat is not active")
        if any(row.get("kind") == HIT_SPELL_DECISION for row in combat.get("pending_decisions", [])) and intent_type not in {"cast_spell", "decision.resolve", "combat.message"}:
            raise CombatIntentError("resolve the melee hit spell decision first")
        if any(row.get("kind") == HIT_REACTION_DECISION for row in combat.get("pending_decisions", [])) and intent_type not in {"decision.resolve", "combat.message"}:
            raise CombatIntentError("waiting for the defender's reaction spell decision")
        if any(row.get("kind") == COUNTER_SPELL_DECISION for row in combat.get("pending_decisions", [])) and intent_type not in {"decision.resolve", "combat.message"}:
            raise CombatIntentError("resolve the counter spell decision first")
        if any(
            row.get("kind") in {CHECK_WINDOW_DECISION, DAMAGE_WINDOW_DECISION}
            for row in combat.get("pending_decisions", [])
        ) and intent_type not in {"decision.resolve", "combat.message"}:
            raise CombatIntentError("resolve the pending check window first")
        if any(
            row.get("kind") == RELENTLESS_RAGE_DECISION
            for row in combat.get("pending_decisions", [])
        ) and intent_type not in {"decision.resolve", "combat.message"}:
            raise CombatIntentError("resolve the relentless rage decision first")
        if intent_type == "combat.message":
            if submitted_by not in instance.players:
                raise CombatIntentError("only a party member can send a combat message")
            text = str(intent.get("text") or "").strip()
            if not text or len(text) > 500:
                raise CombatIntentError("combat message must contain 1 to 500 characters")
            return
        if intent_type == "combat.end":
            if submitted_by != gm_uid:
                raise CombatIntentError("only the GM can end combat")
            return
        if intent_type == "decision.resolve":
            decision = next(
                (
                    item for item in combat.get("pending_decisions", [])
                    if item.get("decision_id") == intent.get("decision_id")
                ),
                None,
            )
            if decision is None or intent.get("option") not in decision.get("options", []):
                raise CombatIntentError("pending decision or option is invalid")
            if decision.get("kind") in {CHECK_WINDOW_DECISION, DAMAGE_WINDOW_DECISION}:
                # A post-roll window may belong to someone who is not the
                # current actor, so ownership is exactly ``assigned_to``.
                if submitted_by != str(decision.get("assigned_to") or gm_uid):
                    raise CombatIntentError("only the window owner can resolve this check window")
                self._validate_check_window(instance, combat, decision, intent)
                return
            if decision.get("kind") == RELENTLESS_RAGE_DECISION:
                # 归零存活是本人（或托管该席位的 GM）的决定；GM 始终可以
                # 代为处理，与其它托管决策同一条边界。
                kind, raw_id = _actor_kind(str(decision.get("actor_id") or ""))
                expected_owner = (
                    gm_uid
                    if kind == "companion"
                    or (kind == "player" and is_ai_controlled(instance, raw_id))
                    else str(decision.get("assigned_to") or gm_uid)
                )
                if submitted_by != expected_owner and submitted_by != gm_uid:
                    raise CombatIntentError(
                        "only the assigned actor can resolve this survival decision"
                    )
                return
            if decision.get("kind") in {HIT_SPELL_DECISION, HIT_REACTION_DECISION}:
                kind, raw_id = _actor_kind(str(decision.get("actor_id") or ""))
                expected_owner = gm_uid if kind == "companion" or (kind == "player" and is_ai_controlled(instance, raw_id)) else decision.get("assigned_to")
                if submitted_by != expected_owner:
                    raise CombatIntentError("only the assigned actor can resolve this spell decision")
                if decision.get("kind") == HIT_REACTION_DECISION:
                    self._validate_hit_reaction(instance, combat, decision, intent)
                    return
            elif submitted_by != str(decision.get("assigned_to") or gm_uid):
                raise CombatIntentError("only the assigned GM can resolve this reaction")
            # 借机攻击的最终资格由 resolution 的 _reaction_eligible 复查：
            # 开窗后威胁者失能 / 禁反应 / 反应耗尽时清除决策并继续移动。
            # 这里若提前按反应预算拒绝，最终复查将永远不可达。
            return
        actor_id = str(intent.get("actor_id") or "")
        if actor_id != self._current_actor(combat):
            raise CombatIntentError("it is not this actor's turn")
        kind, raw_id = _actor_kind(actor_id)
        if not kind:
            raise CombatIntentError("actor_id is invalid")
        if kind == "player":
            if is_ai_controlled(instance, raw_id):
                # AI 托管席位由 GM/server automation authority 代为提交（与
                # companion 同一条自动链路）。席位一旦被真人接管就不再走这里。
                if submitted_by != gm_uid:
                    raise CombatIntentError(
                        "only the GM can submit intents for an AI-hosted character"
                    )
            elif submitted_by != raw_id:
                raise CombatIntentError("a player can submit intents only for their own character")
        if kind == "companion":
            # AI 队友由 GM/server automation authority 提交；玩家不能直接控制。
            if submitted_by != gm_uid:
                raise CombatIntentError("only the GM can submit companion intents")
            if intent_type == "death_save":
                raise CombatIntentError("companions do not make death saves")
        if kind == "enemy" and submitted_by != gm_uid:
            raise CombatIntentError("only the GM can submit enemy intents")
        actor = self._actor_view(instance, combat, actor_id)
        if actor["hp"] <= 0 and intent_type != "death_save":
            if not (intent_type == "end_turn" and "stable" in actor["conditions"]):
                raise CombatIntentError("an unconscious actor can only make a death save")
        if actor["hp"] > 0 and intent_type == "death_save":
            raise CombatIntentError("death saves are only available at 0 HP")
        if intent_type == "death_save" and "stable" in actor["conditions"]:
            raise CombatIntentError("a stable actor does not make death saves")
        if intent_type == "death_save":
            self._validate_lucky_spend(instance, intent, actor)
        # 失能族只禁"动作"（动作/附赠动作/反应），移动不是动作：纯 incapacitated
        # （睡眠术）的单位仍可移动，只有 speed_zero（麻痹/昏迷）才门控 move。
        # 读的是有效条件：被光环抑制的 Frightened 不能继续禁动作。
        conditions = self._effective_conditions(instance, combat, actor)
        flags = condition_flags(self.catalog.conditions, conditions)
        if flags["incapacitated"] and intent_type in {
            "attack", "cast_spell", "activate_item", "class_capability", "dash", "dodge",
            "disengage", "hide", "stabilize",
        }:
            raise CombatIntentError("an incapacitated actor cannot take actions")
        if flags["speed_zero"] and intent_type == "move":
            raise CombatIntentError("an actor with speed zero cannot move")
        economy = combat.get("economy") or {}
        if intent_type in {"attack", "dash", "dodge", "disengage", "hide", "stabilize"}:
            has_action = int(economy.get("action", 0) or 0) > 0 or int(
                economy.get("surge_action", 0) or 0
            ) > 0
            if intent_type == "attack":
                if not has_action and int(
                    economy.get("attacks_remaining", 0) or 0
                ) < 1:
                    raise CombatIntentError("the Attack action is no longer available")
            elif not has_action:
                raise CombatIntentError("the action has already been spent")
        if intent_type == "move":
            distance = intent.get("distance")
            if isinstance(distance, bool) or not isinstance(distance, int) or distance == 0:
                raise CombatIntentError("movement distance must be a non-zero integer")
            if abs(distance) > int(economy.get("movement", 0) or 0):
                raise CombatIntentError("movement exceeds the remaining speed")
            for source_id in self._restriction_sources(conditions, "cannot_approach_source"):
                before = self._distance(combat, actor["actor_id"], source_id)
                after = self._distance_after_move(
                    combat, actor["actor_id"], source_id, distance,
                )
                if after < before:
                    raise CombatIntentError(
                        "a frightened creature cannot move closer to the source of its fear"
                    )
        if intent_type == "attack":
            self._validate_attack(instance, combat, intent, actor)
        if intent_type == "end_turn":
            self._validate_self_restoration(actor, intent)
        if intent_type == "class_capability":
            self._validate_class_capability(instance, combat, intent, actor, economy)
        if intent_type == "cast_spell":
            self._validate_spell(instance, combat, intent, actor, economy)
        if intent_type == "activate_item":
            self._validate_item_activation(instance, combat, intent, actor, economy)
        if intent_type == "stabilize":
            target = self._actor_view(instance, combat, str(intent.get("target_id") or ""))
            if target["kind"] != "player" or target["hp"] > 0:
                raise CombatIntentError("stabilize requires a player at 0 HP")
            self._require_range(combat, actor_id, target["actor_id"], 5)

    def _validate_class_capability(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        """Authoritative gate for feature-provided combat capabilities.

        The UI only ever offers capabilities the server reported as available;
        this check is what actually protects the boundary, so a forged
        capability id, a forged target, or a stale resource state is rejected
        here even when the button was never rendered.
        """

        if actor.get("kind") not in {"player", "companion"}:
            raise CombatIntentError("class capabilities are not available to this actor")
        capability_id = str(intent.get("capability_id") or "")
        if not capability_id:
            raise CombatIntentError("capability_id is required")
        capability = self.features.declared_capability(
            self._feature_character(actor), capability_id,
        )
        if capability is not None:
            conditions = actor.get("conditions") or {}
            required = capability.requires_condition
            if required and required not in conditions:
                raise CombatIntentError(
                    f"{self.features.catalog.label(capability_id, capability_id)} requires "
                    f"the {self.features.catalog.label(required, required)} condition"
                )
            forbidden = capability.forbidden_condition
            if forbidden and forbidden in conditions:
                raise CombatIntentError(
                    f"{self.features.catalog.label(capability_id, capability_id)} cannot be "
                    f"used while {self.features.catalog.label(forbidden, forbidden)} is active"
                )
        choice = intent.get("open_hand_choice")
        if choice is not None:
            # Open Hand Technique 只在 Flurry of Blows 授予的攻击上成立；把它
            # 挂在普通徒手攻击上等于凭空多出一个免费 rider。
            if capability_id != "flurry_of_blows":
                raise CombatIntentError(
                    "open hand technique applies only to Flurry of Blows attacks"
                )
            character = self._feature_character(actor)
            if not self.features.has_feature(character, "open_hand_technique"):
                raise CombatIntentError(
                    "open hand technique is not available to this actor"
                )
            allowed = self.features.feature_value(
                character, "open_hand_technique", "flurry_hit_choices", [],
            ) or []
            if choice not in allowed:
                raise CombatIntentError(
                    "the declared open hand technique choice is not available"
                )
        views = {
            view.id: view
            for view in self._available_class_capabilities(
                actor, economy, include_unavailable=True,
            )
        }
        view = views.get(capability_id)
        if view is None:
            raise CombatIntentError("class capability is not available to this actor")
        if not view.available:
            raise CombatIntentError(view.blocked_reason or "class capability is not available")
        if capability is None:  # pragma: no cover - availability already proved it
            raise CombatIntentError("class capability is not available to this actor")
        has_unarmed_strike = any(
            action.kind == "unarmed_strike" for action in capability.actions
        )
        has_bardic_grant = any(
            action.kind == "bardic_grant" for action in capability.actions
        )
        if has_unarmed_strike:
            # 复用 canonical 徒手打击校验：目标必须存活且敌对、射程由同一份
            # 攻击档案决定，Combat 不自己算命中/伤害。
            rider_actor = deepcopy(actor)
            # The capability's own spend precedes every granted hit. Validate
            # rider affordability against that balance, not the original pool.
            for cost in capability.cost.resources:
                for pool in rider_actor.get("class_resources") or []:
                    if pool.get("id") == cost.resource_id:
                        pool["current"] = int(pool.get("current", 0)) - cost.amount
            self._validate_attack(instance, combat, {
                "target_id": str(intent.get("target_id") or ""),
                "weapon_ref": UNARMED_STRIKE_REF,
                **{key: intent[key] for key in (
                    "unarmed_damage_type", "rider_damage_type", "stunning_strike",
                ) if key in intent},
            }, rider_actor)
        elif has_bardic_grant:
            # 授予目标是另一位、能看见或听见你、且在 60 尺内的友方活体，并且
            # 对方此刻没有一枚未过期的骰。这些事实在校验期就能用同一组权威
            # 状态判定，所以非法提交在这里得到结构化拒绝，而不是等到结算时
            # 抛异常；resolution 仍会重验一遍（纵深防御，且它要写缩略视图）。
            target_id = str(intent.get("target_id") or "")
            target = self._actor_view(instance, combat, target_id)
            if target_id == actor["actor_id"]:
                raise CombatIntentError("you cannot grant Bardic Inspiration to yourself")
            if str(target.get("side") or "") != str(actor.get("side") or "") or target["hp"] <= 0:
                raise CombatIntentError("Bardic Inspiration targets a living ally")
            range_ft = self.features.feature_value(
                self._feature_character(actor), "bardic_inspiration", "range_ft", 0,
            )
            if not isinstance(range_ft, int) or range_ft <= 0:
                raise CombatIntentError("no Bardic Inspiration range is declared")
            self._require_range(combat, actor["actor_id"], target_id, range_ft)
            # "can see or hear you"：现有状态词表里能同时切断两条通道的只有
            # 隐身施予者 + 目盲承受者；deafened 未建模，因此不参与判定。
            if (
                "invisible" in (actor.get("conditions") or {})
                and "blinded" in (target.get("conditions") or {})
            ):
                raise CombatIntentError("the target can neither see nor hear you")
            if self._active_bardic_instance(instance, combat, target_id) is not None:
                raise CombatIntentError("the target already holds a Bardic Inspiration die")
        elif any(action.kind == "inspiration_slot_exchange" for action in capability.actions):
            # Font 的槽换次数：必须有已消耗次数、且该法术位仍然可用。
            character = self._canonical_actor(instance, actor)
            pool = dict(resource_state(character, "bardic_inspiration"))
            if int(pool.get("current", 0) or 0) >= int(pool.get("maximum", 0) or 0):
                raise CombatIntentError("no expended Bardic Inspiration use to regain")
            slot_level = intent.get("slot_level")
            if isinstance(slot_level, bool) or not isinstance(slot_level, int) or slot_level < 1:
                raise CombatIntentError("a spell slot level is required")
            slots = dict(
                ((character.get("spellcasting") or {}).get("class") or {}).get("slots_current") or {}
            )
            if int(slots.get(str(slot_level), 0) or 0) < 1:
                raise CombatIntentError("the spell slot is already spent")
        elif any(action.kind == "font_slot_to_points" for action in capability.actions):
            # 槽换点没有动作成本，所以"能不能换"全在这里判：源槽必须是真槽、
            # 目标点数不能超过上限（超了整笔拒绝，不静默截断）。
            try:
                slot_to_points_plan(
                    self._canonical_actor(instance, actor),
                    intent.get("slot_level"),
                )
            except FontOfMagicError as exc:
                raise CombatIntentError(str(exc)) from exc
        elif any(action.kind == "font_create_slot" for action in capability.actions):
            # 点换槽的五行走内容表；等级下限按"授予该特性的职业等级"算，
            # 不是角色总等级——兼职时两者不是一回事。
            character = self._canonical_actor(instance, actor)
            definition = self.features.feature_definition(capability.feature_id)
            class_id = str(getattr(definition, "class_id", "") or "")
            try:
                costs = parse_created_slot_costs(
                    self.features.feature_value(
                        character, capability.feature_id, "created_slot_costs", (),
                    )
                )
                create_slot_plan(
                    character, intent.get("slot_level"), costs,
                    declaring_class_level(character, class_id),
                )
            except FontOfMagicError as exc:
                raise CombatIntentError(str(exc)) from exc
        elif any(action.kind == "divine_spark" for action in capability.actions):
            self._validate_divine_spark(instance, combat, intent, actor, capability)
        elif any(action.kind == "turn_undead" for action in capability.actions):
            self._validate_turn_undead(instance, combat, intent, actor, capability)
        elif any(action.kind == "preserve_life" for action in capability.actions):
            self._validate_preserve_life(instance, combat, intent, actor, capability)
        elif any(action.kind == "lay_on_hands" for action in capability.actions):
            self._validate_lay_on_hands(instance, combat, intent, actor, capability)
        elif capability.requires_hostile_target:
            target = self._actor_view(
                instance, combat, str(intent.get("target_id") or ""),
            )
            if str(target.get("side") or "") == str(actor.get("side") or "") or target["hp"] <= 0:
                raise CombatIntentError("this capability requires a living hostile target")

    def _capability_range(
        self, actor: dict[str, Any], capability: Any, key: str, default: int,
    ) -> int:
        """The declared reach of one capability, in feet.

        Range comes from the feature declaration, never from the intent: a
        client that sends a friendlier number must not be able to widen it.
        The declaring feature is read (not the capability id) because the
        Channel Divinity options are capabilities *of* one feature, and their
        numbers live in that feature's parameters under a prefixed key.
        """

        range_ft = self.features.feature_value(
            self._feature_character(actor), capability.feature_id, key, default,
        )
        return int(range_ft) if isinstance(range_ft, int) and range_ft > 0 else default

    def _validate_divine_spark(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Divine Spark: another living creature, 30 feet, one of two modes."""

        target_id = str(intent.get("target_id") or "")
        if not target_id:
            raise CombatIntentError("Divine Spark requires a target")
        if target_id == str(actor["actor_id"]):
            raise CombatIntentError("Divine Spark cannot target yourself")
        target = self._actor_view(instance, combat, target_id)
        if target["hp"] <= 0:
            raise CombatIntentError("Divine Spark targets a living creature")
        self._require_range(
            combat, str(actor["actor_id"]), target_id,
            self._capability_range(actor, capability, "spark_target_range_ft", 30),
        )
        mode = str(intent.get("spark_mode") or "")
        if mode not in {"heal", "damage"}:
            raise CombatIntentError("Divine Spark must be declared as heal or damage")
        damage_type = str(intent.get("damage_type") or "")
        allowed = [
            str(kind) for kind in (
                self.features.feature_value(
                    self._feature_character(actor), capability.feature_id,
                    "spark_damage_types", ["necrotic", "radiant"],
                ) or []
            )
        ]
        if mode == "damage":
            if damage_type not in allowed:
                raise CombatIntentError(
                    "Divine Spark damage must be one of its declared damage types"
                )
        elif damage_type:
            raise CombatIntentError("Divine Spark healing does not choose a damage type")

    def _validate_turn_undead(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Turn Undead: a de-duplicated set of Undead inside the declared reach."""

        raw = intent.get("target_ids")
        if not isinstance(raw, list) or not raw:
            raise CombatIntentError("Turn Undead requires at least one Undead target")
        targets = [str(item) for item in raw]
        if len(set(targets)) != len(targets):
            raise CombatIntentError("Turn Undead targets must not repeat")
        limit = self._capability_range(actor, capability, "turn_target_range_ft", 30)
        for target_id in targets:
            target = self._actor_view(instance, combat, target_id)
            if target["hp"] <= 0:
                raise CombatIntentError("Turn Undead targets a living Undead")
            if "undead" not in str(target.get("creature_type") or "").lower():
                raise CombatIntentError("Turn Undead targets only Undead creatures")
            self._require_range(combat, str(actor["actor_id"]), target_id, limit)

    def _validate_preserve_life(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Preserve Life: a split of one budget, capped by each target's half HP.

        Both ceilings are checked here because both are facts the server owns:
        the budget is 5 x Cleric level for *this* action, and a creature can
        never be restored above half its Hit Point maximum by this feature.
        """

        rows = intent.get("allocations")
        if not isinstance(rows, list) or not rows:
            raise CombatIntentError("Preserve Life requires at least one allocation")
        character = self._feature_character(actor)
        per_level = self.features.feature_value(
            character, capability.id, "preserve_life_per_level", 5,
        )
        state = self.features.class_state(character)
        level = int(state[1]) if state else 0
        budget = int(per_level or 5) * level
        limit = self._capability_range(actor, capability, "target_range_ft", 30)
        seen: set[str] = set()
        total = 0
        for row in rows:
            if not isinstance(row, dict):
                raise CombatIntentError("each Preserve Life allocation must name a target")
            target_id = str(row.get("target_id") or "")
            amount = row.get("amount")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise CombatIntentError("a Preserve Life amount must be a non-negative integer")
            if target_id in seen:
                raise CombatIntentError("a Preserve Life target may appear only once")
            seen.add(target_id)
            target = self._actor_view(instance, combat, target_id)
            if target["hp"] <= 0:
                raise CombatIntentError("Preserve Life targets a living creature")
            self._require_range(combat, str(actor["actor_id"]), target_id, limit)
            maximum = int(target["max_hp"])
            if int(target["hp"]) * 2 > maximum:
                raise CombatIntentError(
                    "Preserve Life targets only Bloodied creatures"
                )
            ceiling = max(0, maximum // 2 - int(target["hp"]))
            if amount > ceiling:
                raise CombatIntentError(
                    "Preserve Life cannot restore a creature above half its Hit Points"
                )
            total += amount
        if total > budget:
            raise CombatIntentError(
                "the Preserve Life allocation exceeds this use's budget"
            )
        if total <= 0:
            raise CombatIntentError("Preserve Life requires a positive allocation")

    def _lay_on_hands_conditions(
        self, actor: dict[str, Any], capability_id: str,
    ) -> tuple[dict[str, int], int]:
        """Conditions this Paladin may pay to remove, and the price of each.

        Restoring Touch (level 14) is a second declaration over the same pool,
        so the allowed set is the union of what each owned feature names.  The
        base list is never edited in place: a character without the feature
        simply has the shorter set.
        """

        character = self._feature_character(actor)
        cost = int(self.features.feature_value(
            character, capability_id, "condition_cost", 5,
        ) or 5)
        allowed: dict[str, int] = {
            str(code): cost
            for code in (self.features.feature_value(
                character, "lay_on_hands", "removable_conditions", ["poisoned"],
            ) or [])
        }
        for code in self.features.feature_value(
            character, "restoring_touch", "removable_conditions", [],
        ) or []:
            allowed[str(code)] = int(self.features.feature_value(
                character, "restoring_touch", "condition_cost", 5,
            ) or 5)
        return allowed, cost

    def _validate_lay_on_hands(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Lay On Hands: one touched creature, a pool that pays, conditions owned."""

        target_id = str(intent.get("target_id") or "")
        if not target_id:
            raise CombatIntentError("Lay On Hands requires a target")
        target = self._actor_view(instance, combat, target_id)
        if "dead" in (target.get("conditions") or {}):
            raise CombatIntentError("Lay On Hands cannot restore a dead creature")
        self._require_range(
            combat, str(actor["actor_id"]), target_id,
            self._capability_range(actor, capability, "target_range_ft", 5),
        )
        amount = intent.get("healing_amount", 0)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise CombatIntentError("healing_amount must be a non-negative integer")
        raw_conditions = intent.get("remove_conditions") or []
        if not isinstance(raw_conditions, list):
            raise CombatIntentError("remove_conditions must be an array")
        conditions = [str(code) for code in raw_conditions]
        if len(set(conditions)) != len(conditions):
            raise CombatIntentError("a condition may be removed only once per use")
        allowed, _base_cost = self._lay_on_hands_conditions(actor, capability.id)
        held = target.get("conditions") or {}
        total = amount
        for condition_id in conditions:
            if condition_id not in allowed:
                raise CombatIntentError(
                    "this Paladin cannot remove that condition with Lay On Hands"
                )
            if condition_id not in held:
                # 目标身上没有这个状态时拒绝，而不是照扣 5 点却什么也没移除。
                raise CombatIntentError(
                    "the target does not have the condition being removed"
                )
            total += allowed[condition_id]
        if total <= 0:
            raise CombatIntentError(
                "Lay On Hands needs healing or a condition to remove"
            )
        character = self._canonical_actor(instance, actor)
        pool = resource_state(character, "lay_on_hands")
        if int(pool.get("current", 0) or 0) < total:
            raise CombatIntentError("the Lay On Hands pool is too small for that use")

    def _validate_two_handed(
        self, actor: dict[str, Any], intent: dict[str, Any], weapon: dict[str, Any],
    ) -> None:
        """两用武器双手持用的声明校验。

        ``equipment_refs`` 是携带清单而不是持握槽位：角色可以随身带备用
        武器而不握在另一只手上，因此只有盾牌（明确占据一只手）能确定阻挡
        双手持用。真正的副手槽位判定仍属于后续 slot map 改造；当前模型不会
        假装能区分"携带"与"正在持握"。
        """

        declared = intent.get("two_handed")
        if declared is None or declared is False:
            return
        if declared is not True:
            raise CombatIntentError("two_handed must be a boolean")
        if actor["kind"] not in {"player", "companion"}:
            # 敌方 stat block 没有两用概念，其攻击档也不声明该字段。
            raise CombatIntentError("two_handed is not available to this actor")
        if not weapon.get("damage_two_handed"):
            raise CombatIntentError("this weapon has no two-handed damage")
        for ref in actor.get("equipment_refs") or []:
            ref_id = str(ref).removeprefix("item:")
            item = self.bundle.get("item", ref_id) or {}
            if item.get("item_type") == "shield":
                raise CombatIntentError("a shield in the other hand blocks two-handed use")

    def _validate_lucky_spend(
        self, instance: Any, intent: dict[str, Any], actor: dict[str, Any],
    ) -> None:
        """Declaring Lucky's advantage face requires an unspent point in hand.

        Ownership of the pool *is* the feat requirement (the resource is only
        synced for characters with ``feat:lucky``), so an absent pool is
        rejected exactly like an empty one.
        """

        spend_luck = intent.get("spend_luck")
        if spend_luck is None or spend_luck is False:
            return
        if spend_luck is not True:
            raise CombatIntentError("spend_luck must be a boolean")
        if not self._window_pool_available(
            instance, str(actor["actor_id"]), LUCKY_RESOURCE,
        ):
            raise CombatIntentError("lucky pool is empty")

    def _validate_self_restoration(
        self, actor: dict[str, Any], intent: dict[str, Any],
    ) -> None:
        """The turn-end removal is a choice, and a choice has to be legal.

        Absent means "not used": the engine never picks a condition for the
        player, and it never removes two.  A declared value must name one of
        the feature's own conditions *and* be one the actor actually has -- a
        stale client must not be able to delete a condition that is not there,
        nor one that arrived after the panel was rendered.
        """

        if "self_restoration_condition" not in intent:
            return
        requested = intent["self_restoration_condition"]
        if requested is None:
            return
        chosen = self._self_restoration_choice(actor)
        if chosen is None:
            raise CombatIntentError(
                "this actor has no feature that removes a condition at turn end"
            )
        feature_id, allowed = chosen
        if not isinstance(requested, str) or not requested:
            raise CombatIntentError("self_restoration_condition must be a condition id")
        if requested not in allowed:
            raise CombatIntentError(
                f"{feature_id} cannot remove the {requested} condition"
            )
        if requested not in (actor.get("conditions") or {}):
            raise CombatIntentError(f"the actor does not have the {requested} condition")

    def _restriction_sources(
        self, conditions: dict[str, Any], flag: str,
    ) -> list[str]:
        """Actor ids named by the conditions that declare ``flag``.

        A condition such as Frightened restricts behaviour *towards its own
        source*, so the restriction is data on the stored entry, not a branch
        on the condition id.  An entry without a recorded source cannot be
        scoped to anyone and is therefore not enforced -- blocking every
        direction would be a worse answer than a restriction that visibly does
        nothing, and the settlement paths always record the source.
        """

        sources: list[str] = []
        for condition_id, entry in self.catalog.conditions.items():
            if not entry.get(flag) or condition_id not in conditions:
                continue
            stored = conditions.get(condition_id)
            source = (
                str(stored.get("source_actor_id") or "")
                if isinstance(stored, dict) else ""
            )
            if source and source not in sources:
                sources.append(source)
        return sources

    def _validate_attack(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], actor: dict[str, Any],
    ) -> None:
        target_id = str(intent.get("target_id") or "")
        target = self._actor_view(instance, combat, target_id)
        # 敌我判断基于 side：companion/player 同为 party，互不视为合法攻击目标。
        actor_side = str(actor.get("side") or "")
        target_side = str(target.get("side") or "")
        if target_side == actor_side or target["hp"] <= 0:
            raise CombatIntentError("attack target must be a living hostile actor")
        conditions = self._effective_conditions(instance, combat, actor)
        if target_id in self._restriction_sources(conditions, "cannot_attack_source"):
            raise CombatIntentError(
                "a charmed creature cannot attack the creature that charmed it"
            )
        weapon = self._declared_attack(actor, intent)
        if weapon is None:
            # 一个 lookup 同时覆盖装备武器、徒手打击与敌人 attack profile：
            # 客户端无法声明角色并不真正拥有的攻击档案。
            raise CombatIntentError("attack profile is not available to the actor")
        self._validate_ammunition(instance, actor, weapon)
        self._validate_two_handed(actor, intent, weapon)
        self._validate_lucky_spend(instance, intent, actor)
        distance = self._distance(combat, actor["actor_id"], target_id)
        normal_range = int(weapon.get("thrown_range") or weapon.get("range", 5))
        long_range = int(weapon.get("long_range") or normal_range)
        if distance > long_range:
            raise CombatIntentError(f"target is out of range ({distance} > {long_range} feet)")
        self._validate_hit_riders(actor, intent, combat)
        self._validate_attack_state_options(instance, combat, intent, actor, weapon, distance)

    def _validate_attack_state_options(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], weapon: dict[str, Any], distance: int,
    ) -> None:
        """Reject a pre-roll declaration the actor cannot legally make now.

        Three facts are re-derived from the authoritative state rather than
        trusted from the panel: whether this is the first attack roll of the
        turn (Reckless), whether any Disadvantage source exists at all
        (Brutal), and whether the once-per-turn budget is already spent.  A
        submitted declaration that fails any of them must fail the intent --
        silently ignoring it would let the client believe it had Advantage
        while the roll was a straight d20.
        """

        reckless = intent.get("reckless_attack")
        brutal = intent.get("brutal_strike")
        effects = intent.get("brutal_strike_effects")
        if reckless is None and brutal is None and effects is None:
            return
        character = self._feature_character(actor)
        actor_id = str(actor["actor_id"])

        if reckless is not None:
            if not isinstance(reckless, bool):
                raise CombatIntentError("reckless_attack must be a boolean")
            if reckless:
                if not self.features.has_feature(character, "reckless_attack"):
                    raise CombatIntentError(
                        "reckless attack is not available to this actor"
                    )
                condition = str(self.features.feature_value(
                    character, "reckless_attack", "self_condition", "",
                ) or "")
                if condition and condition in (actor.get("conditions") or {}):
                    raise CombatIntentError("this actor is already attacking recklessly")
                if str((combat.get("economy") or {}).get("actor_id") or "") != actor_id:
                    # "When you make your first attack roll on your turn": an
                    # Opportunity Attack happens on someone else's turn.
                    raise CombatIntentError(
                        "reckless attack can only be declared on your own turn"
                    )
                if self._attack_roll_count(combat, actor_id) > 0:
                    raise CombatIntentError(
                        "reckless attack must be declared before the first attack roll"
                    )

        if brutal is not None:
            if not isinstance(brutal, bool):
                raise CombatIntentError("brutal_strike must be a boolean")
            if brutal:
                if not self.features.has_feature(character, "brutal_strike"):
                    raise CombatIntentError(
                        "brutal strike is not available to this actor"
                    )
                ability = str(self.features.feature_value(
                    character, "brutal_strike", "ability", "",
                ) or "")
                if ability and self.attack_ability(actor, weapon) != ability:
                    raise CombatIntentError(
                        "brutal strike requires a Strength-based attack"
                    )
                required = str(self.features.feature_value(
                    character, "brutal_strike", "requires_declaration", "",
                ) or "")
                reckless_condition = str(self.features.feature_value(
                    character, required, "self_condition", "",
                ) or "") if required else ""
                declared_now = bool(reckless) or (
                    reckless_condition in (actor.get("conditions") or {})
                )
                if not declared_now:
                    raise CombatIntentError(
                        "brutal strike requires reckless attack this turn"
                    )
                if self._feature_used_this_turn(combat, actor_id, "brutal_strike"):
                    raise CombatIntentError("brutal strike was already used this turn")
                # "The chosen attack roll mustn't have Disadvantage." 相消不算：
                # 只要来源里存在任何一份 Disadvantage 就不合格，否则"劣势被
                # 另一份优势抵消"会变成 Brutal 的通行证。
                check_mode = self._attack_check_mode(
                    instance, combat, actor, self._actor_view(
                        instance, combat, str(intent["target_id"]),
                    ), self.attack_range_disadvantage(weapon, distance),
                    ability=self.attack_ability(actor, weapon),
                    declared_reckless=bool(reckless),
                    spend_luck=bool(intent.get("spend_luck")),
                )
                if any(
                    str(row.get("mode") or "") == "disadvantage"
                    for row in check_mode["sources"]
                ):
                    raise CombatIntentError(
                        "brutal strike is not available while the attack has disadvantage"
                    )
                self._validate_brutal_strike_effects(
                    character, intent.get("brutal_strike_effects"),
                )
        elif effects:
            # Options without the declaration are a forged submission: ignoring
            # them would let a client believe a push was coming while the roll
            # was a plain one.
            raise CombatIntentError(
                "brutal strike effects require a brutal strike declaration"
            )

    def _validate_brutal_strike_effects(
        self, character: Any, submitted: Any,
    ) -> None:
        """Every chosen option must be one this level unlocks, at most ``cap``."""

        allowed = [str(value) for value in self.features.feature_value(
            character, "brutal_strike", "effect_options", [],
        ) or []]
        if self.features.has_feature(character, "improved_brutal_strike"):
            allowed.extend(
                str(value) for value in self.features.feature_value(
                    character, "improved_brutal_strike",
                    "additional_effect_options", [],
                ) or []
            )
        state = self.features.class_state(character)
        level = int(state[1]) if state else 0
        cap = int(self.features.feature_value(
            character, "brutal_strike", "effects_per_use", 0,
        ) or 0)
        if level >= int(self.features.feature_value(
            character, "brutal_strike", "upgrade_level", 0,
        ) or 0):
            cap = int(self.features.feature_value(
                character, "brutal_strike", "effects_per_use_upgrade", cap,
            ) or cap)
        if submitted is None:
            return
        if not isinstance(submitted, list) or any(
            not isinstance(value, str) for value in submitted
        ):
            raise CombatIntentError("brutal strike effects must be a list of ids")
        if len(submitted) > cap:
            raise CombatIntentError(
                f"brutal strike allows at most {cap} effect(s) at this level"
            )
        if len(set(submitted)) != len(submitted):
            raise CombatIntentError(
                "brutal strike cannot apply the same effect twice"
            )
        unavailable = sorted(set(submitted) - set(allowed))
        if unavailable:
            raise CombatIntentError(
                f"brutal strike effect is not available: {unavailable[0]}"
            )

    @staticmethod
    def _attack_roll_count(combat: dict[str, Any], actor_id: str) -> int:
        """Attack rolls this actor has already made in the current turn."""

        record = (combat.get("attack_rolls") or {}).get(str(actor_id))
        if not isinstance(record, dict):
            return 0
        if int(record.get("round", -1)) != int(combat.get("round", 0)):
            return 0
        if int(record.get("turn_index", -1)) != int(combat.get("turn_index", 0)):
            return 0
        return int(record.get("count", 0) or 0)

    def _validate_hit_riders(
        self, actor: dict[str, Any], intent: dict[str, Any], combat: dict[str, Any],
    ) -> None:
        """Reject a rider choice the actor cannot legally make right now.

        Every rejection happens here rather than being silently ignored at
        resolve time: a submitted ``stunning_strike`` the actor cannot pay for
        must fail the intent, not quietly produce a plain hit.
        """

        unarmed_type = intent.get("unarmed_damage_type")
        rider_type = intent.get("rider_damage_type")
        stunning = intent.get("stunning_strike")
        if intent.get("open_hand_choice") is not None:
            raise CombatIntentError(
                "open hand technique applies only to Flurry of Blows attacks"
            )
        if unarmed_type is None and rider_type is None and stunning is None:
            return
        weapon = self._declared_attack(actor, intent)
        if weapon is None:
            raise CombatIntentError("attack profile is not available to the actor")
        character = self._feature_character(actor)

        if unarmed_type is not None:
            if not weapon.get("unarmed"):
                raise CombatIntentError(
                    "an unarmed damage type applies to Unarmed Strikes only"
                )
            allowed = self.features.feature_value(
                character, "empowered_strikes", "unarmed_damage_type_choices", [],
            ) or []
            if unarmed_type not in allowed:
                raise CombatIntentError(
                    "the declared unarmed damage type is not available"
                )

        if rider_type is not None:
            allowed_types: set[str] = set()
            kind = rider_attack_kind(weapon)
            for feature_id in ("divine_strike", "primal_strike"):
                if not self.features.has_feature(character, feature_id):
                    continue
                kinds = {
                    str(value) for value in self.features.feature_value(
                        character, feature_id, "attack_kinds", [],
                    ) or []
                }
                if kind not in kinds and not (
                    kind != "unarmed_strike" and "weapon" in kinds
                ):
                    # 这一击不在该 rider 的 attack_kinds 里：声明它就是声明了一件
                    # 结算期会静默丢掉的事，必须当场拒绝。
                    continue
                values = self.features.feature_value(
                    character, feature_id, "damage_types", [],
                ) or []
                allowed_types.update(str(value) for value in values)
            if rider_type not in allowed_types:
                raise CombatIntentError(
                    "the declared rider damage type is not available"
                )

        if stunning is not None:
            if not isinstance(stunning, bool):
                raise CombatIntentError("stunning_strike must be a boolean")
            if not stunning:
                return
            if not self.features.has_feature(character, "stunning_strike"):
                raise CombatIntentError("stunning strike is not available to this actor")
            if not (weapon.get("unarmed") or weapon.get("ability_choice")):
                raise CombatIntentError(
                    "stunning strike requires a Monk weapon or an Unarmed Strike"
                )
            if self._feature_used_this_turn(
                combat, str(actor["actor_id"]), "stunning_strike",
            ):
                raise CombatIntentError("stunning strike was already used this turn")
            cost = int(
                self.features.feature_value(
                    character, "stunning_strike", "focus_cost", 0,
                ) or 0
            )
            current = 0
            for entry in actor.get("class_resources") or []:
                if isinstance(entry, dict) and str(entry.get("id")) == "focus_points":
                    current = int(entry.get("current", 0) or 0)
            if cost and current < cost:
                raise CombatIntentError(
                    "not enough Focus Points for a stunning strike"
                )

    def _canonical_actor(self, instance: Any, actor: dict[str, Any]) -> dict[str, Any]:
        if actor["kind"] == "player":
            return _canonical(instance.get_character_sheet(actor["id"]))
        if actor["kind"] == "companion":
            return self._companion(instance, actor["id"]).get("ruleset_character") or {}
        raise CombatIntentError("item activation is not available to this actor")

    def _activation_payload(self, instance: Any, actor: dict[str, Any]) -> Any:
        if actor["kind"] == "player":
            return instance.get_character_sheet(actor["id"])
        return self._companion(instance, actor["id"])

    def _validate_ammunition(
        self, instance: Any, actor: dict[str, Any], weapon: dict[str, Any],
    ) -> None:
        """A ranged profile that declares ammunition needs a carried copy.

        Companions have no live backpack in this slice; their ranged attacks
        keep the pre-D3 behaviour instead of inventing an inventory authority.
        """

        raw = weapon.get("ammunition_item_ref")
        if not raw or actor["kind"] != "player":
            return
        try:
            item_ref = canonical_item_ref(raw)
        except AttunementError as exc:
            raise CombatIntentError(str(exc)) from None
        payload = instance.get_character_sheet(actor["id"])
        if inventory_ref_counts(payload).get(item_ref, 0) < 1:
            raise CombatIntentError("no ammunition for this attack")

    def _validate_item_activation(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        if actor["kind"] not in {"player", "companion"}:
            raise CombatIntentError("item activation is not available to this actor")
        try:
            item_ref = canonical_item_ref(intent.get("item_ref"))
        except AttunementError as exc:
            raise CombatIntentError(str(exc)) from None
        item = self.bundle.get("item", item_ref.removeprefix("item:"))
        if not isinstance(item, dict):
            raise CombatIntentError("item is not defined in the active bundle")
        try:
            declaration = validate_activation_declaration(item)
            maximum = validate_charges_declaration(item)
            consumable = validate_consumable_declaration(item)
            # 销毁判定在全部效果之后才掷骰；声明有误要在这里拒绝，
            # 不能等结算到一半才抛出。
            validate_destroy_declaration(item)
        except (ItemActivationError, ChargesError) as exc:
            raise CombatIntentError(str(exc)) from None
        if declaration is None:
            raise CombatIntentError("item cannot be activated")
        if consumable and actor["kind"] == "companion":
            # companion 还没有权威背包与原子扣件路径：在动作成本、充能和
            # consumed 事件之前拒绝，避免把 quantity 消耗留在玩家事件链之外。
            raise CombatIntentError(
                "companion consumable activation requires an authoritative backpack"
            )
        cost = declaration["cost"]
        if int(economy.get(cost, 0) or 0) < 1:
            raise CombatIntentError(f"the {cost.replace('_', ' ')} has already been spent")
        canonical = self._canonical_actor(instance, actor)
        payload = self._activation_payload(instance, actor)
        # Consumables are removed from the live inventory when the batch is
        # applied.  Equipment rows and canonical equipment refs are not a
        # backpack take path, so a consumable must prove an inventory copy here
        # instead of passing validation and failing after the roll.
        counted = inventory_ref_counts(payload) if consumable else owned_ref_counts(payload)
        owned = counted.get(item_ref, 0)
        if owned < 1:
            raise CombatIntentError("item is not carried")
        if item_requires_attunement(self.bundle, item_ref) and item_ref not in attuned_refs(canonical):
            raise CombatIntentError("item requires attunement")
        charge_cost = declaration.get("charge_cost")
        if maximum is not None:
            if charge_cost is None:
                raise CombatIntentError("charged item activation requires charge_cost")
            if owned > 1:
                raise CombatIntentError("cannot activate a charged item carried in multiple copies")
            current = charges_of(canonical, item_ref)
            if current is None:
                raise CombatIntentError("item charges are not initialized")
            if current < int(charge_cost):
                raise CombatIntentError("not enough charges")
        elif charge_cost is not None:
            raise CombatIntentError("item has no charges")
        if consumable and owned < 1:
            raise CombatIntentError("item is not carried")
        target_id = str(intent.get("target_id") or actor["actor_id"])
        if declaration["target"] == "self" and target_id != actor["actor_id"]:
            raise CombatIntentError("this item can only target its user")
        target = self._actor_view(instance, combat, target_id)
        if target["kind"] not in {"player", "companion"}:
            raise CombatIntentError("item targets a party member or the user")
        if str(target.get("side") or "") != str(actor.get("side") or ""):
            raise CombatIntentError("item targets a party member or the user")
        if "dead" in (target.get("conditions") or {}):
            raise CombatIntentError("item cannot target a dead actor")
        # Using an item on an ally is a touch-range action: the user must be
        # within 5 feet.  Self-use has no separate distance check.
        if target_id != actor["actor_id"]:
            distance = self._distance(combat, actor["actor_id"], target_id)
            if distance > 5:
                raise CombatIntentError(
                    f"ally is out of item range ({distance} > 5 feet)"
                )

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
        if effect["mode"] == "reaction_defense":
            raise CombatIntentError("this spell is cast as a reaction when you are hit")
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
        cost = "bonus_action" if str(spell["casting_time"]).startswith("Bonus Action") else "action"
        if int(economy.get(cost, 0) or 0) < 1:
            raise CombatIntentError(f"the {cost.replace('_', ' ')} has already been spent")
        slot_level = intent.get("slot_level", spell["level"])
        if isinstance(slot_level, bool) or not isinstance(slot_level, int):
            raise CombatIntentError("slot_level must be an integer")
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
        if upcast_targets:
            required_count += upcast_targets * max(0, slot_level - int(spell["level"]))
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
            elif target_side == actor_side and effect["mode"] != "dispel":
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
                    self._spell_effective_range(actor, spell, effect),
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

    def _validate_initiative_choices(self, instance: Any, intent: dict[str, Any]) -> None:
        """Validate the Initiative-time declarations before any dice are rolled.

        A declaration is a yes/no for an optional feature.  It cannot name
        another player, cannot spell out an amount, and cannot be made when the
        feature is missing or its Long Rest charge is already spent -- all three
        would otherwise turn into a free resource on the next replay.
        """

        raw = intent.get("initiative_choices")
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise CombatIntentError("initiative choices must be an object")
        for actor_id, entry in raw.items():
            actor = str(actor_id)
            kind, raw_id = _actor_kind(actor)
            if kind != "player" or raw_id not in instance.players:
                raise CombatIntentError("initiative choices can only name a player")
            if not isinstance(entry, dict):
                raise CombatIntentError("an initiative choice entry must be an object")
            unknown = sorted(set(entry) - INITIATIVE_CHOICES)
            if unknown:
                raise CombatIntentError(f"unknown initiative choice: {unknown[0]}")
            for value in entry.values():
                if value is not True and value is not False:
                    raise CombatIntentError("initiative choices only accept true or false")
            character = _canonical(instance.get_character_sheet(raw_id))
            for choice in sorted(INITIATIVE_CHOICES):
                if entry.get(choice) is not True:
                    continue
                if not self.features.has_feature(character, choice):
                    raise CombatIntentError(f"this character cannot use {choice}")
                if int(resource_state(character, choice).get("current", 0) or 0) < 1:
                    raise CombatIntentError(f"{choice} is already spent")

    @staticmethod
    def _validate_player_positions(instance: Any, positions: Any) -> None:
        if positions is None:
            return
        if not isinstance(positions, dict) or len(positions) > len(instance.players):
            raise CombatIntentError("player_positions must map current players to positions")
        for uid, value in positions.items():
            if uid not in instance.players:
                raise CombatIntentError("player_positions contains an unknown player")
            if isinstance(value, bool) or not isinstance(value, int) or not -10000 <= value <= 10000:
                raise CombatIntentError("player position is invalid")
