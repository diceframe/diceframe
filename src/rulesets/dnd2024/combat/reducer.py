"""Event reducer stage for D&D 2024 combat."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.rulesets.dnd2024.spells.feat_spells import concentration_holder, spend_feat_free_cast
from src.rulesets.events import EventBatchError
from .primitives import actor_kind as _actor_kind


# 只有行动经济池可以被特性追加；其余 economy 字段（actor_id/speed/...）不是资源。
_GRANTABLE_ECONOMY = frozenset({
    "action", "bonus_action", "reaction", "movement", "surge_action",
})
# economy.granted 的 flags 是引擎内部记账（如 action_surge_used 的「每回合
# 一次」），不是向 economy 任意写字段的通道；未知 flag fail closed。
_GRANTABLE_FLAGS = frozenset({"action_surge_used"})


class CombatReducerMixin:
    """Apply validated events to a detached authoritative snapshot."""

    __slots__ = ()

    def _reduce_event(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        state = snapshot["ruleset_state"]
        combat = state["combat"]
        event_type = str(event["type"])
        if event_type in {
            "intent.submitted", "check.resolved", "dnd2024.spell.cast",
            "dnd2024.combat.message",
        }:
            return
        if event_type == "dnd2024.encounter.readiness.changed":
            request = state.get("encounter_request")
            if not isinstance(request, dict) or request.get("status") != "pending":
                raise EventBatchError("encounter readiness has no pending request")
            player_id = str(event["player_id"])
            ready_ids = {
                str(item) for item in request.get("ready_player_ids") or [] if str(item)
            }
            if event.get("ready"):
                ready_ids.add(player_id)
            else:
                ready_ids.discard(player_id)
            request["ready_player_ids"] = sorted(ready_ids)
            return
        if event_type == "dnd2024.combat.player_joined":
            actor_id = str(event["actor_id"])
            if not actor_id.startswith("player:"):
                raise EventBatchError("joined combat actor must be a player")
            if actor_id in combat.get("initiative", []):
                raise EventBatchError("player is already in combat initiative")
            raw_id = actor_id.removeprefix("player:")
            if raw_id not in snapshot["characters"]:
                raise EventBatchError("joined combat player does not exist")
            order = combat.setdefault("initiative", [])
            insert_index = max(0, min(len(order), int(event.get("insert_index", len(order)))))
            order.insert(insert_index, actor_id)
            combat.setdefault("initiative_rolls", []).append({
                "actor_id": actor_id,
                "roll": int(event.get("roll", 0) or 0),
                "modifier": int(event.get("modifier", 0) or 0),
                "total": int(event.get("total", 0) or 0),
                "kind": "player",
                "joined_late": True,
            })
            combat.setdefault("positions", {})[actor_id] = int(event.get("position", 0) or 0)
            combat.setdefault("reactions", {})[actor_id] = 1
            return
        if event_type == "dnd2024.character.revived":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind != "player" or raw_id not in snapshot["characters"]:
                raise EventBatchError("revived actor must be an existing player")
            character = snapshot["characters"][raw_id]
            resources = character.setdefault("resources", {})
            max_hp = int(resources.get("max_hp", 0) or 0)
            hp = max(1, int(event.get("hp", 1) or 1))
            if max_hp > 0:
                hp = min(max_hp, hp)
            resources["hp"] = hp
            conditions = character.setdefault("conditions", {})
            for condition in ("dead", "unconscious", "stable", "death_saves"):
                conditions.pop(condition, None)
            return
        if event_type == "dnd2024.combat.started":
            state["combat"] = {
                "status": "active", "round": event["round"], "turn_index": 0,
                "initiative": deepcopy(event["initiative"]),
                "initiative_rolls": deepcopy(event["initiative_rolls"]),
                "enemies": deepcopy(event["enemies"]),
                "positions": deepcopy(event["positions"]),
                "economy": deepcopy(event["economy"]),
                "pending_decisions": [], "position_mode": event["position_mode"],
                "reactions": deepcopy(event["reactions"]),
                "feature_usage": {},
                "encounter_instance_id": str(event.get("encounter_instance_id") or ""),
                "encounter_preset_id": str(event.get("encounter_preset_id") or ""),
                "origin_step_id": str(event.get("origin_step_id") or ""),
                "mode": str(event.get("mode") or "sandbox"),
                "adventure_binding": deepcopy(event.get("adventure_binding")),
            }
            return
        if event_type == "dnd2024.reaction.spent":
            actor_id = str(event["actor_id"])
            reactions = combat.setdefault("reactions", {})
            if int(reactions.get(actor_id, 0) or 0) < 1:
                raise EventBatchError("reaction is already spent")
            reactions[actor_id] = 0
            return
        if event_type == "dnd2024.combat.ended":
            combat["status"] = "ended"
            combat["outcome"] = str(event.get("reason") or "ended")
            combat["economy"] = {}
            # 狂暴靠每回合攻击或附赠动作续期；战斗结束后没有回合可续，
            # 狂暴随之结束，不能带进下一场战斗。
            for character in snapshot["characters"].values():
                (character.get("conditions") or {}).pop("rage", None)
            companions = (state.get("party") or {}).get("companions") or {}
            for companion in companions.values():
                canonical = companion.get("ruleset_character") if isinstance(companion, dict) else None
                if isinstance(canonical, dict):
                    (canonical.get("conditions") or {}).pop("rage", None)
            encounter_id = str(combat.get("encounter_instance_id") or "")
            if encounter_id:
                history = state.setdefault("combat_history", [])
                if not any(
                    isinstance(item, dict) and item.get("encounter_instance_id") == encounter_id
                    for item in history
                ):
                    history.append({
                        "encounter_instance_id": encounter_id,
                        "encounter_preset_id": str(combat.get("encounter_preset_id") or ""),
                        "origin_step_id": str(combat.get("origin_step_id") or ""),
                        "outcome": combat["outcome"],
                    })
                    del history[:-64]
            return
        if event_type == "dnd2024.action.spent":
            resource = str(event["resource"])
            current = int(combat["economy"].get(resource, 0) or 0)
            amount = int(event.get("amount", 1) or 1)
            if current < amount:
                raise EventBatchError(f"combat resource is already spent: {resource}")
            combat["economy"][resource] = current - amount
            return
        if event_type == "dnd2024.economy.granted":
            economy = combat["economy"]
            if str(event["actor_id"]) != str(economy.get("actor_id") or ""):
                raise EventBatchError("economy grants belong to the current actor")
            grants = event.get("grants") or {}
            flags = event.get("flags") or {}
            if not isinstance(grants, dict) or not isinstance(flags, dict):
                raise EventBatchError("economy grant payload is invalid")
            for key, amount in grants.items():
                if str(key) not in _GRANTABLE_ECONOMY:
                    raise EventBatchError(f"economy grant key is unknown: {key}")
                if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                    raise EventBatchError("economy grant amount is invalid")
                economy[str(key)] = int(economy.get(str(key), 0) or 0) + amount
            for key, value in flags.items():
                if str(key) not in _GRANTABLE_FLAGS:
                    raise EventBatchError(f"economy grant flag is unknown: {key}")
                economy[str(key)] = value
            return
        if event_type == "dnd2024.feature.used":
            # 「每回合一次」按真实攻击者 + 具体回合（round + turn_index）记账。
            # 借机攻击发生在别人的回合，因此不能读当前行动者的 economy。
            actor_id = str(event["actor_id"])
            round_index = int(event["round"])
            turn_index = int(event["turn_index"])
            feature_id = str(event["feature_id"])
            usage = combat.setdefault("feature_usage", {})
            if not isinstance(usage, dict):
                raise EventBatchError("feature usage ledger is invalid")
            record = usage.get(actor_id)
            if (
                not isinstance(record, dict)
                or int(record.get("round", -1)) != round_index
                or int(record.get("turn_index", -1)) != turn_index
            ):
                usage[actor_id] = {
                    "round": round_index, "turn_index": turn_index,
                    "feature_ids": [feature_id],
                }
                return
            feature_ids = record.setdefault("feature_ids", [])
            if feature_id in feature_ids:
                raise EventBatchError(f"feature is already used this turn: {feature_id}")
            feature_ids.append(feature_id)
            return
        if event_type == "dnd2024.class_resource.spent":
            # 职业资源永远写在既有的 canonical resources.class 结构里，
            # 不新增第二套资源表，也不允许扣成负数。
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion class resources are canonical")
            character = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )
            resource_id = str(event["resource_id"])
            class_state = character.setdefault("resources", {}).setdefault("class", {})
            state = class_state.get(resource_id)
            if not isinstance(state, dict):
                raise EventBatchError(f"class resource is not initialized: {resource_id}")
            current = int(state.get("current", 0) or 0)
            amount = int(event.get("amount", 1) or 1)
            if current < amount:
                raise EventBatchError(f"class resource is already spent: {resource_id}")
            state["current"] = current - amount
            return
        if event_type == "dnd2024.attack.spent":
            economy = combat["economy"]
            if int(economy.get("attacks_remaining", 0) or 0) > 0:
                economy["attacks_remaining"] -= 1
            else:
                # 支付选择由 resolution 的单一入口决定：普通 action 先用完，
                # 只有它耗尽时 Action Surge 的 surge_action 才支付攻击。
                resource = str(event.get("resource") or "action")
                if int(economy.get(resource, 0) or 0) < 1:
                    raise EventBatchError("Attack action is already spent")
                economy[resource] -= 1
                economy["attacks_remaining"] = max(0, int(event["attacks_per_action"]) - 1)
            return
        if event_type == "dnd2024.movement.granted":
            combat["economy"]["movement"] = int(combat["economy"].get("movement", 0)) + int(
                event["amount"]
            )
            return
        if event_type == "dnd2024.position.changed":
            actor_id = str(event["actor_id"])
            combat["positions"][actor_id] = int(combat["positions"].get(actor_id, 0)) + int(
                event["distance"]
            )
            cost = int(event.get("movement_cost", 0) or 0)
            if cost:
                remaining = int(combat["economy"].get("movement", 0) or 0)
                if remaining < cost:
                    raise EventBatchError("movement resource is already spent")
                combat["economy"]["movement"] = remaining - cost
            return
        if event_type == "resource.changed":
            self._apply_hp_change(snapshot, event)
            return
        if event_type == "condition.applied":
            conditions = self._conditions(snapshot, combat, str(event["target_id"]))
            conditions[str(event["condition"])] = {
                key: deepcopy(value) for key, value in event.items()
                if key not in {"type", "target_id", "condition"}
            }
            if event["condition"] == "stable":
                kind, raw = _actor_kind(str(event["target_id"]))
                if kind == "player":
                    snapshot["characters"][raw].setdefault("conditions", {})[
                        "death_saves"
                    ] = {"successes": 3, "failures": 0}
            return
        if event_type == "condition.removed":
            self._conditions(snapshot, combat, str(event["target_id"])).pop(
                str(event["condition"]), None
            )
            return
        if event_type == "dnd2024.condition.sustained":
            conditions = self._conditions(snapshot, combat, str(event["target_id"]))
            condition_id = str(event["condition"])
            if condition_id not in conditions:
                raise EventBatchError("sustained condition is not active")
            conditions[condition_id]["sustained_this_turn"] = True
            return
        if event_type == "dnd2024.spell.slot_spent":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion spell slots are canonical")
            slots = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )["spellcasting"]["class"]["slots_current"]
            level = str(event["slot_level"])
            if int(slots.get(level, 0) or 0) < 1:
                raise EventBatchError("spell slot is already spent")
            slots[level] -= 1
            combat["economy"]["slot_spell_cast"] = True
            return
        if event_type == "dnd2024.feat_spell.free_cast_spent":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion feat spells are canonical")
            try:
                spend_feat_free_cast(
                    self._canonical_of(snapshot, kind, raw_id), str(event["feat_ref"]),
                )
            except ValueError as exc:
                raise EventBatchError(str(exc)) from exc
            return
        if event_type == "dnd2024.concentration.started":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind in {"player", "companion"}:
                concentration_holder(self._canonical_of(snapshot, kind, raw_id))[
                    "concentration"
                ] = {
                    "spell_ref": event["spell_ref"], "target_ids": deepcopy(event["target_ids"]),
                }
            return
        if event_type == "dnd2024.concentration.ended":
            owner = str(event["actor_id"])
            kind, raw_id = _actor_kind(owner)
            if kind in {"player", "companion"}:
                concentration_holder(self._canonical_of(snapshot, kind, raw_id))[
                    "concentration"
                ] = None
            self._remove_concentration_conditions(snapshot, combat, owner)
            return
        if event_type == "dnd2024.turn.advanced":
            self._expire_conditions(
                snapshot, combat, str(event["actor_id"]),
                str(event["previous_actor_id"]),
            )
            combat["round"] = int(event["round"])
            combat["turn_index"] = int(event["turn_index"])
            combat["economy"] = deepcopy(event["economy"])
            combat.setdefault("reactions", {})[str(event["actor_id"])] = 1
            return
        if event_type == "dnd2024.death_save.resolved":
            _kind, raw_id = _actor_kind(str(event["actor_id"]))
            character = snapshot["characters"][raw_id]
            character.setdefault("conditions", {})["death_saves"] = {
                "successes": int(event["successes"]), "failures": int(event["failures"]),
            }
            if event.get("hp"):
                character["resources"]["hp"] = int(event["hp"])
                character["conditions"].pop("unconscious", None)
            if event.get("stable"):
                character["conditions"]["stable"] = {"duration": "until_healed"}
            if event.get("dead"):
                character["conditions"]["dead"] = {"source": "death_saves"}
                character["conditions"].pop("unconscious", None)
            return
        if event_type == "dnd2024.decision.pending":
            combat.setdefault("pending_decisions", []).append({
                key: deepcopy(value) for key, value in event.items() if key != "type"
            })
            return
        if event_type == "dnd2024.decision.cleared":
            combat["pending_decisions"] = [
                item for item in combat.get("pending_decisions", [])
                if item.get("decision_id") != event["decision_id"]
            ]
            return
        raise EventBatchError(f"unsupported event type: {event_type}")

    def _companion_character(self, snapshot: dict[str, Any], companion_id: str) -> dict[str, Any]:
        """从 detached snapshot 读取 companion 的权威 canonical 角色。"""
        companions = (
            snapshot["ruleset_state"].setdefault("party", {}).setdefault("companions", {})
        )
        companion = companions.get(companion_id)
        if not isinstance(companion, dict) or not isinstance(
            companion.get("ruleset_character"), dict,
        ):
            raise EventBatchError("companion canonical character is missing")
        return companion["ruleset_character"]

    def _canonical_of(self, snapshot: dict[str, Any], kind: str, raw_id: str) -> dict[str, Any]:
        if kind == "player":
            return snapshot["characters"][raw_id]
        return self._companion_character(snapshot, raw_id)

    def _apply_hp_change(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        target_id = str(event["target_id"])
        kind, raw_id = _actor_kind(target_id)
        delta = int(event["delta"])
        if kind in {"player", "companion"}:
            character = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )
            resources = character["resources"]
            before = int(resources.get("hp", 0) or 0)
            maximum = int(resources.get("max_hp", 0) or 0)
            resources["hp"] = max(0, min(maximum, before + delta))
            conditions = character.setdefault("conditions", {})
            if resources["hp"] <= 0:
                conditions["unconscious"] = {"source": "zero_hp"}
                # 昏迷即失能：狂暴立即结束。
                conditions.pop("rage", None)
                death = conditions.setdefault("death_saves", {"successes": 0, "failures": 0})
                if before == 0 and delta < 0:
                    death["failures"] = min(
                        3, int(death.get("failures", 0)) + (2 if event.get("critical") else 1)
                    )
                    if death["failures"] >= 3:
                        conditions["dead"] = {"source": "damage_at_zero_hp"}
                        conditions.pop("unconscious", None)
            elif delta > 0:
                conditions.pop("unconscious", None)
                conditions.pop("stable", None)
                conditions["death_saves"] = {"successes": 0, "failures": 0}
        elif kind == "enemy":
            enemy = snapshot["ruleset_state"]["combat"]["enemies"][raw_id]
            enemy["hp"] = max(0, min(int(enemy["max_hp"]), int(enemy["hp"]) + delta))
            if enemy["hp"] <= 0:
                enemy.setdefault("conditions", {})["defeated"] = {"source": "zero_hp"}
        else:
            raise EventBatchError("HP target actor is invalid")
