"""Intent validation stage for D&D 2024 combat."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .primitives import (
    DICE_RE,
    INTENT_TYPES,
    CombatIntentError,
    actor_kind as _actor_kind,
    canonical as _canonical,
)


class CombatValidationMixin:
    """Validate combat declarations without resolving dice or changing state."""

    __slots__ = ()

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
            # With a preset, the resolver replaces any submitted enemy list
            # with the catalog entry. Enemy data is intentionally optional so
            # clients cannot smuggle a forged stat block into combat.
            self._validate_player_positions(instance, intent.get("player_positions"))
            return
        if combat.get("status") != "active":
            raise CombatIntentError("combat is not active")
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
            if submitted_by != gm_uid:
                raise CombatIntentError("only the assigned GM can resolve this reaction")
            decision = next(
                (
                    item for item in combat.get("pending_decisions", [])
                    if item.get("decision_id") == intent.get("decision_id")
                ),
                None,
            )
            if decision is None or intent.get("option") not in decision.get("options", []):
                raise CombatIntentError("pending decision or option is invalid")
            if intent.get("option") == "resolve":
                threat_ids = list(decision.get("threat_actor_ids") or [])
                if not threat_ids or int(combat.get("reactions", {}).get(threat_ids[0], 0)) < 1:
                    raise CombatIntentError("the threatening actor has no reaction available")
            return
        actor_id = str(intent.get("actor_id") or "")
        if actor_id != self._current_actor(combat):
            raise CombatIntentError("it is not this actor's turn")
        kind, raw_id = _actor_kind(actor_id)
        if not kind:
            raise CombatIntentError("actor_id is invalid")
        if kind == "player" and submitted_by != raw_id:
            raise CombatIntentError("a player can submit intents only for their own character")
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
        economy = combat.get("economy") or {}
        if intent_type in {"attack", "dash", "dodge", "disengage", "stabilize"}:
            if intent_type == "attack":
                if int(economy.get("action", 0) or 0) < 1 and int(
                    economy.get("attacks_remaining", 0) or 0
                ) < 1:
                    raise CombatIntentError("the Attack action is no longer available")
            elif int(economy.get("action", 0) or 0) < 1:
                raise CombatIntentError("the action has already been spent")
        if intent_type == "move":
            distance = intent.get("distance")
            if isinstance(distance, bool) or not isinstance(distance, int) or distance == 0:
                raise CombatIntentError("movement distance must be a non-zero integer")
            if abs(distance) > int(economy.get("movement", 0) or 0):
                raise CombatIntentError("movement exceeds the remaining speed")
        if intent_type == "attack":
            self._validate_attack(instance, combat, intent, actor)
        if intent_type == "cast_spell":
            self._validate_spell(instance, combat, intent, actor, economy)
        if intent_type == "stabilize":
            target = self._actor_view(instance, combat, str(intent.get("target_id") or ""))
            if target["kind"] != "player" or target["hp"] > 0:
                raise CombatIntentError("stabilize requires a player at 0 HP")
            self._require_range(combat, actor_id, target["actor_id"], 5)

    def _validate_attack(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], actor: dict[str, Any],
    ) -> None:
        target_id = str(intent.get("target_id") or "")
        target = self._actor_view(instance, combat, target_id)
        if target["kind"] == actor["kind"] or target["hp"] <= 0:
            raise CombatIntentError("attack target must be a living hostile actor")
        if actor["kind"] == "player":
            weapon_ref = str(intent.get("weapon_ref") or "")
            if weapon_ref not in actor["equipment_refs"]:
                raise CombatIntentError("weapon is not equipped by the actor")
            weapon_id = weapon_ref.removeprefix("item:")
            weapon = self.catalog.weapons.get(weapon_id)
            if weapon is None:
                raise CombatIntentError("weapon has no deterministic combat profile")
        else:
            attack_id = str(intent.get("attack_id") or "")
            weapon = next(
                (item for item in actor["attacks"] if item.get("id") == attack_id), None,
            )
            if weapon is None:
                raise CombatIntentError("enemy attack is not available")
        distance = self._distance(combat, actor["actor_id"], target_id)
        normal_range = int(weapon.get("thrown_range") or weapon.get("range", 5))
        long_range = int(weapon.get("long_range") or normal_range)
        if distance > long_range:
            raise CombatIntentError(f"target is out of range ({distance} > {long_range} feet)")

    def _validate_spell(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        if actor["kind"] != "player":
            raise CombatIntentError("enemy spell profiles are not enabled for this actor")
        spell_ref = str(intent.get("spell_ref") or "")
        spell = self.spells.get(spell_ref)
        effect = self.catalog.spell_effects.get(spell_ref.removeprefix("spell:"))
        if spell is None or effect is None:
            raise CombatIntentError("spell is not deterministically automated yet")
        if spell_ref not in actor["spell_refs"]:
            raise CombatIntentError("spell is not prepared or known by the actor")
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
            if economy.get("slot_spell_cast"):
                raise CombatIntentError("only one spell slot can be expended on a turn")
            if slot_level < spell["level"]:
                raise CombatIntentError("slot_level is lower than the spell level")
            if int(actor["slots"].get(str(slot_level), 0) or 0) < 1:
                raise CombatIntentError("the selected spell slot is not available")
        raw_targets = intent.get("target_ids")
        target_values = raw_targets if isinstance(raw_targets, list) else [intent.get("target_id")]
        target_ids: list[str] = [
            str(item or "") for item in target_values if str(item or "")
        ]
        required_count = int(effect.get("target_count", 1) or 1)
        if not target_ids or len(target_ids) > required_count or len(set(target_ids)) != len(target_ids):
            raise CombatIntentError(f"spell requires 1 to {required_count} unique targets")
        for target_id in target_ids:
            target = self._actor_view(instance, combat, target_id)
            if effect["mode"] in {"healing", "buff"}:
                if target["kind"] != actor["kind"]:
                    raise CombatIntentError("healing and beneficial spells require an allied target")
                if "dead" in target.get("conditions", {}):
                    raise CombatIntentError("dead targets require a resurrection effect")
            elif target["kind"] == actor["kind"]:
                raise CombatIntentError("offensive spells require a hostile target")
            if target["hp"] <= 0 and effect["mode"] not in {"healing", "buff"}:
                raise CombatIntentError("spell target is not active")
            self._require_range(combat, actor["actor_id"], target_id, int(effect["range"]))
        damage_choices = effect.get("damage_type_choice")
        if damage_choices and intent.get("damage_type") not in damage_choices:
            raise CombatIntentError("spell damage_type choice is required")

    @staticmethod
    def _validate_enemies(enemies: Any) -> None:
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
            for field_name, minimum, maximum in (
                ("hp", 1, 10000), ("armor_class", 1, 40),
                ("speed", 0, 200), ("position", -10000, 10000),
                ("initiative_modifier", -20, 30),
            ):
                default = (
                    30 if field_name in {"speed", "position"}
                    else 0 if field_name == "initiative_modifier" else None
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
