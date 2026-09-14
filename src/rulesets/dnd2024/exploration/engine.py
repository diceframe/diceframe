"""Out-of-combat (exploration) spellcasting for D&D 2024.

Exploration casting deliberately does NOT reuse the combat action economy:
no action/bonus-action/movement accounting, no turn order. What it must
uphold is the same resource authority as combat — spell slots, concentration,
and HP changes are applied to the same canonical character state, and every
cast is recorded as an event with ``context = "exploration"``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.combat.action_adapter import (
    heal_ability_modifier_node,
    roll_damage_node,
    spell_formula_node,
)
from src.rulesets.dnd2024.combat.catalog import Dnd2024CombatCatalog
from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog
from src.rulesets.events import EventBatchError, apply_event_batch, stable_batch_id

EXPLORATION_INTENT_TYPES = frozenset({"exploration.cast_spell"})

# 探索态没有敌方 actor；伤害/豁免/法术攻击模式的确定性结算需要敌对目标，
# 第一阶段不支持——已知但非确定的法术走 narrative 分支（§32）。
_SUPPORTED_EFFECT_MODES = frozenset({"healing", "buff"})


class ExplorationIntentError(ValueError):
    """Raised when an exploration intent is structurally or rules invalid."""


@dataclass(slots=True)
class Dnd2024ExplorationEngine:
    bundle: LoadedRulesetBundle
    locale: str = ""
    catalog: Dnd2024CombatCatalog = field(init=False)
    spells: Dnd2024SpellCatalog = field(init=False)

    def __post_init__(self) -> None:
        self.catalog = Dnd2024CombatCatalog.from_bundle(self.bundle)
        self.spells = Dnd2024SpellCatalog.from_bundle(self.bundle)

    # ------------------------------------------------------------------
    # state helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _state(instance: Any) -> dict[str, Any]:
        state = instance.ruleset_state
        if not isinstance(state, dict):
            raise ExplorationIntentError("ruleset_state must be an object")
        state.setdefault("state_schema_version", 1)
        state.setdefault("version", 0)
        state.setdefault("party", {"companions": {}})
        state["party"].setdefault("companions", {})
        state.setdefault("combat", {"status": "none"})
        return state

    @staticmethod
    def _canonical_of(instance: Any, actor_id: str) -> dict[str, Any]:
        kind, raw_id = actor_id.split(":", 1) if ":" in actor_id else ("", actor_id)
        if kind == "player":
            if raw_id not in instance.players:
                raise ExplorationIntentError("spellcasting actor does not exist")
            return instance.get_character_sheet(raw_id).setdefault("ruleset_character", {})
        party = instance.ruleset_state.get("party", {})
        companion = (party.get("companions", {}) or {}).get(raw_id)
        if not isinstance(companion, dict) or not companion.get("active", True):
            raise ExplorationIntentError("spellcasting target does not exist")
        return companion["ruleset_character"]

    def _party_targets(self, instance: Any) -> list[dict[str, Any]]:
        targets = []
        for uid in instance.players:
            canonical = self._canonical_of(instance, f"player:{uid}")
            resources = canonical.get("resources", {})
            targets.append({
                "actor_id": f"player:{uid}", "side": "party",
                "hp": int(resources.get("hp", 0) or 0),
                "max_hp": int(resources.get("max_hp", 0) or 0),
                "name": str(instance.players[uid].get("character_name") or uid),
            })
        for companion_id, companion in sorted(
            (instance.ruleset_state.get("party", {}).get("companions", {}) or {}).items(),
        ):
            if not isinstance(companion, dict) or not companion.get("active", True):
                continue
            canonical = companion.get("ruleset_character", {})
            resources = canonical.get("resources", {})
            targets.append({
                "actor_id": f"companion:{companion_id}", "side": "party",
                "hp": int(resources.get("hp", 0) or 0),
                "max_hp": int(resources.get("max_hp", 0) or 0),
                "name": str(companion.get("name") or companion_id),
            })
        return targets

    def _spell_refs(self, canonical: dict[str, Any]) -> list[str]:
        class_magic = canonical.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        return list(dict.fromkeys([
            *list(class_magic.get("cantrip_refs") or []),
            *list(class_magic.get("prepared_spell_refs") or []),
        ]))

    # ------------------------------------------------------------------
    # intents
    # ------------------------------------------------------------------

    def available_intents(self, instance: Any, actor_id: str) -> list[dict[str, Any]]:
        state = self._state(instance)
        if state["combat"].get("status") == "active":
            return []
        if not actor_id.startswith("player:") or actor_id.removeprefix("player:") not in instance.players:
            return []
        uid = actor_id.removeprefix("player:")
        canonical = self._canonical_of(instance, actor_id)
        class_magic = canonical.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        spells = []
        for spell_ref in self._spell_refs(canonical):
            spell = self.spells.get(spell_ref)
            if spell is None:
                continue
            available_slots = (
                [
                    int(level) for level, count in sorted(
                        (class_magic.get("slots_current") or {}).items(),
                        key=lambda item: int(item[0]),
                    )
                    if int(count) > 0 and int(level) >= int(spell["level"])
                ]
                if int(spell["level"]) > 0 else [0]
            )
            spells.append({
                "spell_ref": spell_ref, "name": spell["name"], "level": spell["level"],
                "casting_time": spell["casting_time"], "available_slot_levels": available_slots,
            })
        if not spells:
            return []
        return [{
            "type": "exploration.cast_spell", "label": "Cast a spell",
            "expected_version": int(state.get("version", 0) or 0),
            "actor_id": actor_id, "spells": spells,
            "targets": self._party_targets(instance),
        }]

    def validate_intent(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        try:
            self._validate(instance, intent)
        except ExplorationIntentError as exc:
            return {"ok": False, "code": "INVALID_INTENT", "error": str(exc)}
        return {"ok": True}

    def _validate(self, instance: Any, intent: dict[str, Any]) -> None:
        if not isinstance(intent, dict):
            raise ExplorationIntentError("intent must be an object")
        if str(intent.get("type") or "") not in EXPLORATION_INTENT_TYPES:
            raise ExplorationIntentError("intent type is not supported")
        state = self._state(instance)
        expected = intent.get("expected_version")
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise ExplorationIntentError("expected_version must be an integer")
        if expected != int(state.get("version", 0) or 0):
            raise ExplorationIntentError(
                f"state version conflict: expected {expected}, current {state.get('version')}"
            )
        if state["combat"].get("status") == "active":
            raise ExplorationIntentError("exploration casting requires no active combat")
        actor_id = str(intent.get("actor_id") or "")
        uid = actor_id.removeprefix("player:")
        if not actor_id.startswith("player:") or uid not in instance.players:
            raise ExplorationIntentError("exploration casting is player-only in this phase")
        if str(intent.get("submitted_by") or "") != uid:
            raise ExplorationIntentError("a player can only cast for their own character")
        canonical = self._canonical_of(instance, actor_id)
        spell_ref = str(intent.get("spell_ref") or "")
        spell = self.spells.get(spell_ref)
        if spell is None:
            raise ExplorationIntentError("spell does not exist in the ruleset")
        if spell_ref not in self._spell_refs(canonical):
            raise ExplorationIntentError("spell is not prepared or known by the actor")
        slot_level = intent.get("slot_level", spell["level"])
        if isinstance(slot_level, bool) or not isinstance(slot_level, int):
            raise ExplorationIntentError("slot_level must be an integer")
        class_magic = canonical.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        slots = class_magic.get("slots_current") or {}
        if spell["level"] == 0:
            if slot_level != 0:
                raise ExplorationIntentError("a cantrip does not use a spell slot")
        else:
            if slot_level < spell["level"]:
                raise ExplorationIntentError("slot_level is lower than the spell level")
            if int(slots.get(str(slot_level), 0) or 0) < 1:
                raise ExplorationIntentError("the selected spell slot is not available")
        effect = self.catalog.spell_effects.get(spell_ref.removeprefix("spell:"))
        if effect is not None and str(effect.get("mode")) not in _SUPPORTED_EFFECT_MODES:
            # 伤害/豁免类确定性效果需要敌对目标，探索态不存在；这类请求拒绝，
            # 而不是让叙事层自行决定数值。
            raise ExplorationIntentError(
                "this spell has no exploration-safe deterministic effect"
            )
        target_values = intent.get("target_ids")
        targets = [str(item) for item in (target_values or []) if str(item)]
        required_count = int(effect.get("target_count", 1) or 1) if effect else 1
        if effect is not None and effect.get("mode") in _SUPPORTED_EFFECT_MODES:
            if not targets or len(targets) > required_count or len(set(targets)) != len(targets):
                raise ExplorationIntentError(
                    f"spell requires 1 to {required_count} unique targets"
                )
            party_ids = {target["actor_id"] for target in self._party_targets(instance)}
            for target_id in targets:
                if target_id not in party_ids:
                    raise ExplorationIntentError("spell target must be a party member")

    def resolve_intent(
        self, instance: Any, intent: dict[str, Any], rng: Any,
    ) -> dict[str, Any]:
        try:
            self._validate(instance, intent)
        except ExplorationIntentError as exc:
            return {"ok": False, "code": "INVALID_INTENT", "error": str(exc)}
        actor_id = str(intent["actor_id"])
        spell_ref = str(intent["spell_ref"])
        spell = self.spells.get(spell_ref)
        if spell is None:  # pragma: no cover - validation guards this
            raise ExplorationIntentError("spell does not exist in the ruleset")
        slot_level = int(intent.get("slot_level", spell["level"]) or 0)
        effect = self.catalog.spell_effects.get(spell_ref.removeprefix("spell:"))
        target_values = intent.get("target_ids")
        target_ids = [str(item) for item in (target_values or []) if str(item)]

        events: list[dict[str, Any]] = [{
            "type": "intent.submitted", "intent_type": "exploration.cast_spell",
            "actor_id": actor_id, "submitted_by": str(intent.get("submitted_by") or ""),
        }]
        expected = int(intent["expected_version"])
        intent_id = str(intent.get("intent_id") or "")
        if spell["level"] > 0:
            events.append({
                "type": "dnd2024.spell.slot_spent", "actor_id": actor_id,
                "slot_level": slot_level, "amount": 1,
            })
        if effect is not None and effect.get("concentration"):
            actor_canonical = self._canonical_of(instance, actor_id)
            class_magic = actor_canonical.get("spellcasting", {}).get("class") or {}
            if class_magic.get("concentration"):
                events.append({
                    "type": "dnd2024.concentration.ended", "actor_id": actor_id,
                    "reason": "new_concentration_spell",
                })
            events.append({
                "type": "dnd2024.concentration.started", "actor_id": actor_id,
                "spell_ref": spell_ref, "target_ids": target_ids,
            })
        events.append({
            "type": "dnd2024.spell.cast", "actor_id": actor_id, "spell_ref": spell_ref,
            "slot_level": slot_level, "target_ids": target_ids,
            "context": "exploration",
        })
        if effect is None:
            # 未自动化的 utility spell：合法施放、扣资源、事件交给叙事层（§32）。
            events.append({
                "type": "dnd2024.spell.narrative", "actor_id": actor_id,
                "spell_ref": spell_ref, "slot_level": slot_level,
                "resolution": "narrative",
            })
            return {"ok": True, "events": events, "event_batch": self._batch(intent_id, expected, events)}
        for target_id in target_ids:
            target_canonical = self._canonical_of(instance, target_id)
            mode = str(effect["mode"])
            if mode == "healing":
                healing_node = spell_formula_node(
                    str(effect["healing"]), effect.get("upcast_healing"),
                    spell, slot_level, self._actor_view(instance, actor_id),
                )
                if effect.get("add_spell_ability"):
                    healing_node = heal_ability_modifier_node(
                        healing_node, self._actor_view(instance, actor_id),
                    )
                healing, rolls = roll_damage_node(healing_node, rng)
                healing = max(1, healing)
                resources = target_canonical.get("resources", {})
                before = int(resources.get("hp", 0) or 0)
                maximum = int(resources.get("max_hp", 0) or 0)
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": target_id,
                    "delta": healing, "amount": healing, "healing": True, "rolls": rolls,
                    "hp_after": min(maximum, before + healing),
                })
                conditions = target_canonical.setdefault("conditions", {})
                if healing > 0:
                    conditions.pop("unconscious", None)
                    conditions.pop("stable", None)
                    conditions["death_saves"] = {"successes": 0, "failures": 0}
            elif mode == "buff":
                condition = str(effect.get("condition") or "")
                if condition:
                    events.append({
                        "type": "condition.applied", "target_id": target_id,
                        "condition": condition,
                        "duration": str(effect.get("condition_duration") or ""),
                        "source_actor_id": actor_id,
                        "concentration_owner": actor_id if effect.get("concentration") else "",
                    })
        batch = self._batch(intent_id, expected, events)
        return {"ok": True, "events": events, "event_batch": batch}

    @staticmethod
    def _batch(intent_id: str, expected: int, events: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "batch_id": stable_batch_id(
                {"intent_id": intent_id, "expected_version": expected}, expected,
            ),
            "intent_id": intent_id,
            "intent_type": "exploration.cast_spell",
            "expected_version": expected,
            "result_version": expected + 1,
            "events": events,
            "source_ref": "srd-5.2.1:p24-p27:playing-the-game",
        }

    def _actor_view(self, instance: Any, actor_id: str) -> dict[str, Any]:
        """探索态施法者的最小施法视图（spell_formula_node 需要的形状）。"""
        canonical = self._canonical_of(instance, actor_id)
        class_magic = canonical.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        derived = canonical.get("derived", {}) if isinstance(canonical.get("derived"), dict) else {}
        return {
            "actor_id": actor_id,
            "spell_attack_bonus": int(derived.get("spell_attack_bonus", 0) or 0),
            "spell_save_dc": int(derived.get("spell_save_dc", 0) or 0),
            "spell_ability": str(class_magic.get("ability") or ""),
            "abilities": deepcopy(canonical.get("abilities") or {}),
        }

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def apply_batch(self, instance: Any, batch: dict[str, Any]) -> dict[str, Any]:
        snapshot = {
            "version": int(instance.ruleset_state.get("version", 0) or 0),
            "ruleset_state": deepcopy(instance.ruleset_state),
            "characters": {
                uid: deepcopy(instance.get_character_sheet(uid).get("ruleset_character", {}))
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
                if uid not in instance.players:
                    continue
                sheet = deepcopy(instance.get_character_sheet(uid))
                sheet["ruleset_character"] = deepcopy(canonical)
                instance.set_character_sheet(uid, sheet)
            instance.event_ledger = ledger
        return {
            "ok": True,
            "applied": not duplicate,
            "duplicate": duplicate,
            "state_version": int(instance.ruleset_state.get("version", 0) or 0),
            "event_batch": deepcopy(batch),
        }

    def _reduce_event(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        event_type = str(event["type"])
        if event_type in {"intent.submitted", "dnd2024.spell.cast", "dnd2024.spell.narrative"}:
            return
        if event_type == "dnd2024.spell.slot_spent":
            kind, raw_id = str(event["actor_id"]).split(":", 1)
            if kind != "player":
                raise EventBatchError("exploration casting is player-only")
            canonical = snapshot["characters"][raw_id]
            slots = canonical["spellcasting"]["class"]["slots_current"]
            level = str(event["slot_level"])
            if int(slots.get(level, 0) or 0) < 1:
                raise EventBatchError("spell slot is already spent")
            slots[level] -= 1
            return
        if event_type == "dnd2024.concentration.started":
            kind, raw_id = str(event["actor_id"]).split(":", 1)
            if kind == "player":
                canonical = snapshot["characters"][raw_id]
                canonical["spellcasting"]["class"]["concentration"] = {
                    "spell_ref": event["spell_ref"], "target_ids": deepcopy(event["target_ids"]),
                }
            else:
                self._companion_canonical(snapshot, raw_id)["spellcasting"]["class"][
                    "concentration"
                ] = {
                    "spell_ref": event["spell_ref"], "target_ids": deepcopy(event["target_ids"]),
                }
            return
        if event_type == "dnd2024.concentration.ended":
            kind, raw_id = str(event["actor_id"]).split(":", 1)
            if kind == "player":
                canonical = snapshot["characters"][raw_id]
            else:
                canonical = self._companion_canonical(snapshot, raw_id)
            canonical["spellcasting"]["class"]["concentration"] = None
            self._remove_concentration_conditions(snapshot, str(event["actor_id"]))
            return
        if event_type == "resource.changed":
            self._apply_hp_change(snapshot, event)
            return
        if event_type == "condition.applied":
            conditions = self._conditions(snapshot, str(event["target_id"]))
            conditions[str(event["condition"])] = {
                key: deepcopy(value) for key, value in event.items()
                if key not in {"type", "target_id", "condition"}
            }
            return
        if event_type == "condition.removed":
            self._conditions(snapshot, str(event["target_id"])).pop(str(event["condition"]), None)
            return
        raise EventBatchError(f"unsupported exploration event type: {event_type}")

    def _companion_canonical(self, snapshot: dict[str, Any], companion_id: str) -> dict[str, Any]:
        companions = snapshot["ruleset_state"].setdefault("party", {}).setdefault("companions", {})
        companion = companions.get(companion_id)
        if not isinstance(companion, dict) or not isinstance(
            companion.get("ruleset_character"), dict,
        ):
            raise EventBatchError("companion canonical character is missing")
        return companion["ruleset_character"]

    def _conditions(self, snapshot: dict[str, Any], actor_id: str) -> dict[str, Any]:
        kind, raw_id = actor_id.split(":", 1)
        if kind == "player":
            return snapshot["characters"][raw_id].setdefault("conditions", {})
        return self._companion_canonical(snapshot, raw_id).setdefault("conditions", {})

    def _apply_hp_change(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        target_id = str(event["target_id"])
        kind, raw_id = target_id.split(":", 1)
        if kind == "player":
            character = snapshot["characters"][raw_id]
        else:
            character = self._companion_canonical(snapshot, raw_id)
        resources = character["resources"]
        before = int(resources.get("hp", 0) or 0)
        maximum = int(resources.get("max_hp", 0) or 0)
        resources["hp"] = max(0, min(maximum, before + int(event["delta"])))
        if kind == "player" and resources["hp"] > 0:
            conditions = character.setdefault("conditions", {})
            conditions.pop("unconscious", None)
            conditions.pop("stable", None)
            conditions["death_saves"] = {"successes": 0, "failures": 0}

    def _remove_concentration_conditions(self, snapshot: dict[str, Any], owner: str) -> None:
        party = snapshot["ruleset_state"].get("party", {}) or {}
        actor_ids = [
            *(f"player:{uid}" for uid in snapshot["characters"]),
            *(f"companion:{companion_id}" for companion_id in party.get("companions", {})),
        ]
        for actor_id in actor_ids:
            conditions = self._conditions(snapshot, actor_id)
            for condition_id in [
                key for key, value in conditions.items()
                if isinstance(value, dict) and value.get("concentration_owner") == owner
            ]:
                conditions.pop(condition_id, None)
