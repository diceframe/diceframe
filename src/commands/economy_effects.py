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

    This is a fallback for a GM response that narrated a payment but emitted
    no ``REWARD`` tag, so it is deliberately narrow:

    - only the GM's narration is evidence; the player's own action text is
      never read, so a player cannot write themselves a reward;
    - one acting player, and the paying sentence must address that player
      (second person or their character name) and name no other player;
    - an explicit reward/payment cue directly before an Arabic amount and a
      declared currency unit;
    - price quotes, per-item/each splits, conditional promises and the player
      paying someone else are rejected.

    The result is still a pending reward proposal; this helper never mutates a
    character sheet and never treats a bare balance mention as a reward.
    """

    import re

    text = str(narration or "")
    if not text or not instance.players:
        return []
    actor_ids = [
        str(action.get("user_id") or "")
        for action in instance.action_queue
        if str(action.get("user_id") or "") in instance.players
    ]
    if len(set(actor_ids)) != 1:
        return []
    uid = actor_ids[0]
    actor_name = str(instance.players[uid].get("character_name") or "").strip()
    other_names = {
        str(player.get("character_name") or "").strip()
        for other_uid, player in instance.players.items()
        if other_uid != uid and isinstance(player, dict)
    } - {"", actor_name}
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
    # Require a reward/payment cue right before the amount. This excludes
    # balance/status narration such as “余额 710 金币”. A bare “给你” only
    # counts when the amount follows it directly (“给你710金币”), because
    # “给你看看……800金币” is a quote, not a payment.
    pattern = re.compile(
        rf"(?:(?:奖励|报酬|酬金|酬劳|赏金|赏给你|支付给你|付给你|"
        rf"rewards?|rewarded|bounty|pays you|paid you)[^0-9０-９]{{0,16}}|给你\s*)"
        rf"(?P<amount>[0-9]+)\s*(?P<label>{label_pattern})",
        re.IGNORECASE,
    )
    # A sentence with any of these is not a payment to the actor right now:
    # a price quote or per-item split, a conditional/future promise, or the
    # player character paying someone else.
    not_a_payment = re.compile(
        r"卖|售|价|多少钱|每|一共|总共|共计|收费|花费|要价|开价|"
        r"如果|若是|只要|答应|承诺|将会|事成|你们|"
        r"你[^，,；;：:]{0,8}?(?:支付|付了|付给|付清|交给|掏出|花了|花费)|"
        r"\b(?:costs?|sells?|sold|price[sd]?|for sale|each|per|charges?|"
        r"if|once|will|would|promises?|you all|you pay|you paid|you spend|you hand)\b",
        re.IGNORECASE,
    )
    addressed = re.compile(
        r"你|您|\byou\b" + (rf"|{re.escape(actor_name)}" if actor_name else ""),
        re.IGNORECASE,
    )
    matches: list[tuple[Any, str]] = []
    for sentence in re.split(r"[。！？!?\n]+|(?<=\.)\s+", text):
        sentence = sentence.strip()
        if not sentence or not_a_payment.search(sentence):
            continue
        if not addressed.search(sentence) or any(name in sentence for name in other_names):
            continue
        matches.extend((match, sentence) for match in pattern.finditer(sentence))
    if not matches:
        return []
    parsed_matches: list[tuple[Any, str, Any, str]] = []
    for match, sentence in matches:
        label = match.group("label")
        unit_id = next((unit for raw, unit in labels if raw.casefold() == label.casefold()), "")
        if not unit_id:
            return []
        try:
            amount = parse_currency_amount(match.group("amount"), unit_id, spec)
        except Exception:
            return []
        parsed_matches.append((match, unit_id, amount, sentence))
    # The narration may repeat the same explicit reward. Repeated identical
    # evidence is one candidate; conflicting amounts or units remain
    # ambiguous and fail closed.
    identities = {(int(amount), str(unit_id)) for _, unit_id, amount, _ in parsed_matches}
    if len(identities) != 1:
        return []
    match, unit_id, amount, sentence = parsed_matches[0]
    if amount <= 0:
        return []
    return [{
        "kind": "reward",
        "uid": uid,
        "recipient_uid": uid,
        "amount": amount,
        "reason": sentence[:240],
        "evidence": match.group(0).strip()[:300],
        "evidence_validated": True,
        "source": "narrative_reward_extractor",
    }]


def merge_narrative_reward_candidates(
    data: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> int:
    """Append prose reward candidates only when the response has no structured reward.

    The extractor is a fallback for a GM who narrated a payment without a
    ``REWARD`` tag. When the model did emit a reward proposal, that channel
    owns the response's rewards: its reason text rarely matches the prose
    sentence, so trying to match the two would credit one payment twice.
    """

    state_update = data.setdefault("state_update", {})
    if not isinstance(state_update, dict):
        return 0
    proposals = state_update.setdefault("economy_proposals", [])
    if not isinstance(proposals, list):
        proposals = []
        state_update["economy_proposals"] = proposals
    if any(
        isinstance(proposal, dict) and str(proposal.get("kind") or "") == "reward"
        for proposal in proposals
    ):
        return 0

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
