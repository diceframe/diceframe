"""Registry for player-initiated class capability effects, not reaction windows.

This module does not import the combat engine. Kind handlers can request narrow
combat operations for effects also used outside class capabilities (e.g. Hide).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from . import activation_effects as effects
from . import activation_exchanges as exchanges
from . import activation_targets as targets

if TYPE_CHECKING:
    from .combat import CombatContext
    from .models import CapabilityAction, CombatCapabilityView, FeatureCapability


class CapabilityOperations(
    effects.EffectOperations, exchanges.ExchangeOperations, targets.TargetOperations, Protocol,
):
    def hide_preconditions(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
    ) -> dict[str, Any]: ...

    def _hide_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str, rng: Any,
        *, intent_id: str = "", spend_action: bool = True,
    ) -> list[dict[str, Any]]: ...

    def has_moved_this_turn(self, combat: dict[str, Any], actor_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class KindContext:
    operations: CapabilityOperations
    instance: Any
    combat: dict[str, Any]
    actor: dict[str, Any]
    intent: dict[str, Any]
    # Resolution only: all costs and effects already staged for this intent.
    prior_events: tuple[dict[str, Any], ...] = ()

    @property
    def actor_id(self) -> str:
        return str(self.actor["actor_id"])


@dataclass(frozen=True, slots=True)
class KindHandler:
    resolve: Callable[
        [FeatureCapability, CapabilityAction, KindContext, Any], list[dict[str, Any]],
    ]
    # Return an authoritative rejection reason, or an empty string if allowed.
    validate: Callable[[FeatureCapability, CapabilityAction, KindContext], str] | None = None
    # Check effect-specific targets/arguments after shared affordability has
    # been checked, but before payment. Raises the usual combat rejection.
    validate_available: Callable[[FeatureCapability, CapabilityAction, KindContext], None] | None = None
    # Additional display fields, never overrides for shared cost/authority fields.
    project: Callable[
        [FeatureCapability, CapabilityAction, CombatContext, CombatCapabilityView],
        Mapping[str, Any],
    ] | None = None
    requires_resource_cost: bool = False
    grants_attack: bool = False


def _validate_hide(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext,
) -> str:
    gate = context.operations.hide_preconditions(context.instance, context.combat, context.actor_id)
    return "" if gate["allowed"] else str(gate["reason"])


def _resolve_hide(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return context.operations._hide_events(
        context.instance, context.combat, context.actor_id, rng,
        spend_action=False,
    )


def _validate_steady_aim(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext,
) -> str:
    if context.operations.has_moved_this_turn(context.combat, context.actor_id):
        return "you have already moved this turn"
    return ""


def _resolve_steady_aim(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    actor_id = context.actor_id
    return [
        {"type": "condition.applied", "target_id": actor_id,
         "condition": "next_attack_advantage", "duration": "combat"},
        {"type": "condition.applied", "target_id": actor_id,
         "condition": "steady_aim_slow", "duration": "caster_turn_end",
         "source_actor_id": actor_id},
    ]


def _resolve_end_long_effect(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    # Prefer the declared effect id (e.g. Quivering Palm's dismiss capability
    # has its own id); keep the owning feature as the legacy default.
    return [{
        "type": "dnd2024.long_effect.ended",
        "actor_id": context.actor_id,
        "effect_id": action.effect_id or capability.feature_id,
        "reason": "dismissed",
    }]


KIND_HANDLERS: dict[str, KindHandler] = {
    "hide": KindHandler(resolve=_resolve_hide, validate=_validate_hide),
    "steady_aim": KindHandler(resolve=_resolve_steady_aim, validate=_validate_steady_aim),
    "end_long_effect": KindHandler(resolve=_resolve_end_long_effect),
    "dash": KindHandler(resolve=effects.basic_action),
    "dodge": KindHandler(resolve=effects.basic_action),
    "disengage": KindHandler(resolve=effects.basic_action),
    "temp_hp": KindHandler(resolve=effects.temp_hp),
    "invisible": KindHandler(resolve=effects.invisible),
    "resistance_all": KindHandler(resolve=effects.resistance_all),
    "quivering_palm_end": KindHandler(
        resolve=effects.quivering_palm_end, validate=effects.validate_quivering_palm_end,
    ),
    "holy_nimbus": KindHandler(resolve=effects.holy_nimbus),
    "dragon_wings": KindHandler(resolve=effects.dragon_wings),
    "intimidating_presence": KindHandler(resolve=effects.intimidating_presence),
    "restore_feature_use": KindHandler(
        resolve=effects.restore_feature_use, validate=effects.validate_restore_feature_use,
        requires_resource_cost=True,
    ),
    "rage_sustain": KindHandler(resolve=effects.rage_sustain),
    "action_surge": KindHandler(resolve=effects.action_surge),
    "heal": KindHandler(resolve=effects.heal),
    "rage": KindHandler(resolve=effects.rage),
    "unarmed_strike": KindHandler(
        resolve=targets.unarmed_strike, validate_available=targets.validate_unarmed_strike,
        grants_attack=True,
    ),
    "bardic_grant": KindHandler(
        resolve=targets.bardic_grant, validate_available=targets.validate_bardic_grant,
    ),
    "divine_spark": KindHandler(
        resolve=targets.divine_spark, validate_available=targets.validate_divine_spark,
    ),
    "turn_undead": KindHandler(
        resolve=targets.turn_undead, validate_available=targets.validate_turn_undead,
    ),
    "abjure_foes": KindHandler(
        resolve=targets.abjure_foes, validate_available=targets.validate_abjure_foes,
    ),
    "preserve_life": KindHandler(
        resolve=targets.preserve_life, validate_available=targets.validate_preserve_life,
    ),
    "lay_on_hands": KindHandler(
        resolve=targets.lay_on_hands, validate_available=targets.validate_lay_on_hands,
    ),
    "divine_sense": KindHandler(resolve=targets.divine_sense),
    "inspiration_slot_exchange": KindHandler(
        resolve=exchanges.inspiration_slot_exchange,
        validate_available=exchanges.validate_inspiration_slot_exchange,
    ),
    "font_slot_to_points": KindHandler(
        resolve=exchanges.font_of_magic, validate_available=exchanges.validate_font_slot_to_points,
    ),
    "font_create_slot": KindHandler(
        resolve=exchanges.font_of_magic, validate_available=exchanges.validate_font_create_slot,
    ),
}
