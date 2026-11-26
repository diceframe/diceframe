"""Metamagic: one修饰 per casting, except where the option says otherwise.

The ten options are *modifiers on a casting*, not spells and not features. This
module owns the two things that must be decided before any of them can act:
which options are legal together, and what they cost.

Rules that shape the model:

* only one Metamagic option per casting, unless an option says it can be used
  alongside another (Empowered Spell and Seeking Spell do);
* Sorcery Incarnate (level 7) raises that ceiling to two;
* Arcane Apotheosis (level 20) makes one option per turn free, which is a
  discount, not an extra slot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

DAMAGE_TYPES = ("acid", "cold", "fire", "lightning", "poison", "thunder")


class MetamagicError(ValueError):
    """A metamagic request that cannot be honoured."""


@dataclass(frozen=True)
class Option:
    id: str
    cost: int
    exclusive: bool
    parameter: str
    requires: Mapping[str, Any] = field(default_factory=dict)


#: 十个选项。`exclusive=False` 的两个是原文明确写着"可以与别的超魔同时使用"的。
OPTIONS: dict[str, Option] = {
    "careful_spell": Option("careful_spell", 1, True, "targets",
                            {"saving_throw": True}),
    "distant_spell": Option("distant_spell", 1, True, "none",
                            {"range_min_ft": 5}),
    "empowered_spell": Option("empowered_spell", 1, False, "count",
                              {"damage": True}),
    "extended_spell": Option("extended_spell", 1, True, "none",
                             {"duration_min_minutes": 1}),
    "heightened_spell": Option("heightened_spell", 2, True, "target",
                               {"saving_throw": True}),
    "quickened_spell": Option("quickened_spell", 2, True, "none",
                              {"casting_time": "action"}),
    "seeking_spell": Option("seeking_spell", 1, False, "none",
                            {"attack_roll": True}),
    "subtle_spell": Option("subtle_spell", 1, True, "none", {}),
    "transmuted_spell": Option("transmuted_spell", 1, True, "damage_type",
                               {"damage_types": list(DAMAGE_TYPES)}),
    "twinned_spell": Option("twinned_spell", 1, True, "none",
                            {"upcast_target_bonus": True}),
}


@dataclass(frozen=True)
class Plan:
    applied: tuple[str, ...]
    cost: int
    free_used: int

    def has(self, option_id: str) -> bool:
        return option_id in self.applied

    def parameter(self, option_id: str) -> Any:
        return self._parameters.get(option_id)

    _parameters: Mapping[str, Any] = field(default_factory=dict)


def slots_available(*, incarnate: bool) -> int:
    """How many exclusive options one casting may carry."""

    return 2 if incarnate else 1


def plan(
    intent: Mapping[str, Any],
    *,
    spell: Mapping[str, Any],
    effect: Mapping[str, Any],
    known: Mapping[str, Any],
    points: int,
    incarnate: bool,
    apotheosis: bool,
    free_used_this_turn: bool,
    cast_level_one_plus_this_turn: bool,
) -> Plan:
    """Validate a metamagic request and price it.

    ``known`` maps option id -> parameter payload the player supplied for this
    casting (empty dict when the option takes none).
    """

    requested = intent.get("metamagic") or []
    if not isinstance(requested, list):
        raise MetamagicError("metamagic must be a list")
    if not requested:
        return Plan((), 0, 0)

    seen: list[str] = []
    parameters: dict[str, Any] = {}
    exclusive = 0
    cost = 0
    for row in requested:
        if not isinstance(row, dict):
            raise MetamagicError("each metamagic entry must be an object")
        option_id = str(row.get("option_id") or "")
        meta = OPTIONS.get(option_id)
        if meta is None:
            raise MetamagicError(f"unknown metamagic option: {option_id!r}")
        if option_id not in known:
            raise MetamagicError(f"that metamagic option is not known: {option_id}")
        if option_id in seen:
            raise MetamagicError(f"that metamagic option is applied twice: {option_id}")
        seen.append(option_id)
        _check_requires(meta, spell, effect, cast_level_one_plus_this_turn)
        parameters[option_id] = _parameter_payload(meta, row, effect)
        cost += meta.cost
        if meta.exclusive:
            exclusive += 1

    if exclusive > slots_available(incarnate=incarnate):
        raise MetamagicError(
            "only one metamagic option can be applied to a spell"
            if not incarnate else "at most two metamagic options can be applied"
        )
    if cast_level_one_plus_this_turn and "quickened_spell" in seen:
        raise MetamagicError(
            "Quickened Spell cannot be used after casting a level 1+ spell this turn"
        )
    # Apotheosis 免掉的是"其中一个选项"的整份费用，不是固定 1 点：玩家显然会
    # 挑最贵的那个，所以按最贵的一项抵扣。
    free = max((OPTIONS[option_id].cost for option_id in seen), default=0) if (
        apotheosis and not free_used_this_turn
    ) else 0
    payable = max(0, cost - free)
    if payable > points:
        raise MetamagicError("not enough Sorcery Points for that metamagic")
    return Plan(tuple(seen), payable, min(free, cost), parameters)


def _check_requires(
    meta: Option, spell: Mapping[str, Any], effect: Mapping[str, Any],
    cast_level_one_plus_this_turn: bool,
) -> None:
    requires = meta.requires
    if requires.get("saving_throw") and not _saves(effect):
        raise MetamagicError(f"{meta.id} requires a spell that forces a saving throw")
    if "range_min_ft" in requires:
        # 数据里 Self 法术也带一个 range 数值（Burning Hands 是锥形长度 15、
        # Shield 是 5），只看数值会把它们放行，而结算期 Self 又原样返回——
        # 玩家付掉 1 点、什么也没变。所以按原文先排除 Self。
        if _self_only(spell):
            raise MetamagicError(
                f"{meta.id} cannot extend a spell whose range is Self"
            )
        range_ft = _range_ft(effect, spell)
        if range_ft < int(requires["range_min_ft"]):
            raise MetamagicError(f"{meta.id} requires a spell with range 5 feet or more")
    if "duration_min_minutes" in requires:
        if _duration_minutes(effect, spell) < int(requires["duration_min_minutes"]):
            raise MetamagicError(f"{meta.id} requires a spell lasting 1 minute or more")
    if requires.get("casting_time") == "action":
        if str(effect.get("casting_time") or spell.get("casting_time") or "").lower() != "action":
            raise MetamagicError(f"{meta.id} requires a casting time of an action")
    if "damage_types" in requires and not _damage_type(effect, spell):
        raise MetamagicError(f"{meta.id} requires a spell that deals damage")
    if requires.get("attack_roll") and not _attack_roll(effect):
        raise MetamagicError(f"{meta.id} requires a spell attack roll")
    if requires.get("upcast_target_bonus") and not bool(
        effect.get("upcast_target_bonus")
        or int(effect.get("upcast_target_count", 0) or 0) > 0
    ):
        # 原文的"可升环多选一个目标"在数据里有两种写法：布尔标记，或升环
        # 目标数。两种都认，缺了才拒绝。
        raise MetamagicError(f"{meta.id} requires a spell that can gain a target when upcast")
    del cast_level_one_plus_this_turn


def _parameter_payload(
    meta: Option, row: Mapping[str, Any], effect: Mapping[str, Any],
) -> Any:
    kind = meta.parameter
    if kind == "none":
        return None
    if kind == "damage_type":
        chosen = str(row.get("damage_type") or "")
        original = _damage_type(effect, {})
        if original not in DAMAGE_TYPES:
            # 原文只允许在六种之间切换：Radiant/Necrotic/Force 等不在列表内，
            # 这类法术不能被 Transmuted。
            raise MetamagicError(
                "Transmuted Spell only applies to acid, cold, fire, lightning, poison or thunder"
            )
        allowed = [t for t in DAMAGE_TYPES if t != original]
        if chosen not in allowed:
            raise MetamagicError("Transmuted Spell must pick one of the other damage types")
        return chosen
    if kind == "damage_type_allowed":
        return None
    if kind == "targets":
        targets = row.get("targets")
        if not isinstance(targets, list) or not targets:
            raise MetamagicError("that metamagic needs at least one target")
        return tuple(str(ref) for ref in targets)
    if kind == "target":
        target = str(row.get("target") or "")
        if not target:
            raise MetamagicError("that metamagic needs a target")
        return target
    if kind == "count":
        count = row.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise MetamagicError("Empowered Spell needs a positive reroll count")
        return count
    return None


def known_after_picks(known: Sequence[str], picked: Sequence[str]) -> list[str]:
    """What a character knows once this level's new picks are written.

    Level-up writes picks before replacements, and a replacement may drop an
    option picked in the same batch. Both the validator and the writer have to
    treat those picks as already known, or a preview refresh in between changes
    which options the two dropdowns offer.
    """

    result = [str(item) for item in known]
    for option_id in picked:
        option_id = str(option_id)
        if option_id not in result:
            result.append(option_id)
    return result


def resolve_replacement(
    *, known: Sequence[str], picked: Sequence[str], drop: str, add: str,
) -> list[str]:
    """Swap one known option for one unknown option.

    ``picked`` is this level's new picks; they count as known. Refusing an
    ``add`` that is already known *after* those picks is what keeps the list
    from ending a level with the same option twice.
    """

    current = known_after_picks(known, picked)
    if drop not in current:
        raise MetamagicError("metamagic_replace.drop must be a known option")
    if add not in OPTIONS:
        raise MetamagicError(f"unknown metamagic option: {add!r}")
    if add in current:
        raise MetamagicError("metamagic_replace.add must be an option you do not know")
    current[current.index(drop)] = add
    return current


def effective_components(
    spell: Mapping[str, Any], plan: Plan,
) -> tuple[str, ...]:
    """The components a casting actually has, after Subtle Spell.

    One authority for both the casting gate and the Counterspell window: if the
    two read different things, a player can claim a silent casting that the
    reaction believes it saw.
    """

    if not plan.has("subtle_spell"):
        return tuple(str(c) for c in spell.get("components") or ())
    if spell.get("material_consumed") or spell.get("material_cost_specified"):
        return ("M",)
    return ()


def _saves(effect: Mapping[str, Any]) -> bool:
    return str(effect.get("mode") or "") in {
        "save_damage", "save_or_condition", "save_negates", "saving_throw",
    } or bool(effect.get("save_ability"))


def _range_ft(effect: Mapping[str, Any], spell: Mapping[str, Any]) -> int:
    raw = effect.get("range", spell.get("range", 0))
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw or "").lower()
    if text.startswith("touch"):
        return 0
    if text.startswith("self"):
        return 0
    digits = "".join(c for c in text if c.isdigit())
    return int(digits) if digits else 0


def _duration_minutes(effect: Mapping[str, Any], spell: Mapping[str, Any]) -> int:
    rounds = int(effect.get("duration_rounds", 0) or 0)
    if rounds:
        return max(1, rounds // 10)
    text = str(effect.get("duration", spell.get("duration", "")) or "").lower()
    if "minute" in text:
        digits = "".join(c for c in text if c.isdigit())
        return int(digits) if digits else 1
    if "hour" in text:
        digits = "".join(c for c in text if c.isdigit())
        return (int(digits) if digits else 1) * 60
    return 0


def _damage_type(effect: Mapping[str, Any], spell: Mapping[str, Any]) -> str:
    return str(effect.get("damage_type") or spell.get("damage_type") or "").lower()


def _self_only(spell: Mapping[str, Any]) -> bool:
    """Whether the spell's own range is Self (so there is no distance to double)."""

    return str(spell.get("range") or "").strip().lower().startswith("self")


def _attack_roll(effect: Mapping[str, Any]) -> bool:
    return str(effect.get("mode") or "") in {"spell_attack", "attack"}
