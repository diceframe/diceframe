"""Capability handlers for basic actions, buffs and long-lived effects.

The adapters reuse existing combat operations without importing the engine or
charging a second cost. The registry wires these effects into the intent path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from .models import ResourceCost
from .resources import USAGE_SCOPE_LONG_REST, feature_usage_count, long_effects, resource_state

if TYPE_CHECKING:
    from .kinds import KindContext
    from .models import CapabilityAction, FeatureCapability


class EffectOperations(Protocol):
    def _feature_character(self, actor: dict[str, Any]) -> dict[str, Any]: ...

    def _basic_action_events(
        self, combat: dict[str, Any], intent_type: str, actor_id: str,
        *, spend_action: bool = True,
    ) -> list[dict[str, Any]]: ...

    def _temp_hp_grant_events(
        self, actor: dict[str, Any], feature_id: str, rng: Any,
    ) -> list[dict[str, Any]]: ...

    def _self_invisible_events(
        self, actor: dict[str, Any], feature_id: str,
    ) -> list[dict[str, Any]]: ...

    def _resistance_all_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any], feature_id: str,
    ) -> list[dict[str, Any]]: ...

    def _quivering_palm_end_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        actor: dict[str, Any], capability: FeatureCapability, rng: Any,
    ) -> list[dict[str, Any]]: ...

    def _holy_nimbus_events(
        self, instance: Any, actor_id: str, actor: dict[str, Any], capability: FeatureCapability,
    ) -> list[dict[str, Any]]: ...

    def _dragon_wings_events(
        self, instance: Any, actor_id: str, actor: dict[str, Any], capability: FeatureCapability,
    ) -> list[dict[str, Any]]: ...

    def _intimidating_presence_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        actor: dict[str, Any], capability: FeatureCapability, intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]: ...


def basic_action(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return context.operations._basic_action_events(
        context.combat, action.kind, context.actor_id, spend_action=False,
    )


def temp_hp(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return context.operations._temp_hp_grant_events(context.actor, capability.feature_id, rng)


def invisible(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return context.operations._self_invisible_events(context.actor, capability.feature_id)


def resistance_all(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return context.operations._resistance_all_events(
        context.instance, context.combat, context.actor, capability.feature_id,
    )


def validate_quivering_palm_end(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext,
) -> str:
    # The combat actor projection omits expired entries. No authoritative
    # planes exist, so ending the vibrations still requires an explicit assertion.
    if "quivering_palm" not in long_effects(context.actor):
        return "no creature is under Quivering Palm"
    if context.intent.get("same_plane") is not True:
        return "Quivering Palm requires an explicit same-plane assertion"
    return ""


def quivering_palm_end(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return context.operations._quivering_palm_end_events(
        context.instance, context.combat, context.actor_id, context.actor, capability, rng,
    )


def holy_nimbus(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return context.operations._holy_nimbus_events(
        context.instance, context.actor_id, context.actor, capability,
    )


def dragon_wings(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return context.operations._dragon_wings_events(
        context.instance, context.actor_id, context.actor, capability,
    )


def intimidating_presence(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return context.operations._intimidating_presence_events(
        context.instance, context.combat, context.actor_id, context.actor, capability,
        context.intent, rng,
    )


def validate_restore_feature_use(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext,
) -> str:
    character = context.operations._feature_character(context.actor)
    feature_id = capability.feature_id
    if feature_usage_count(character, feature_id, feature_id) < 1:
        return "this feature has no use left to restore"
    # Preserve the established pool diagnostic. The shared affordability
    # check still owns spell slot shortages and the declared price.
    for cost in capability.cost.resources:
        if isinstance(cost, ResourceCost):
            state = resource_state(character, cost.resource_id)
            if int(state.get("current", 0) or 0) < cost.amount:
                return f"not enough {cost.resource_id} to restore this feature"
    return ""


def restore_feature_use(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    character = context.operations._feature_character(context.actor)
    feature_id = capability.feature_id
    before = feature_usage_count(character, feature_id, feature_id, USAGE_SCOPE_LONG_REST)
    return [{
        "type": "dnd2024.feature_usage.restored", "actor_id": context.actor_id,
        "owner": feature_id, "feature_id": feature_id,
        "scope": USAGE_SCOPE_LONG_REST,
        "before_count": before,
        "reason": "restored",
    }]


def rage_sustain(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    # Refresh the sustain flag, not the original Rage duration.
    return [{
        "type": "dnd2024.condition.sustained", "target_id": context.actor_id,
        "condition": "rage",
    }]


def action_surge(
    capability: FeatureCapability, action: CapabilityAction, context: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    # This separate slot cannot pay for a Magic action.
    return [{
        "type": "dnd2024.economy.granted", "actor_id": context.actor_id,
        "grants": {"surge_action": 1},
        "flags": {"action_surge_used": True},
    }]
