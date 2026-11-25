"""Combat capability projection for D&D 2024 class features.

The combat engine consumes a *capability id*, its action/resource cost, whether
it needs a hostile target, and the underlying canonical actions it performs.
Which class grants that capability, and from which level, is answered here --
never by the combat engine, and never by the frontend.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from .kinds import KIND_HANDLERS
from .models import (
    ClassFeatureDefinition,
    CombatCapabilityView,
    Dnd2024ClassFeatureCatalog,
    CapabilityCostView,
    FeatureCapability,
    SpellSlotCost,
    TURN_START_TIMING,
)
from .resources import ResourceDefinition


_DICE_FORMULA_RE = re.compile(r"^(\d+)d(\d+)(?:\+(\d+))?$")
_FLAT_FORMULA_RE = re.compile(r"^(\d+)$")


def damage_mean(formula: str) -> float | None:
    """Expected value of a canonical damage formula, or ``None`` if unparsed."""

    text = str(formula or "").strip()
    match = _DICE_FORMULA_RE.fullmatch(text)
    if match is not None:
        count, sides = int(match.group(1)), int(match.group(2))
        return count * (sides + 1) / 2 + int(match.group(3) or 0)
    match = _FLAT_FORMULA_RE.fullmatch(text)
    if match is not None:
        return float(match.group(1))
    return None


def martial_arts_die_is_better(candidate: str, current: str) -> bool:
    """Whether the feature die improves on the profile's own damage formula.

    V1 deterministic automation must never *downgrade* a weapon: when the
    profile's own dice are at least as good, or cannot be compared at all, the
    weapon keeps its own damage formula and only the ability choice applies.
    """

    candidate_mean = damage_mean(candidate)
    current_mean = damage_mean(current)
    if candidate_mean is None or current_mean is None:
        return False
    return candidate_mean > current_mean


def spell_slot_count(slots: Mapping[str, Any], level: int) -> int:
    return int(slots.get(str(level), 0) or 0)


@dataclass(frozen=True, slots=True)
class CombatContext:
    """The turn and spell-slot budgets a capability is evaluated against."""

    action: int = 0
    bonus_action: int = 0
    reaction: int = 0
    # 当前回合开始时开启的窗口是否属于正在评估的角色。窗口不花经济，所以
    # 它是一个独立布尔而不是第四种"剩余次数"：跳过之后它由 combat 层关闭，
    # 而不是被某种动作消耗。
    turn_start: bool = False
    # Fleet Step：一个非 Step 的附赠动作结算后，本回合还剩一次免费的 Step。
    fleet_step: bool = False
    spell_slots: Mapping[str, Any] = field(default_factory=dict)
    spell_slot_maxima: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_economy(
        cls, economy: Mapping[str, Any] | None, actor_id: str = "",
        *, class_magic: Mapping[str, Any] | None = None,
    ) -> CombatContext:
        source: Mapping[str, Any] = economy if isinstance(economy, Mapping) else {}

        def budget(key: str) -> int:
            value = source.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int):
                return 0
            return max(0, value)

        window = source.get("turn_start_window")
        window = window if isinstance(window, Mapping) else {}
        slots = (class_magic or {}).get("slots_current")
        maxima = (class_magic or {}).get("slots_max")
        return cls(
            action=budget("action"),
            bonus_action=budget("bonus_action"),
            reaction=budget("reaction"),
            turn_start=bool(actor_id) and str(window.get("actor_id") or "") == actor_id,
            fleet_step=bool(source.get("fleet_step_available")),
            spell_slots=slots if isinstance(slots, Mapping) else {},
            spell_slot_maxima=maxima if isinstance(maxima, Mapping) else {},
        )

    def remaining(self, action: str) -> int:
        if action == "bonus_action":
            return self.bonus_action
        if action == "action":
            return self.action
        return 0


def _cost_views(
    catalog: Dnd2024ClassFeatureCatalog,
    capability: FeatureCapability,
    context: CombatContext,
    definitions: Mapping[str, ResourceDefinition],
) -> tuple[CapabilityCostView, ...]:
    views: list[CapabilityCostView] = []
    if capability.cost.timing == TURN_START_TIMING:
        # 窗口本身就是这份成本的"剩余":1 表示还没被跳过也没被别的动作关掉。
        views.append(CapabilityCostView(
            kind="turn_start",
            name=catalog.label("turn_start", "turn start"),
            amount=1,
            current=1 if context.turn_start else 0,
            maximum=1,
        ))
    if capability.cost.slot:
        views.append(CapabilityCostView(
            kind=capability.cost.slot,
            name=catalog.label(capability.cost.slot, capability.cost.slot),
            amount=1,
            current=context.remaining(capability.cost.slot),
            maximum=1,
        ))
    for resource_cost in capability.cost.resources:
        if isinstance(resource_cost, SpellSlotCost):
            level = resource_cost.level
            views.append(CapabilityCostView(
                kind="spell_slot",
                name=catalog.label("spell_slot_cost", "Level {level} spell slot").replace(
                    "{level}", str(level),
                ),
                amount=1,
                current=spell_slot_count(context.spell_slots, level),
                maximum=spell_slot_count(context.spell_slot_maxima, level),
            ))
            continue
        definition = definitions.get(resource_cost.resource_id)
        views.append(CapabilityCostView(
            kind="resource",
            name=catalog.label(resource_cost.resource_id, resource_cost.resource_id),
            amount=resource_cost.amount,
            current=int(definition.current) if definition is not None else 0,
            maximum=int(definition.maximum) if definition is not None else 0,
        ))
    return tuple(views)


def _is_step_of_the_wind(capability: FeatureCapability) -> bool:
    """Whether this capability *is* Step of the Wind (paid or free)."""

    return capability.feature_id == "step_of_the_wind"


def _blocked_reason(
    catalog: Dnd2024ClassFeatureCatalog,
    capability: FeatureCapability,
    context: CombatContext,
    definitions: Mapping[str, ResourceDefinition],
) -> str:
    for resource_cost in capability.cost.resources:
        if isinstance(resource_cost, SpellSlotCost):
            if spell_slot_count(context.spell_slots, resource_cost.level) < 1:
                return f"no level {resource_cost.level} spell slot is available"
            continue
        definition = definitions.get(resource_cost.resource_id)
        name = catalog.label(resource_cost.resource_id, resource_cost.resource_id)
        if definition is None:
            return f"{name} is not available to this character"
        if int(definition.current) < resource_cost.amount:
            return (
                f"not enough {name}: this capability needs {resource_cost.amount}, "
                f"the character has {int(definition.current)}"
            )
    pool_id = capability.pool_resource_id
    if pool_id:
        # 池的支出额由意图决定，所以这里只回答"池里还有没有至少一点"：
        # 空池的能力照样要被列出来带着原因，而不是变成一个可点的按钮。
        pool = definitions.get(pool_id)
        name = catalog.label(pool_id, pool_id)
        if pool is None:
            return f"{name} is not available to this character"
        if int(pool.current) < 1:
            return f"{name} is empty"
    action = capability.cost.slot
    if action and context.remaining(action) < 1:
        if not (action == "bonus_action" and context.fleet_step
                and _is_step_of_the_wind(capability)):
            return f"the {action.replace('_', ' ')} has already been spent"
    if capability.cost.timing == TURN_START_TIMING and not context.turn_start:
        # 窗口一旦被动作或跳过关掉，就不能回头再激活：这就是"at the start of
        # your turn"的可执行含义。
        return "the start of your turn has already passed"
    return ""


def combat_capabilities(
    catalog: Dnd2024ClassFeatureCatalog,
    owned: tuple[ClassFeatureDefinition, ...],
    context: CombatContext,
    definitions: Mapping[str, ResourceDefinition],
    *,
    include_unavailable: bool = False,
) -> tuple[CombatCapabilityView, ...]:
    """Project the capabilities the character may declare right now.

    Only capabilities the character actually owns are considered; a capability
    whose action or resource cost cannot be paid is returned with a reason when
    ``include_unavailable`` is set, and omitted otherwise.  The combat layer
    therefore never has to re-derive class level, feature ownership, or
    resource maxima.
    """

    result: list[CombatCapabilityView] = []
    for definition in owned:
        for capability in definition.capabilities:
            blocked = _blocked_reason(catalog, capability, context, definitions)
            if blocked and not include_unavailable:
                continue
            view = CombatCapabilityView(
                id=capability.id,
                feature_id=definition.id,
                name=catalog.label(capability.id, capability.id),
                available=not blocked,
                blocked_reason=blocked,
                requires_hostile_target=capability.requires_hostile_target,
                costs=_cost_views(catalog, capability, context, definitions),
                target_kind=capability.target_kind,
            )
            details: dict[str, dict[str, Any]] = {}
            for action in capability.actions:
                project = KIND_HANDLERS[action.kind].project
                if project is not None:
                    detail = project(capability, action, context, view)
                    if detail:
                        details.setdefault(action.kind, {}).update(detail)
            result.append(replace(view, kind_details=details) if details else view)
    return tuple(result)


def martial_arts_profile(
    base: Mapping[str, Any],
    *,
    damage_die: str,
    ability_choice: Iterable[str] = (),
) -> dict[str, Any]:
    """Return the actor-specific effective profile of a canonical attack.

    Used for the canonical Unarmed Strike and for a wielded Monk Weapon: the
    canonical identity, damage type, properties, range and proficiency still
    come from the combat catalog, and only the *effective* base damage and
    attack-ability choice are feature-derived.  No new attack identity is ever
    created here.

    V1 自动化是确定性的：属性取投影给出的选择中修正值更高者，伤害骰只在
    Martial Arts Die 更优时才替换，绝不把武器自己的伤害骰降级。
    """

    profile = deepcopy(dict(base))
    applied = False
    if damage_die and martial_arts_die_is_better(damage_die, str(base.get("damage") or "")):
        profile["damage"] = damage_die
        applied = True
    choice = [str(item) for item in ability_choice if str(item)]
    if choice:
        profile["ability_choice"] = choice
        applied = True
    if applied:
        profile["martial_arts"] = True
    return profile
