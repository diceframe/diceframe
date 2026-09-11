"""Authoritative, versioned D&D 2024 combat intents and EventBatch reducer."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.combat.catalog import Dnd2024CombatCatalog
from src.rulesets.dnd2024.play.contracts import EncounterAccess
from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog
from src.rulesets.events import EventBatchError, apply_event_batch, stable_batch_id


from .primitives import (
    INTENT_TYPES,
    CombatIntentError,
    actor_kind as _actor_kind,
    canonical as _canonical,
    enemy_actor as _enemy_actor,
    player_actor as _player_actor,
)
from .reducer import CombatReducerMixin
from .resolution import CombatResolutionMixin
from .validation import CombatValidationMixin
from .view import CombatViewMixin


@dataclass(slots=True)
class Dnd2024CombatEngine(
    CombatValidationMixin, CombatResolutionMixin, CombatReducerMixin, CombatViewMixin,
):
    bundle: LoadedRulesetBundle
    encounter_access: EncounterAccess = field(default_factory=EncounterAccess.blocked)
    encounter_catalog: dict[str, Any] | None = None
    catalog: Dnd2024CombatCatalog = field(init=False)
    spells: Dnd2024SpellCatalog = field(init=False)

    def __post_init__(self) -> None:
        self.catalog = Dnd2024CombatCatalog.from_bundle(self.bundle)
        self.spells = Dnd2024SpellCatalog.from_bundle(self.bundle)

    def initialize_state(self, instance: Any) -> dict[str, Any]:
        state = instance.ruleset_state
        if not isinstance(state, dict):
            raise CombatIntentError("ruleset_state must be an object")
        state.setdefault("state_schema_version", 1)
        state.setdefault("version", 0)
        state.setdefault("combat_history", [])
        state.setdefault("combat", {
            "status": "none", "round": 0, "turn_index": 0,
            "initiative": [], "enemies": {}, "positions": {},
            "economy": {}, "pending_decisions": [], "position_mode": "theater",
        })
        if state.get("state_schema_version") != 1:
            raise CombatIntentError("unsupported D&D 2024 combat state schema")
        if not isinstance(state.get("combat_history"), list):
            raise CombatIntentError("combat_history must be an array")
        return state

    def available_intents(self, instance: Any, actor_id: str) -> list[dict[str, Any]]:
        state = self.initialize_state(instance)
        combat = state["combat"]
        version = int(state["version"])
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        if combat.get("status") != "active":
            actions: list[dict[str, Any]] = []
            request = state.get("encounter_request")
            if (
                isinstance(request, dict)
                and request.get("status") == "pending"
                and actor_id in instance.players
                and actor_id != gm_uid
            ):
                ready_ids = {
                    str(item) for item in request.get("ready_player_ids") or [] if str(item)
                }
                ready = actor_id in ready_ids
                actions.append({
                    "type": "encounter.unready" if ready else "encounter.ready",
                    "label": "Cancel ready" if ready else "Ready",
                    "expected_version": version,
                })
            if actor_id != gm_uid or not self.encounter_access.can_start:
                return actions
            guided_preset_id = (
                self.encounter_access.encounter_preset_id
                if self.encounter_access.mode == "story"
                else ""
            )
            action = {
                "type": "combat.start", "label": "Start combat", "expected_version": version,
                "requires": ["encounter_preset_id"] if guided_preset_id else ["enemies"],
            }
            if guided_preset_id:
                action["encounter_preset_id"] = guided_preset_id
                action["encounter_instance_id"] = self.encounter_access.encounter_instance_id
            actions.append(action)
            return actions
        communication = [{
            "type": "combat.message", "label": "Combat message",
            "expected_version": version,
        }] if actor_id in instance.players else []
        current = self._current_actor(combat)
        if current.startswith("enemy:"):
            return communication
        requested = _player_actor(actor_id)
        if requested != current:
            return communication
        pending = [
            item for item in combat.get("pending_decisions", [])
            if item.get("assigned_to") == actor_id
        ]
        if pending:
            return [*communication, {
                "type": "decision.resolve", "label": "Resolve reaction",
                "expected_version": version, "decisions": deepcopy(pending),
            }]
        actor = self._actor_view(instance, combat, current)
        if actor["hp"] <= 0 and actor["kind"] == "player":
            if "stable" in actor["conditions"]:
                return [*communication, {
                    "type": "end_turn", "label": "End turn",
                    "expected_version": version, "actor_id": current,
                }]
            return [*communication, {
                "type": "death_save", "label": "Death saving throw",
                "expected_version": version, "actor_id": current,
            }]
        economy = combat.get("economy", {})
        actions: list[dict[str, Any]] = []
        if int(economy.get("action", 0) or 0) > 0 or int(
            economy.get("attacks_remaining", 0) or 0
        ) > 0:
            weapons = self._available_weapons(actor)
            if weapons:
                actions.append({
                    "type": "attack", "label": "Attack", "actor_id": current,
                    "expected_version": version, "weapons": weapons,
                    "targets": self._hostile_targets(instance, combat, current),
                })
            actions.extend([
                {"type": "dash", "label": "Dash", "actor_id": current, "expected_version": version},
                {"type": "dodge", "label": "Dodge", "actor_id": current, "expected_version": version},
                {"type": "disengage", "label": "Disengage", "actor_id": current, "expected_version": version},
            ])
            adjacent_downed = [
                target for target in self._all_targets(instance, combat)
                if target["kind"] == actor["kind"] and target["hp"] <= 0
                and self._distance(combat, current, target["actor_id"]) <= 5
            ]
            if adjacent_downed:
                actions.append({
                    "type": "stabilize", "label": "Stabilize", "actor_id": current,
                    "expected_version": version, "targets": adjacent_downed,
                })
        spells = self._available_spells(actor, economy)
        if spells:
            actions.append({
                "type": "cast_spell", "label": "Cast a spell", "actor_id": current,
                "expected_version": version, "spells": spells,
                "targets": self._all_targets(instance, combat),
            })
        if int(economy.get("movement", 0) or 0) > 0:
            actions.append({
                "type": "move", "label": "Move", "actor_id": current,
                "expected_version": version, "movement_remaining": economy["movement"],
            })
        actions.append({
            "type": "end_turn", "label": "End turn", "actor_id": current,
            "expected_version": version,
        })
        if actor_id == gm_uid:
            actions.append({
                "type": "combat.end", "label": "End combat", "expected_version": version,
            })
        return [*communication, *actions]

    def next_automatic_intent(self, instance: Any) -> dict[str, Any] | None:
        """Choose one bounded server-owned enemy operation.

        The method declares an intent only; validation, dice, events, and state
        mutation still pass through the same authoritative pipeline as player
        actions.
        """

        state = self.initialize_state(instance)
        combat = state["combat"]
        if combat.get("status") != "active":
            return None
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        version = int(state.get("version", 0) or 0)
        pending = list(combat.get("pending_decisions") or [])
        if pending:
            decision = pending[0]
            threats = [str(item) for item in decision.get("threat_actor_ids") or []]
            option = (
                "resolve"
                if "resolve" in decision.get("options", [])
                and any(actor_id.startswith("enemy:") for actor_id in threats)
                else "decline"
            )
            return {
                "intent_id": f"auto:decision:{version}:{decision.get('decision_id', '')}",
                "type": "decision.resolve",
                "expected_version": version,
                "submitted_by": gm_uid,
                "decision_id": str(decision.get("decision_id") or ""),
                "option": option,
            }
        actor_id = self._current_actor(combat)
        if not actor_id.startswith("enemy:"):
            return None
        base = {
            "intent_id": f"auto:enemy:{version}:{actor_id}",
            "expected_version": version,
            "submitted_by": gm_uid,
            "actor_id": actor_id,
        }
        actor = self._actor_view(instance, combat, actor_id)
        targets = [
            target for target in self._hostile_targets(instance, combat, actor_id)
            if int(target.get("hp", 0) or 0) > 0
        ]
        targets.sort(key=lambda target: (
            self._distance(combat, actor_id, str(target["actor_id"])),
            int(target.get("hp", 0) or 0),
            str(target["actor_id"]),
        ))
        economy = combat.get("economy") or {}
        can_act = int(economy.get("action", 0) or 0) > 0 or int(
            economy.get("attacks_remaining", 0) or 0
        ) > 0
        if can_act and targets:
            target = targets[0]
            distance = self._distance(combat, actor_id, str(target["actor_id"]))
            attacks = [
                attack for attack in actor.get("attacks") or []
                if distance <= int(attack.get("long_range") or attack.get("range", 5) or 5)
            ]
            attacks.sort(key=lambda attack: (
                distance > int(attack.get("range", 5) or 5),
                -int(attack.get("attack_bonus", 0) or 0),
                str(attack.get("id") or ""),
            ))
            if attacks:
                return {
                    **base,
                    "intent_id": f"{base['intent_id']}:attack",
                    "type": "attack",
                    "target_id": str(target["actor_id"]),
                    "attack_id": str(attacks[0]["id"]),
                }
            movement = int(economy.get("movement", 0) or 0)
            if movement > 0:
                actor_position = self._position(combat, actor_id)
                target_position = self._position(combat, str(target["actor_id"]))
                desired_range = max(
                    (int(item.get("range", 5) or 5) for item in actor.get("attacks") or []),
                    default=5,
                )
                needed = max(0, abs(target_position - actor_position) - desired_range)
                distance_to_move = min(movement, needed)
                if distance_to_move > 0:
                    signed = distance_to_move if target_position > actor_position else -distance_to_move
                    return {
                        **base,
                        "intent_id": f"{base['intent_id']}:move",
                        "type": "move",
                        "distance": signed,
                    }
            return {
                **base,
                "intent_id": f"{base['intent_id']}:dodge",
                "type": "dodge",
            }
        return {
            **base,
            "intent_id": f"{base['intent_id']}:end",
            "type": "end_turn",
        }

    def validate_intent(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        try:
            self._validate(instance, intent)
        except CombatIntentError as exc:
            return {"ok": False, "code": "INVALID_INTENT", "error": str(exc)}
        return {"ok": True}

    def resolve_intent(
        self, instance: Any, intent: dict[str, Any], rng: Any,
    ) -> dict[str, Any]:
        state = self.initialize_state(instance)
        intent_id = str(intent.get("intent_id") or "")
        prior = next(
            (item for item in instance.event_ledger if item.get("intent_id") == intent_id),
            None,
        )
        if prior is not None:
            expected = intent.get("expected_version")
            if (
                isinstance(expected, bool) or not isinstance(expected, int)
                or str(prior.get("batch_id") or "") != stable_batch_id(intent, expected)
            ):
                return {
                    "ok": False,
                    "code": "INTENT_ID_CONFLICT",
                    "error": "intent_id was already used for a different request",
                }
            return {"ok": True, "event_batch": deepcopy(prior), "replayed": True}
        validation = self.validate_intent(instance, intent)
        if not validation["ok"]:
            return validation
        combat = state["combat"]
        intent_type = str(intent["type"])
        events = [{
            "type": "intent.submitted", "intent_type": intent_type,
            "actor_id": str(intent.get("actor_id") or ""),
            "submitted_by": str(intent.get("submitted_by") or ""),
        }]
        pending: dict[str, Any] | None = None
        if intent_type in {"encounter.ready", "encounter.unready"}:
            events.append({
                "type": "dnd2024.encounter.readiness.changed",
                "player_id": str(intent.get("submitted_by") or ""),
                "ready": intent_type == "encounter.ready",
            })
        elif intent_type == "combat.start":
            # The catalog is authoritative.  The client may select a preset, but
            # it must never be able to alter the enemy stat block in the event.
            resolved_intent = deepcopy(intent)
            preset_id = str(intent.get("encounter_preset_id") or "")
            if preset_id:
                preset = self._preset(preset_id)
                if preset is None:  # validation normally catches this
                    raise CombatIntentError("encounter preset is not available")
                resolved_intent["enemies"] = deepcopy(preset["enemies"])
            if self.encounter_access.mode == "story":
                resolved_intent.update({
                    "encounter_instance_id": self.encounter_access.encounter_instance_id,
                    "encounter_preset_id": self.encounter_access.encounter_preset_id,
                    "origin_step_id": self.encounter_access.origin_step_id,
                    "mode": "story",
                    "adventure_binding": {
                        "adventure_id": self.encounter_access.adventure_id,
                        "step_id": self.encounter_access.origin_step_id,
                        "encounter_preset_id": self.encounter_access.encounter_preset_id,
                        "encounter_instance_id": self.encounter_access.encounter_instance_id,
                    },
                })
            else:
                # 自由遭遇不属于任何冒险包；把这个事实写进权威状态，界面才能
                # 持续显示“自由战斗”，而叙事文本无法改变它。
                resolved_intent.update({"mode": "sandbox", "adventure_binding": None})
            events.append(self._start_combat_event(instance, resolved_intent, rng))
        elif intent_type == "combat.end":
            events.append({"type": "dnd2024.combat.ended", "reason": "gm"})
        elif intent_type == "combat.message":
            events.append({
                "type": "dnd2024.combat.message",
                "actor_id": _player_actor(str(intent.get("submitted_by") or "")),
                "text": str(intent.get("text") or "").strip(),
            })
        elif intent_type == "attack":
            events.extend(self._attack_events(instance, combat, intent, rng))
        elif intent_type == "cast_spell":
            events.extend(self._spell_events(instance, combat, intent, rng))
        elif intent_type == "move":
            pending, movement_events = self._movement_events(instance, combat, intent)
            events.extend(movement_events)
        elif intent_type in {"dash", "dodge", "disengage"}:
            events.extend(self._basic_action_events(combat, intent_type, str(intent["actor_id"])))
        elif intent_type == "end_turn":
            events.extend(self._end_turn_events(instance, combat))
        elif intent_type == "death_save":
            events.extend(self._death_save_events(instance, combat, intent, rng))
        elif intent_type == "stabilize":
            events.extend(self._stabilize_events(instance, combat, intent, rng))
        elif intent_type == "decision.resolve":
            events.extend(self._decision_events(instance, combat, intent, rng))
        else:  # pragma: no cover - validation guards this
            raise CombatIntentError("unsupported intent")
        expected = int(intent["expected_version"])
        batch = {
            "batch_id": stable_batch_id(intent, expected),
            "intent_id": intent_id,
            "intent_type": intent_type,
            "expected_version": expected,
            "result_version": expected + 1,
            "events": events,
            "source_ref": "srd-5.2.1:p24-p27:playing-the-game",
        }
        return {"ok": True, "event_batch": batch, "pending_decision": pending}

    def apply_batch(self, instance: Any, batch: dict[str, Any]) -> dict[str, Any]:
        state = self.initialize_state(instance)
        snapshot = {
            "version": int(state.get("version", 0) or 0),
            "ruleset_state": deepcopy(state),
            "characters": {
                uid: deepcopy(_canonical(instance.get_character_sheet(uid)))
                for uid in instance.players
            },
        }
        updated, ledger, duplicate = apply_event_batch(
            snapshot, instance.event_ledger, batch, self._reduce_event,
        )
        if not duplicate:
            ruleset_state = updated["ruleset_state"]
            ruleset_state["version"] = updated["version"]
            instance.ruleset_state = ruleset_state
            for uid, canonical in updated["characters"].items():
                existing = deepcopy(instance.get_character_sheet(uid))
                projection = self._project_character(canonical)
                existing.update(projection)
                instance.set_character_sheet(uid, existing)
            instance.event_ledger = ledger
            combat = ruleset_state["combat"]
            instance.combat_state = (
                "active" if combat.get("status") == "active" else "none"
            )
            instance.combat_active = instance.combat_state == "active"
            instance.initiative_order = list(combat.get("initiative") or [])
            instance.initiative_current = int(combat.get("turn_index", 0) or 0)
        return {
            "ok": True,
            "applied": not duplicate,
            "duplicate": duplicate,
            "state_version": int(instance.ruleset_state.get("version", 0) or 0),
            "event_batch": deepcopy(batch),
            "combat": deepcopy(instance.ruleset_state.get("combat") or {}),
        }

    def gameplay_view(self, instance: Any) -> dict[str, Any]:
        state = self.initialize_state(instance)
        combat = state["combat"]
        actors: list[dict[str, Any]] = []
        if combat.get("status") == "active":
            for target in self._all_targets(instance, combat):
                view = self._actor_view(instance, combat, str(target["actor_id"]))
                actors.append({
                    **target,
                    "armor_class": view["armor_class"],
                    "speed": view["speed"],
                    "conditions": deepcopy(view["conditions"]),
                    "concentration": deepcopy(view.get("concentration")),
                    "death_saves": deepcopy(view.get("death_saves") or {}),
                })
        initiative = list(combat.get("initiative") or [])
        turn_index = int(combat.get("turn_index", 0) or 0)
        current_actor_id = (
            str(initiative[turn_index])
            if initiative and 0 <= turn_index < len(initiative)
            else ""
        )
        return {
            "state_schema_version": int(state.get("state_schema_version", 1) or 1),
            "state_version": int(state.get("version", 0) or 0),
            "combat": {
                "status": str(combat.get("status") or "none"),
                "outcome": str(combat.get("outcome") or ""),
                "round": int(combat.get("round", 0) or 0),
                "turn_index": turn_index,
                "current_actor_id": current_actor_id,
                "initiative": initiative,
                "position_mode": str(combat.get("position_mode") or "theater"),
                "economy": deepcopy(combat.get("economy") or {}),
                "reactions": deepcopy(combat.get("reactions") or {}),
                "pending_decisions": deepcopy(combat.get("pending_decisions") or []),
                "encounter_instance_id": str(combat.get("encounter_instance_id") or ""),
                "encounter_preset_id": str(combat.get("encounter_preset_id") or ""),
                "origin_step_id": str(combat.get("origin_step_id") or ""),
                "mode": str(combat.get("mode") or ""),
                "adventure_binding": deepcopy(combat.get("adventure_binding")),
                "actors": actors,
            },
            "encounter_presets": self.encounter_presets(),
            # 权威模式投影：前端据此区分“剧情绑定/剧情未准备/自由遭遇”，
            # 不再靠显示名或通用目录推断当前敌情来源。
            "encounter_access": {
                "mode": self.encounter_access.mode,
                "status": self.encounter_access.status,
                # 进行中的战斗不再开放 start 入口，视图也要如实反映这一点。
                "can_start": bool(
                    self.encounter_access.can_start and combat.get("status") != "active"
                ),
                "unprepared": bool(self.encounter_access.unprepared),
                "adventure_id": self.encounter_access.adventure_id,
                "encounter_preset_id": self.encounter_access.encounter_preset_id,
                "encounter_instance_id": self.encounter_access.encounter_instance_id,
                "origin_step_id": self.encounter_access.origin_step_id,
                "catalog": "adventure" if self.encounter_access.mode == "story" else "bundle",
            },
        }

    def encounter_presets(self) -> list[dict[str, Any]]:
        catalog = (
            deepcopy(self.encounter_catalog)
            if isinstance(self.encounter_catalog, dict)
            else self.bundle.get("encounter_catalog", "srd_training_encounters") or {}
        )
        raw_labels = catalog.get("labels")
        labels: dict[str, Any] = raw_labels if isinstance(raw_labels, dict) else {}
        raw_preset_labels = labels.get("presets")
        raw_profile_labels = labels.get("profiles")
        raw_attack_labels = labels.get("attacks")
        preset_labels: dict[str, Any] = (
            raw_preset_labels if isinstance(raw_preset_labels, dict) else {}
        )
        profile_labels: dict[str, Any] = (
            raw_profile_labels if isinstance(raw_profile_labels, dict) else {}
        )
        attack_labels: dict[str, Any] = (
            raw_attack_labels if isinstance(raw_attack_labels, dict) else {}
        )
        result: list[dict[str, Any]] = []
        for raw in catalog.get("presets") or []:
            if not isinstance(raw, dict):
                continue
            preset = deepcopy(raw)
            if (
                self.encounter_access.mode == "sandbox"
                and str(preset.get("difficulty") or "") == "tutorial"
            ):
                continue
            preset_id = str(preset.get("id") or "")
            text = preset_labels.get(preset_id, {})
            text = text if isinstance(text, dict) else {}
            preset["name"] = str(text.get("name") or preset_id or "Encounter")
            preset["description"] = str(text.get("description") or "")
            for enemy in preset.get("enemies") or []:
                profile_id = str(enemy.pop("profile_id", "") or "")
                enemy["name"] = str(
                    profile_labels.get(profile_id) or profile_id or enemy["id"]
                )
                for attack in enemy.get("attacks") or []:
                    attack_id = str(attack.get("id") or "")
                    attack["name"] = str(attack_labels.get(attack_id) or attack_id)
            self._validate_enemies(preset.get("enemies"))
            result.append(preset)
        return result

    def _preset(self, preset_id: str) -> dict[str, Any] | None:
        wanted = str(preset_id or "")
        if not wanted:
            return None
        return next(
            (preset for preset in self.encounter_presets() if preset.get("id") == wanted),
            None,
        )

    @staticmethod
    def _current_actor(combat: dict[str, Any]) -> str:
        order = combat.get("initiative")
        index = int(combat.get("turn_index", 0) or 0)
        if not isinstance(order, list) or not order or not 0 <= index < len(order):
            raise CombatIntentError("combat initiative state is invalid")
        return str(order[index])

    @staticmethod
    def _fresh_economy(actor: dict[str, Any]) -> dict[str, Any]:
        speed = max(0, int(actor.get("speed", 0) or 0))
        if "slowed_10" in actor.get("conditions", {}):
            speed = max(0, speed - 10)
        return {
            "actor_id": actor["actor_id"], "action": 1, "bonus_action": 1,
            "reaction": 1, "movement": speed, "speed": speed,
            "attacks_remaining": 0, "disengaged": False,
        }

    @staticmethod
    def _attacks_per_action(actor: dict[str, Any]) -> int:
        if actor["kind"] != "player":
            return 1
        class_levels = actor.get("build", {}).get("class_levels") or []
        row = class_levels[0] if class_levels else {}
        class_ref = str(row.get("class_ref") or "")
        level = int(row.get("level", 1) or 1)
        if class_ref == "class:fighter":
            return 4 if level >= 20 else 3 if level >= 11 else 2 if level >= 5 else 1
        return 2 if level >= 5 and class_ref in {
            "class:barbarian", "class:monk", "class:paladin", "class:ranger",
        } else 1

    def _attack_cost_event(
        self, combat: dict[str, Any], actor: dict[str, Any],
    ) -> dict[str, Any]:
        del combat
        return {
            "type": "dnd2024.attack.spent", "actor_id": actor["actor_id"],
            "attacks_per_action": self._attacks_per_action(actor),
        }

    @staticmethod
    def _attack_modes(
        actor: dict[str, Any], target: dict[str, Any], range_disadvantage: bool,
    ) -> tuple[bool, bool]:
        advantage = "next_attack_advantage" in target["conditions"] or "faerie_fire" in target[
            "conditions"
        ]
        disadvantage = (
            range_disadvantage
            or "dodging" in target["conditions"]
            or "next_attack_disadvantage" in actor["conditions"]
        )
        if advantage and disadvantage:
            return False, False
        return advantage, disadvantage

    @staticmethod
    def _consume_attack_conditions(
        actor_id: str, target_id: str, actor: dict[str, Any], target: dict[str, Any],
    ) -> list[dict[str, Any]]:
        events = []
        if "next_attack_disadvantage" in actor["conditions"]:
            events.append({
                "type": "condition.removed", "target_id": actor_id,
                "condition": "next_attack_disadvantage",
            })
        if "next_attack_advantage" in target["conditions"]:
            events.append({
                "type": "condition.removed", "target_id": target_id,
                "condition": "next_attack_advantage",
            })
        return events

    @staticmethod
    def _concentration_events(
        target: dict[str, Any], damage: int, rng: Any,
    ) -> list[dict[str, Any]]:
        if not target.get("concentration") or damage <= 0:
            return []
        dc = max(10, damage // 2)
        roll = int(rng.randint(1, 20))
        modifier = int(target["saving_throws"].get("con", 0) or 0)
        success = roll + modifier >= dc
        events: list[dict[str, Any]] = [{
            "type": "check.resolved", "kind": "concentration", "actor_id": target["actor_id"],
            "rolls": [roll], "natural": roll, "modifier": modifier,
            "total": roll + modifier, "target": dc, "success": success,
        }]
        if not success:
            events.append({
                "type": "dnd2024.concentration.ended", "actor_id": target["actor_id"],
                "reason": "failed_concentration_save",
            })
        return events

    @staticmethod
    def _position(combat: dict[str, Any], actor_id: str) -> int:
        return int(combat.get("positions", {}).get(actor_id, 0) or 0)

    def _distance(self, combat: dict[str, Any], first: str, second: str) -> int:
        return abs(self._position(combat, first) - self._position(combat, second))

    def _distance_after_move(
        self, combat: dict[str, Any], mover: str, target: str, distance: int,
    ) -> int:
        return abs(self._position(combat, mover) + distance - self._position(combat, target))

    def _require_range(
        self, combat: dict[str, Any], actor_id: str, target_id: str, maximum: int,
    ) -> None:
        distance = self._distance(combat, actor_id, target_id)
        if distance > maximum:
            raise CombatIntentError(f"target is out of range ({distance} > {maximum} feet)")

    @staticmethod
    def _conditions(
        snapshot: dict[str, Any], combat: dict[str, Any], actor_id: str,
    ) -> dict[str, Any]:
        kind, raw_id = _actor_kind(actor_id)
        if kind == "player":
            return snapshot["characters"][raw_id].setdefault("conditions", {})
        if kind == "enemy":
            return combat["enemies"][raw_id].setdefault("conditions", {})
        raise EventBatchError("condition target actor is invalid")

    def _remove_concentration_conditions(
        self, snapshot: dict[str, Any], combat: dict[str, Any], owner: str,
    ) -> None:
        actor_ids = [
            *(_player_actor(uid) for uid in snapshot["characters"]),
            *(_enemy_actor(enemy_id) for enemy_id in combat.get("enemies", {})),
        ]
        for actor_id in actor_ids:
            conditions = self._conditions(snapshot, combat, actor_id)
            for condition_id in [
                key for key, value in conditions.items()
                if isinstance(value, dict) and value.get("concentration_owner") == owner
            ]:
                conditions.pop(condition_id, None)

    def _expire_conditions(
        self, snapshot: dict[str, Any], combat: dict[str, Any], actor_id: str,
        previous_actor_id: str,
    ) -> None:
        conditions = self._conditions(snapshot, combat, actor_id)
        for condition_id in [
            key for key, value in conditions.items()
            if isinstance(value, dict) and value.get("duration") in {
                "actor_turn_start", "target_turn_start",
            }
        ]:
            conditions.pop(condition_id, None)
        all_actor_ids = [
            *(_player_actor(uid) for uid in snapshot["characters"]),
            *(_enemy_actor(enemy_id) for enemy_id in combat.get("enemies", {})),
        ]
        for target_id in all_actor_ids:
            target_conditions = self._conditions(snapshot, combat, target_id)
            for condition_id in [
                key for key, value in target_conditions.items()
                if isinstance(value, dict)
                and value.get("duration") == "caster_turn_end"
                and value.get("source_actor_id") == previous_actor_id
            ]:
                target_conditions.pop(condition_id, None)

    @staticmethod
    def _last_hostile_defeated(
        combat: dict[str, Any], target: dict[str, Any], damage: int,
    ) -> bool:
        if target["kind"] != "enemy" or target["hp"] - damage > 0:
            return False
        return not any(
            enemy_id != target["id"] and int(enemy.get("hp", 0) or 0) > 0
            for enemy_id, enemy in combat.get("enemies", {}).items()
        )
