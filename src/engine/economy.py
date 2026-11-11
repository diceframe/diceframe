"""Ruleset-neutral authoritative economy proposals and transactions.

``amount`` / ``delta`` / ledger entries are canonical base-unit integers
(see ``src.engine.currency``).  This engine never learns currency names,
rule ids, or any real-world denomination; display conversion happens only
at the CurrencyCodec / frontend boundary.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from src.engine import progression
from src.engine.character_utils import apply_currency_delta
from src.engine.currency.constants import MAX_ECONOMY_AMOUNT
from src.engine.memory_outbox import (
    MAX_EXTERNAL_EFFECT_DELIVERIES,
    pending_memory_deliveries,
    pending_memory_reversals,
)
from src.engine.modules import economy_state
from src.localization.messages import t

# transfer/fee/all_contributors 已随 schema 8 退役：PAY/TEAM_PAY 标签契约
# 在 schema 6 停用后，它们没有任何存活创建路径（见 migrations.instance）。
ECONOMY_KINDS = {"payment", "purchase", "reward"}
APPROVAL_POLICIES = {"payer", "gm", "system"}
MAX_ECONOMY_OUTCOMES = 50
MAX_EFFECT_GROUPS = 50

# Explicit transition whitelist for the persisted offer state machine.  A
# proposal starts at "pending"; "committed" may only be reversed by a rollback
# (or reopened by a settlement-only rollback).  Everything else is terminal and
# must never be re-resolved.
PAYER_ECONOMY_KINDS = {"payment", "purchase"}
PROPOSAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"committed", "declined", "cancelled", "rejected", "superseded"}),
    "committed": frozenset({"reversed", "pending"}),
}


def era_key(instance: Any) -> int:
    """The settlement era for a new proposal, transaction or effect.

    Today an era is exactly one narrative round (ADR-0003). This is the
    single seam for Track R5's future progression model; persisted keys and
    whole-round rollback semantics remain unchanged.
    """

    return progression.current_era(instance)


def proposal_transition_allowed(current_status: Any, next_status: str) -> bool:
    return next_status in PROPOSAL_TRANSITIONS.get(str(current_status or "pending"), frozenset())


def set_proposal_status(proposal: dict[str, Any], next_status: str) -> None:
    """Move a persisted proposal along the whitelisted state machine."""

    current = str(proposal.get("status") or "pending")
    if not proposal_transition_allowed(current, next_status):
        raise ValueError(f"illegal economy proposal transition: {current} -> {next_status}")
    proposal["status"] = next_status


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
        "round": int(proposal.get("round", era_key(instance)) or 0),
        # Keep proposal origin round separate from the round in which this
        # decision was actually settled.  Rollback uses this field to remove
        # late-payment outcomes without invalidating the original offer.
        "resolved_round": era_key(instance),
        "resolved_at": str(proposal.get("resolved_at") or datetime.now(timezone.utc).isoformat()),
    }
    outcomes = economy_state.state(instance).setdefault("outcomes", [])
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
        "round": era_key(instance),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    groups = economy_state.state(instance).setdefault("effect_groups", [])
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
        economy_state.state(instance)["effect_groups"] = (
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
            group for group in economy_state.state(instance).get("effect_groups", [])
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
        for outcome in economy_state.state(instance).get("outcomes", []):
            if str(outcome.get("effect_group_id") or "") == group_id:
                outcome["effects_status"] = "discarded"
        return None
    proposal_ids = {str(item) for item in group.get("proposal_ids", []) if str(item)}
    states = {
        str(item.get("id") or ""): str(item.get("status") or "")
        for item in economy_state.state(instance).get("proposals", [])
        if isinstance(item, dict) and str(item.get("id") or "") in proposal_ids
    }
    if proposal_ids and all(states.get(proposal_id) == "committed" for proposal_id in proposal_ids):
        group["status"] = "ready"
        return deepcopy(group)
    return None


def complete_effect_group(instance: Any, group_id: str) -> bool:
    group = next(
        (
            item for item in economy_state.state(instance).get("effect_groups", [])
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
    for outcome in economy_state.state(instance).get("outcomes", []):
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
    for proposal in economy_state.state(instance).get("proposals", []):
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
        refunded = _refund_installment_deposit(
            instance, proposal, actor_uid="system", now=now,
        )
        set_proposal_status(proposal, "cancelled")
        proposal["resolved_at"] = now
        proposal["resolution_code"] = resolution_code
        if refunded is not None:
            proposal["refund_transaction_id"] = refunded["id"]
        proposal_id = str(proposal.get("id") or "")
        affected_ids.add(proposal_id)
        _record_outcome(
            instance,
            proposal,
            status="cancelled",
            actor_uid="system",
        )
        _settle_effect_group(instance, proposal)
    return affected_ids


def pending_proposals(instance: Any) -> list[dict[str, Any]]:
    economy = economy_state.state(instance)
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


REWARD_POLICY_MODES = frozenset({"auto_small_cash", "gm_confirm"})


def normalize_reward_policy(policy: Any) -> dict[str, Any]:
    """Normalize a per-game reward policy payload; invalid input means unset.

    ``{"mode": "auto_small_cash" | "gm_confirm", "auto_reward_cap": int|None}``.
    An unrecognized payload returns ``{}`` (fall through to rule/global
    defaults) instead of guessing, so a bad settings request can never
    silently widen auto-settlement.
    """

    if not isinstance(policy, dict):
        return {}
    mode = str(policy.get("mode") or "")
    if mode not in REWARD_POLICY_MODES:
        return {}
    normalized: dict[str, Any] = {"mode": mode}
    cap = policy.get("auto_reward_cap")
    if cap is not None:
        try:
            cap_value = int(cap)
        except (TypeError, ValueError):
            return {}
        if not 1 <= cap_value <= MAX_ECONOMY_AMOUNT:
            return {}
        normalized["auto_reward_cap"] = cap_value
    return normalized


def resolve_auto_reward_policy(
    *,
    game_policy: Any = None,
    rule_template: Any = None,
    global_enabled: bool = True,
    global_cap: int = 500,
) -> tuple[bool, int]:
    """Resolve the effective (auto_enabled, gold_cap) for one game.

    Precedence: explicit per-game override -> rule template
    ``economy_defaults`` -> server global config.  The global value is the
    server fallback for games that never configured a policy; rule templates
    give each ruleset (D&D gold vs COC dollars) its own default scale.
    """

    defaults = (
        rule_template.get("economy_defaults")
        if isinstance(rule_template, dict) and isinstance(
            rule_template.get("economy_defaults"), dict,
        )
        else {}
    )
    game = normalize_reward_policy(game_policy)
    if game:
        mode = str(game["mode"])
    elif str(defaults.get("reward_policy") or "") in REWARD_POLICY_MODES:
        mode = str(defaults["reward_policy"])
    else:
        mode = "auto_small_cash" if global_enabled else "gm_confirm"
    enabled = mode == "auto_small_cash"
    if game.get("auto_reward_cap") is not None:
        cap = int(game["auto_reward_cap"])
    else:
        template_cap = defaults.get("auto_reward_cap")
        if isinstance(template_cap, int) and 1 <= template_cap <= MAX_ECONOMY_AMOUNT:
            cap = template_cap
        else:
            cap = max(1, int(global_cap or 1))
    return enabled, max(1, min(cap, MAX_ECONOMY_AMOUNT))


def is_auto_settleable_reward(
    instance: Any,
    proposal: dict[str, Any],
    *,
    gold_cap: int = 50,
) -> bool:
    """Whether a pending narrative reward may settle without a GM click.

    Deliberately narrow: plain single-recipient pure-currency rewards for a
    current player within the configured cap.  Proposals carrying item
    rewards, team splits, cross-run entries and anything outside the cap stay
    blocking so the GM keeps final say over unusual or high-impact grants.
    """

    if not isinstance(proposal, dict):
        return False
    if proposal.get("status") != "pending" or str(proposal.get("run_id") or "") != str(instance.run_id):
        return False
    if str(proposal.get("kind") or "") != "reward":
        return False
    # Model-originated rewards must carry the exact structured/prose evidence
    # that produced the proposal before the small-cash convenience path may
    # settle them automatically. Manually queued rewards keep the historical
    # behavior because their caller is already an authority boundary.
    if str(proposal.get("source") or "") in {
        "narrative_reward_tag", "legacy_gold_tag", "narrative_reward_extractor",
        "narrative_structured",
    } and (
        proposal.get("evidence_validated") is not True
        or not str(proposal.get("evidence") or "").strip()
    ):
        return False
    if proposal.get("contributors"):
        return False
    # 物品奖励不属于“小额现金”：即使结算逻辑今天只入账金币，也不能让
    # 带物品的奖励在 GM 看不到的情况下自动通过。
    if proposal.get("rewards"):
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

    economy = economy_state.state(instance)
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
        for item in economy_state.state(instance).get("proposals", [])
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
    economy = economy_state.state(instance)
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
    evidence: str = "",
    evidence_validated: bool = False,
    visibility: str = "private",
) -> dict[str, Any]:
    """Queue one idempotent proposal; no balance is changed here."""

    amount = int(amount)
    kind = str(kind)
    if kind not in ECONOMY_KINDS:
        raise ValueError("unsupported economy proposal kind")
    named_rewards = [
        reward for reward in (rewards or [])
        if isinstance(reward, dict) and str(reward.get("name") or "").strip()
    ]
    if kind == "reward":
        # FIX-08 §10 步骤 12：``reward`` 允许 ``amount = 0`` —— "只给物品、不给
        # 货币"的奖励本来就没有金额。但零金额提案必须真的带物品，否则它什么都不做
        # （宁可拒绝，也不留一条永远无效果的空提案）。
        if not 0 <= amount <= MAX_ECONOMY_AMOUNT:
            raise ValueError("economy amount is out of range")
        if amount == 0 and not named_rewards:
            raise ValueError("a zero-amount reward proposal requires item rewards")
    elif not 0 < amount <= MAX_ECONOMY_AMOUNT:
        raise ValueError("economy amount is out of range")
    if approval_policy not in APPROVAL_POLICIES:
        raise ValueError("unsupported economy approval policy")
    if kind in PAYER_ECONOMY_KINDS and approval_policy != "payer":
        raise ValueError("chargeable economy proposals require payer approval")
    payer_uid = str(payer_uid)
    if payer_uid and kind in PAYER_ECONOMY_KINDS and payer_uid not in instance.players:
        # Fail closed at the proposal layer: a payer outside the current game
        # can never be settled, so the offer must not become pending.
        raise ValueError("economy payer is not part of the current game")
    existing = _existing_by_source(instance, source_ref)
    if existing is not None:
        return existing
    economy = economy_state.state(instance)
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
        "evidence": str(evidence or "")[:300],
        "evidence_validated": bool(evidence_validated),
        "source": str(source),
        "source_ref": str(source_ref),
        "approval_policy": str(approval_policy),
        "rewards": deepcopy(list(rewards or [])),
        "approvals": {},
        "visibility": visibility if visibility in {"private", "party"} else "private",
        "status": "pending",
        "round": era_key(instance),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    economy.setdefault("proposals", []).append(proposal)
    economy["next_sequence"] = sequence + 1
    if source_ref:
        economy.setdefault("idempotency_records", {})[source_ref] = proposal["id"]
    return proposal


PURCHASE_OFFER_SOURCES = frozenset({"gm_manual", "table_offer"})


def has_pending_identical_purchase(instance: Any, payer_uid: str, target: str) -> bool:
    """True when a pending purchase proposal already covers this payer+item.

    AI offer extraction re-reads shop narration every round, so a deal that is
    still awaiting confirmation can be quoted again and stack a second dialog
    on the first.  Queueing is skippable in that case; a settled or declined
    purchase is intentionally NOT covered here — a genuine re-buy stays legal
    (ADR 0002: a hallucinated extra costs one decline click, never a charge).
    """

    key = str(target or "").strip().casefold()
    payer = str(payer_uid or "")
    if not key or not payer:
        return False
    for proposal in economy_state.state(instance).get("proposals", []):
        if not isinstance(proposal, dict) or proposal.get("status") != "pending":
            continue
        if str(proposal.get("kind") or "") != "purchase":
            continue
        if str(proposal.get("payer_uid") or "") != payer:
            continue
        for reward in proposal.get("rewards") or []:
            name = (
                str(reward.get("name") or "").strip().casefold()
                if isinstance(reward, dict) else str(reward).strip().casefold()
            )
            if name and (name in key or key in name):
                return True
    return False


def queue_purchase_offer(
    instance: Any,
    *,
    payer_uid: str,
    amount: int,
    items: list[str],
    reason: str = "",
    source: str = "table_offer",
    source_ref: str = "",
    initial_due: int | None = None,
    remaining_due: int | None = None,
    quote_evidence: str = "",
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
    payment_terms: dict[str, Any] | None = None
    if initial_due is not None or remaining_due is not None:
        first = int(initial_due if initial_due is not None else amount)
        rest = int(remaining_due if remaining_due is not None else int(amount) - first)
        if first <= 0 or rest < 0 or first + rest != int(amount):
            raise ValueError("purchase installment totals are invalid")
        payment_terms = {
            "total_amount": int(amount),
            "initial_due": first,
            "remaining_due": rest,
            "stage": "deposit_pending" if rest else "full",
        }
    proposal = queue_proposal(
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
    if payment_terms is not None:
        proposal["payment_terms"] = payment_terms
    if str(quote_evidence or "").strip():
        proposal["quote_evidence"] = str(quote_evidence).strip()[:300]
    return proposal


def purchase_grant_protection_snapshot(instance: Any) -> dict[str, set[str]]:
    """Capture pending chargeable item grants before narrative generation.

    The narrative may finish after a payer declines an offer or an accepted
    offer is rejected for insufficient funds.  The live proposal status is no
    longer ``pending`` in either case, but the response was generated while
    the charge still owned its item.  Returning a detached payer-to-item map
    lets the caller preserve that ownership through the response commit.
    """

    protected: dict[str, set[str]] = {}
    for proposal in economy_state.state(instance).get("proposals", []):
        if not isinstance(proposal, dict) or proposal.get("status") != "pending":
            continue
        if str(proposal.get("kind") or "") not in PAYER_ECONOMY_KINDS:
            continue
        uid = str(proposal.get("payer_uid") or proposal.get("uid") or "")
        if not uid:
            continue
        for reward in proposal.get("rewards") or []:
            name = (
                str(reward.get("name") or "").strip().casefold()
                if isinstance(reward, dict) else str(reward).strip().casefold()
            )
            if name:
                protected.setdefault(uid, set()).add(name)
    return protected


def filter_unconfirmed_purchase_grants(
    instance: Any,
    data: dict[str, Any],
    *,
    unpriced_purchase_intents: list[dict[str, Any]] | None = None,
    protected_purchase_items: dict[str, set[str]] | None = None,
) -> int:
    """Strip model grants for items that are part of an unconfirmed charge.

    A pending chargeable proposal owns its reward items until the payer
    confirms.  Model-emitted grants whose names match a pending purchase
    proposal's rewards are removed so the item cannot bypass the payment.
    ``protected_purchase_items`` preserves the same ownership for proposals
    that were pending when narrative generation started but were declined or
    rejected before the response was applied.
    Same-round unpriced purchase intents (in-memory round state only, never
    persisted) are matched the same way: a purchase whose price nobody has
    stated yet must not be delivered through a narrative LOOT tag either.
    Narrative item events (item_gains / equip equipment_ops) are filtered
    entry by entry with the same authority; unequip ops grant nothing and
    pass through.  Other loot and rewards continue through the normal
    narrative pipeline.

    Three authority layers: pending chargeable proposals always block (a
    FREE_GRANT can never bypass a real offer); a caller may also pass the
    proposal items captured when narrative generation began, so a
    resolution-only decline/rejection cannot reopen the grant path;
    unpriced purchase intents
    block unless the same round carries an explicit FREE_GRANT marker
    (GM narrated the item as genuinely free/gifted/rewarded) for the same
    player and item.  The marker channel ``state_update.free_grants`` is
    ephemeral: consumed here, never persisted.
    """

    state_update = data.get("state_update")
    if not isinstance(state_update, dict):
        return 0
    # FREE_GRANT 是本轮 ephemeral 授权标记（GM 叙事明确免费/赠送/奖励时输出）：
    # 无论是否发生拦截都立即消费，绝不持久化。
    free_grant_items: dict[str, set[str]] = {}
    for marker in state_update.pop("free_grants", None) or []:
        if not isinstance(marker, dict):
            continue
        uid = str(marker.get("player") or "").strip()
        name = str(marker.get("item") or "").strip().casefold()
        if uid and name:
            free_grant_items.setdefault(uid, set()).add(name)

    # 第一层：待确认扣款提案（payment/purchase）。FREE_GRANT 无权绕过——
    # 否则物品先免费发放、付款人之后仍可能确认扣款，形成双重状态错误。
    pending_purchase_items = purchase_grant_protection_snapshot(instance)
    # A response may have started while these proposals were pending, then
    # observed a decline/rejection before its state update is applied.  Keep
    # the detached start-of-generation ownership as an additional block.
    for uid, names in (protected_purchase_items or {}).items():
        normalized_uid = str(uid or "")
        if not normalized_uid:
            continue
        for name in names or ():
            normalized_name = str(name or "").strip().casefold()
            if normalized_name:
                pending_purchase_items.setdefault(normalized_uid, set()).add(normalized_name)
    # 第二层：无价购买意图（价格未知/无法 canonicalize）。默认拦截；
    # 本轮 GM 明确免费（FREE_GRANT）才放行。unknown price != free。
    unpriced_purchase_items: dict[str, set[str]] = {}
    for intent in unpriced_purchase_intents or []:
        if not isinstance(intent, dict):
            continue
        uid = str(intent.get("payer_uid") or "")
        name = str(intent.get("target") or "").strip().casefold()
        if uid and name:
            unpriced_purchase_items.setdefault(uid, set()).add(name)

    def _matches(names: set[str], key: str) -> bool:
        return any(name and (name in key or key in name) for name in names)

    def blocked(uid: str, item: str) -> bool:
        key = str(item or "").strip().casefold()
        if not uid or not key:
            return False
        if _matches(pending_purchase_items.get(uid, set()), key):
            return True
        if _matches(unpriced_purchase_items.get(uid, set()), key):
            return not _matches(free_grant_items.get(uid, set()), key)
        return False

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
            # 新列表事件逐条执行同样的 authority：未确认购买的商品既不能通过
            # 叙事获得（item_gains），也不能借装备操作凭空上身（equip op）。
            # 卸下（unequip）不授予任何物品，不过滤。
            gains = update.get("item_gains")
            if isinstance(gains, list):
                kept_gains = [
                    gain for gain in gains
                    if not (
                        isinstance(gain, dict)
                        and blocked(str(uid), str(gain.get("name") or ""))
                    )
                ]
                removed += len(gains) - len(kept_gains)
                if kept_gains:
                    update["item_gains"] = kept_gains
                else:
                    update.pop("item_gains", None)
            ops = update.get("equipment_ops")
            if isinstance(ops, list):
                kept_ops = [
                    op for op in ops
                    if not (
                        isinstance(op, dict)
                        and op.get("op") == "equip"
                        and blocked(str(uid), str(op.get("name") or ""))
                    )
                ]
                removed += len(ops) - len(kept_ops)
                if kept_ops:
                    update["equipment_ops"] = kept_ops
                else:
                    update.pop("equipment_ops", None)
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


def _grant_rewards_with_snapshot(
    instance: Any,
    recipient_uid: str,
    rewards: list[dict[str, Any]],
    grant_reward: Callable[[dict[str, Any], dict[str, Any]], None],
    reward_snapshots: list[dict[str, Any]],
) -> None:
    """Grant one settlement's item rewards and record their absolute before-image.

    FIX-08：付款/购买与纯物品奖励（``amount = 0``）共用这一条发放路径，所以
    "整轮回滚能精确还原物品"对两者同样成立（ADR 0003：靠绝对 before-image，
    不做选择性 diff）。
    """

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
        (item for item in economy_state.state(instance).get("proposals", []) if item.get("id") == proposal_id),
        None,
    )
    if proposal is None:
        return {"ok": False, "code": "PROPOSAL_NOT_FOUND", "error": t("engine.economy.proposal_missing")}
    if proposal.get("run_id") != instance.run_id:
        return {"ok": False, "code": "STALE_RUN", "error": t("engine.economy.proposal_from_previous_run")}
    if proposal.get("status") != "pending":
        return {"ok": False, "code": "ALREADY_RESOLVED", "error": t("engine.economy.proposal_already_handled")}

    kind = str(proposal.get("kind") or "payment")
    payer_uid = str(proposal.get("payer_uid") or proposal.get("uid") or "")
    recipient_uid = str(proposal.get("recipient_uid") or payer_uid)
    policy = str(proposal.get("approval_policy") or "payer")
    is_gm = actor_uid == instance.gm_uid
    if policy == "gm":
        allowed = is_gm
    elif policy == "system":
        allowed = actor_uid == "system"
    elif policy == "payer_or_gm_legacy":
        allowed = actor_uid in {payer_uid, instance.gm_uid}
    else:
        allowed = actor_uid == payer_uid or (not accepted and is_gm)
    if not allowed:
        return {"ok": False, "code": "FORBIDDEN", "error": t("engine.economy.not_allowed")}

    now = datetime.now(timezone.utc).isoformat()
    terms = proposal.get("payment_terms") if isinstance(proposal.get("payment_terms"), dict) else None
    settlement_stage = str((terms or {}).get("stage") or "full")
    if not accepted and settlement_stage == "deposit_paid":
        return {
            "ok": False,
            "code": "INSTALLMENT_ALREADY_STARTED",
            "error": t("engine.economy.deposit_settled_cannot_reject"),
            "proposal": deepcopy(proposal),
        }
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

    amount = int(proposal.get("amount", 0) or 0)
    charge_amount = amount
    partial_settlement = False
    if terms and kind in {"payment", "purchase"}:
        # Installment terms are persisted data.  Do not trust a partially
        # written/legacy payload just because its current stage happens to be
        # recognizable: an inconsistent total could charge one amount while
        # recording another and would make the tail impossible to reconcile.
        try:
            terms_total = int(terms.get("total_amount"))
            terms_initial = int(terms.get("initial_due"))
            terms_remaining = int(terms.get("remaining_due"))
        except (TypeError, ValueError):
            return {"ok": False, "code": "INVALID_PAYMENT_TERMS", "error": t("engine.economy.payment_terms_amount_invalid")}
        if (
            terms_total != amount
            or terms_initial <= 0
            or terms_remaining < 0
            or terms_initial + terms_remaining != amount
        ):
            return {"ok": False, "code": "INVALID_PAYMENT_TERMS", "error": t("engine.economy.payment_terms_total_mismatch")}
        if settlement_stage == "deposit_pending":
            if terms_remaining <= 0:
                return {"ok": False, "code": "INVALID_PAYMENT_TERMS", "error": t("engine.economy.deposit_terms_missing_balance")}
            charge_amount = terms_initial
            partial_settlement = True
        elif settlement_stage == "deposit_paid":
            if terms_remaining <= 0:
                return {"ok": False, "code": "INVALID_PAYMENT_TERMS", "error": t("engine.economy.remainder_amount_invalid")}
            charge_amount = terms_remaining
        elif settlement_stage == "full":
            if terms_initial != amount or terms_remaining != 0:
                return {"ok": False, "code": "INVALID_PAYMENT_TERMS", "error": t("engine.economy.full_payment_terms_mismatch")}
        else:
            return {"ok": False, "code": "INVALID_PAYMENT_TERMS", "error": t("engine.economy.payment_terms_stage_invalid")}
    # FIX-08：零金额只在 ``reward``（纯物品奖励）下合法；付款/购买永远需要正金额。
    minimum = 0 if kind == "reward" else 1
    if charge_amount < minimum or charge_amount > MAX_ECONOMY_AMOUNT or amount > MAX_ECONOMY_AMOUNT:
        return {"ok": False, "code": "INVALID_AMOUNT", "error": t("engine.economy.amount_invalid")}
    entries: list[dict[str, Any]] = []
    reward_snapshots: list[dict[str, Any]] = []
    if kind in {"payment", "purchase"}:
        if payer_uid not in instance.players:
            return {"ok": False, "code": "PAYER_NOT_FOUND", "error": t("engine.economy.payer_missing")}
        rewards = list(proposal.get("rewards") or [])
        if rewards and recipient_uid not in instance.players:
            return {"ok": False, "code": "RECIPIENT_NOT_FOUND", "error": t("engine.economy.item_recipient_missing")}
        payer = instance.get_character_sheet(payer_uid)
        currency = payer.get("currency") if isinstance(payer.get("currency"), dict) else {}
        current = int(currency.get("amount", payer.get("gold", 0)) or 0)
        if current < charge_amount:
            # A paid deposit is already a committed transaction.  A failed
            # tail payment must remain retryable so the player can add funds;
            # marking the whole proposal rejected would discard its effect
            # group while leaving the deposit charged with no contract state.
            if terms and settlement_stage == "deposit_paid":
                proposal["resolution_code"] = "INSUFFICIENT_FUNDS_TAIL"
                return {
                    "ok": False,
                    "code": "INSUFFICIENT_FUNDS",
                    "error": t("engine.economy.insufficient_balance_for_remainder", charge_amount=charge_amount, current=current),
                    "proposal": deepcopy(proposal),
                }
            set_proposal_status(proposal, "rejected")
            proposal["resolved_at"] = now
            proposal["resolution_code"] = "INSUFFICIENT_FUNDS"
            outcome = _record_outcome(
                instance, proposal, status="rejected", actor_uid=actor_uid,
            )
            _settle_effect_group(instance, proposal)
            return {
                "ok": False,
                "code": "INSUFFICIENT_FUNDS",
                "error": t("engine.economy.insufficient_balance", charge_amount=charge_amount, current=current),
                "proposal": deepcopy(proposal),
                "outcome": deepcopy(outcome),
            }
        before = current
        after = apply_currency_delta(payer, -charge_amount)
        instance.set_character_sheet(payer_uid, payer)
        entries.append({"account": f"character:{payer_uid}", "delta": -charge_amount, "before": before, "after": after})
        entries.append({
            "account": "system:world",
            "delta": charge_amount,
            "before": None,
            "after": None,
        })
        if rewards and grant_reward and not partial_settlement:
            _grant_rewards_with_snapshot(
                instance, recipient_uid, rewards, grant_reward, reward_snapshots,
            )
    elif kind == "reward":
        if recipient_uid not in instance.players:
            return {"ok": False, "code": "RECIPIENT_NOT_FOUND", "error": t("engine.economy.reward_character_missing")}
        rewards = list(proposal.get("rewards") or [])
        if rewards and grant_reward is None:
            # 有物品奖励却没有发放通道时绝不能提交：那会留下"提案已结算、物品没到
            # 手"的半截状态。宁可 fail closed 让调用方补上通道。
            return {
                "ok": False, "code": "REWARD_UNSUPPORTED",
                "error": t("engine.economy.item_reward_no_channel"),
            }
        recipient = instance.get_character_sheet(recipient_uid)
        if amount:
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
        if rewards:
            # FIX-08 §10 步骤 12：纯物品奖励（amount = 0）也走同一条结算路径——
            # 物品仍然只在这里写，并记录 before-image，整轮回滚照旧能精确还原。
            _grant_rewards_with_snapshot(
                instance, recipient_uid, rewards, grant_reward, reward_snapshots,
            )
    else:
        return {"ok": False, "code": "UNSUPPORTED_KIND", "error": t("engine.economy.proposal_type_unsupported")}

    if partial_settlement:
        terms["stage"] = "deposit_paid"
        proposal["settlement_stage"] = "deposit_paid"
        proposal["resolution_code"] = "DEPOSIT_COMMITTED"
    else:
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
        "settlement_stage": "deposit" if partial_settlement else "final",
        "round": era_key(instance),
        "committed_at": now,
    }
    if reward_snapshots:
        transaction["reward_snapshots"] = reward_snapshots
    economy_state.state(instance).setdefault("transactions", []).append(transaction)
    outcome = _record_outcome(
        instance, proposal, status="committed", actor_uid=actor_uid,
    )
    outcome["amount"] = charge_amount
    outcome["total_amount"] = amount
    effect_group = None if partial_settlement else _settle_effect_group(instance, proposal)
    return {
        "ok": True,
        "accepted": True,
        "committed": not partial_settlement,
        "proposal": deepcopy(proposal),
        "transaction": deepcopy(transaction),
        "outcome": deepcopy(outcome),
        "effect_group": effect_group,
    }


def _committed_deposits(instance: Any, proposal_id: str) -> list[dict[str, Any]]:
    return [
        transaction
        for transaction in economy_state.state(instance).get("transactions", [])
        if isinstance(transaction, dict)
        and str(transaction.get("proposal_id") or "") == proposal_id
        and transaction.get("status") == "committed"
        and transaction.get("settlement_stage") == "deposit"
    ]


def _resync_installment_stage(instance: Any, proposal: dict[str, Any]) -> None:
    """Re-derive an installment's stage from its surviving deposit."""

    terms = proposal.get("payment_terms")
    if not isinstance(terms, dict) or str(terms.get("stage") or "full") == "full":
        return
    if _committed_deposits(instance, str(proposal.get("id") or "")):
        terms["stage"] = "deposit_paid"
        proposal["settlement_stage"] = "deposit_paid"
        return
    terms["stage"] = "deposit_pending"
    proposal.pop("settlement_stage", None)
    if proposal.get("resolution_code") in {"DEPOSIT_COMMITTED", "INSUFFICIENT_FUNDS_TAIL"}:
        proposal.pop("resolution_code", None)


def _refund_installment_deposit(
    instance: Any,
    proposal: dict[str, Any],
    *,
    actor_uid: str,
    now: str,
) -> dict[str, Any] | None:
    """Return a paid deposit when its contract is cancelled before the tail.

    The refund is a compensating ledger transaction, so a later whole-round
    rollback reverses it like any other settlement.
    """

    terms = proposal.get("payment_terms")
    if not isinstance(terms, dict) or terms.get("stage") != "deposit_paid":
        return None
    proposal_id = str(proposal.get("id") or "")
    payer_uid = str(proposal.get("payer_uid") or proposal.get("uid") or "")
    account = f"character:{payer_uid}"
    paid = sum(
        -int(entry.get("delta", 0) or 0)
        for transaction in _committed_deposits(instance, proposal_id)
        for entry in transaction.get("entries", [])
        if isinstance(entry, dict)
        and str(entry.get("account") or "") == account
        and int(entry.get("delta", 0) or 0) < 0
    )
    if paid <= 0:
        return None
    entries: list[dict[str, Any]] = []
    if payer_uid in instance.players:
        payer = instance.get_character_sheet(payer_uid)
        currency = payer.get("currency") if isinstance(payer.get("currency"), dict) else {}
        before = int(currency.get("amount", payer.get("gold", 0)) or 0)
        after = apply_currency_delta(payer, paid)
        instance.set_character_sheet(payer_uid, payer)
        entries.append({"account": account, "delta": paid, "before": before, "after": after})
    entries.append({"account": "system:world", "delta": -paid, "before": None, "after": None})
    transaction = {
        "id": f"tx_{uuid4().hex}",
        "run_id": instance.run_id,
        "proposal_id": proposal_id,
        "kind": "refund",
        "source": proposal.get("source", ""),
        "source_ref": proposal.get("source_ref", ""),
        "reason": proposal.get("reason", ""),
        "actor_uid": actor_uid,
        "entries": entries,
        "status": "committed",
        "settlement_stage": "refund",
        "round": era_key(instance),
        "committed_at": now,
    }
    economy_state.state(instance).setdefault("transactions", []).append(transaction)
    return transaction


def reverse_round_economy(instance: Any, round_number: int) -> None:
    """Withdraw every economic effect at or after ``round_number``.

    Whole-round rollback (ADR 0003): the erased era covers the rolled-back
    round and every settlement made after it.  Proposals whose narrative
    origin lies inside the era are invalidated; committed settlements inside
    the era are reversed by their exact ledger deltas; an offer whose origin
    survives but whose settlement does not is reopened as pending so the
    payer can decide again.  Item effects are never recovered by selective
    diffs -- the callers restore whole player snapshots, and
    ``reconcile_rollback_snapshot`` projects absolute before-images.
    """

    rollback_round = int(round_number)
    proposals = [
        item for item in economy_state.state(instance).get("proposals", [])
        if isinstance(item, dict)
    ]
    origin_ids = {
        str(proposal.get("id") or "")
        for proposal in proposals
        if int(proposal.get("round", -1) or -1) >= rollback_round
    }
    transactions = [
        item for item in economy_state.state(instance).get("transactions", [])
        if isinstance(item, dict)
    ]
    settlement_transactions = [
        transaction for transaction in transactions
        if transaction.get("status") == "committed"
        and (
            int(transaction.get("round", -1) or -1) >= rollback_round
            or str(transaction.get("proposal_id") or "") in origin_ids
        )
    ]
    settlement_ids = {
        str(transaction.get("proposal_id") or "")
        for transaction in settlement_transactions
    }
    now = datetime.now(timezone.utc).isoformat()

    # Invalidate proposals whose narrative origin was erased, then reverse
    # every committed settlement inside the era (even if its offer
    # originated before it).
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
            economy_state.state(instance).get("idempotency_records", {}).pop(source_ref, None)

    # Reverse exactly the era's settlements.  An installment can own several
    # transactions; a deposit committed before the era survives a rollback
    # of its tail, just as the restored snapshot still shows it paid.
    for transaction in settlement_transactions:
        transaction["status"] = "reversed"
        transaction["reversed_at"] = now

    # A settlement-era rollback keeps an earlier valid offer actionable. Do
    # not resurrect proposals whose origin round is itself being erased.
    for proposal in proposals:
        proposal_id = str(proposal.get("id") or "")
        if proposal_id not in settlement_ids or proposal_id in origin_ids:
            continue
        if proposal.get("status") == "committed" and proposal.get("run_id") == instance.run_id:
            set_proposal_status(proposal, "pending")
            proposal.pop("resolved_at", None)
            proposal.pop("resolution_code", None)
        # The installment stage is derived from the ledger: a reopened or
        # still-pending contract whose deposit was just reversed must charge
        # the deposit again instead of delivering after only the tail.
        _resync_installment_stage(instance, proposal)
    for group in economy_state.state(instance).get("effect_groups", []):
        if int(group.get("round", -1) or -1) >= rollback_round:
            group["status"] = "superseded"
            group.pop("effects", None)
    for delivery in economy_state.state(instance).get("external_effects_outbox", []):
        if int(delivery.get("round", -1) or -1) < rollback_round:
            continue
        if delivery.get("status") == "pending":
            delivery["status"] = "superseded"
            delivery.pop("payload", None)
        elif delivery.get("status") == "delivered":
            delivery["status"] = "reversal_pending"
            delivery["reversal_requested_at"] = datetime.now(timezone.utc).isoformat()
    economy_state.state(instance)["outcomes"] = [
        item for item in economy_state.state(instance).get("outcomes", [])
        if not (
            isinstance(item, dict)
            and (
                str(item.get("proposal_id") or "") in origin_ids
                or int(item.get("resolved_round", item.get("round", -1)) or -1) >= rollback_round
                or (
                    str(item.get("proposal_id") or "") in settlement_ids
                    and str(item.get("status") or "") == "committed"
                )
            )
        )
    ]


def _undo_transaction_before_images(
    transaction: dict[str, Any],
    *,
    get_sheet: Callable[[str], Any],
    set_sheet: Callable[[str, dict[str, Any]], None],
) -> None:
    """Restore one settlement's absolute pre-settlement state.

    Whole-round rollback replays the era's settlements in strict reverse
    commit order, so writing each transaction's absolute ``before`` balances
    and inventory rows reconstructs the era's original state exactly.  No
    selective later activity needs to survive inside an erased era, which is
    why no inventory diffing is needed here (ADR 0003).
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
    for reward_snapshot in transaction.get("reward_snapshots", []):
        if not isinstance(reward_snapshot, dict):
            continue
        uid = str(reward_snapshot.get("recipient_uid") or "")
        before = reward_snapshot.get("before")
        target = get_sheet(uid)
        if not isinstance(target, dict) or not isinstance(before, dict):
            continue
        for key in ("inventory", "equipment", "key_items"):
            if key in before:
                target[key] = deepcopy(before[key])
        set_sheet(uid, target)


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
    snapshot) to project the pre-settlement character state before restoring
    it.
    """

    if not isinstance(snapshot, dict):
        return snapshot
    rollback_round = int(round_number)
    # Snapshot reconciliation has a narrower responsibility than economy
    # invalidation: only settlements embedded in this snapshot's own round
    # may rewrite its character state.  Later settlements tied to an erased
    # origin are already reversed by ``reverse_round_economy``; their effects
    # never entered this snapshot, so projecting them here would leak
    # later-round state into the old snapshot.
    transactions = [
        item for item in economy_state.state(instance).get("transactions", [])
        if isinstance(item, dict)
        and item.get("status") == "reversed"
        and int(item.get("round", -1) or -1) == rollback_round
    ]
    if not transactions:
        return snapshot
    reconciled = deepcopy(snapshot)
    # Undo settlements in reverse commit order.  Each transaction's absolute
    # before-image (balances and inventory rows) is the state immediately
    # before that settlement; applying an earlier transaction after a later
    # one reconstructs the round's original state exactly.
    for transaction in reversed(transactions):
        _undo_transaction_before_images(
            transaction,
            get_sheet=lambda uid: (
                reconciled.get(uid) if isinstance(reconciled.get(uid), dict) else None
            ),
            set_sheet=lambda uid, sheet: reconciled.__setitem__(uid, sheet),
        )
    return reconciled
