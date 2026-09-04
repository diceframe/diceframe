"""Authoritative purchase requests and orders.

Natural-language actions are intentionally kept as requests only.  A purchase
order is created by an authenticated GM with an explicit item list and amount;
the payer then confirms the order through the normal economy settlement path.
This module owns the small amount of state needed to keep a rejected or
misidentified request available for a corrected quote.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from src.commands.state_items import add_owned_equipment_to_inventory
from src.engine.economy import (
    MAX_ECONOMY_AMOUNT,
    advance_economy_revision,
    queue_proposal,
    record_economy_outcome,
)
from src.engine.intent.parser import parse_purchase_intents

MAX_PURCHASE_REQUEST_HISTORY = 100
MAX_PURCHASE_ORDER_HISTORY = 100
VALID_DELIVERY_MODES = {"immediate", "deferred"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _collection(instance: Any, key: str) -> list[dict[str, Any]]:
    economy = getattr(instance, "economy", None)
    if not isinstance(economy, dict):
        return []
    values = economy.setdefault(key, [])
    if not isinstance(values, list):
        values = []
        economy[key] = values
    return values


def _trim_history(values: list[dict[str, Any]], limit: int) -> None:
    if len(values) <= limit:
        return
    active = [
        value for value in values
        if isinstance(value, dict) and value.get("status") in {"open", "pending", "paid"}
    ]
    history = [value for value in values if value not in active]
    budget = max(0, limit - len(active))
    values[:] = active + (history[-budget:] if budget else [])


def _clean_text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def queue_purchase_request(
    instance: Any,
    *,
    actor_uid: str,
    item_hint: str,
    action_text: str,
    round_number: int | None = None,
) -> dict[str, Any] | None:
    """Persist one player's purchase request without a price or grant.

    The request is deliberately not an economy proposal and can never charge
    or deliver.  Re-submitting the same action is idempotent; editing an action
    supersedes the old open request for that actor and round.
    """

    actor = _clean_text(actor_uid, 120)
    action = _clean_text(action_text, 1000)
    hint = _clean_text(item_hint, 120)
    if not actor or actor not in getattr(instance, "players", {}):
        return None
    if not action:
        return None
    round_no = int(
        getattr(instance, "round_number", 0) if round_number is None else round_number
    )
    requests = _collection(instance, "purchase_requests")
    for request in requests:
        if not isinstance(request, dict):
            continue
        if (
            request.get("status") == "open"
            and str(request.get("run_id") or "") == str(getattr(instance, "run_id", ""))
            and str(request.get("actor_uid") or "") == actor
            and int(request.get("round", -1) or -1) == round_no
            and str(request.get("action_text") or "") == action
        ):
            return request
    for request in requests:
        if (
            isinstance(request, dict)
            and request.get("status") == "open"
            and str(request.get("run_id") or "") == str(getattr(instance, "run_id", ""))
            and str(request.get("actor_uid") or "") == actor
            and int(request.get("round", -1) or -1) == round_no
        ):
            request["status"] = "superseded"
            request["resolved_at"] = _now()
            request["resolution_code"] = "ACTION_REVISED"
    request = {
        "id": f"purchase_req_{uuid4().hex}",
        "run_id": str(getattr(instance, "run_id", "")),
        "round": round_no,
        "actor_uid": actor,
        "item_hint": hint,
        "action_text": action,
        "status": "open",
        "source": "player_action",
        "created_at": _now(),
    }
    requests.append(request)
    _trim_history(requests, MAX_PURCHASE_REQUEST_HISTORY)
    return request


def record_purchase_requests(
    instance: Any,
    actions: Iterable[dict[str, Any]],
    *,
    language: str = "",
    currency_labels: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Record all explicit player purchase actions for the current round."""

    recorded: list[dict[str, Any]] = []
    intents = parse_purchase_intents(
        actions,
        getattr(instance, "players", {}),
        language,
        currency_labels,
    )
    for intent in intents:
        request = queue_purchase_request(
            instance,
            actor_uid=intent.actor_uid,
            item_hint=intent.item_context,
            action_text=intent.action_text,
        )
        if request is not None:
            recorded.append(request)
    return recorded


def pending_purchase_requests(instance: Any) -> list[dict[str, Any]]:
    run_id = str(getattr(instance, "run_id", ""))
    return [
        request for request in _collection(instance, "purchase_requests")
        if isinstance(request, dict)
        and request.get("status") == "open"
        and str(request.get("run_id") or "") == run_id
    ]


def filter_unordered_purchase_grants(instance: Any, data: dict[str, Any]) -> int:
    """Remove model grants that correspond to a player's purchase request.

    This is not price attribution. It is a narrow safety gate: a grant for a
    requested purchase must come from an explicit server proposal/order. Other
    loot and rewards continue through the normal narrative state pipeline.
    """

    state_update = data.get("state_update")
    if not isinstance(state_update, dict):
        return 0
    proposals = state_update.get("economy_proposals") or []
    explicit_items: set[tuple[str, str]] = set()
    for proposal in proposals:
        if not isinstance(proposal, dict) or str(proposal.get("kind") or "") != "purchase":
            continue
        uid = str(proposal.get("uid") or proposal.get("payer_uid") or "")
        for item in proposal.get("items") or proposal.get("rewards") or []:
            name = str(item.get("name") or item.get("item") or item).strip().casefold() if isinstance(item, dict) else str(item).strip().casefold()
            if uid and name:
                explicit_items.add((uid, name))
    requests = pending_purchase_requests(instance)
    requested: dict[str, set[str]] = {}
    for request in requests:
        hint = str(request.get("item_hint") or "").strip().casefold()
        if hint in {"", "这个", "那个", "它", "this", "that", "it"}:
            continue
        requested.setdefault(str(request.get("actor_uid") or ""), set()).add(
            hint
        )

    def blocked(uid: str, item: str) -> bool:
        key = str(item or "").strip().casefold()
        if not uid or not key or (uid, key) in explicit_items:
            return False
        return any(
            hint and (hint in key or key in hint)
            for hint in requested.get(uid, set())
        )

    removed = 0
    players = state_update.get("players")
    if isinstance(players, dict):
        for uid, update in players.items():
            if not isinstance(update, dict):
                continue
            for field in ("equip_gain", "weapon_change"):
                value = str(update.get(field) or "").strip()
                if value and blocked(str(uid), value):
                    update.pop(field, None)
                    removed += 1
    loot = state_update.get("loot")
    if isinstance(loot, list):
        kept = []
        for entry in loot:
            uid = str(entry.get("player") or "") if isinstance(entry, dict) else ""
            item = str(entry.get("item") or "") if isinstance(entry, dict) else ""
            if blocked(uid, item):
                removed += 1
                continue
            kept.append(entry)
        state_update["loot"] = kept
    return removed


def find_purchase_request(instance: Any, request_id: str) -> dict[str, Any] | None:
    return next(
        (
            request for request in _collection(instance, "purchase_requests")
            if isinstance(request, dict)
            and str(request.get("id") or "") == str(request_id)
            and str(request.get("run_id") or "") == str(getattr(instance, "run_id", ""))
        ),
        None,
    )


def _normalize_items(items: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for item in list(items or [])[:8]:
        value = _clean_text(item, 120)
        if value and value not in result:
            result.append(value)
    return result


def find_purchase_order(instance: Any, order_id: str) -> dict[str, Any] | None:
    return next(
        (
            order for order in _collection(instance, "purchase_orders")
            if isinstance(order, dict)
            and str(order.get("id") or "") == str(order_id)
            and str(order.get("run_id") or "") == str(getattr(instance, "run_id", ""))
        ),
        None,
    )


def create_purchase_order(
    instance: Any,
    *,
    payer_uid: str,
    amount: int,
    items: Iterable[Any],
    reason: str = "",
    recipient_uid: str = "",
    request_id: str = "",
    delivery_mode: str = "immediate",
    delivery_condition: str = "",
    source: str = "gm_manual",
    rewards: Iterable[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create an explicit pending order and its linked payer proposal."""

    payer = _clean_text(payer_uid, 120)
    recipient = _clean_text(recipient_uid or payer, 120)
    try:
        price = int(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("金额必须是整数") from exc
    if not payer or payer not in getattr(instance, "players", {}):
        raise ValueError("付款角色不存在")
    if not recipient or recipient not in getattr(instance, "players", {}):
        raise ValueError("接收角色不存在")
    if not 0 < price <= MAX_ECONOMY_AMOUNT:
        raise ValueError("金额必须在 1 到 100000 之间")
    normalized_items = _normalize_items(items)
    if not normalized_items:
        raise ValueError("购买订单至少需要一个商品")
    mode = _clean_text(delivery_mode, 20).lower() or "immediate"
    if mode not in VALID_DELIVERY_MODES:
        raise ValueError("交付方式必须是 immediate 或 deferred")
    request = find_purchase_request(instance, request_id) if request_id else None
    if request_id and request is None:
        raise ValueError("购买请求不存在")
    if request is not None and request.get("status") == "ordered":
        # A request may have at most one live order.  Retries from a flaky
        # client must not create a second proposal (and therefore a second
        # possible charge).  Return the existing pair idempotently while the
        # order is still active or already resolved.
        existing_order_id = str(request.get("order_id") or "")
        existing_order = find_purchase_order(instance, existing_order_id)
        if existing_order is not None:
            existing_proposal_id = str(existing_order.get("proposal_id") or "")
            existing_proposal = next(
                (
                    proposal for proposal in _collection(instance, "proposals")
                    if isinstance(proposal, dict)
                    and str(proposal.get("id") or "") == existing_proposal_id
                ),
                None,
            )
            if existing_proposal is not None:
                return existing_order, existing_proposal
        # A persisted ordered request without its order is inconsistent.  Do
        # not silently manufacture a new charge; let the caller surface a
        # repairable error instead.
        raise ValueError("购买请求已有订单但订单记录缺失")
    if request is not None and request.get("status") not in {"open"}:
        raise ValueError("购买请求已处理")
    order_id = f"order_{uuid4().hex}"
    normalized_rewards = [
        {
            "name": _clean_text(reward.get("name") or reward.get("item") or "", 120),
            "category": _clean_text(reward.get("category") or "", 40),
        }
        for reward in list(rewards or [])[:8]
        if isinstance(reward, dict)
        and _clean_text(reward.get("name") or reward.get("item") or "", 120)
    ]
    if not normalized_rewards:
        normalized_rewards = [{"name": item, "category": ""} for item in normalized_items]
    proposal = queue_proposal(
        instance,
        kind="purchase",
        payer_uid=payer,
        recipient_uid=recipient,
        amount=price,
        rewards=normalized_rewards,
        reason=_clean_text(reason) or f"购买 {'、'.join(normalized_items)}",
        source=source,
        source_ref=f"purchase_order:{instance.run_id}:{order_id}",
        approval_policy="payer",
        order_id=order_id,
        delivery_mode=mode,
        delivery_condition=_clean_text(delivery_condition),
        request_id=str(request_id or ""),
    )
    order = {
        "id": order_id,
        "run_id": str(instance.run_id),
        "request_id": str(request_id or ""),
        "proposal_id": str(proposal.get("id") or ""),
        "payer_uid": payer,
        "recipient_uid": recipient,
        "amount": price,
        "items": normalized_items,
        "rewards": deepcopy(normalized_rewards),
        "reason": str(proposal.get("reason") or ""),
        "delivery_mode": mode,
        "delivery_condition": _clean_text(delivery_condition),
        "status": "pending",
        "created_at": _now(),
    }
    _collection(instance, "purchase_orders").append(order)
    _trim_history(_collection(instance, "purchase_orders"), MAX_PURCHASE_ORDER_HISTORY)
    if request is not None:
        request["status"] = "ordered"
        request["order_id"] = order_id
        request["ordered_at"] = _now()
    return order, proposal


def sync_purchase_order_status(instance: Any, proposal: dict[str, Any]) -> None:
    """Mirror proposal settlement into its order without adding new semantics."""

    order_id = str(proposal.get("order_id") or "")
    if not order_id:
        return
    order = find_purchase_order(instance, order_id)
    if order is None:
        return
    status = str(proposal.get("status") or "pending")
    if status == "committed":
        order["status"] = "paid" if str(proposal.get("delivery_mode") or "immediate") == "deferred" else "delivered"
        order["paid_at"] = str(proposal.get("resolved_at") or _now())
        if order["status"] == "delivered":
            order["delivered_at"] = order["paid_at"]
    elif status in {"declined", "cancelled", "rejected", "superseded"}:
        order["status"] = status
        order["resolved_at"] = str(proposal.get("resolved_at") or _now())
        order["resolution_code"] = str(proposal.get("resolution_code") or "")


def mark_purchase_request_open(instance: Any, order: dict[str, Any]) -> None:
    request = find_purchase_request(instance, str(order.get("request_id") or ""))
    if request is not None and request.get("status") == "ordered":
        request["status"] = "open"
        request.pop("order_id", None)
        request["reopened_at"] = _now()


def mark_purchase_order_delivered(instance: Any, order: dict[str, Any], transaction_id: str) -> None:
    order["status"] = "delivered"
    order["delivered_at"] = _now()
    order["delivery_transaction_id"] = str(transaction_id)
    request = find_purchase_request(instance, str(order.get("request_id") or ""))
    if request is not None:
        request["status"] = "fulfilled"
        request["fulfilled_at"] = order["delivered_at"]


def deliver_purchase_order(
    instance: Any,
    order_id: str,
    *,
    grant_reward: Any,
) -> dict[str, Any]:
    """Deliver a paid deferred order exactly once.

    The caller owns the aggregate lock and persistence transaction.  Delivery
    creates a separate ledger entry so a later swipe can reverse the item grant
    without charging or refunding the player a second time.
    """

    order = find_purchase_order(instance, order_id)
    if order is None:
        return {"ok": False, "code": "ORDER_NOT_FOUND", "error": "购买订单不存在"}
    status = str(order.get("status") or "")
    if status == "delivered":
        return {"ok": True, "already_delivered": True, "order": deepcopy(order)}
    if status != "paid":
        return {"ok": False, "code": "ORDER_NOT_PAID", "error": "订单尚未付款，不能交付"}
    proposal_id = str(order.get("proposal_id") or "")
    proposal = next(
        (
            item for item in instance.economy.get("proposals", [])
            if isinstance(item, dict) and str(item.get("id") or "") == proposal_id
        ),
        None,
    )
    if proposal is None or proposal.get("status") != "committed":
        return {"ok": False, "code": "ORDER_PROPOSAL_MISSING", "error": "订单付款记录不存在"}
    recipient_uid = str(order.get("recipient_uid") or "")
    if recipient_uid not in getattr(instance, "players", {}):
        return {"ok": False, "code": "RECIPIENT_NOT_FOUND", "error": "接收角色不存在"}
    recipient = instance.get_character_sheet(recipient_uid)
    before = {
        key: deepcopy(recipient.get(key, []))
        for key in ("inventory", "equipment", "key_items")
    }
    rewards = order.get("rewards")
    if not isinstance(rewards, list) or not rewards:
        rewards = [{"name": item, "category": ""} for item in _normalize_items(order.get("items") or [])]
    for reward in rewards:
        if isinstance(reward, dict):
            # A purchased weapon/armor is owned first; equipping is a separate
            # explicit player action and can never replace the active weapon
            # merely because the item was delivered.
            if str(reward.get("category") or "") == "equipment":
                add_owned_equipment_to_inventory(
                    recipient,
                    str(reward.get("name") or ""),
                )
            else:
                grant_reward(recipient, reward)
    instance.set_character_sheet(recipient_uid, recipient)
    after = {
        key: deepcopy(recipient.get(key, []))
        for key in ("inventory", "equipment", "key_items")
    }
    transaction = {
        "id": f"tx_{uuid4().hex}",
        "run_id": str(instance.run_id),
        "proposal_id": proposal_id,
        "order_id": str(order.get("id") or ""),
        "kind": "delivery",
        "source": "purchase_order_delivery",
        "reason": str(order.get("reason") or "购买交付"),
        "actor_uid": str(getattr(instance, "gm_uid", "") or "system"),
        "entries": [],
        "reward_snapshots": [{
            "recipient_uid": recipient_uid,
            "before": before,
            "after": after,
        }],
        "status": "committed",
        "round": int(getattr(instance, "round_number", 0) or 0),
        "delivery_mode": "deferred",
        "delivery_status": "delivered",
        "committed_at": _now(),
    }
    instance.economy.setdefault("transactions", []).append(transaction)
    proposal["delivery_status"] = "delivered"
    proposal["delivered_at"] = transaction["committed_at"]
    mark_purchase_order_delivered(instance, order, str(transaction["id"]))
    advance_economy_revision(instance)
    outcome = record_economy_outcome(
        instance,
        {
            **proposal,
            "reason": f"{order.get('reason') or '购买'}（交付）",
            "resolved_at": transaction["committed_at"],
        },
        status="delivered",
        actor_uid=str(getattr(instance, "gm_uid", "") or "system"),
    )
    return {
        "ok": True,
        "order": deepcopy(order),
        "transaction": deepcopy(transaction),
        "outcome": deepcopy(outcome),
    }


def reopen_purchase_order_request(instance: Any, proposal: dict[str, Any]) -> None:
    order_id = str(proposal.get("order_id") or "")
    order = find_purchase_order(instance, order_id) if order_id else None
    if order is not None and order.get("status") in {"rejected", "declined", "cancelled"}:
        mark_purchase_request_open(instance, order)


def purchase_order_view(order: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(order)
