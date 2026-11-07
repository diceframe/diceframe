"""Charge budgets for charged magic items.

``max`` lives in the bundle declaration; the only mutable runtime value is
``canonical.equipment.item_state[item_ref].charges.current``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from random import SystemRandom
from typing import Any

logger = logging.getLogger(__name__)

CHARGES_KEYS = frozenset({"max"})
RECHARGE_KEYS = frozenset({"on", "amount"})
RECHARGE_TRIGGERS = frozenset({"dawn", "short_rest", "none"})
_RECHARGE_TRIGGER_TO_REST = {"dawn": "long", "short_rest": "short"}


class ChargesError(ValueError):
    """Raised when charge declarations or runtime state are unusable."""


def _dice_amount(formula: str) -> tuple[int, int, int]:
    from ..combat.catalog import DICE_RE

    if DICE_RE.fullmatch(formula) is None:
        raise ChargesError("recharge amount must be a positive integer or dice formula")
    count, sides = formula.split("d", 1)
    bonus = 0
    if "+" in sides:
        sides, _, raw_bonus = sides.partition("+")
        bonus = int(raw_bonus)
    return int(count), int(sides), bonus


def validate_charges_declaration(item: Mapping[str, Any]) -> int | None:
    """Return the declared maximum for ``item.charges``, or ``None``."""

    if not isinstance(item, Mapping):
        raise ChargesError("item must be an object")
    if "charges" not in item:
        return None
    declaration = item["charges"]
    if not isinstance(declaration, Mapping):
        raise ChargesError("charges must be an object")
    unknown = sorted(set(declaration) - CHARGES_KEYS)
    if unknown:
        raise ChargesError(f"unknown charges field: {unknown[0]}")
    maximum = declaration.get("max")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ChargesError("charges.max must be a positive integer")
    return maximum


def validate_recharge_declaration(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate ``item.recharge`` and return ``{"on", "amount"}``."""

    if not isinstance(item, Mapping):
        raise ChargesError("item must be an object")
    if "recharge" not in item:
        return None
    declaration = item["recharge"]
    if not isinstance(declaration, Mapping):
        raise ChargesError("recharge must be an object")
    unknown = sorted(set(declaration) - RECHARGE_KEYS)
    if unknown:
        raise ChargesError(f"unknown recharge field: {unknown[0]}")
    trigger = declaration.get("on")
    if trigger not in RECHARGE_TRIGGERS:
        raise ChargesError("recharge.on must be dawn, short_rest, or none")
    if trigger == "none":
        if "amount" in declaration:
            raise ChargesError("recharge.on none does not accept amount")
        return {"on": "none"}
    amount = declaration.get("amount", 1)
    if isinstance(amount, bool) or not isinstance(amount, (int, str)):
        raise ChargesError("recharge.amount must be a positive integer or dice formula")
    if isinstance(amount, int):
        if amount < 1:
            raise ChargesError("recharge.amount must be a positive integer")
        return {"on": trigger, "amount": amount}
    from ..combat.catalog import DICE_RE

    if DICE_RE.fullmatch(amount) is None:
        raise ChargesError("recharge.amount must be a positive integer or dice formula")
    return {"on": trigger, "amount": amount}


def item_charges_max(item: Mapping[str, Any]) -> int | None:
    """Declared maximum charges, or ``None`` when the item has none."""

    return validate_charges_declaration(item)


def charges_of(canonical: Mapping[str, Any], item_ref: str) -> int | None:
    """Current charges for one canonical item ref, ``None`` when uninitialised."""

    equipment = canonical.get("equipment")
    if not isinstance(equipment, Mapping):
        return None
    item_state = equipment.get("item_state")
    if not isinstance(item_state, Mapping):
        return None
    state = item_state.get(item_ref)
    if not isinstance(state, Mapping):
        return None
    charges = state.get("charges")
    if not isinstance(charges, Mapping):
        return None
    current = charges.get("current")
    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
        return None
    return current


def spend_charges(current: int, amount: int, maximum: int) -> int:
    """Pure charge spend: validate the budget and return the new current value."""

    for value in (current, amount, maximum):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ChargesError("charge values must be integers")
    if maximum < 0 or current < 0 or current > maximum:
        raise ChargesError("item charges are outside the declared maximum")
    if amount < 1:
        raise ChargesError("charge cost must be a positive integer")
    if current < amount:
        raise ChargesError("not enough charges")
    return current - amount


def initialize_item_state(
    canonical: dict[str, Any], bundle: Any, held_refs: Iterable[str],
) -> bool:
    """Initialise missing charge state for held items; never reset used charges."""

    equipment = canonical.get("equipment")
    existing = equipment.get("item_state") if isinstance(equipment, Mapping) else None
    if existing is not None and not isinstance(existing, dict):
        logger.warning("D&D2024 item_state is not an object; skipping charges")
        return False
    state = existing
    changed = False
    for item_ref in sorted(set(held_refs)):
        item_id = str(item_ref).removeprefix("item:")
        item = bundle.get("item", item_id) if bundle is not None else None
        if not isinstance(item, Mapping):
            continue
        try:
            maximum = validate_charges_declaration(item)
        except ChargesError as exc:
            logger.warning("D&D2024 item charge declaration rejected: %s", exc)
            continue
        if maximum is None or (state is not None and item_ref in state):
            continue
        if state is None:
            equipment = canonical.setdefault("equipment", {})
            state = {}
            equipment["item_state"] = state
        state[item_ref] = {"charges": {"current": maximum}}
        changed = True
    return changed


def recharge_for_rest(
    canonical: dict[str, Any], bundle: Any, held_refs: Iterable[str], rest: str,
) -> list[dict[str, Any]]:
    """Recharge held items whose declaration matches this rest.

    Returns audit events and mutates only ``charges.current`` in canonical state.
    One item's unusable state is contained to that item: the rest still finishes
    and the skip shows up as a ``recharge_item.skipped`` event.
    """

    if rest not in {"short", "long"}:
        raise ChargesError("rest must be short or long")
    equipment = canonical.get("equipment")
    item_state = equipment.get("item_state") if isinstance(equipment, Mapping) else None
    if not isinstance(item_state, dict):
        return []
    held = set(held_refs)
    plans: list[tuple[str, dict[str, Any], int, int, list[int]]] = []
    skipped: list[dict[str, Any]] = []
    for item_ref in sorted(item_state):
        if item_ref not in held:
            continue
        item_id = str(item_ref).removeprefix("item:")
        item = bundle.get("item", item_id) if bundle is not None else None
        if not isinstance(item, Mapping):
            # 内容被删除后残留的 canonical 状态：忽略，不恢复也不报错。
            continue
        declaration = validate_recharge_declaration(item)
        if declaration is None or declaration["on"] == "none":
            continue
        if _RECHARGE_TRIGGER_TO_REST[declaration["on"]] != rest:
            continue
        maximum = validate_charges_declaration(item)
        if maximum is None:
            raise ChargesError(f"{item_ref} declares recharge without charges")
        state = item_state[item_ref]
        charges = state.get("charges") if isinstance(state, dict) else None
        if not isinstance(charges, dict):
            # 单件状态损坏不能拖垮整次休息：跳过这件并留下可审计记录。
            logger.warning(
                "D&D2024 charge state is not an object for %s; skipped recharge", item_ref,
            )
            skipped.append({"item_ref": item_ref, "reason": "charges_not_initialized"})
            continue
        current = charges.get("current")
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            # Corrupt legacy value for this one item: skip it without failing
            # the rest of the rest.  No value is invented or truncated, but the
            # skip is recorded so the bad value is not invisible.
            logger.warning(
                "D&D2024 charge state rejected for %s: current=%r", item_ref, current,
            )
            skipped.append({"item_ref": item_ref, "reason": "invalid_current"})
            continue
        before = current
        if current > maximum:
            # The content's maximum changed under an old save.  Clamp explicitly
            # and keep the original value in the audit event instead of silently
            # rewriting it or aborting the whole rest.
            current = maximum
        amount = declaration["amount"]
        rolls: list[int] = []
        if isinstance(amount, str):
            count, sides, bonus = _dice_amount(amount)
            rng = SystemRandom()
            rolls = [rng.randint(1, sides) for _ in range(count)]
            gained = sum(rolls) + bonus
        else:
            gained = amount
        after = min(maximum, current + max(0, gained))
        if after == current and before == current:
            continue
        plans.append((item_ref, charges, before, after, rolls))

    # 先校验并算完全部条目，再统一写入；后续条目损坏时不得留下部分回充。
    events: list[dict[str, Any]] = []
    for item_ref, charges, current, after, rolls in plans:
        charges["current"] = after
        events.append({
            "type": "recharge_item",
            "item_ref": item_ref,
            "before": current,
            "after": after,
            "rolls": rolls,
        })
    for entry in skipped:
        events.append({"type": "recharge_item.skipped", **entry})
    return events
