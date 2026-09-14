"""Authoritative actor and action views for D&D 2024 combat."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .primitives import (
    CombatIntentError,
    actor_kind as _actor_kind,
    actor_side as _actor_side,
    canonical as _canonical,
    companion_actor as _companion_actor,
    enemy_actor as _enemy_actor,
    player_actor as _player_actor,
)


class CombatViewMixin:
    """Project canonical combat state into presentation-safe actor views."""

    __slots__ = ()

    def _companion(self, instance: Any, raw_id: str) -> dict[str, Any]:
        """从 DND party companion 权威状态读取一个活跃的 AI 队友。"""
        state = getattr(instance, "ruleset_state", None)
        party = state.get("party") if isinstance(state, dict) else None
        companions = party.get("companions") if isinstance(party, dict) else None
        companion = companions.get(raw_id) if isinstance(companions, dict) else None
        if not isinstance(companion, dict) or not companion.get("active", True):
            raise CombatIntentError("companion actor does not exist")
        return companion

    def _active_companions(self, instance: Any) -> dict[str, dict[str, Any]]:
        state = getattr(instance, "ruleset_state", None)
        party = state.get("party") if isinstance(state, dict) else None
        companions = party.get("companions") if isinstance(party, dict) else None
        if not isinstance(companions, dict):
            return {}
        return {
            companion_id: companion
            for companion_id, companion in companions.items()
            if isinstance(companion, dict) and companion.get("active", True)
        }

    def _actor_view(self, instance: Any, combat: dict[str, Any], actor_id: str) -> dict[str, Any]:
        kind, raw_id = _actor_kind(actor_id)
        if kind == "player":
            if raw_id not in instance.players:
                raise CombatIntentError("player actor does not exist")
            character = _canonical(instance.get_character_sheet(raw_id))
            return self._player_view(raw_id, character)
        if kind == "companion":
            # Companion 是"己方角色"：复用 player canonical 视图结构与装备/法术
            # 目录，不走 enemy attack profile。
            character = self._companion(instance, raw_id).get("ruleset_character") or {}
            view = self._player_view(raw_id, character)
            view["actor_id"] = _companion_actor(raw_id)
            view["kind"] = "companion"
            return view
        if kind == "enemy":
            enemy = combat.get("enemies", {}).get(raw_id)
            if not isinstance(enemy, dict):
                raise CombatIntentError("enemy actor does not exist")
            return {
                "actor_id": actor_id, "kind": "enemy", "id": raw_id, "side": "enemy",
                "hp": int(enemy.get("hp", 0) or 0),
                "max_hp": int(enemy.get("max_hp", 0) or 0),
                "armor_class": int(enemy.get("armor_class", 10) or 10),
                "speed": int(enemy.get("speed", 30) or 30),
                "abilities": deepcopy(enemy.get("abilities") or {}),
                "saving_throws": deepcopy(enemy.get("saving_throws") or {}),
                "attacks": deepcopy(enemy.get("attacks") or []),
                "conditions": deepcopy(enemy.get("conditions") or {}),
                "death_saves": {}, "equipment_refs": [], "spell_refs": [], "slots": {},
                "weapon_category_refs": [], "proficiency_bonus": 0,
                "skill_values": {}, "concentration": None,
            }
        raise CombatIntentError("actor_id is invalid")

    def _actor_view_from_data(
        self, instance: Any, enemies: dict[str, dict[str, Any]], actor_id: str,
        companions: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kind, raw_id = _actor_kind(actor_id)
        if kind == "player":
            return self._player_view(raw_id, _canonical(instance.get_character_sheet(raw_id)))
        if kind == "companion":
            companion = (companions or {}).get(raw_id) or self._companion(instance, raw_id)
            view = self._player_view(raw_id, companion.get("ruleset_character") or {})
            view["actor_id"] = _companion_actor(raw_id)
            view["kind"] = "companion"
            return view
        enemy = enemies[raw_id]
        return {
            "actor_id": actor_id, "kind": "enemy", "hp": enemy["hp"],
            "speed": enemy["speed"], "conditions": {}, "build": {},
        }

    @staticmethod
    def _player_view(uid: str, character: dict[str, Any]) -> dict[str, Any]:
        class_magic = character.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        conditions = character.get("conditions")
        conditions = conditions if isinstance(conditions, dict) else {}
        return {
            "actor_id": _player_actor(uid), "kind": "player", "id": uid, "side": "party",
            "hp": int(character.get("resources", {}).get("hp", 0) or 0),
            "max_hp": int(character.get("resources", {}).get("max_hp", 0) or 0),
            "armor_class": int(character.get("derived", {}).get("armor_class", 10) or 10),
            "speed": int(character.get("derived", {}).get("speed", 30) or 30),
            "abilities": deepcopy(character.get("abilities") or {}),
            "saving_throws": deepcopy(character.get("derived", {}).get("saving_throws") or {}),
            "skill_values": deepcopy(character.get("proficiencies", {}).get("skill_values") or {}),
            "proficiency_bonus": int(
                character.get("derived", {}).get("proficiency_bonus", 2) or 2
            ),
            "weapon_category_refs": list(
                character.get("proficiencies", {}).get("weapon_category_refs") or []
            ),
            "equipment_refs": list(character.get("equipment", {}).get("item_refs") or []),
            "spell_refs": list(dict.fromkeys([
                *list(class_magic.get("cantrip_refs") or []),
                *list(class_magic.get("prepared_spell_refs") or []),
            ])),
            "slots": deepcopy(class_magic.get("slots_current") or {}),
            "spell_attack_bonus": int(
                character.get("derived", {}).get("spell_attack_bonus", 0) or 0
            ),
            "spell_save_dc": int(character.get("derived", {}).get("spell_save_dc", 0) or 0),
            "spell_ability": str(class_magic.get("ability") or ""),
            "concentration": deepcopy(class_magic.get("concentration")),
            "conditions": deepcopy(conditions),
            "death_saves": deepcopy(conditions.get("death_saves") or {}),
            "build": deepcopy(character.get("build") or {}),
        }

    def _project_character(self, character: dict[str, Any]) -> dict[str, Any]:
        from src.rulesets.dnd2024.character.builder import Dnd2024CharacterBuilder

        return {
            **Dnd2024CharacterBuilder(self.bundle).project_legacy(character),
            "rule_binding": deepcopy(character["rule_binding"]),
            "ruleset_character": deepcopy(character),
        }

    def _available_weapons(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        if actor["kind"] == "enemy":
            return deepcopy(actor["attacks"])
        result = []
        for ref in actor["equipment_refs"]:
            weapon_id = str(ref).removeprefix("item:")
            weapon = self.catalog.weapons.get(weapon_id)
            if weapon:
                item = self.bundle.get("item", weapon_id) or {}
                result.append({
                    "weapon_ref": ref,
                    "id": weapon_id,
                    **deepcopy(weapon),
                    "name": str(item.get("name") or weapon.get("name") or weapon_id),
                })
        return result

    def _available_spells(
        self, actor: dict[str, Any], economy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if actor["kind"] not in {"player", "companion"}:
            return []
        result = []
        for ref in actor["spell_refs"]:
            spell = self.spells.get(ref)
            effect = self.catalog.spell_effects.get(str(ref).removeprefix("spell:"))
            if spell is None or effect is None:
                continue
            cost = "bonus_action" if str(spell["casting_time"]).startswith("Bonus Action") else "action"
            if int(economy.get(cost, 0) or 0) < 1:
                continue
            available_slots = [
                int(level) for level, count in actor["slots"].items()
                if int(count) > 0 and int(level) >= int(spell["level"])
            ] if spell["level"] > 0 else [0]
            if available_slots:
                result.append({
                    "spell_ref": ref, "name": spell["name"], "level": spell["level"],
                    "casting_time": spell["casting_time"], "range": effect["range"],
                    "mode": effect["mode"], "available_slot_levels": available_slots,
                })
        return result

    def _all_targets(self, instance: Any, combat: dict[str, Any]) -> list[dict[str, Any]]:
        targets = []
        for uid in instance.players:
            view = self._actor_view(instance, combat, _player_actor(uid))
            targets.append({
                "actor_id": view["actor_id"], "kind": "player", "side": "party", "hp": view["hp"],
                "max_hp": view["max_hp"], "position": self._position(combat, view["actor_id"]),
                "name": str(instance.players[uid].get("character_name") or uid),
            })
        for companion_id in sorted(self._active_companions(instance)):
            view = self._actor_view(instance, combat, _companion_actor(companion_id))
            targets.append({
                "actor_id": view["actor_id"], "kind": "companion", "side": "party", "hp": view["hp"],
                "max_hp": view["max_hp"], "position": self._position(combat, view["actor_id"]),
                "name": str(self._companion(instance, companion_id).get("name") or companion_id),
            })
        for enemy_id in combat.get("enemies", {}):
            view = self._actor_view(instance, combat, _enemy_actor(enemy_id))
            targets.append({
                "actor_id": view["actor_id"], "kind": "enemy", "side": "enemy", "hp": view["hp"],
                "max_hp": view["max_hp"], "position": self._position(combat, view["actor_id"]),
                "name": str(combat["enemies"][enemy_id].get("name") or enemy_id),
            })
        return targets

    def _hostile_targets(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
    ) -> list[dict[str, Any]]:
        kind, _raw = _actor_kind(actor_id)
        actor_side = _actor_side(kind)
        # 敌我判断只基于 side：companion 与 player 同为 party，互不视为敌对。
        return [
            target for target in self._all_targets(instance, combat)
            if target.get("side", _actor_side(target["kind"])) != actor_side
        ]
