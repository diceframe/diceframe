"""Narrative economy gates that do not create charges.

Purchases are created only by the explicit GM purchase-order service. Model
output can describe a payment, but it cannot create a payment proposal.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.engine.intent.economy_intent import has_economy_proposal
from src.engine.intent.parser import completed_payment_pattern, currency_labels_for_rule
from src.engine.currency import parse_currency_amount
from src.engine.intent.locale_messages import locale_message

_DEFERRED_DATA_KEYS = {
    "confirmed", "growth_skills", "info_asymmetry", "memory_delta",
    "milestone_grants", "plot_update", "quick_actions", "scene_image_prompt",
    "xp_rewards",
}
def _meaningful(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_meaningful(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_meaningful(item) for item in value)
    return value not in {None, "", False, 0}


def should_warn_unbacked_payment(
    narration: str,
    data: dict[str, Any],
    language: str,
    *,
    currency_labels: Any = None,
) -> bool:
    """Return whether narration describes a payment without an authority proposal."""

    text = str(narration or "").strip()
    if not text or has_economy_proposal(data):
        return False
    return bool(completed_payment_pattern(language, currency_labels).search(text))


def unbacked_payment_notice(language: str) -> str:
    return locale_message(language, "unbacked_payment_notice")


def guard_unbacked_payment_narration(narration: str, data: dict[str, Any], language: str, *, currency_labels: Any = None) -> str:
    text = str(narration or "").strip()
    if not should_warn_unbacked_payment(
        text, data, language, currency_labels=currency_labels,
    ):
        return text
    return f"{text}\n\n{unbacked_payment_notice(language)}"


def unbacked_purchase_notice(language: str) -> str:
    return locale_message(language, "unbacked_purchase_notice")


def unpriced_purchase_notice(language: str) -> str:
    """Explain why a purchase intent did not create a chargeable order."""

    return locale_message(language, "unpriced_purchase_notice")


def defer_narrative_effects(data: dict[str, Any], response: Any, *, defer_state_update: bool = True) -> dict[str, Any]:
    if not has_economy_proposal(data):
        return {}
    state_update = dict(data.get("state_update") or {})
    immediate = {
        key: deepcopy(value)
        for key, value in state_update.items()
        if not defer_state_update or key in {"economy_proposals"}
    }
    deferred_state = {
        key: deepcopy(value)
        for key, value in state_update.items()
        if defer_state_update and key not in immediate and _meaningful(value)
    }
    deferred: dict[str, Any] = {}
    if deferred_state:
        deferred["state_update"] = deferred_state
    data["state_update"] = immediate
    response.state_update = immediate
    completion_candidate = None
    for key in _DEFERRED_DATA_KEYS:
        value = data.get(key)
        if _meaningful(value):
            if key == "plot_update" and isinstance(value, dict):
                deferred_plot = deepcopy(value)
                completion_candidate = deferred_plot.pop("adventure_completion", None)
                if _meaningful(deferred_plot):
                    deferred[key] = deferred_plot
            else:
                deferred[key] = deepcopy(value)
        if key == "memory_delta":
            data[key] = {"add": [], "update": [], "forget": []}
        elif key == "plot_update":
            data[key] = {
                "quests": [], "relations": [], "decisions": [],
                "adventure_completion": completion_candidate,
            }
        elif key == "info_asymmetry":
            data[key] = {}
        elif key in {"confirmed", "growth_skills", "milestone_grants", "quick_actions"}:
            data[key] = []
        elif key == "xp_rewards":
            data[key] = {}
        else:
            data[key] = ""
    response.memory_delta = data.get("memory_delta", {})
    response.info_asymmetry = data.get("info_asymmetry", {})
    response.plot_update = data.get("plot_update", {})
    return deferred


def pending_decision_notice(language: str) -> str:
    return locale_message(language, "pending_decision_notice")


def infer_narrative_reward_candidates(
    instance: Any,
    narration: str,
    rule: Any = None,
) -> list[dict[str, Any]]:
    """Extract explicit narrative reward offers without changing balances.

    This is deliberately narrow: only a single-player round, an explicit
    reward/payment verb, an Arabic amount, and a declared currency unit are
    accepted.  The result is still a GM-approved reward proposal; this helper
    never mutates a character sheet and never treats a bare balance mention as
    a reward.
    """

    import re

    text = str(narration or "")
    if not text and not instance.action_queue:
        return []
    if not instance.players:
        return []
    actor_ids = [
        str(action.get("user_id") or "")
        for action in instance.action_queue
        if str(action.get("user_id") or "") in instance.players
    ]
    if len(set(actor_ids)) != 1:
        return []
    uid = actor_ids[0]
    spec = getattr(rule, "currency_spec", None)
    if spec is None:
        return []
    labels: list[tuple[str, str]] = []
    for unit in spec.units:
        for label in (str(unit.id or "").strip(), str(unit.name or "").strip(), str(unit.symbol or "").strip()):
            if label:
                labels.append((label, unit.id))
    if not labels:
        return []
    label_pattern = "|".join(re.escape(label) for label, _ in sorted(labels, key=lambda item: len(item[0]), reverse=True))
    # Require a reward/settlement verb before the amount. This excludes
    # balance/status narration such as “余额 710 金币”.
    pattern = re.compile(
        rf"(?:奖励|报酬|酬金|给你|支付给你|结算|reward|payment|pay you)"
        rf"[^0-9０-９]{{0,32}}(?P<amount>[0-9]+)\s*(?P<label>{label_pattern})",
        re.IGNORECASE,
    )
    action_text = "\n".join(
        str(action.get("text") or "")
        for action in instance.action_queue
        if str(action.get("user_id") or "") in instance.players
    )
    evidence_text = f"{action_text}\n{text}".strip()
    matches = list(pattern.finditer(evidence_text))
    if not matches:
        return []
    parsed_matches: list[tuple[Any, str, Any]] = []
    for match in matches:
        label = match.group("label")
        unit_id = next((unit for raw, unit in labels if raw.casefold() == label.casefold()), "")
        if not unit_id:
            return []
        try:
            amount = parse_currency_amount(match.group("amount"), unit_id, spec)
        except Exception:
            return []
        parsed_matches.append((match, unit_id, amount))
    # Player action and GM narration may repeat the same explicit reward.
    # Repeated identical evidence is one candidate; conflicting amounts or
    # units remain ambiguous and fail closed.
    identities = {(int(amount), str(unit_id)) for _, unit_id, amount in parsed_matches}
    if len(identities) != 1:
        return []
    match, unit_id, amount = parsed_matches[0]
    if amount <= 0:
        return []
    reason = evidence_text[max(0, match.start() - 80): min(len(evidence_text), match.end() + 80)].strip()[:240]
    return [{
        "kind": "reward",
        "uid": uid,
        "recipient_uid": uid,
        "amount": amount,
        "reason": reason,
        "evidence": evidence_text[match.start():match.end()].strip()[:300],
        "evidence_validated": True,
        "source": "narrative_reward_extractor",
    }]


def merge_narrative_reward_candidates(
    data: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> int:
    """Append inferred rewards once when the model already emitted the same proposal.

    The narrative extractor and the structured model output can both describe
    one reward in the same response.  They must share one proposal identity so
    a later confirmation cannot credit the player twice.
    """

    state_update = data.setdefault("state_update", {})
    if not isinstance(state_update, dict):
        return 0
    proposals = state_update.setdefault("economy_proposals", [])
    if not isinstance(proposals, list):
        proposals = []
        state_update["economy_proposals"] = proposals

    def identity(proposal: Any) -> tuple[str, str, int] | None:
        if not isinstance(proposal, dict) or str(proposal.get("kind") or "") != "reward":
            return None
        uid = str(proposal.get("recipient_uid") or proposal.get("uid") or "").strip()
        try:
            amount = int(proposal.get("amount"))
        except (TypeError, ValueError):
            return None
        if not uid or amount <= 0:
            return None
        return ("reward", uid, amount)

    existing = [proposal for proposal in proposals if identity(proposal) is not None]
    added = 0
    for candidate in candidates:
        key = identity(candidate)
        if key is None:
            continue
        candidate_evidence = str(candidate.get("evidence") or "").strip().casefold()
        duplicate = False
        for proposal in existing:
            if identity(proposal) != key:
                continue
            proposal_evidence = str(proposal.get("evidence") or "").strip().casefold()
            # If both sides carry evidence, only the same evidence is one
            # reward. A missing evidence field is legacy structured output, so
            # retain the old amount-based de-duplication for that compatibility
            # case instead of crediting the same response twice.
            if not candidate_evidence or not proposal_evidence or candidate_evidence == proposal_evidence:
                duplicate = True
                break
        if duplicate:
            continue
        proposals.append(candidate)
        existing.append(candidate)
        added += 1
    return added
