"""Handlers for declaration-selected exchanges with an intent-selected slot."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .kinds import KindContext
    from .models import CapabilityAction, FeatureCapability


class ExchangeOperations(Protocol):
    def _validate_inspiration_slot_exchange(
        self, instance: Any, actor: dict[str, Any], slot_level: Any,
    ) -> None: ...

    def _font_slot_exchange_events(
        self, instance: Any, actor: dict[str, Any], slot_level: Any,
    ) -> list[dict[str, Any]]: ...

    def _validate_font_slot_to_points(
        self, instance: Any, actor: dict[str, Any], slot_level: Any,
    ) -> None: ...

    def _validate_font_create_slot(
        self, instance: Any, actor: dict[str, Any], slot_level: Any,
        capability: FeatureCapability,
    ) -> None: ...

    def _font_of_magic_events(
        self, instance: Any, actor: dict[str, Any], slot_level: Any,
        kind: str, feature_id: str,
    ) -> list[dict[str, Any]]: ...


def validate_inspiration_slot_exchange(
    capability: FeatureCapability, action: CapabilityAction, ctx: KindContext,
) -> None:
    ctx.operations._validate_inspiration_slot_exchange(ctx.instance, ctx.actor, ctx.intent.get("slot_level"))


def inspiration_slot_exchange(
    capability: FeatureCapability, action: CapabilityAction, ctx: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return ctx.operations._font_slot_exchange_events(ctx.instance, ctx.actor, ctx.intent.get("slot_level"))


def validate_font_slot_to_points(
    capability: FeatureCapability, action: CapabilityAction, ctx: KindContext,
) -> None:
    ctx.operations._validate_font_slot_to_points(ctx.instance, ctx.actor, ctx.intent.get("slot_level"))


def validate_font_create_slot(
    capability: FeatureCapability, action: CapabilityAction, ctx: KindContext,
) -> None:
    ctx.operations._validate_font_create_slot(
        ctx.instance, ctx.actor, ctx.intent.get("slot_level"), capability,
    )


def font_of_magic(
    capability: FeatureCapability, action: CapabilityAction, ctx: KindContext, rng: Any,
) -> list[dict[str, Any]]:
    return ctx.operations._font_of_magic_events(
        ctx.instance, ctx.actor, ctx.intent.get("slot_level"), action.kind, capability.feature_id,
    )
