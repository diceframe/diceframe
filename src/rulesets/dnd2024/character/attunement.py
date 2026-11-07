"""D&D 2024 attunement: the one owning module for magic-item binding.

The 2024 rules put attunement in a short rest and cap a creature at three
attuned magic items (DMG 2024, chapter 7, "Attunement").  The cap is a
*derived* value: a class feature may add slots, so it can never be a literal
``3`` scattered through the codebase.

Two boundaries are deliberate:

* canonical state is keyed by **item ref**, not by inventory row.  The live
  layer has no per-instance identity and the canonical equipment projection
  dedupes by ref, so two copies of the same magic item cannot be told apart.
  When a character owns more than one copy of the same attunable ref, binding
  and unbinding are rejected instead of silently making every copy work.
* every consumer asks :func:`attuned_refs` whether an item is bound.  No other
  module re-implements that predicate.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import Any

logger = logging.getLogger("trpg")

BASE_ATTUNEMENT_SLOTS = 3
MAX_ATTUNEMENT_SLOTS = 10

# Short-rest payload keys.  Anything else is rejected: the rest entry point is
# a narrow, explicit contract.
REST_REQUEST_KEYS = frozenset({"attune", "unattune"})


class AttunementError(ValueError):
    """An attunement declaration or transition is illegal: fail closed."""


def canonical_item_ref(value: Any) -> str:
    """Normalize one ``item:<id>`` reference; reject any other kind."""

    text = str(value or "").strip()
    if not text:
        raise AttunementError("item reference must not be empty")
    if ":" not in text:
        raise AttunementError(f"item reference must be explicit: {text!r}")
    kind, item_id = text.split(":", 1)
    if kind != "item" or not item_id:
        raise AttunementError(f"item reference must be item:<id>: {text!r}")
    return f"item:{item_id}"


def validate_item_declaration(item: Any) -> bool:
    """Validate the one attunement field an item definition may carry.

    ``requires_attunement`` is a boolean switch.  A missing field means false;
    any other type is a content error rather than a truthy guess.
    """

    if not isinstance(item, Mapping):
        raise AttunementError("item definition must be an object")
    if "requires_attunement" not in item:
        return False
    value = item["requires_attunement"]
    if not isinstance(value, bool):
        raise AttunementError("requires_attunement must be a boolean")
    return value


def requires_attunement(item: Any) -> bool:
    """Read the declaration with fail-closed validation."""

    return validate_item_declaration(item)


def attuned_refs(canonical: Any) -> frozenset[str]:
    """The only gate: canonical ``attunement.refs`` as a set of item refs.

    A missing or malformed segment is an old or damaged save, not evidence that
    everything is attuned: it degrades to the empty set with a warning.
    """

    if not isinstance(canonical, Mapping):
        logger.warning("D&D2024 同调告警: canonical character is not an object")
        return frozenset()
    state = canonical.get("attunement")
    if state is None:
        return frozenset()
    if not isinstance(state, Mapping):
        logger.warning("D&D2024 同调告警: attunement segment is malformed")
        return frozenset()
    raw_refs = state.get("refs")
    if not isinstance(raw_refs, list):
        logger.warning("D&D2024 同调告警: attunement.refs is malformed")
        return frozenset()
    refs: set[str] = set()
    for raw in raw_refs:
        try:
            refs.add(canonical_item_ref(raw))
        except AttunementError:
            logger.warning("D&D2024 同调告警: invalid attuned reference ignored: %r", raw)
    return frozenset(refs)


def attunement_capacity(canonical: Any, resolver: Any) -> int:
    """Base 3 plus every owned ``attunement_slot_bonus`` feature parameter.

    The resolver owns ownership and table lookups, so this module never walks
    class tables itself.  Out-of-range declarations fail closed rather than
    producing a negative or unbounded capacity.
    """

    if resolver is None:
        return BASE_ATTUNEMENT_SLOTS
    bonus = 0
    for definition in resolver.features_for(canonical):
        value = resolver.feature_value(
            canonical, definition.id, "attunement_slot_bonus", 0,
        )
        if isinstance(value, bool) or not isinstance(value, int):
            raise AttunementError(
                f"attunement_slot_bonus for {definition.id!r} must be an integer"
            )
        bonus += value
    capacity = BASE_ATTUNEMENT_SLOTS + bonus
    if not 0 <= capacity <= MAX_ATTUNEMENT_SLOTS:
        raise AttunementError(
            f"attunement capacity {capacity} is outside 0..{MAX_ATTUNEMENT_SLOTS}"
        )
    return capacity


def attunement_state_for_projection(
    canonical: Any, capacity: int | None = None,
) -> dict[str, Any]:
    """Read-only projection for the live sheet and frontend text.

    ``capacity`` is supplied by reconciliation (which owns the resolver); when
    omitted only the bound refs are projected.
    """

    state: dict[str, Any] = {"refs": sorted(attuned_refs(canonical))}
    if capacity is not None:
        state["capacity"] = int(capacity)
    return state


def pending_attunement_refs(
    equipped_refs: Collection[str], bundle: Any, attuned: Collection[str],
) -> list[str]:
    """Worn items that require attunement but are not currently bound."""

    bound = {canonical_item_ref(ref) for ref in attuned}
    pending: list[str] = []
    for raw in equipped_refs:
        try:
            ref = canonical_item_ref(raw)
        except AttunementError:
            continue
        item = bundle.get("item", ref.removeprefix("item:"))
        if item is None:
            continue
        if requires_attunement(item) and ref not in bound:
            pending.append(ref)
    return pending


def item_requires_attunement(bundle: Any, ref: str) -> bool:
    item = bundle.get("item", canonical_item_ref(ref).removeprefix("item:"))
    return item is not None and requires_attunement(item)


def _canonical_of(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        nested = payload.get("ruleset_character")
        if isinstance(nested, Mapping):
            return nested
    return payload


def _row_ref(row: Any) -> str:
    if not isinstance(row, Mapping):
        return ""
    raw = row.get("item_ref")
    if raw:
        try:
            return canonical_item_ref(raw)
        except AttunementError:
            return ""
    item_key = str(row.get("item_key") or "").strip()
    return f"item:{item_key}" if item_key else ""


def _row_owned_copies(row: Any) -> int:
    """Copies represented by one live row; a missing quantity means one.

    ``qty`` is the live inventory/equipment quantity field (see
    ``commands.state_items``).  Counting rows instead of copies would let a
    single ``qty: 2`` stack bypass the duplicate guard.
    """

    if not isinstance(row, Mapping):
        return 0
    raw = row.get("qty", 1)
    if isinstance(raw, bool):
        return 1
    try:
        copies = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(0, copies)


def owned_ref_counts(payload: Any) -> dict[str, int]:
    """How many copies of each canonical ref the character owns.

    Live equipment/inventory rows are the authority when they carry identity.
    A canonical ref with no live row still counts once (older saves project
    equipment without annotating rows yet); a live row for the same ref does
    not double count.
    """

    canonical = _canonical_of(payload)
    counts: dict[str, int] = {}
    for field in ("equipment", "inventory"):
        rows = payload.get(field) if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            ref = _row_ref(row)
            if ref:
                counts[ref] = counts.get(ref, 0) + _row_owned_copies(row)
    canonical_refs = (
        (canonical.get("equipment") or {}).get("item_refs")
        if isinstance(canonical, Mapping) else None
    )
    if isinstance(canonical_refs, list):
        live = set(counts)
        for raw in canonical_refs:
            try:
                ref = canonical_item_ref(raw)
            except AttunementError:
                continue
            if ref not in live:
                live.add(ref)
                counts[ref] = counts.get(ref, 0) + 1
    return counts


def inventory_ref_counts(payload: Any) -> dict[str, int]:
    """Copies carried in the live inventory only.

    Consumables and ammunition are physically taken from the backpack by
    ``take_from_inventory``.  Equipment rows and canonical equipment refs are
    not a backpack path, so counting them here would let an activation pass
    validation and only fail after the roll, once the batch is applied.
    """

    counts: dict[str, int] = {}
    rows = payload.get("inventory") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return counts
    for row in rows:
        ref = _row_ref(row)
        if ref:
            counts[ref] = counts.get(ref, 0) + _row_owned_copies(row)
    return counts


def count_requires_attunement_copies(
    payload: Any, ref: str, bundle: Any,
) -> int:
    """Owned copies of one attunable ref (0 when the ref is not attunable)."""

    ref = canonical_item_ref(ref)
    if not item_requires_attunement(bundle, ref):
        return 0
    return owned_ref_counts(payload).get(ref, 0)


def _refs_list(canonical: Mapping[str, Any]) -> list[str]:
    return sorted(attuned_refs(canonical))


def _write_refs(canonical: dict[str, Any], refs: Collection[str]) -> None:
    canonical["attunement"] = {"refs": sorted(refs)}


def _validate_request(request: Any) -> tuple[str, str]:
    if request is None:
        return "", ""
    if not isinstance(request, Mapping):
        raise AttunementError("attunement must be an object")
    unknown = sorted(set(request) - REST_REQUEST_KEYS)
    if unknown:
        raise AttunementError(f"unknown attunement field: {unknown[0]}")
    attune = ""
    unattune = ""
    if request.get("attune") is not None:
        attune = canonical_item_ref(request["attune"])
    if request.get("unattune") is not None:
        unattune = canonical_item_ref(request["unattune"])
    if not attune and not unattune:
        raise AttunementError("attunement must name an item to bind or unbind")
    return attune, unattune


def _guard_single_copy(payload: Any, ref: str, bundle: Any) -> None:
    copies = count_requires_attunement_copies(payload, ref, bundle)
    if copies == 0:
        raise AttunementError(f"character does not own {ref}")
    if copies > 1:
        raise AttunementError(
            f"character owns {copies} copies of {ref}; "
            "a single attunement cannot cover them"
        )


def _validate_binding(
    canonical: dict[str, Any], ref: str, payload: Any, bundle: Any,
) -> None:
    if not item_requires_attunement(bundle, ref):
        raise AttunementError(f"{ref} does not require attunement")
    _guard_single_copy(payload, ref, bundle)
    if ref in attuned_refs(canonical):
        raise AttunementError(f"{ref} is already attuned")


def _validate_unbinding(
    canonical: dict[str, Any], ref: str, payload: Any, bundle: Any,
) -> None:
    """Unbinding frees a slot and never depends on the item still existing.

    An item may have been dropped, sold, stacked, or removed from content
    since it was bound.  Binding requires ownership and a single copy; unbinding
    must not, otherwise the slot can never be released and an old save keeps an
    attunement it cannot clear.
    """

    del payload, bundle
    if ref not in attuned_refs(canonical):
        raise AttunementError(f"{ref} is not attuned")


def apply_short_rest_attunement(
    canonical: dict[str, Any], payload: Any, request: Any,
    bundle: Any, resolver: Any,
) -> list[dict[str, Any]]:
    """Resolve one short-rest ``attunement`` payload atomically.

    Unbinding is validated first and applied before binding, so a swap fits in
    one short rest even at full capacity.  Nothing is written until every
    requested step is legal.  Raises :class:`AttunementError` for the caller to
    record as a rest event; the rest itself is never rolled back.
    """

    attune, unattune = _validate_request(request)
    if not attune and not unattune:
        return []
    capacity = attunement_capacity(canonical, resolver)
    if unattune:
        _validate_unbinding(canonical, unattune, payload, bundle)
    if attune:
        _validate_binding(canonical, attune, payload, bundle)

    remaining = set(attuned_refs(canonical))
    events: list[dict[str, Any]] = []
    if unattune:
        remaining.discard(unattune)
        events.append({
            "type": "unattune_item",
            "item_ref": unattune,
            "reason": "swap" if attune else "manual",
        })
    if attune:
        remaining.add(attune)
        events.append({"type": "attune_item", "item_ref": attune})
    # 解绑永远允许（即使旧档/特性变化已让现有数量超容量）；只有新增绑定
    # 才需要满足容量。这样失配的旧档可以逐步收敛回合法状态。
    if attune and len(remaining) > capacity:
        raise AttunementError(
            f"attunement capacity {capacity} would be exceeded"
        )
    _write_refs(canonical, remaining)
    return events


def clear_attunement_on_death(canonical: dict[str, Any]) -> list[str]:
    """Death ends every attunement; revival never restores it."""

    cleared = _refs_list(canonical)
    if cleared:
        _write_refs(canonical, ())
    return cleared
