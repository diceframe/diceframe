"""Validated compact combat mechanics from the SRD bundle."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle


DICE_RE = re.compile(r"^[1-9]\d*d(?:4|6|8|10|12|20)(?:\+[1-9]\d*)?$")
# 固定伤害（例如徒手打击的 1 点基础伤害）：没有骰子，因此重击不翻倍。
FLAT_DAMAGE_RE = re.compile(r"^[1-9]\d{0,3}$")
SPELL_MODES = frozenset({
    "spell_attack", "saving_throw", "healing", "automatic_damage", "buff", "debuff", "triggered_damage",
    "stabilize", "reaction_defense", "temp_hp", "teleport", "dispel", "revive",
})
DAMAGE_TYPES = frozenset({
    "bludgeoning", "slashing", "piercing", "acid", "cold", "fire", "lightning",
    "thunder", "poison", "psychic", "force", "necrotic", "radiant",
})
SAVE_ABILITIES = frozenset({"str", "dex", "con", "int", "wis", "cha"})


class CombatCatalogError(ValueError):
    """Raised when compact combat data cannot be used authoritatively."""


@dataclass(frozen=True, slots=True)
class Dnd2024CombatCatalog:
    source_ref: str
    weapons: dict[str, dict[str, Any]]
    spell_effects: dict[str, dict[str, Any]]
    # 徒手打击是每个 player-like actor 的天然攻击能力，不是捆绑物品；
    # 因此它有自己的 compact 档案，而不是混进 weapons（weapons 全部必须是 item）。
    unarmed_strike: dict[str, Any] | None = None
    # condition -> 攻击掷骰模式注册表：condition 自己声明它挂在攻击者还是
    # 受击者身上、贡献优势还是劣势、被哪些感官 condition 抵消、何时消耗。
    conditions: dict[str, dict[str, Any]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_bundle(cls, bundle: LoadedRulesetBundle) -> Dnd2024CombatCatalog:
        raw = bundle.get("combat_catalog", "srd_combat_core")
        if raw is None:
            raise CombatCatalogError("D&D 2024 combat catalog is missing")
        weapons = raw.get("weapons")
        effects = raw.get("spell_effects")
        if not isinstance(weapons, dict) or not isinstance(effects, dict):
            raise CombatCatalogError("combat weapons and spell effects must be objects")
        item_ids = {str(item["id"]) for item in bundle.list("item")}
        for weapon_id, weapon in weapons.items():
            if weapon_id not in item_ids or not isinstance(weapon, dict):
                raise CombatCatalogError(f"combat weapon is not a bundled item: {weapon_id}")
            if not DICE_RE.fullmatch(str(weapon.get("damage") or "")):
                raise CombatCatalogError(f"combat weapon damage is invalid: {weapon_id}")
            if not 5 <= int(weapon.get("range", 0) or 0) <= 600:
                raise CombatCatalogError(f"combat weapon range is invalid: {weapon_id}")
        spell_catalog = bundle.get("spell_catalog", "srd_spells") or {}
        spell_ids = {
            str(item.get("id") or "") for item in spell_catalog.get("spells", [])
            if isinstance(item, dict)
        }
        for spell_id, effect in effects.items():
            if spell_id not in spell_ids or not isinstance(effect, dict):
                raise CombatCatalogError(f"combat spell is not in the spell catalog: {spell_id}")
            if effect.get("mode") not in SPELL_MODES:
                raise CombatCatalogError(f"combat spell mode is invalid: {spell_id}")
            # 本批只支持缺省与 self；显式 null、空串或未来语义都在装载时
            # fail closed，避免未实现的声明在前端伪装成合法约束。
            if "target_scope" in effect and effect["target_scope"] != "self":
                raise CombatCatalogError(
                    f"combat spell target scope is invalid: {spell_id}"
                )
            for dice_field in ("damage", "healing", "upcast_damage", "upcast_healing", "extra_damage"):
                dice = effect.get(dice_field)
                if dice is not None and not DICE_RE.fullmatch(str(dice)):
                    raise CombatCatalogError(
                        f"combat spell {dice_field} is invalid: {spell_id}"
                    )
            if effect["mode"] == "triggered_damage" and (effect.get("trigger") != "melee_hit" or not effect.get("damage")):
                raise CombatCatalogError(f"combat spell trigger is invalid: {spell_id}")
            if effect["mode"] == "reaction_defense" and (
                effect.get("trigger") != "hit_by_attack"
                or not 1 <= int(effect.get("armor_class_bonus", 0) or 0) <= 10
                or not effect.get("condition")
            ):
                raise CombatCatalogError(f"combat reaction spell is invalid: {spell_id}")
            if "extra_damage_creature_types" in effect and (
                not isinstance(effect["extra_damage_creature_types"], list)
                or any(not isinstance(kind, str) or kind not in {"fiend", "undead"} for kind in effect["extra_damage_creature_types"])
            ):
                raise CombatCatalogError(f"combat spell creature types are invalid: {spell_id}")
            upcast_targets = effect.get("upcast_target_count")
            if upcast_targets is not None and (
                isinstance(upcast_targets, bool)
                or not isinstance(upcast_targets, int)
                or not 0 < upcast_targets <= 10
            ):
                raise CombatCatalogError(f"combat spell upcast target count is invalid: {spell_id}")
            resist_choice = effect.get("resist_choice")
            if resist_choice is not None and (
                not isinstance(resist_choice, list)
                or not resist_choice
                or any(not isinstance(kind, str) or kind not in DAMAGE_TYPES for kind in resist_choice)
                or len(set(resist_choice)) != len(resist_choice)
            ):
                raise CombatCatalogError(f"combat spell resist choice is invalid: {spell_id}")
            removes_conditions = effect.get("removes_conditions")
            if removes_conditions is not None and (
                not isinstance(removes_conditions, list)
                or not removes_conditions
                or any(
                    not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", code)
                    for code in removes_conditions
                )
            ):
                raise CombatCatalogError(
                    f"combat spell removed conditions are invalid: {spell_id}"
                )
            ends_on = effect.get("ends_on")
            if ends_on is not None and (
                not isinstance(ends_on, list)
                or any(code not in {"attack_roll", "damage_dealt", "spell_cast"} for code in ends_on)
            ):
                raise CombatCatalogError(f"combat spell end triggers are invalid: {spell_id}")
            save_repeat = effect.get("save_repeat")
            if save_repeat is not None and save_repeat not in SAVE_ABILITIES:
                raise CombatCatalogError(f"combat spell repeat save is invalid: {spell_id}")
            target_creature_types = effect.get("target_creature_types")
            if target_creature_types is not None and (
                not isinstance(target_creature_types, list)
                or not target_creature_types
                or any(not isinstance(kind, str) or not kind for kind in target_creature_types)
            ):
                raise CombatCatalogError(
                    f"combat spell target creature types are invalid: {spell_id}"
                )
            if "repeat_targets" in effect and not isinstance(effect["repeat_targets"], bool):
                raise CombatCatalogError(
                    f"combat spell repeat targets flag is invalid: {spell_id}"
                )
            if effect["mode"] == "temp_hp" and not isinstance(effect.get("temp_hp"), str):
                # 公式本身由 spell_formula_node 在结算时 fail closed；这里只锁类型。
                raise CombatCatalogError(f"combat spell temp HP formula is invalid: {spell_id}")
            upcast_flat = effect.get("upcast_flat")
            if upcast_flat is not None and (
                isinstance(upcast_flat, bool)
                or not isinstance(upcast_flat, int)
                or not 0 < upcast_flat <= 50
            ):
                raise CombatCatalogError(
                    f"combat spell upcast flat bonus is invalid: {spell_id}"
                )
            if not 5 <= int(effect.get("range", 0) or 0) <= 600:
                raise CombatCatalogError(f"combat spell range is invalid: {spell_id}")
        conditions = cls._validated_conditions(raw.get("conditions"))
        labels = raw.get("labels") or {}
        if not isinstance(labels, dict):
            raise CombatCatalogError("combat catalog labels must be an object")
        return cls(
            source_ref=str(raw.get("source_ref") or ""),
            weapons=deepcopy(weapons),
            spell_effects=deepcopy(effects),
            unarmed_strike=deepcopy(cls._validated_unarmed_strike(raw.get("unarmed_strike"))),
            conditions=deepcopy(conditions),
            labels={str(key): str(value) for key, value in labels.items()},
        )

    @staticmethod
    def _validated_conditions(raw: Any) -> dict[str, dict[str, Any]]:
        """Validate the optional condition -> attack-mode registry.

        Missing data means "no condition declares attack modifiers"; the engine
        then simply has no registry sources instead of owning a hard-coded list.
        Every entry is checked here so combat code may trust the shape.
        """

        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise CombatCatalogError("combat conditions must be an object")
        result: dict[str, dict[str, Any]] = {}
        for condition_id, entry in raw.items():
            if not isinstance(entry, dict):
                raise CombatCatalogError(
                    f"combat condition must be an object: {condition_id}"
                )
            if set(entry) - {
                "attack_modes", "resist_damage_types", "implies_incapacitated",
                "speed_zero", "auto_fail_saves", "crit_on_hit_within_5",
                "speed_modifier",
            }:
                raise CombatCatalogError(
                    f"combat condition has unknown fields: {condition_id}"
                )
            for flag in ("implies_incapacitated", "speed_zero", "crit_on_hit_within_5"):
                if flag in entry and not isinstance(entry[flag], bool):
                    raise CombatCatalogError(
                        f"combat condition flag must be a boolean: {condition_id}"
                    )
            speed_modifier = entry.get("speed_modifier")
            if speed_modifier is not None and (
                isinstance(speed_modifier, bool)
                or not isinstance(speed_modifier, int)
                or speed_modifier == 0
                or not -600 <= speed_modifier <= 600
            ):
                raise CombatCatalogError(
                    f"combat condition speed modifier is invalid: {condition_id}"
                )
            auto_fail = entry.get("auto_fail_saves")
            if auto_fail is not None and (
                not isinstance(auto_fail, list)
                or any(
                    not isinstance(ability, str) or ability not in SAVE_ABILITIES
                    for ability in auto_fail
                )
            ):
                raise CombatCatalogError(
                    f"combat condition auto-fail saves are invalid: {condition_id}"
                )
            resist_types = entry.get("resist_damage_types")
            if resist_types is not None and (
                not isinstance(resist_types, list)
                or not resist_types
                or any(
                    not isinstance(kind, str) or kind not in DAMAGE_TYPES
                    for kind in resist_types
                )
                or len(set(resist_types)) != len(resist_types)
            ):
                raise CombatCatalogError(
                    f"combat condition resist types are invalid: {condition_id}"
                )
            modes = entry.get("attack_modes")
            # A condition may declare resistance or a passive flag only; if it
            # declares neither mechanics nor attack modes the entry is still
            # rejected, matching the P1 rule that every entry states something.
            if modes is None and (
                resist_types is not None
                or any(
                    key in entry for key in (
                        "implies_incapacitated", "speed_zero",
                        "auto_fail_saves", "crit_on_hit_within_5", "speed_modifier",
                    )
                )
            ):
                result[str(condition_id)] = deepcopy(entry)
                continue
            if not isinstance(modes, list):
                raise CombatCatalogError(
                    f"combat condition attack_modes must be an array: {condition_id}"
                )
            for declaration in modes:
                if not isinstance(declaration, dict):
                    raise CombatCatalogError(
                        f"combat condition attack mode must be an object: {condition_id}"
                    )
                if set(declaration) - {"side", "mode", "negated_by", "consume_on"}:
                    raise CombatCatalogError(
                        f"combat condition attack mode has unknown fields: {condition_id}"
                    )
                if (
                    declaration.get("side") not in {"actor", "target"}
                    or declaration.get("mode") not in {"advantage", "disadvantage"}
                ):
                    raise CombatCatalogError(
                        f"combat condition attack mode is invalid: {condition_id}"
                    )
                negated_by = declaration.get("negated_by")
                if negated_by is not None and (
                    not isinstance(negated_by, list)
                    or any(not isinstance(code, str) or not code for code in negated_by)
                ):
                    raise CombatCatalogError(
                        f"combat condition negated_by is invalid: {condition_id}"
                    )
                if "consume_on" in declaration and declaration["consume_on"] != "attack_resolved":
                    raise CombatCatalogError(
                        f"combat condition consume_on is invalid: {condition_id}"
                    )
            result[str(condition_id)] = deepcopy(entry)
        return result

    @staticmethod
    def _validated_unarmed_strike(raw: Any) -> dict[str, Any] | None:
        """Validate the optional canonical Unarmed Strike profile.

        Missing data means "this content does not declare unarmed strikes": the
        engine then exposes none, instead of inventing mechanics in code.
        """

        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise CombatCatalogError("combat unarmed strike profile must be an object")
        damage = str(raw.get("damage") or "")
        if not (DICE_RE.fullmatch(damage) or FLAT_DAMAGE_RE.fullmatch(damage)):
            raise CombatCatalogError("combat unarmed strike damage is invalid")
        if not str(raw.get("damage_type") or "").strip():
            raise CombatCatalogError("combat unarmed strike damage type is invalid")
        if not 5 <= int(raw.get("range", 0) or 0) <= 600:
            raise CombatCatalogError("combat unarmed strike range is invalid")
        return raw
