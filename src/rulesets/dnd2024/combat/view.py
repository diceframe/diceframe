"""Authoritative actor and action views for D&D 2024 combat."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from .triggered_spells import free_cast_resource

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features import CombatCapabilityView
from src.rulesets.dnd2024.features.combat import martial_arts_profile
from src.rulesets.dnd2024.spells.feat_spells import feat_spell_grants

from .primitives import (
    CombatIntentError,
    UNARMED_STRIKE_REF,
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
        state = ruleset_runtime.state(instance)
        party = state.get("party") if isinstance(state, dict) else None
        companions = party.get("companions") if isinstance(party, dict) else None
        companion = companions.get(raw_id) if isinstance(companions, dict) else None
        if not isinstance(companion, dict) or not companion.get("active", True):
            raise CombatIntentError("companion actor does not exist")
        return companion

    def _active_companions(self, instance: Any) -> dict[str, dict[str, Any]]:
        state = ruleset_runtime.state(instance)
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
                "creature_type": str(enemy.get("creature_type") or ""),
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

    def _player_view(self, uid: str, character: dict[str, Any]) -> dict[str, Any]:
        class_magic = character.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        conditions = character.get("conditions")
        conditions = conditions if isinstance(conditions, dict) else {}
        resources = character.get("resources")
        resources = resources if isinstance(resources, dict) else {}
        # 职业资源与 feature-derived 攻击参数在此一次性投影：后面的 combat 代码
        # 只消费结果，不再各自解析职业等级或 level -> 武艺骰。
        feature_projection = self.features.actor_projection(character)
        # 起源专长（魔法学徒）授予的戏法/1 环法术与职业特性授予并列：
        # 各自带施法属性、攻击加值与 DC，结算时按法术来源取值。
        spell_grants = [
            *feature_projection["spell_grants"], *feat_spell_grants(character),
        ]
        return {
            "actor_id": _player_actor(uid), "kind": "player", "id": uid, "side": "party",
            "hp": int(resources.get("hp", 0) or 0),
            "max_hp": int(resources.get("max_hp", 0) or 0),
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
                *[row["spell_ref"] for row in spell_grants],
            ])),
            "class_spell_refs": list(dict.fromkeys([
                *list(class_magic.get("cantrip_refs") or []),
                *list(class_magic.get("prepared_spell_refs") or []),
            ])),
            "slots": deepcopy(class_magic.get("slots_current") or {}),
            "spell_grants": deepcopy(spell_grants),
            "spell_attack_bonus": int(
                character.get("derived", {}).get("spell_attack_bonus", 0) or 0
            ),
            "spell_save_dc": int(character.get("derived", {}).get("spell_save_dc", 0) or 0),
            "spell_ability": str(class_magic.get("ability") or ""),
            "concentration": deepcopy(
                class_magic.get("concentration") if class_magic
                else (character.get("spellcasting") or {}).get("concentration")
            ),
            "conditions": deepcopy(conditions),
            "death_saves": deepcopy(conditions.get("death_saves") or {}),
            "build": deepcopy(character.get("build") or {}),
            "class_resources": deepcopy(feature_projection["class_resources"]),
            "martial_arts_die": str(feature_projection["unarmed_damage_die"]),
            "unarmed_ability_choice": list(feature_projection["unarmed_ability_choice"]),
            "martial_arts_weapon_refs": list(
                feature_projection["martial_arts_weapon_refs"]
            ),
        }

    @staticmethod
    def _spell_caster(actor: dict[str, Any], spell_ref: str) -> dict[str, Any]:
        """The actor as the caster of one spell.

        A class spell uses the class spellcasting numbers; a spell known only
        through an origin feat uses that feat's chosen spellcasting ability.
        """

        if spell_ref in actor.get("class_spell_refs", []):
            return actor
        grant = next(
            (
                row for row in actor.get("spell_grants", [])
                if row.get("spell_ref") == spell_ref and row.get("source") == "feat"
            ),
            None,
        )
        if grant is None:
            return actor
        return {
            **actor,
            "spell_ability": grant["spell_ability"],
            "spell_attack_bonus": int(grant["spell_attack_bonus"]),
            "spell_save_dc": int(grant["spell_save_dc"]),
        }

    def _project_character(self, character: dict[str, Any]) -> dict[str, Any]:
        from src.rulesets.dnd2024.character.builder import Dnd2024CharacterBuilder

        return {
            **Dnd2024CharacterBuilder(self.bundle).project_legacy(
                character, features=self.features,
            ),
            "rule_binding": deepcopy(character["rule_binding"]),
            "ruleset_character": deepcopy(character),
        }

    def _available_attacks(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        """Every attack profile this actor may declare right now.

        Enemy actors expose their stat-block attacks.  Player-like actors expose
        their equipped weapons plus the canonical Unarmed Strike, which is a
        natural capability rather than an inventory item: it is never part of
        ``equipment.item_refs`` and cannot be removed by equipment changes.
        Equipped weapons stay first so callers that prefer the first usable
        profile keep preferring real weapons.

        A wielded Monk Weapon keeps its canonical identity, damage type,
        properties, range and proficiency; only the *effective* damage formula
        and attack-ability choice are feature-derived (see
        :func:`~src.rulesets.dnd2024.features.combat.martial_arts_profile`),
        and only for the refs the feature boundary reported as benefiting.
        """

        if actor["kind"] == "enemy":
            return deepcopy(actor["attacks"])
        benefitting = {
            str(ref) for ref in actor.get("martial_arts_weapon_refs") or []
        }
        result = []
        for ref in actor["equipment_refs"]:
            weapon_id = str(ref).removeprefix("item:")
            weapon = self.catalog.weapons.get(weapon_id)
            if weapon:
                item = self.bundle.get("item", weapon_id) or {}
                profile = {
                    "weapon_ref": ref,
                    "id": weapon_id,
                    **deepcopy(weapon),
                    "name": str(item.get("name") or weapon.get("name") or weapon_id),
                }
                if str(ref) in benefitting:
                    profile = martial_arts_profile(
                        profile,
                        damage_die=str(actor.get("martial_arts_die") or ""),
                        ability_choice=actor.get("unarmed_ability_choice") or (),
                    )
                result.append(profile)
        unarmed = self._unarmed_strike(actor)
        if unarmed is not None:
            result.append(unarmed)
        return result

    def _spell_statuses(self, actor, economy, trigger=None, *, is_current=True):
        """Known spells remain visible even when no cast is currently legal."""
        if actor["kind"] not in {"player", "companion"}:
            return []
        available = {row["spell_ref"] for row in self._available_spells(actor, economy, trigger)}
        statuses = []
        for ref in actor["spell_refs"]:
            spell = self.spells.get(ref)
            if spell is None:
                continue
            effect = self.catalog.spell_effects.get(ref.removeprefix("spell:"))
            reason = ""
            if effect is None:
                reason = "not_automated"
            elif not is_current:
                reason = "not_your_turn"
            elif "rage" in actor["conditions"]:
                reason = "raging"
            elif effect["mode"] == "reaction_defense":
                reason = "requires_being_hit"
            elif effect["mode"] == "triggered_damage" and not trigger:
                reason = "requires_melee_hit"
            elif trigger and effect["mode"] != "triggered_damage":
                reason = "finish_melee_hit"
            elif ref not in available:
                cost = "bonus_action" if str(spell["casting_time"]).startswith("Bonus Action") else "action"
                reason = "action_spent" if int(economy.get(cost, 0)) < 1 else "slot_already_spent" if economy.get("slot_spell_cast") else "no_spell_slot"
            statuses.append({"spell_ref": ref, "name": spell["name"], "blocked_reason": reason})
        return statuses

    def _unarmed_strike(self, actor: dict[str, Any]) -> dict[str, Any] | None:
        """Canonical Unarmed Strike profile, or None when content omits it.

        The identity, damage type and reach come from the combat catalog exactly
        once; the feature boundary only supplies the actor-specific effective
        base damage (the Martial Arts die) and ability choice, and returns both
        empty while Martial Arts is not in force, so the profile falls back to
        plain canonical Unarmed Strike.  No combat file re-derives a
        ``level -> die`` table.
        """

        if actor["kind"] not in {"player", "companion"}:
            return None
        profile = self.catalog.unarmed_strike
        if not profile:
            return None
        effective = martial_arts_profile(
            profile,
            damage_die=str(actor.get("martial_arts_die") or ""),
            ability_choice=actor.get("unarmed_ability_choice") or (),
        )
        return {
            **effective,
            "weapon_ref": UNARMED_STRIKE_REF,
            "id": UNARMED_STRIKE_REF,
            "name": self.catalog.labels.get(UNARMED_STRIKE_REF) or UNARMED_STRIKE_REF,
            "unarmed": True,
        }

    def _declared_attack(
        self, actor: dict[str, Any], intent: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve the attack an intent declares against this actor's authority.

        Player-like actors declare ``weapon_ref`` (an equipped item or the
        canonical unarmed strike); enemy stat blocks declare ``attack_id``.
        Validation and resolution share this one lookup so a client cannot
        declare a profile the actor does not actually have.
        """

        if actor["kind"] == "enemy":
            attack_id = str(intent.get("attack_id") or "")
            return next(
                (deepcopy(item) for item in actor["attacks"] if item.get("id") == attack_id),
                None,
            )
        weapon_ref = str(intent.get("weapon_ref") or "")
        return next(
            (
                item for item in self._available_attacks(actor)
                if item["weapon_ref"] == weapon_ref
            ),
            None,
        )

    @staticmethod
    def _feature_character(actor: dict[str, Any]) -> dict[str, Any]:
        """The feature-relevant slice of an actor's canonical character.

        The actor view already carries exactly what the feature boundary reads
        (canonical build, the canonical class resource entries, and the
        currently equipped refs), so combat never reaches back into the instance
        for a second copy of character state.  The round trip is lossless for
        the fields the boundary consumes: ``current`` / ``maximum``, and the
        equipment that decides whether an equipment-gated feature is active.
        """

        class_state: dict[str, Any] = {}
        for entry in actor.get("class_resources") or []:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            class_state[str(entry["id"])] = {
                "current": entry.get("current", 0),
                "maximum": entry.get("maximum", 0),
            }
        return {
            "build": actor.get("build") or {},
            "abilities": actor.get("abilities") or {},
            "resources": {"class": class_state},
            "equipment": {"item_refs": list(actor.get("equipment_refs") or [])},
        }

    def _available_class_capabilities(
        self, actor: dict[str, Any], economy: dict[str, Any],
        *, include_unavailable: bool = False,
    ) -> tuple[CombatCapabilityView, ...]:
        """Feature-provided combat capabilities for this actor, in catalog order."""

        if actor.get("kind") not in {"player", "companion"}:
            return ()
        return self.features.combat_capabilities(
            self._feature_character(actor),
            economy,
            include_unavailable=include_unavailable,
        )

    def _available_spells(
        self, actor: dict[str, Any], economy: dict[str, Any],
        trigger: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if actor["kind"] not in {"player", "companion"}:
            return []
        if "rage" in actor["conditions"]:
            # 狂暴期间不能施法（SRD 5.2.1 Rage: No Concentration or Spells）。
            return []
        result = []
        for ref in actor["spell_refs"]:
            spell = self.spells.get(ref)
            effect = self.catalog.spell_effects.get(str(ref).removeprefix("spell:"))
            if spell is None or effect is None:
                continue
            if effect["mode"] == "triggered_damage" and not trigger:
                continue
            if effect["mode"] == "reaction_defense":
                # 只能在被攻击命中时，通过 hit_reaction_spell 决策施放。
                continue
            cost = "bonus_action" if str(spell["casting_time"]).startswith("Bonus Action") else "action"
            if int(economy.get(cost, 0) or 0) < 1:
                continue
            available_slots = [
                int(level) for level, count in actor["slots"].items()
                if int(count) > 0 and int(level) >= int(spell["level"]) and not economy.get("slot_spell_cast")
            ] if spell["level"] > 0 else [0]
            if spell["level"] > 0 and free_cast_resource(actor, ref):
                available_slots.insert(0, 0)
            available_slots.sort()
            if available_slots:
                result.append({
                    "spell_ref": ref, "name": spell["name"], "level": spell["level"],
                    "casting_time": spell["casting_time"], "range": effect["range"],
                    "mode": effect["mode"], "available_slot_levels": available_slots,
                    **({"trigger_target_id": trigger["target_id"]} if effect["mode"] == "triggered_damage" else {}),
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
