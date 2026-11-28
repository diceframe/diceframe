"""Validated compact combat mechanics from the SRD bundle."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle

from ..items.activation import validate_item_declarations
from ..character.primitives import ABILITY_IDS


DICE_RE = re.compile(r"^[1-9]\d*d(?:4|6|8|10|12|20)(?:\+[1-9]\d*)?$")

#: 条件能施加劣势的三类检定。与 `check_modifiers.surface` 不同——那个
#: 只有 `skill_check` 有消费者，这里三类各有结算入口（`_ability_check_events`
#: / `_skill_check_events` / `_saving_throw_check`）。
CHECK_SURFACES = frozenset({"ability_check", "skill_check", "saving_throw"})

#: `spell_effects.ongoing` 的两种触发点。`bonus_action` / `magic_action` 不在
#: 这里：那是"授予一个动作"，属 capability，不是自动 tick。
ONGOING_TRIGGERS = frozenset({"turn_end", "caster_turn_start"})
# 固定伤害（例如徒手打击的 1 点基础伤害）：没有骰子，因此重击不翻倍。
FLAT_DAMAGE_RE = re.compile(r"^[1-9]\d{0,3}$")
_DICE_PARTS_RE = re.compile(r"^([1-9]\d*)d(4|6|8|10|12|20)(?:\+([1-9]\d*))?$")


def _dice_average(formula: str) -> float:
    """Expected value of one dice formula, used only for data-shape checks."""

    match = _DICE_PARTS_RE.fullmatch(formula)
    if match is None:
        return 0.0
    count, sides, bonus = match.groups()
    return int(count) * (int(sides) + 1) / 2 + int(bonus or 0)


# SRD 5.2.1 p91：Two-Handed 的"骑乘例外"只出现在骑枪上。合法值做成闭集，
# 否则 ``two_handed_exception`` 会退化成"随便写的豁免开关"。
TWO_HANDED_HAND_EXCEPTIONS = frozenset({"mounted"})


def _validate_weapon_hand_properties(weapon_id: str, weapon: dict[str, Any]) -> None:
    """Validate the weapon's base hand properties.

    ``two_handed`` / ``loading`` are closed-list SRD properties: a weapon either
    has the property or it does not, so there is no third "unknown" state. The
    bundle must therefore declare both keys explicitly — a forgotten
    declaration is a load error rather than a silently weaker weapon. External
    content is a different problem (it is not loaded through this catalog).
    """

    for prop in ("two_handed", "loading"):
        if prop not in weapon:
            raise CombatCatalogError(
                f"combat weapon {weapon_id} must declare {prop!r} explicitly"
            )
        if not isinstance(weapon[prop], bool):
            raise CombatCatalogError(
                f"combat weapon {prop} must be a boolean: {weapon_id}"
            )
    if weapon["two_handed"] and "damage_two_handed" in weapon:
        # 两用是"可单手可双手"，与"必须双手"互斥。
        raise CombatCatalogError(
            f"a two-handed weapon cannot also declare versatile damage: {weapon_id}"
        )
    if weapon["two_handed"] and bool(weapon.get("light")):
        raise CombatCatalogError(f"a two-handed weapon cannot be light: {weapon_id}")
    if weapon["loading"] and not bool(weapon.get("ranged")):
        # Loading 描述的是"装填-射击"的节流，近战武器没有这个语义。
        raise CombatCatalogError(f"only a ranged weapon can be loading: {weapon_id}")
    exception = weapon.get("two_handed_exception")
    if exception is not None:
        if not weapon["two_handed"]:
            raise CombatCatalogError(
                f"two_handed_exception requires a two-handed weapon: {weapon_id}"
            )
        if exception not in TWO_HANDED_HAND_EXCEPTIONS:
            raise CombatCatalogError(
                f"combat weapon hand exception is invalid: {weapon_id}"
            )


SPELL_MODES = frozenset({
    "spell_attack", "saving_throw", "healing", "automatic_damage", "buff", "debuff", "triggered_damage",
    "stabilize", "reaction_defense", "temp_hp", "teleport", "dispel", "revive",
    # 区域遮蔽：法术自己不定目标伤害，只在场上落一个区域事实。
    "obscuring_zone",
})
DAMAGE_TYPES = frozenset({
    "bludgeoning", "slashing", "piercing", "acid", "cold", "fire", "lightning",
    "thunder", "poison", "psychic", "force", "necrotic", "radiant",
})
SAVE_ABILITIES = frozenset({"str", "dex", "con", "int", "wis", "cha"})
# check_bonus_dice 使用规则语言的语义检定类别，而不是事件 kind 值。
CHECK_BONUS_KINDS = frozenset({"attack", "saving_throw", "ability_check"})
CHECK_BONUS_DIE_SIDES = frozenset({4, 6, 8, 10, 12, 20})


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
    # 来源绑定效应与规则书状态分表存放，但解释器只需要一张合并注册表；
    # 这里两者都留着：``conditions`` 是合并视图（运行时读），``effects``
    # 是效应自己的目录（反查来源用）。
    effects: dict[str, dict[str, Any]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)

    def gm_appliable_conditions(self) -> dict[str, dict[str, Any]]:
        """The states a GM may hand out, i.e. the rulebook conditions.

        ``conditions`` remains the merged runtime registry so every existing
        reader (``condition_flags`` 等) keeps working; the split between state
        and source-bound effect is data-level (``kind``).  Effects are produced
        by a spell / feature / item and are never handed out by hand.
        """

        return {
            condition_id: entry
            for condition_id, entry in self.conditions.items()
            if str(entry.get("kind") or "") == "condition"
        }

    def condition_definition(self, condition_id: str) -> dict[str, Any] | None:
        """A rulebook state's three facts: name, rules summary, mechanical flags.

        名称与摘要是 locale 文本（``labels`` 通道），机械 flag 留在 canonical
        条目里。GM 选择器、战斗面板与将来的规则查询都从这里读，不各自拼。
        """

        entry = self.conditions.get(condition_id)
        if entry is None or str(entry.get("kind") or "") != "condition":
            return None
        return {
            "id": condition_id,
            "name": self.labels.get(f"condition.{condition_id}") or condition_id,
            "summary": self.labels.get(f"condition.{condition_id}.summary") or "",
            "effects": {
                key: deepcopy(value) for key, value in entry.items() if key != "kind"
            },
        }

    def effects_for_source(self, source_ref: str) -> list[str]:
        """Effects produced by one source, e.g. ``feature:brutal_strike``.

        来源侧不写反向指针：两份表各存一次同一个关系，只会在改名时漂移。
        反查由效应目录派生，永远和 ``source_ref`` 一致。
        """

        return sorted(
            effect_id
            for effect_id, entry in self.effects.items()
            if str(entry.get("source_ref") or "") == source_ref
        )

    def condition_catalog(self) -> list[dict[str, Any]]:
        """Every state a GM may hand out, in stable id order."""

        rows = (
            self.condition_definition(condition_id)
            for condition_id in sorted(self.gm_appliable_conditions())
        )
        return [row for row in rows if row is not None]

    @classmethod
    def from_bundle(
        cls, bundle: LoadedRulesetBundle, *, validate_items: bool = True,
    ) -> Dnd2024CombatCatalog:
        raw = bundle.get("combat_catalog", "srd_combat_core")
        if raw is None:
            raise CombatCatalogError("D&D 2024 combat catalog is missing")
        weapons = raw.get("weapons")
        effects = raw.get("spell_effects")
        if not isinstance(weapons, dict) or not isinstance(effects, dict):
            raise CombatCatalogError("combat weapons and spell effects must be objects")
        items = {
            str(item["id"]): item for item in bundle.list("item") if isinstance(item, dict)
        }
        if validate_items:
            try:
                validate_item_declarations(bundle)
            except ValueError as exc:
                raise CombatCatalogError(str(exc)) from None
        for weapon_id, weapon in weapons.items():
            if weapon_id not in items or not isinstance(weapon, dict):
                raise CombatCatalogError(f"combat weapon is not a bundled item: {weapon_id}")
            damage = str(weapon.get("damage") or "")
            if not (DICE_RE.fullmatch(damage) or FLAT_DAMAGE_RE.fullmatch(damage)):
                # SRD 的吹箭筒只造成固定 1 点伤害；固定伤害与徒手打击同源，
                # 走同一套"没有骰子、重击不翻倍"的结算，不是特例分支。
                raise CombatCatalogError(f"combat weapon damage is invalid: {weapon_id}")
            if not 5 <= int(weapon.get("range", 0) or 0) <= 600:
                raise CombatCatalogError(f"combat weapon range is invalid: {weapon_id}")
            _validate_weapon_hand_properties(weapon_id, weapon)
            if "damage_two_handed" in weapon:
                two_handed = weapon["damage_two_handed"]
                if not isinstance(two_handed, str) or not DICE_RE.fullmatch(two_handed):
                    raise CombatCatalogError(
                        f"combat weapon two-handed damage is invalid: {weapon_id}"
                    )
                # 两用是"换取更大骰子"的声明：固定伤害或远程武器携带该字段，
                # 以及双手骰并不优于单手骰，都是数据错误而不是可选写法。
                if DICE_RE.fullmatch(damage) is None or bool(weapon.get("ranged")):
                    raise CombatCatalogError(
                        f"weapon cannot declare two-handed damage: {weapon_id}"
                    )
                if _dice_average(two_handed) <= _dice_average(damage):
                    raise CombatCatalogError(
                        f"two-handed damage does not exceed one-handed: {weapon_id}"
                    )
            if "ammunition_item_ref" in weapon:
                ammo_ref = weapon["ammunition_item_ref"]
                if not isinstance(ammo_ref, str) or not ammo_ref.startswith("item:"):
                    raise CombatCatalogError(
                        f"combat weapon ammunition ref is invalid: {weapon_id}"
                    )
                if not bool(weapon.get("ranged")):
                    raise CombatCatalogError(
                        f"only ranged weapons consume ammunition: {weapon_id}"
                    )
                ammo = items.get(ammo_ref.removeprefix("item:"))
                if not isinstance(ammo, dict) or ammo.get("item_type") != "ammunition":
                    raise CombatCatalogError(
                        f"combat weapon ammunition is not a bundled ammunition item: {weapon_id}"
                    )
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
            # S0-SP2：`condition` 允许字符串或 {choices, picked} 对象。对象形态
            # 只服务"二选一"（Blindness/Deafness），所以只允许这两个键。
            condition = effect.get("condition")
            if isinstance(condition, dict):
                if set(condition) - {"choices", "picked"}:
                    raise CombatCatalogError(
                        f"combat spell condition object has unknown fields: {spell_id}"
                    )
                choices = condition.get("choices")
                # 与字符串形态一致：只校验形状，不校验候选项是否在当前
                # catalog 里登记过。字符串 `condition` 从来不做存在性校验，
                # 对象形态更严格会让"删掉 conditions 的最小 catalog"装不上。
                if (
                    not isinstance(choices, list) or not choices
                    or any(not isinstance(c, str) or not c for c in choices)
                ):
                    raise CombatCatalogError(
                        f"combat spell condition choices are invalid: {spell_id}"
                    )
                picked = condition.get("picked", 0)
                if isinstance(picked, bool) or not isinstance(picked, int) or not 0 <= picked < len(choices):
                    raise CombatCatalogError(
                        f"combat spell condition picked is out of range: {spell_id}"
                    )
            elif condition is not None and not isinstance(condition, str):
                raise CombatCatalogError(
                    f"combat spell condition is invalid: {spell_id}"
                )
            ongoing = effect.get("ongoing")
            if ongoing is not None:
                if not isinstance(ongoing, dict):
                    raise CombatCatalogError(
                        f"combat spell ongoing must be an object: {spell_id}"
                    )
                if set(ongoing) - {"trigger", "save_repeat", "damage", "damage_type"}:
                    raise CombatCatalogError(
                        f"combat spell ongoing has unknown fields: {spell_id}"
                    )
                if str(ongoing.get("trigger") or "") not in ONGOING_TRIGGERS:
                    raise CombatCatalogError(
                        f"combat spell ongoing trigger is invalid: {spell_id}"
                    )
                repeat = ongoing.get("save_repeat")
                if repeat is not None and str(repeat) not in ABILITY_IDS:
                    raise CombatCatalogError(
                        f"combat spell ongoing save_repeat is invalid: {spell_id}"
                    )
                dice = ongoing.get("damage")
                if dice is not None and not DICE_RE.fullmatch(str(dice)):
                    raise CombatCatalogError(
                        f"combat spell ongoing damage is invalid: {spell_id}"
                    )
                if repeat is None and dice is None:
                    raise CombatCatalogError(
                        f"combat spell ongoing does nothing: {spell_id}"
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
        states = cls._validated_conditions(raw.get("conditions"))
        # 顶层 ``effects`` 是 bundle 自己保留的字段（实体效果操作数组），
        # 所以状态效应目录用 ``condition_effects``，避免撞名。
        condition_effects = cls._validated_conditions(raw.get("condition_effects"))
        overlap = sorted(set(states) & set(condition_effects))
        if overlap:
            # 合并视图里后者会无声盖掉前者：一条规则书状态就这样变成不可手工施加
            # 的效应，而没有任何地方报错。
            raise CombatCatalogError(
                f"combat condition is declared in both sections: {overlap}"
            )
        # 解释器（condition_flags / attack_modes / resist）只按 flag 取值，
        # 所以运行时必须看到合并视图；分表的目的是"谁能被手工施加"。
        conditions = {**states, **condition_effects}
        labels = raw.get("labels") or {}
        if not isinstance(labels, dict):
            raise CombatCatalogError("combat catalog labels must be an object")
        return cls(
            source_ref=str(raw.get("source_ref") or ""),
            weapons=deepcopy(weapons),
            spell_effects=deepcopy(effects),
            unarmed_strike=deepcopy(cls._validated_unarmed_strike(raw.get("unarmed_strike"))),
            conditions=deepcopy(conditions),
            effects=deepcopy(condition_effects),
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
                "kind", "source_ref", "attack_modes", "resist_damage_types", "implies_incapacitated",
                "speed_zero", "auto_fail_saves", "crit_on_hit_within_5",
                "speed_modifier", "speed_halved", "check_bonus_dice",
                "initiative_advantage", "cannot_attack_source", "cannot_approach_source",
                # S0-SP2：条件对**检定**的劣势与对**伤害骰**的减值。
                # 与 `check_modifiers`（特性给的加值/替换）不是同一件事：那是
                # "改数值"，这是"优劣势"，两套并存。
                "check_modes", "damage_roll_modifier",
                # S0-MM3e：条件自带"怎么解除"，与"怎么施加"分开声明。
                "escape", "stand_up",
            }:
                raise CombatCatalogError(
                    f"combat condition has unknown fields: {condition_id}"
                )
            # 状态与来源绑定效应共用一个运行时注册表（解释器只按 flag 取值），
            # 但只有 ``condition`` 是规则书里的公共状态、可以由 GM 直接施加；
            # ``effect`` 是法术/特性/物品自己制造的产物，一对一绑在来源上。
            check_modes = entry.get("check_modes")
            if check_modes is not None:
                if not isinstance(check_modes, list):
                    raise CombatCatalogError(
                        f"combat condition check_modes must be an array: {condition_id}"
                    )
                for row in check_modes:
                    if not isinstance(row, dict):
                        raise CombatCatalogError(
                            f"combat condition check_modes row must be an object: {condition_id}"
                        )
                    if set(row) - {"surface", "mode", "abilities"}:
                        raise CombatCatalogError(
                            f"combat condition check_modes row has unknown fields: {condition_id}"
                        )
                    if str(row.get("surface") or "") not in CHECK_SURFACES:
                        raise CombatCatalogError(
                            f"combat condition check_modes surface is invalid: {condition_id}"
                        )
                    # 只有 disadvantage 有消费者。写 advantage 会静默无效，
                    # 与"枚举值存在但引擎丢弃"是同一类问题。
                    if str(row.get("mode") or "") != "disadvantage":
                        raise CombatCatalogError(
                            f"combat condition check_modes mode is invalid: {condition_id}"
                        )
                    abilities = row.get("abilities")
                    if abilities is not None and (
                        not isinstance(abilities, list)
                        or any(str(a) not in ABILITY_IDS for a in abilities)
                    ):
                        raise CombatCatalogError(
                            f"combat condition check_modes abilities are invalid: {condition_id}"
                        )
            modifier = entry.get("damage_roll_modifier")
            if modifier is not None:
                if not isinstance(modifier, dict) or set(modifier) != {"dice"}:
                    raise CombatCatalogError(
                        f"combat condition damage_roll_modifier must be {{dice}}: {condition_id}"
                    )
                if not DICE_RE.fullmatch(str(modifier["dice"]).lstrip("-")):
                    raise CombatCatalogError(
                        f"combat condition damage_roll_modifier dice is invalid: {condition_id}"
                    )
            kind = entry.get("kind")
            if kind is not None and str(kind) not in {"condition", "effect"}:
                raise CombatCatalogError(
                    f"combat condition kind is invalid: {condition_id}"
                )
            # 效应是某个来源的产物，必须说清它来自哪里；状态是规则书条目，
            # 来源就是规则书本身，不需要反向指针。
            source_ref = entry.get("source_ref")
            if kind == "effect" and not (
                isinstance(source_ref, str) and source_ref.strip()
            ):
                raise CombatCatalogError(
                    f"combat effect must name its source: {condition_id}"
                )
            for flag in (
                "implies_incapacitated", "speed_zero", "crit_on_hit_within_5",
                "initiative_advantage", "cannot_attack_source", "cannot_approach_source",
                "speed_halved",
            ):
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
            bonus_dice = entry.get("check_bonus_dice")
            if bonus_dice is not None and (
                not isinstance(bonus_dice, list)
                or not bonus_dice
                or any(
                    not isinstance(declaration, dict)
                    or set(declaration) != {"die", "kinds"}
                    or isinstance(declaration.get("die"), bool)
                    or not isinstance(declaration.get("die"), int)
                    or declaration.get("die") not in CHECK_BONUS_DIE_SIDES
                    or not isinstance(declaration.get("kinds"), list)
                    or not declaration.get("kinds")
                    or any(
                        not isinstance(kind, str) or kind not in CHECK_BONUS_KINDS
                        for kind in declaration["kinds"]
                    )
                    or len(set(declaration["kinds"])) != len(declaration["kinds"])
                    for declaration in bonus_dice
                )
            ):
                raise CombatCatalogError(
                    f"combat condition bonus dice are invalid: {condition_id}"
                )
            # A condition may declare resistance, a passive flag, or bonus
            # dice only; if it declares no mechanics and no attack modes the
            # entry is still rejected, matching the P1 rule that every entry
            # states something.
            if modes is None and (
                resist_types is not None
                or bonus_dice is not None
                or any(
                    key in entry for key in (
                        "implies_incapacitated", "speed_zero",
                        "auto_fail_saves", "crit_on_hit_within_5", "speed_modifier",
                        "speed_halved", "cannot_attack_source", "cannot_approach_source",
                        "escape", "stand_up",
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
                if set(declaration) - {
                    "side", "mode", "negated_by", "consume_on", "within_ft", "beyond_ft",
                }:
                    raise CombatCatalogError(
                        f"combat condition attack mode has unknown fields: {condition_id}"
                    )
                for bound in ("within_ft", "beyond_ft"):
                    value = declaration.get(bound)
                    if value is None:
                        continue
                    if (
                        isinstance(value, bool) or not isinstance(value, int)
                        or not 0 <= value <= 600
                    ):
                        raise CombatCatalogError(
                            f"combat condition attack mode {bound} is invalid: {condition_id}"
                        )
                if "within_ft" in declaration and "beyond_ft" in declaration:
                    raise CombatCatalogError(
                        "combat condition attack mode cannot be both within and beyond"
                        f": {condition_id}"
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
            Dnd2024CombatCatalog._validate_condition_release(entry, str(condition_id))
            result[str(condition_id)] = deepcopy(entry)
        return result

    @staticmethod
    def _validate_condition_release(entry: dict[str, Any], condition_id: str) -> None:
        """S0-MM3e: how a condition can be ended from inside it.

        ``escape`` names the checks that may remove it and what they cost;
        ``stand_up`` names the movement price of ending it yourself.  The DC is
        *not* here: two creatures can grab with different DCs, so the number
        lives on the applied instance and this block only says what shape the
        attempt has.
        """

        escape = entry.get("escape")
        if escape is not None:
            if not isinstance(escape, dict) or set(escape) - {"cost", "checks"}:
                raise CombatCatalogError(
                    f"combat condition escape is invalid: {condition_id}"
                )
            if escape.get("cost") not in {"action", "bonus_action"}:
                raise CombatCatalogError(
                    f"combat condition escape cost is invalid: {condition_id}"
                )
            checks = escape.get("checks")
            if not isinstance(checks, list) or not 1 <= len(checks) <= 4:
                raise CombatCatalogError(
                    f"combat condition escape checks are invalid: {condition_id}"
                )
            seen_abilities: set[str] = set()
            for row in checks:
                if not isinstance(row, dict) or set(row) - {"ability", "skill"}:
                    raise CombatCatalogError(
                        f"combat condition escape check is invalid: {condition_id}"
                    )
                ability = row.get("ability")
                if ability not in SAVE_ABILITIES or ability in seen_abilities:
                    raise CombatCatalogError(
                        f"combat condition escape ability is invalid: {condition_id}"
                    )
                seen_abilities.add(str(ability))
                skill = row.get("skill")
                if skill is not None and (not isinstance(skill, str) or not skill):
                    raise CombatCatalogError(
                        f"combat condition escape skill is invalid: {condition_id}"
                    )
        stand_up = entry.get("stand_up")
        if stand_up is not None:
            if not isinstance(stand_up, dict) or set(stand_up) - {"cost", "fraction"}:
                raise CombatCatalogError(
                    f"combat condition stand up is invalid: {condition_id}"
                )
            if stand_up.get("cost") != "movement":
                raise CombatCatalogError(
                    f"combat condition stand up cost is invalid: {condition_id}"
                )
            fraction = stand_up.get("fraction")
            if (
                isinstance(fraction, bool) or not isinstance(fraction, int)
                or not 1 <= fraction <= 4
            ):
                raise CombatCatalogError(
                    f"combat condition stand up fraction is invalid: {condition_id}"
                )

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
