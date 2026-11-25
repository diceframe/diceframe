"""Handlers that delegate target validation and events to combat rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .kinds import KindContext
    from .models import CapabilityAction, FeatureCapability


class TargetOperations(Protocol):
    def _validate_capability_unarmed_strike(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: FeatureCapability,
    ) -> None: ...

    def _capability_unarmed_strike_events(
        self, instance: Any, intent: dict[str, Any],
        actor: dict[str, Any], capability: FeatureCapability, count: int,
        prior_events: tuple[dict[str, Any], ...], rng: Any,
    ) -> list[dict[str, Any]]: ...

    def _validate_bardic_grant(
        self, instance: Any, combat: dict[str, Any], target_id: str, actor: dict[str, Any],
    ) -> None: ...

    def _bardic_grant_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str, holder_id: str, intent_id: Any,
    ) -> list[dict[str, Any]]: ...

    def _validate_divine_spark(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: FeatureCapability,
    ) -> None: ...

    def _divine_spark_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str, actor: dict[str, Any],
        capability: FeatureCapability, intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]: ...

    def _validate_turn_undead(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: FeatureCapability,
    ) -> None: ...

    def _turn_undead_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str, actor: dict[str, Any],
        capability: FeatureCapability, intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]: ...

    def _validate_abjure_foes(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: FeatureCapability,
    ) -> None: ...

    def _abjure_foes_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str, actor: dict[str, Any],
        capability: FeatureCapability, intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]: ...

    def _validate_preserve_life(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: FeatureCapability,
    ) -> None: ...

    def _preserve_life_events(
        self, actor: dict[str, Any], capability: FeatureCapability, intent: dict[str, Any],
    ) -> list[dict[str, Any]]: ...

    def _validate_lay_on_hands(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: FeatureCapability,
    ) -> None: ...

    def _lay_on_hands_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        capability: FeatureCapability, intent: dict[str, Any],
    ) -> list[dict[str, Any]]: ...

    def _divine_sense_events(
        self, instance: Any, actor: dict[str, Any], capability: FeatureCapability,
    ) -> list[dict[str, Any]]: ...


def validate_unarmed_strike(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext) -> None:
    ctx.operations._validate_capability_unarmed_strike(
        ctx.instance, ctx.combat, ctx.intent, ctx.actor, capability,
    )


def unarmed_strike(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext, rng: Any) -> list[dict[str, Any]]:
    return ctx.operations._capability_unarmed_strike_events(
        ctx.instance, ctx.intent, ctx.actor, capability, action.count, ctx.prior_events, rng,
    )


def validate_bardic_grant(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext) -> None:
    ctx.operations._validate_bardic_grant(
        ctx.instance, ctx.combat, str(ctx.intent.get("target_id") or ""), ctx.actor,
    )


def bardic_grant(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext, rng: Any) -> list[dict[str, Any]]:
    return ctx.operations._bardic_grant_events(
        ctx.instance, ctx.combat, str(ctx.intent["actor_id"]),
        str(ctx.intent.get("target_id") or ""), ctx.intent.get("intent_id", ""),
    )


def validate_divine_spark(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext) -> None:
    ctx.operations._validate_divine_spark(ctx.instance, ctx.combat, ctx.intent, ctx.actor, capability)


def divine_spark(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext, rng: Any) -> list[dict[str, Any]]:
    return ctx.operations._divine_spark_events(
        ctx.instance, ctx.combat, ctx.actor_id, ctx.actor, capability, ctx.intent, rng,
    )


def validate_turn_undead(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext) -> None:
    ctx.operations._validate_turn_undead(ctx.instance, ctx.combat, ctx.intent, ctx.actor, capability)


def turn_undead(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext, rng: Any) -> list[dict[str, Any]]:
    return ctx.operations._turn_undead_events(
        ctx.instance, ctx.combat, ctx.actor_id, ctx.actor, capability, ctx.intent, rng,
    )


def validate_abjure_foes(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext) -> None:
    ctx.operations._validate_abjure_foes(ctx.instance, ctx.combat, ctx.intent, ctx.actor, capability)


def abjure_foes(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext, rng: Any) -> list[dict[str, Any]]:
    return ctx.operations._abjure_foes_events(
        ctx.instance, ctx.combat, ctx.actor_id, ctx.actor, capability, ctx.intent, rng,
    )


def validate_preserve_life(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext) -> None:
    ctx.operations._validate_preserve_life(ctx.instance, ctx.combat, ctx.intent, ctx.actor, capability)


def preserve_life(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext, rng: Any) -> list[dict[str, Any]]:
    return ctx.operations._preserve_life_events(ctx.actor, capability, ctx.intent)


def validate_lay_on_hands(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext) -> None:
    ctx.operations._validate_lay_on_hands(ctx.instance, ctx.combat, ctx.intent, ctx.actor, capability)


def lay_on_hands(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext, rng: Any) -> list[dict[str, Any]]:
    return ctx.operations._lay_on_hands_events(ctx.instance, ctx.combat, ctx.actor_id, capability, ctx.intent)


def divine_sense(capability: FeatureCapability, action: CapabilityAction, ctx: KindContext, rng: Any) -> list[dict[str, Any]]:
    return ctx.operations._divine_sense_events(ctx.instance, ctx.actor, capability)
