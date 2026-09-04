"""Ruleset-neutral authoritative economy proposals and transactions."""

from __future__ import annotations

from copy import deepcopy
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from src.engine.character_utils import apply_currency_delta
from src.engine.memory_outbox import (
    MAX_EXTERNAL_EFFECT_DELIVERIES,
    pending_memory_deliveries,
    pending_memory_reversals,
)

MAX_ECONOMY_AMOUNT = 100_000
ECONOMY_KINDS = {"payment", "purchase", "fee", "transfer", "reward"}
APPROVAL_POLICIES = {"payer", "gm", "system", "all_contributors"}
MAX_ECONOMY_OUTCOMES = 50
MAX_EFFECT_GROUPS = 50

# Explicit transition whitelist for the persisted offer state machine.  A
# proposal starts at "pending"; "committed" may only be reversed by a rollback
# (or reopened by a settlement-only rollback).  Everything else is terminal and
# must never be re-resolved.
PAYER_ECONOMY_KINDS = {"payment", "purchase", "fee", "transfer"}
PROPOSAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"committed", "declined", "cancelled", "rejected", "superseded"}),
    "committed": frozenset({"reversed", "pending"}),
}


def proposal_transition_allowed(current_status: Any, next_status: str) -> bool:
    return next_status in PROPOSAL_TRANSITIONS.get(str(current_status or "pending"), frozenset())


def set_proposal_status(proposal: dict[str, Any], next_status: str) -> None:
    """Move a persisted proposal along the whitelisted state machine."""

    current = str(proposal.get("status") or "pending")
    if not proposal_transition_allowed(current, next_status):
        raise ValueError(f"illegal economy proposal transition: {current} -> {next_status}")
    proposal["status"] = next_status


def economy_revision(instance: Any) -> int:
    economy = getattr(instance, "economy", {})
    if not isinstance(economy, dict):
        return 0
    return int(economy.get("decision_revision", 0) or 0)


def _advance_revision(instance: Any) -> int:
    revision = economy_revision(instance) + 1
    instance.economy["decision_revision"] = revision
    return revision


def advance_economy_revision(instance: Any) -> int:
    """Advance the authoritative economy decision revision."""

    return _advance_revision(instance)


def _record_outcome(
    instance: Any,
    proposal: dict[str, Any],
    *,
    status: str,
    actor_uid: str,
) -> dict[str, Any]:
    effect_group = _effect_group_for(instance, proposal)
    outcome = {
        "id": f"outcome_{uuid4().hex}",
        "run_id": instance.run_id,
        "proposal_id": str(proposal.get("id") or ""),
        "effect_group_id": str(proposal.get("effect_group_id") or ""),
        "kind": str(proposal.get("kind") or "payment"),
        "payer_uid": str(proposal.get("payer_uid") or proposal.get("uid") or ""),
        "recipient_uid": str(proposal.get("recipient_uid") or ""),
        "amount": int(proposal.get("amount", 0) or 0),
        "reason": str(proposal.get("reason") or "经济提案")[:240],
        "status": str(status),
        "effects_status": (
            str(effect_group.get("status") or "pending")
            if effect_group is not None else "none"
        ),
        "actor_uid": str(actor_uid),
        "visibility": str(proposal.get("visibility") or "private"),
        "round": int(proposal.get("round", getattr(instance, "round_number", 0)) or 0),
        # Keep proposal origin round separate from the round in which this
        # decision was actually settled.  Rollback uses this field to remove
        # late-payment outcomes without invalidating the original offer.
        "resolved_round": int(getattr(instance, "round_number", 0) or 0),
        "resolved_at": str(proposal.get("resolved_at") or datetime.now(timezone.utc).isoformat()),
    }
    outcomes = instance.economy.setdefault("outcomes", [])
    outcomes.append(outcome)
    if len(outcomes) > MAX_ECONOMY_OUTCOMES:
        del outcomes[:-MAX_ECONOMY_OUTCOMES]
    return outcome


def record_economy_outcome(
    instance: Any,
    proposal: dict[str, Any],
    *,
    status: str,
    actor_uid: str,
) -> dict[str, Any]:
    """Record one bounded, authoritative economy outcome."""

    return _record_outcome(instance, proposal, status=status, actor_uid=actor_uid)


def queue_effect_group(
    instance: Any,
    proposals: list[dict[str, Any]],
    effects: dict[str, Any],
) -> dict[str, Any] | None:
    """Attach one deferred narrative effect batch to its authoritative decisions.

    The legacy tag protocol cannot assign individual effects to individual
    charges. When one response creates several proposals, the conservative
    contract is therefore one all-or-nothing decision barrier: all proposals
    must commit before effects apply, and any terminal rejection discards them.
    """

    candidates = [
        proposal for proposal in proposals
        if isinstance(proposal, dict)
        and proposal.get("status") == "pending"
        and proposal.get("run_id") == instance.run_id
    ]
    if not candidates or not effects:
        return None
    group = {
        "id": f"effect_{uuid4().hex}",
        "run_id": instance.run_id,
        "proposal_ids": [str(proposal.get("id") or "") for proposal in candidates],
        "effects": deepcopy(effects),
        "status": "pending",
        "round": int(getattr(instance, "round_number", 0) or 0),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    groups = instance.economy.setdefault("effect_groups", [])
    groups.append(group)
    if len(groups) > MAX_EFFECT_GROUPS:
        active = [
            item for item in groups
            if isinstance(item, dict)
            and item.get("status") in {"pending", "ready"}
        ]
        resolved = [
            item for item in groups
            if isinstance(item, dict)
            and item.get("status") not in {"pending", "ready"}
        ]
        resolved_budget = max(0, MAX_EFFECT_GROUPS - len(active))
        instance.economy["effect_groups"] = (
            active + resolved[-resolved_budget:]
            if resolved_budget else active
        )
    for proposal in candidates:
        proposal["effect_group_id"] = group["id"]
    return group


def _effect_group_for(
    instance: Any,
    proposal: dict[str, Any],
) -> dict[str, Any] | None:
    group_id = str(proposal.get("effect_group_id") or "")
    if not group_id:
        return None
    return next(
        (
            group for group in instance.economy.get("effect_groups", [])
            if isinstance(group, dict) and group.get("id") == group_id
        ),
        None,
    )


def _settle_effect_group(
    instance: Any,
    proposal: dict[str, Any],
) -> dict[str, Any] | None:
    group = _effect_group_for(instance, proposal)
    if group is None or group.get("status") != "pending":
        return None
    if proposal.get("status") in {"declined", "cancelled", "rejected"}:
        group["status"] = "discarded"
        group["resolved_at"] = str(proposal.get("resolved_at") or "")
        group.pop("effects", None)
        group_id = str(group.get("id") or "")
        for outcome in instance.economy.get("outcomes", []):
            if str(outcome.get("effect_group_id") or "") == group_id:
                outcome["effects_status"] = "discarded"
        return None
    proposal_ids = {str(item) for item in group.get("proposal_ids", []) if str(item)}
    states = {
        str(item.get("id") or ""): str(item.get("status") or "")
        for item in instance.economy.get("proposals", [])
        if isinstance(item, dict) and str(item.get("id") or "") in proposal_ids
    }
    if proposal_ids and all(states.get(proposal_id) == "committed" for proposal_id in proposal_ids):
        group["status"] = "ready"
        return deepcopy(group)
    return None


def complete_effect_group(instance: Any, group_id: str) -> bool:
    group = next(
        (
            item for item in instance.economy.get("effect_groups", [])
            if isinstance(item, dict) and item.get("id") == group_id
        ),
        None,
    )
    if group is None or group.get("run_id") != instance.run_id:
        return False
    if group.get("status") == "committed":
        return True
    if group.get("status") != "ready":
        return False
    group["status"] = "committed"
    group["committed_at"] = datetime.now(timezone.utc).isoformat()
    group.pop("effects", None)
    for outcome in instance.economy.get("outcomes", []):
        if str(outcome.get("effect_group_id") or "") == group_id:
            outcome["effects_status"] = "committed"
    return True


def cancel_proposals_for_player(
    instance: Any,
    uid: str,
    *,
    resolution_code: str = "PLAYER_REMOVED",
) -> set[str]:
    """Cancel unresolved proposals involving a player and discard their effects."""

    affected_ids: set[str] = set()
    now = datetime.now(timezone.utc).isoformat()
    for proposal in instance.economy.get("proposals", []):
        if not isinstance(proposal, dict) or proposal.get("status") != "pending":
            continue
        participant_uids = {
            str(proposal.get("payer_uid") or proposal.get("uid") or ""),
            str(proposal.get("recipient_uid") or ""),
            *(
                str(item.get("uid") or "")
                for item in (proposal.get("contributors") or [])
                if isinstance(item, dict)
            ),
        }
        if uid not in participant_uids:
            continue
        set_proposal_status(proposal, "cancelled")
        proposal["resolved_at"] = now
        proposal["resolution_code"] = resolution_code
        proposal_id = str(proposal.get("id") or "")
        affected_ids.add(proposal_id)
        _advance_revision(instance)
        _record_outcome(
            instance,
            proposal,
            status="cancelled",
            actor_uid="system",
        )
        _settle_effect_group(instance, proposal)
    return affected_ids


def pending_proposals(instance: Any) -> list[dict[str, Any]]:
    economy = getattr(instance, "economy", {})
    proposals = economy.get("proposals") if isinstance(economy, dict) else []
    return [
        item for item in (proposals or [])
        if (
            isinstance(item, dict)
            and item.get("status") == "pending"
            and item.get("run_id") == getattr(instance, "run_id", "")
        )
    ]


def pending_economy_proposals(instance: Any) -> list[dict[str, Any]]:
    """Return canonical pending proposals for the current run."""

    return pending_proposals(instance)


def is_nonblocking_personal_purchase(
    instance: Any,
    proposal: dict[str, Any],
) -> bool:
    """Whether a purchase is safe to leave pending while the table continues.

    This is deliberately a narrow, fail-closed classification.  Only a
    payer-approved purchase that grants an item to that same payer and has no
    deferred narrative/external effect may cross a round boundary.
    """

    if not isinstance(proposal, dict):
        return False
    if proposal.get("status") != "pending" or proposal.get("run_id") != instance.run_id:
        return False
    if str(proposal.get("kind") or "") != "purchase":
        return False
    if str(proposal.get("approval_policy") or "") != "payer":
        return False
    payer_uid = str(proposal.get("payer_uid") or proposal.get("uid") or "")
    recipient_uid = str(proposal.get("recipient_uid") or payer_uid)
    if not payer_uid or recipient_uid != payer_uid:
        return False
    contributors = proposal.get("contributors")
    if contributors:
        return False
    rewards = proposal.get("rewards")
    if not isinstance(rewards, list) or not rewards:
        return False
    if str(proposal.get("effect_group_id") or ""):
        return False
    # These fields indicate a transaction-dependent result.  Unknown fields
    # are not rejected, but any known external/deferred payload fails closed.
    for key in (
        "deferred_effects", "memory_delta", "scene_image_prompt",
        "quest", "plot", "private_info", "quick_actions", "narrative_effects",
    ):
        if proposal.get(key):
            return False
    return True


def is_auto_settleable_reward(
    instance: Any,
    proposal: dict[str, Any],
    *,
    gold_cap: int = 50,
) -> bool:
    """Whether a pending narrative reward may settle without a GM click.

    Deliberately narrow: plain single-recipient gold rewards for a current
    player within the configured cap.  Team splits, cross-run entries and
    anything outside the cap stay blocking so the GM keeps final say over
    unusual or high-impact grants.
    """

    if not isinstance(proposal, dict):
        return False
    if proposal.get("status") != "pending" or str(proposal.get("run_id") or "") != str(instance.run_id):
        return False
    if str(proposal.get("kind") or "") != "reward":
        return False
    if proposal.get("contributors"):
        return False
    recipient_uid = str(proposal.get("recipient_uid") or "")
    if not recipient_uid or recipient_uid not in instance.players:
        return False
    try:
        amount = int(proposal.get("amount", 0) or 0)
    except (TypeError, ValueError):
        return False
    return 0 < amount <= gold_cap


def blocking_economy_proposals(
    instance: Any,
    *,
    auto_reward_gold_cap: int | None = None,
) -> list[dict[str, Any]]:
    """Return pending proposals that must hold the narrative barrier.

    When ``auto_reward_gold_cap`` is provided (auto-reward enabled), plain
    rewards within the cap are not blockers: they will settle automatically
    right after the current round completes through the standard payment
    path.  Without the argument the behavior is unchanged (all pending
    rewards block, as before).
    """

    def _blocked(proposal: dict[str, Any]) -> bool:
        if not is_nonblocking_personal_purchase(instance, proposal):
            if (
                auto_reward_gold_cap is not None
                and is_auto_settleable_reward(instance, proposal, gold_cap=auto_reward_gold_cap)
            ):
                return False
            return True
        return False

    return [
        proposal for proposal in pending_economy_proposals(instance)
        if _blocked(proposal)
    ]


def pending_effect_groups(instance: Any) -> list[dict[str, Any]]:
    """Return unresolved effect groups owned by the current run."""

    economy = getattr(instance, "economy", {})
    groups = economy.get("effect_groups") if isinstance(economy, dict) else []
    return [
        item for item in (groups or [])
        if (
            isinstance(item, dict)
            and item.get("status") in {"pending", "ready"}
            and item.get("run_id") == getattr(instance, "run_id", "")
        )
    ]


ECONOMY_RESOLUTION_STATUSES = frozenset({"committed", "declined", "cancelled", "rejected"})


def economy_fingerprint(instance: Any) -> dict[str, str]:
    """Snapshot one (proposal id -> status) entry per proposal.

    Optimistic-concurrency helpers compare two fingerprints to decide whether
    the economy changed in a way that invalidates an in-flight LLM response.
    """

    return {
        str(item.get("id") or ""): str(item.get("status") or "")
        for item in getattr(instance, "economy", {}).get("proposals", [])
        if isinstance(item, dict)
    }


def economy_changes_are_resolutions_only(
    before: dict[str, str],
    after: dict[str, str],
) -> bool:
    """Whether proposal changes are solely settlements of pending proposals.

    Payer confirmations/declines that arrive while an LLM response is being
    generated do not invalidate that response: balances were validated
    authoritatively at resolution time and model-emitted charges are dropped
    regardless.  Rollback artifacts (reversed / superseded / reopened
    proposals) and removed entries still count as stale.
    """

    # A proposal that did not exist at the start is a concurrent write, not a
    # resolution of an already-known decision.  It must invalidate the
    # in-flight narrative; otherwise a newly-created charge can be silently
    # folded into a response generated from an older economy snapshot.
    if set(after) - set(before):
        return False
    for pid, before_status in before.items():
        after_status = after.get(pid)
        if after_status == before_status:
            continue
        if (
            before_status == "pending"
            and after_status in ECONOMY_RESOLUTION_STATUSES
        ):
            continue
        return False
    return True


def has_pending_economy_decision(instance: Any) -> bool:
    """Whether any economy decision remains unresolved (compatibility API)."""

    return bool(
        pending_economy_proposals(instance)
        or pending_effect_groups(instance)
        or pending_memory_deliveries(instance)
        or pending_memory_reversals(instance)
    )


def has_blocking_economy_decision(
    instance: Any,
    *,
    auto_reward_gold_cap: int | None = None,
) -> bool:
    """Whether unresolved economy state must stop narrative progression."""

    return bool(
        blocking_economy_proposals(instance, auto_reward_gold_cap=auto_reward_gold_cap)
        or pending_effect_groups(instance)
        or pending_memory_deliveries(instance)
        or pending_memory_reversals(instance)
    )


def _existing_by_source(instance: Any, source_ref: str) -> dict[str, Any] | None:
    if not source_ref:
        return None
    economy = instance.economy
    record_id = economy.get("idempotency_records", {}).get(source_ref)
    for proposal in economy.get("proposals", []):
        if (
            proposal.get("status") not in {"reversed", "superseded"}
            and (proposal.get("id") == record_id or proposal.get("source_ref") == source_ref)
        ):
            return proposal
    return None


def queue_proposal(
    instance: Any,
    *,
    kind: str,
    amount: int,
    payer_uid: str = "",
    recipient_uid: str = "",
    reason: str = "",
    source: str = "narrative",
    source_ref: str = "",
    approval_policy: str = "payer",
    rewards: list[dict[str, Any]] | None = None,
    contributors: list[dict[str, Any]] | None = None,
    visibility: str = "private",
) -> dict[str, Any]:
    """Queue one idempotent proposal; no balance is changed here."""

    amount = int(amount)
    if not 0 < amount <= MAX_ECONOMY_AMOUNT:
        raise ValueError("economy amount is out of range")
    if kind not in ECONOMY_KINDS:
        raise ValueError("unsupported economy proposal kind")
    if approval_policy not in APPROVAL_POLICIES:
        raise ValueError("unsupported economy approval policy")
    if kind in PAYER_ECONOMY_KINDS and approval_policy not in {"payer", "all_contributors"}:
        raise ValueError("chargeable economy proposals require payer approval")
    payer_uid = str(payer_uid)
    if payer_uid and kind in PAYER_ECONOMY_KINDS and payer_uid not in instance.players:
        # Fail closed at the proposal layer: a payer outside the current game
        # can never be settled, so the offer must not become pending.
        raise ValueError("economy payer is not part of the current game")
    normalized_contributors = deepcopy(list(contributors or []))
    if approval_policy == "all_contributors":
        contributor_uids = [
            str(item.get("uid") or "")
            for item in normalized_contributors
            if isinstance(item, dict)
        ]
        contributor_amounts = [
            int(item.get("amount", 0) or 0)
            for item in normalized_contributors
            if isinstance(item, dict)
        ]
        if (
            not contributor_uids
            or any(not uid for uid in contributor_uids)
            or len(set(contributor_uids)) != len(contributor_uids)
            or any(value <= 0 for value in contributor_amounts)
            or sum(contributor_amounts) != amount
        ):
            raise ValueError("contributors must uniquely cover the proposal amount")
    existing = _existing_by_source(instance, source_ref)
    if existing is not None:
        return existing
    economy = instance.economy
    sequence = int(economy.get("next_sequence", 1) or 1)
    proposal = {
        "id": f"eco_{uuid4().hex}",
        "run_id": instance.run_id,
        "sequence": sequence,
        "kind": str(kind),
        "payer_uid": str(payer_uid),
        "recipient_uid": str(recipient_uid),
        "uid": str(payer_uid or recipient_uid),  # legacy payment projection
        "amount": amount,
        "reason": str(reason or "经济提案")[:240],
        "source": str(source),
        "source_ref": str(source_ref),
        "approval_policy": str(approval_policy),
        "rewards": deepcopy(list(rewards or [])),
        "contributors": normalized_contributors,
        "approvals": {},
        "visibility": visibility if visibility in {"private", "party"} else "private",
        "status": "pending",
        "round": int(getattr(instance, "round_number", 0) or 0),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    economy.setdefault("proposals", []).append(proposal)
    economy["next_sequence"] = sequence + 1
    if source_ref:
        economy.setdefault("idempotency_records", {})[source_ref] = proposal["id"]
    return proposal


PURCHASE_OFFER_SOURCES = frozenset({"gm_manual", "table_offer"})


def queue_purchase_offer(
    instance: Any,
    *,
    payer_uid: str,
    amount: int,
    items: list[str],
    reason: str = "",
    source: str = "table_offer",
    source_ref: str = "",
) -> dict[str, Any]:
    """Create one pending purchase charge that only the payer may confirm.

    This is the sole entry point for chargeable purchase proposals. `source`
    records provenance for audit and never alters behavior.
    """
    if str(source) not in PURCHASE_OFFER_SOURCES:
        raise ValueError("unsupported purchase offer source")
    from src.commands.state_items import normalized_reward_entries

    rewards = normalized_reward_entries(items, {})
    if not rewards:
        raise ValueError("purchase offer requires at least one item")
    return queue_proposal(
        instance,
        kind="purchase",
        amount=amount,
        payer_uid=payer_uid,
        recipient_uid=payer_uid,
        reason=reason,
        source=source,
        source_ref=source_ref,
        approval_policy="payer",
        rewards=rewards,
        # A purchase offer changes the shared fiction (the party is waiting
        # for the item/payment decision), so its proposal and final outcome
        # are visible to the whole party.  Private GM payments can still use
        # queue_proposal(..., visibility="private") explicitly.
        visibility="party",
    )


def filter_unconfirmed_purchase_grants(instance: Any, data: dict[str, Any]) -> int:
    """Strip model grants for items that are part of an unconfirmed charge.

    A pending chargeable proposal owns its reward items until the payer
    confirms.  Model-emitted grants whose names match a pending purchase
    proposal's rewards are removed so the item cannot bypass the payment.
    Other loot and rewards continue through the normal narrative pipeline.
    """

    state_update = data.get("state_update")
    if not isinstance(state_update, dict):
        return 0
    pending_items: dict[str, set[str]] = {}
    for proposal in instance.economy.get("proposals", []):
        if not isinstance(proposal, dict) or proposal.get("status") != "pending":
            continue
        if str(proposal.get("kind") or "") not in PAYER_ECONOMY_KINDS:
            continue
        uid = str(proposal.get("payer_uid") or proposal.get("uid") or "")
        for reward in proposal.get("rewards") or []:
            name = (
                str(reward.get("name") or "").strip().casefold()
                if isinstance(reward, dict) else str(reward).strip().casefold()
            )
            if uid and name:
                pending_items.setdefault(uid, set()).add(name)

    def blocked(uid: str, item: str) -> bool:
        key = str(item or "").strip().casefold()
        if not uid or not key:
            return False
        return any(
            name and (name in key or key in name)
            for name in pending_items.get(uid, set())
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


def resolve_proposal(
    instance: Any,
    proposal_id: str,
    *,
    actor_uid: str,
    accepted: bool,
    grant_reward: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Resolve one proposal. Caller must hold the aggregate lock and persist."""

    proposal = next(
        (item for item in instance.economy.get("proposals", []) if item.get("id") == proposal_id),
        None,
    )
    if proposal is None:
        return {"ok": False, "code": "PROPOSAL_NOT_FOUND", "error": "经济提案不存在"}
    if proposal.get("run_id") != instance.run_id:
        return {"ok": False, "code": "STALE_RUN", "error": "这是上一局的经济请求"}
    if proposal.get("status") != "pending":
        return {"ok": False, "code": "ALREADY_RESOLVED", "error": "经济提案已处理"}

    kind = str(proposal.get("kind") or "payment")
    payer_uid = str(proposal.get("payer_uid") or proposal.get("uid") or "")
    recipient_uid = str(proposal.get("recipient_uid") or payer_uid)
    policy = str(proposal.get("approval_policy") or "payer")
    is_gm = actor_uid == instance.gm_uid
    contributors = [
        item for item in (proposal.get("contributors") or [])
        if isinstance(item, dict) and str(item.get("uid") or "")
    ]
    contributor_uids = {str(item.get("uid") or "") for item in contributors}
    if policy == "all_contributors":
        allowed = actor_uid in contributor_uids or (not accepted and is_gm)
    elif policy == "gm":
        allowed = is_gm
    elif policy == "system":
        allowed = actor_uid == "system"
    elif policy == "payer_or_gm_legacy":
        allowed = actor_uid in {payer_uid, instance.gm_uid}
    else:
        allowed = actor_uid == payer_uid or (not accepted and is_gm)
    if not allowed:
        return {"ok": False, "code": "FORBIDDEN", "error": "无权处理这项经济提案"}

    now = datetime.now(timezone.utc).isoformat()
    if not accepted:
        set_proposal_status(
            proposal,
            "cancelled" if is_gm and actor_uid != payer_uid else "declined",
        )
        proposal["resolved_at"] = now
        proposal["resolution_code"] = (
            "CANCELLED_BY_GM" if is_gm and actor_uid != payer_uid
            else "DECLINED_BY_PAYER"
        )
        _advance_revision(instance)
        outcome = _record_outcome(
            instance, proposal, status=str(proposal["status"]), actor_uid=actor_uid,
        )
        _settle_effect_group(instance, proposal)
        return {
            "ok": True,
            "accepted": False,
            "proposal": deepcopy(proposal),
            "outcome": deepcopy(outcome),
        }

    if policy == "all_contributors":
        approvals = proposal.setdefault("approvals", {})
        approvals[actor_uid] = True
        missing = sorted(contributor_uids.difference(
            uid for uid, approved in approvals.items() if approved
        ))
        if missing:
            _advance_revision(instance)
            return {
                "ok": True,
                "accepted": True,
                "committed": False,
                "awaiting_uids": missing,
                "proposal": deepcopy(proposal),
            }

    amount = int(proposal.get("amount", 0) or 0)
    if not 0 < amount <= MAX_ECONOMY_AMOUNT:
        return {"ok": False, "code": "INVALID_AMOUNT", "error": "经济金额无效"}
    entries: list[dict[str, Any]] = []
    reward_snapshots: list[dict[str, Any]] = []
    if policy == "all_contributors":
        balances: dict[str, int] = {}
        for contribution in contributors:
            uid = str(contribution.get("uid") or "")
            contribution_amount = int(contribution.get("amount", 0) or 0)
            if uid not in instance.players or contribution_amount <= 0:
                return {"ok": False, "code": "CONTRIBUTOR_INVALID", "error": "平摊参与者无效"}
            sheet = instance.get_character_sheet(uid)
            currency = sheet.get("currency") if isinstance(sheet.get("currency"), dict) else {}
            balances[uid] = int(currency.get("amount", sheet.get("gold", 0)) or 0)
            if balances[uid] < contribution_amount:
                set_proposal_status(proposal, "rejected")
                proposal["resolved_at"] = now
                proposal["resolution_code"] = "INSUFFICIENT_FUNDS"
                _advance_revision(instance)
                outcome = _record_outcome(
                    instance, proposal, status="rejected", actor_uid=actor_uid,
                )
                _settle_effect_group(instance, proposal)
                return {
                    "ok": False,
                    "code": "INSUFFICIENT_FUNDS",
                    "error": f"{uid} 余额不足",
                    "proposal": deepcopy(proposal),
                    "outcome": deepcopy(outcome),
                }
        for contribution in contributors:
            uid = str(contribution.get("uid") or "")
            contribution_amount = int(contribution.get("amount", 0) or 0)
            sheet = instance.get_character_sheet(uid)
            after = apply_currency_delta(sheet, -contribution_amount)
            instance.set_character_sheet(uid, sheet)
            entries.append({
                "account": f"character:{uid}",
                "delta": -contribution_amount,
                "before": balances[uid],
                "after": after,
            })
        entries.append({
            "account": "system:world",
            "delta": amount,
            "before": None,
            "after": None,
        })
    elif kind in {"payment", "purchase", "fee", "transfer"}:
        if payer_uid not in instance.players:
            return {"ok": False, "code": "PAYER_NOT_FOUND", "error": "付款角色不存在"}
        rewards = list(proposal.get("rewards") or [])
        if (rewards or kind == "transfer") and recipient_uid not in instance.players:
            return {"ok": False, "code": "RECIPIENT_NOT_FOUND", "error": "物品接收角色不存在"}
        payer = instance.get_character_sheet(payer_uid)
        currency = payer.get("currency") if isinstance(payer.get("currency"), dict) else {}
        current = int(currency.get("amount", payer.get("gold", 0)) or 0)
        if current < amount:
            set_proposal_status(proposal, "rejected")
            proposal["resolved_at"] = now
            proposal["resolution_code"] = "INSUFFICIENT_FUNDS"
            _advance_revision(instance)
            outcome = _record_outcome(
                instance, proposal, status="rejected", actor_uid=actor_uid,
            )
            _settle_effect_group(instance, proposal)
            return {
                "ok": False,
                "code": "INSUFFICIENT_FUNDS",
                "error": f"余额不足：需要 {amount}，当前 {current}",
                "proposal": deepcopy(proposal),
                "outcome": deepcopy(outcome),
            }
        before = current
        after = apply_currency_delta(payer, -amount)
        instance.set_character_sheet(payer_uid, payer)
        entries.append({"account": f"character:{payer_uid}", "delta": -amount, "before": before, "after": after})
        if kind == "transfer":
            recipient = instance.get_character_sheet(recipient_uid)
            recipient_currency = (
                recipient.get("currency")
                if isinstance(recipient.get("currency"), dict)
                else {}
            )
            recipient_before = int(
                recipient_currency.get("amount", recipient.get("gold", 0)) or 0
            )
            recipient_after = apply_currency_delta(recipient, amount)
            instance.set_character_sheet(recipient_uid, recipient)
            entries.append({
                "account": f"character:{recipient_uid}",
                "delta": amount,
                "before": recipient_before,
                "after": recipient_after,
            })
        else:
            entries.append({
                "account": "system:world",
                "delta": amount,
                "before": None,
                "after": None,
            })
        if rewards and grant_reward:
            recipient = instance.get_character_sheet(recipient_uid)
            reward_snapshots.append({
                "recipient_uid": recipient_uid,
                "before": {
                    key: deepcopy(recipient.get(key, []))
                    for key in ("inventory", "equipment", "key_items")
                },
            })
            for reward in rewards:
                grant_reward(recipient, reward)
            instance.set_character_sheet(recipient_uid, recipient)
            reward_snapshots[-1]["after"] = {
                key: deepcopy(recipient.get(key, []))
                for key in ("inventory", "equipment", "key_items")
            }
    elif kind == "reward":
        if recipient_uid not in instance.players:
            return {"ok": False, "code": "RECIPIENT_NOT_FOUND", "error": "奖励角色不存在"}
        recipient = instance.get_character_sheet(recipient_uid)
        currency = recipient.get("currency") if isinstance(recipient.get("currency"), dict) else {}
        before = int(currency.get("amount", recipient.get("gold", 0)) or 0)
        after = apply_currency_delta(recipient, amount)
        instance.set_character_sheet(recipient_uid, recipient)
        entries.append({"account": f"character:{recipient_uid}", "delta": amount, "before": before, "after": after})
        entries.append({
            "account": "system:world",
            "delta": -amount,
            "before": None,
            "after": None,
        })
    else:
        return {"ok": False, "code": "UNSUPPORTED_KIND", "error": "不支持的经济提案类型"}

    set_proposal_status(proposal, "committed")
    proposal["resolved_at"] = now
    transaction = {
        "id": f"tx_{uuid4().hex}",
        "run_id": instance.run_id,
        "proposal_id": proposal["id"],
        "kind": kind,
        "source": proposal.get("source", ""),
        "source_ref": proposal.get("source_ref", ""),
        "reason": proposal.get("reason", ""),
        "actor_uid": actor_uid,
        "entries": entries,
        "status": "committed",
        "round": int(getattr(instance, "round_number", 0) or 0),
        "committed_at": now,
    }
    if reward_snapshots:
        transaction["reward_snapshots"] = reward_snapshots
    instance.economy.setdefault("transactions", []).append(transaction)
    _advance_revision(instance)
    outcome = _record_outcome(
        instance, proposal, status="committed", actor_uid=actor_uid,
    )
    effect_group = _settle_effect_group(instance, proposal)
    return {
        "ok": True,
        "accepted": True,
        "proposal": deepcopy(proposal),
        "transaction": deepcopy(transaction),
        "outcome": deepcopy(outcome),
        "effect_group": effect_group,
    }


def reverse_round_economy(instance: Any, round_number: int) -> None:
    """Reverse economy effects associated with either round axis.

    ``proposal.round`` is the narrative/origin round, while
    ``transaction.round`` is the actual settlement round.  A late personal
    purchase can therefore need settlement-only rollback (reopen the offer)
    without erasing its origin, while origin rollback must invalidate all
    later settlements of that offer.
    """

    rollback_round = int(round_number)
    proposals = [
        item for item in instance.economy.get("proposals", [])
        if isinstance(item, dict)
    ]
    origin_ids = {
        str(proposal.get("id") or "")
        for proposal in proposals
        if int(proposal.get("round", -1) or -1) == rollback_round
    }
    transactions = [
        item for item in instance.economy.get("transactions", [])
        if isinstance(item, dict)
    ]
    settlement_transactions = [
        transaction for transaction in transactions
        if transaction.get("status") == "committed"
        and (
            int(transaction.get("round", -1) or -1) == rollback_round
            or str(transaction.get("proposal_id") or "") in origin_ids
        )
    ]
    settlement_ids = {
        str(transaction.get("proposal_id") or "")
        for transaction in settlement_transactions
    }
    affected_ids = origin_ids | settlement_ids
    now = datetime.now(timezone.utc).isoformat()

    # Invalidate proposals whose narrative origin was erased, then reverse
    # every committed settlement linked to those proposals (even if it was
    # paid in a later round).
    for proposal in proposals:
        proposal_id = str(proposal.get("id") or "")
        if proposal_id not in origin_ids:
            continue
        if proposal.get("status") == "committed":
            set_proposal_status(proposal, "reversed")
        elif proposal.get("status") == "pending":
            set_proposal_status(proposal, "superseded")
        proposal.pop("resolved_at", None)
        source_ref = str(proposal.get("source_ref") or "")
        if source_ref:
            instance.economy.get("idempotency_records", {}).pop(source_ref, None)

    for transaction in transactions:
        if (
            transaction.get("status") == "committed"
            and str(transaction.get("proposal_id") or "") in affected_ids
        ):
            transaction["status"] = "reversed"
            transaction["reversed_at"] = now

    # A settlement-only rollback keeps an earlier valid offer actionable. Do
    # not resurrect proposals whose origin round is itself being erased.
    for proposal in proposals:
        proposal_id = str(proposal.get("id") or "")
        if proposal_id not in settlement_ids or proposal_id in origin_ids:
            continue
        if proposal.get("status") == "committed" and proposal.get("run_id") == instance.run_id:
            set_proposal_status(proposal, "pending")
            proposal.pop("resolved_at", None)
            proposal.pop("resolution_code", None)
    for group in instance.economy.get("effect_groups", []):
        if int(group.get("round", -1) or -1) == int(round_number):
            group["status"] = "superseded"
            group.pop("effects", None)
    for delivery in instance.economy.get("external_effects_outbox", []):
        if int(delivery.get("round", -1) or -1) != int(round_number):
            continue
        if delivery.get("status") == "pending":
            delivery["status"] = "superseded"
            delivery.pop("payload", None)
        elif delivery.get("status") == "delivered":
            delivery["status"] = "reversal_pending"
            delivery["reversal_requested_at"] = datetime.now(timezone.utc).isoformat()
    instance.economy["outcomes"] = [
        item for item in instance.economy.get("outcomes", [])
        if not (
            isinstance(item, dict)
            and (
                str(item.get("proposal_id") or "") in origin_ids
                or (
                    str(item.get("proposal_id") or "") in settlement_ids
                    and (
                        int(item.get("resolved_round", item.get("round", -1)) or -1) == rollback_round
                        or str(item.get("status") or "") == "committed"
                    )
                )
            )
        )
    ]
    _advance_revision(instance)


def _undo_transaction_rewards(
    transaction: dict[str, Any],
    *,
    get_sheet: Callable[[str], Any],
    set_sheet: Callable[[str, dict[str, Any]], None],
) -> None:
    """Remove only the reward entries one committed settlement introduced."""

    for reward_snapshot in transaction.get("reward_snapshots", []):
        if not isinstance(reward_snapshot, dict):
            continue
        uid = str(reward_snapshot.get("recipient_uid") or "")
        target = get_sheet(uid)
        before = reward_snapshot.get("before")
        after = reward_snapshot.get("after")
        if not isinstance(target, dict) or not isinstance(before, dict) or not isinstance(after, dict):
            continue
        for key in ("inventory", "equipment", "key_items"):
            if key not in before or key not in after:
                continue
            current = target.get(key)
            if not isinstance(current, list):
                continue
            target[key] = _remove_reward_delta(current, before[key], after[key])
            set_sheet(uid, target)


def _undo_transaction_before_images(
    transaction: dict[str, Any],
    *,
    get_sheet: Callable[[str], Any],
    set_sheet: Callable[[str, dict[str, Any]], None],
) -> None:
    """Restore one settlement's absolute before-images.

    Valid for snapshot reconciliation, which rebuilds historical state by
    undoing settlements in strict reverse commit order.  Not valid for
    selective single-transaction reversal, where writing the before-image
    would erase later unrelated activity.
    """

    for entry in transaction.get("entries", []):
        if not isinstance(entry, dict):
            continue
        account = str(entry.get("account") or "")
        if not account.startswith("character:") or entry.get("before") is None:
            continue
        uid = account.removeprefix("character:")
        target = get_sheet(uid)
        if not isinstance(target, dict):
            continue
        before = int(entry.get("before", 0) or 0)
        if isinstance(target.get("currency"), dict):
            target["currency"] = {**target["currency"], "amount": before}
        target["gold"] = before
        set_sheet(uid, target)
    _undo_transaction_rewards(transaction, get_sheet=get_sheet, set_sheet=set_sheet)


def _undo_live_transaction_delta(instance: Any, transaction: dict[str, Any]) -> None:
    """Selectively reverse one committed settlement against the live state.

    Quote-origin rollback removes one earlier transaction while preserving
    later unrelated ones, so every character currency entry applies its
    inverse delta (``-delta``) to the current balance instead of writing the
    absolute before-image.  System/world balancing entries have no character
    sheet and remain audit-only.
    """

    for entry in transaction.get("entries", []):
        if not isinstance(entry, dict):
            continue
        account = str(entry.get("account") or "")
        if not account.startswith("character:"):
            continue
        try:
            delta = int(entry.get("delta", 0) or 0)
        except (TypeError, ValueError):
            continue
        if delta == 0:
            continue
        uid = account.removeprefix("character:")
        sheet = instance.get_character_sheet(uid)
        if not isinstance(sheet, dict) or not sheet:
            continue
        apply_currency_delta(sheet, -delta)
        instance.set_character_sheet(uid, sheet)
    _undo_transaction_rewards(
        transaction,
        get_sheet=instance.get_character_sheet,
        set_sheet=instance.set_character_sheet,
    )


def reconcile_rollback_snapshot(
    instance: Any,
    snapshot: dict[str, Any],
    round_number: int,
) -> dict[str, Any]:
    """Make a round snapshot agree with settlements being reversed.

    Late purchases can settle after the round-start snapshot was captured. In
    that case the snapshot contains the already-paid state, while rollback has
    reopened the proposal and reversed its ledger transaction. Use the
    transaction's authoritative ``before`` values (and the captured reward
    snapshot) to project the pre-settlement character state before restoring it.
    """

    if not isinstance(snapshot, dict):
        return snapshot
    rollback_round = int(round_number)
    # Snapshot reconciliation has a narrower responsibility than economy
    # invalidation: only settlements embedded in this snapshot's own round
    # may rewrite its character state.  A later settlement tied to an erased
    # origin is already reversed by ``reverse_round_economy``; projecting its
    # ``before`` value here would leak later-round state into the old snapshot.
    transactions = [
        item for item in instance.economy.get("transactions", [])
        if isinstance(item, dict)
        and item.get("status") == "reversed"
        and int(item.get("round", -1) or -1) == rollback_round
    ]
    if not transactions:
        return snapshot
    reconciled = deepcopy(snapshot)
    # Undo settlements in reverse commit order.  Each transaction's ``before``
    # value is the state immediately before that settlement; applying an
    # earlier transaction after a later one reconstructs the round's original
    # balance and correctly removes stacked rewards.
    for transaction in reversed(transactions):
        _undo_transaction_before_images(
            transaction,
            get_sheet=lambda uid: (
                reconciled.get(uid) if isinstance(reconciled.get(uid), dict) else None
            ),
            set_sheet=lambda uid, sheet: reconciled.__setitem__(uid, sheet),
        )
    return reconciled


def _item_key(item: Any, *, include_qty: bool = True) -> str:
    value = dict(item) if isinstance(item, dict) else item
    if isinstance(value, dict) and not include_qty:
        value = {key: val for key, val in value.items() if key != "qty"}
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _remove_reward_delta(current: list[Any], before: Any, after: Any) -> list[Any]:
    """Remove only entries added by a purchase reward, preserving later changes."""
    if not isinstance(before, list) or not isinstance(after, list):
        return deepcopy(current)
    result = deepcopy(current)
    before_counts = Counter(_item_key(item, include_qty=False) for item in before)
    after_counts = Counter(_item_key(item, include_qty=False) for item in after)
    additions = {
        key: max(0, after_counts[key] - before_counts[key])
        for key in after_counts
        if after_counts[key] > before_counts[key]
    }
    if not additions:
        # Inventory grants can merge into an existing stack instead of adding a row.
        before_qty = Counter()
        after_qty = Counter()
        for item in before:
            before_qty[_item_key(item, include_qty=False)] += int(item.get("qty", 1) or 0) if isinstance(item, dict) else 1
        for item in after:
            after_qty[_item_key(item, include_qty=False)] += int(item.get("qty", 1) or 0) if isinstance(item, dict) else 1
        additions = {
            key: max(0, after_qty[key] - before_qty[key])
            for key in after_qty
            if after_qty[key] > before_qty[key]
        }
    for index in range(len(result) - 1, -1, -1):
        key = _item_key(result[index], include_qty=False)
        remaining = additions.get(key, 0)
        if remaining <= 0:
            continue
        item = result[index]
        qty = int(item.get("qty", 1) or 0) if isinstance(item, dict) else 1
        if qty <= remaining:
            result.pop(index)
            additions[key] = remaining - qty
        elif isinstance(item, dict):
            item["qty"] = qty - remaining
            additions[key] = 0
    return result
