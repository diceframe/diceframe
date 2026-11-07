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
from .primitives import (
    DICE_RE,
    INTENT_TYPES,
    UNARMED_STRIKE_REF,
    CombatIntentError,
    actor_kind as _actor_kind,
    canonical as _canonical,
)
from .check_modes import condition_flags
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
        flags = condition_flags(self.catalog.conditions, actor["conditions"])
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
        if intent_type == "attack":
            self._validate_attack(instance, combat, intent, actor)
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
        if has_unarmed_strike:
            # 复用 canonical 徒手打击校验：目标必须存活且敌对、射程由同一份
            # 攻击档案决定，Combat 不自己算命中/伤害。
            self._validate_attack(instance, combat, {
                "target_id": str(intent.get("target_id") or ""),
                "weapon_ref": UNARMED_STRIKE_REF,
            }, actor)
        elif capability.requires_hostile_target:
            target = self._actor_view(
                instance, combat, str(intent.get("target_id") or ""),
            )
            if str(target.get("side") or "") == str(actor.get("side") or "") or target["hp"] <= 0:
                raise CombatIntentError("this capability requires a living hostile target")

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
                if free_cast_resource(actor, spell_ref) is None:
                    raise CombatIntentError("the free spell cast is not available")
            elif economy.get("slot_spell_cast"):
                raise CombatIntentError("only one spell slot can be expended on a turn")
            if slot_level != 0 and slot_level < spell["level"]:
                raise CombatIntentError("slot_level is lower than the spell level")
            if slot_level != 0 and int(actor["slots"].get(str(slot_level), 0) or 0) < 1:
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
                self._require_range(combat, actor["actor_id"], target_id, int(effect["range"]))
        damage_choices = effect.get("damage_type_choice")
        if damage_choices and intent.get("damage_type") not in damage_choices:
            raise CombatIntentError("spell damage_type choice is required")
        resist_choices = effect.get("resist_choice")
        if resist_choices:
            if intent.get("resist_type") not in resist_choices:
                raise CombatIntentError("spell resist_type choice is required")
        elif intent.get("resist_type") is not None:
            raise CombatIntentError("this spell does not take a resist_type")
        maximize = intent.get("maximize_damage")
        if maximize is not None and not isinstance(maximize, bool):
            raise CombatIntentError("maximize_damage must be a boolean")
        if maximize:
            character = self._feature_character(actor)
            max_slot = int(self.features.feature_value(
                character, "overchannel", "max_slot_level", 5,
            ) or 0)
            if not self.features.has_feature(character, "overchannel") or max_slot < 1:
                raise CombatIntentError("overchannel is required to maximize damage")
            if effect["mode"] not in {"spell_attack", "saving_throw", "automatic_damage"} \
                    or not effect.get("damage"):
                raise CombatIntentError("this spell does not deal damage")
            if not 1 <= int(slot_level) <= max_slot:
                raise CombatIntentError("maximized casting requires a spell slot of level 1-5")
        if effect["mode"] == "teleport":
            distance = intent.get("teleport_distance")
            if isinstance(distance, bool) or not isinstance(distance, int) or distance == 0:
                raise CombatIntentError("teleport distance must be a non-zero integer")
            if abs(distance) > int(effect["range"]):
                raise CombatIntentError("teleport distance exceeds the spell range")
        elif intent.get("teleport_distance") is not None:
            raise CombatIntentError("teleport distance is not valid for this spell")

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
