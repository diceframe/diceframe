"""Declarations for mechanically activating a carried item."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

POTION_ITEM_TYPE = "potion"
ACTIVATION_COSTS = frozenset({"action", "bonus_action"})
ACTIVATION_TARGETS = frozenset({"self", "ally"})
_ACTIVATION_KEYS = frozenset({"cost", "target", "effect", "charge_cost"})
_EFFECT_KEYS = frozenset({"mode", "healing"})


class ItemActivationError(ValueError):
    """Raised when an item activation declaration cannot be used."""


def validate_consumable_declaration(item: Mapping[str, Any]) -> bool:
    """Return whether activating the item consumes one carried copy."""

    if "consumable" not in item:
        return False
    value = item["consumable"]
    if not isinstance(value, bool):
        raise ItemActivationError("consumable must be a boolean")
    return value


def validate_item_declarations(bundle: Any) -> None:
    """Validate every bundled item's activation-related declarations.

    This is the ruleset-owned load boundary used by the D&D runtime and the
    combat catalog.  Declarations that cannot form a legal charge lifecycle
    fail here instead of surfacing on first activation or rest.
    """

    from .charges import ChargesError, validate_charges_declaration
    from .charges import validate_recharge_declaration

    for item in bundle.list("item"):
        if not isinstance(item, Mapping):
            raise ItemActivationError("item must be an object")
        item_id = str(item.get("id") or "<unknown>")
        activation = validate_activation_declaration(item)
        validate_consumable_declaration(item)
        maximum = validate_charges_declaration(item)
        recharge = validate_recharge_declaration(item)
        if recharge is not None and recharge["on"] != "none" and maximum is None:
            raise ChargesError(f"item {item_id} declares recharge without charges")
        if activation is not None and maximum is None and activation["charge_cost"] is not None:
            raise ChargesError(f"item {item_id} declares activation charge_cost without charges")
        if activation is not None and maximum is not None and activation["charge_cost"] is None:
            raise ChargesError(f"item {item_id} declares charges without activation charge_cost")


def build_item_healing_event(
    *,
    item_ref: str,
    target_id: str,
    rolls: list[int],
    healing: int,
    hp_before: int,
    hp_max: int,
    blocked_by: str | None = None,
) -> dict[str, Any]:
    """Build the shared item-healing event for combat and exploration."""

    blocked = blocked_by is not None
    hp_after = hp_before if blocked else min(hp_max, hp_before + healing)
    # ``delta`` is the HP actually restored; the raw roll stays in ``rolls`` so
    # an over-heal on a near-full target is still auditable.
    amount = 0 if blocked else max(0, hp_after - hp_before)
    event: dict[str, Any] = {
        "type": "resource.changed",
        "resource": "hp",
        "target_id": target_id,
        "delta": amount,
        "amount": amount,
        "healing": True,
        "rolls": list(rolls),
        "item_ref": item_ref,
        "heal_before": hp_before,
        "heal_after": hp_after,
        "hp_after": hp_after,
    }
    if blocked:
        event["blocked_by"] = blocked_by
    return event


def validate_activation_declaration(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate ``item.activation`` and return a normalised copy.

    Items without an activation declaration return ``None``; every declared
    key is whitelisted so content errors fail closed instead of being ignored.
    """

    if not isinstance(item, Mapping):
        raise ItemActivationError("item must be an object")
    if "activation" not in item:
        return None
    declaration = item["activation"]
    if not isinstance(declaration, Mapping):
        raise ItemActivationError("activation must be an object")
    unknown = sorted(set(declaration) - _ACTIVATION_KEYS)
    if unknown:
        raise ItemActivationError(f"unknown activation field: {unknown[0]}")
    cost = declaration.get("cost")
    if cost not in ACTIVATION_COSTS:
        raise ItemActivationError("activation.cost must be action or bonus_action")
    target = declaration.get("target")
    if target not in ACTIVATION_TARGETS:
        raise ItemActivationError("activation.target must be self or ally")
    effect = declaration.get("effect")
    if not isinstance(effect, Mapping):
        raise ItemActivationError("activation.effect must be an object")
    unknown = sorted(set(effect) - _EFFECT_KEYS)
    if unknown:
        raise ItemActivationError(f"unknown activation.effect field: {unknown[0]}")
    if effect.get("mode") != "healing":
        raise ItemActivationError("activation.effect.mode must be healing")
    healing = effect.get("healing")
    from ..combat.catalog import DICE_RE

    if not isinstance(healing, str) or DICE_RE.fullmatch(healing) is None:
        raise ItemActivationError("activation.effect.healing must be a dice formula")
    charge_cost = declaration.get("charge_cost")
    if charge_cost is not None and (
        isinstance(charge_cost, bool)
        or not isinstance(charge_cost, int)
        or charge_cost < 1
    ):
        raise ItemActivationError("activation.charge_cost must be a positive integer")
    return {
        "cost": cost,
        "target": target,
        "effect": {"mode": "healing", "healing": healing},
        "charge_cost": charge_cost,
    }
