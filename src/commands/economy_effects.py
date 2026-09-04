"""Narrative economy gates that do not create charges.

Purchases are created only by the explicit GM purchase-order service. Model
output can describe a payment, but it cannot create a payment proposal.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from src.engine.intent.economy_intent import has_economy_proposal
from src.engine.intent.parser import completed_payment_pattern, currency_labels_for_rule
from src.engine.language import localized_text

_DEFERRED_DATA_KEYS = {
    "confirmed", "growth_skills", "info_asymmetry", "memory_delta",
    "milestone_grants", "plot_update", "quick_actions", "scene_image_prompt",
    "xp_rewards",
}
_CONDITIONAL_REWARD_RE = re.compile(
    r"(?:要是|如果|若是|完成[^。！？\n]{0,20}后|之后再|等你|待你|才能|才会|以后|将会|承诺|答应|promise|promises|will pay|\bif\b|\bonce\b|\bafter\b|\bwhen\b)",
    re.IGNORECASE,
)
_COMPLETION_EVIDENCE_RE = re.compile(
    r"(?:完成|成功|击败|打倒|交付|归还|回收|达成|兑现|领取|earned|completed|complete|defeated|delivered|recovered|claimed|critical success|大成功)",
    re.IGNORECASE,
)


def _meaningful(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_meaningful(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_meaningful(item) for item in value)
    return value not in {None, "", False, 0}


def discard_unearned_reward_proposals(instance: Any, data: dict[str, Any], narration: str) -> int:
    state_update = data.get("state_update")
    proposals = state_update.get("economy_proposals") if isinstance(state_update, dict) else None
    if not isinstance(proposals, list):
        return 0
    text = str(narration or "")
    completed_titles: set[str] = set()
    plot_update = data.get("plot_update")
    if isinstance(plot_update, dict):
        for quest in plot_update.get("quests", []):
            if isinstance(quest, dict) and str(quest.get("status") or "").casefold() in {
                "completed", "complete", "已完成", "完成", "成功",
            }:
                title = str(quest.get("title") or "").strip().casefold()
                if title:
                    completed_titles.add(title)
    tracker = getattr(instance, "plot_tracker", None)
    for quest in getattr(tracker, "quests", {}).values() if tracker is not None else []:
        status = getattr(getattr(quest, "status", None), "value", getattr(quest, "status", ""))
        if str(status).casefold() in {"completed", "complete", "已完成", "完成", "成功"}:
            title = str(getattr(quest, "title", "") or "").strip().casefold()
            if title:
                completed_titles.add(title)
    kept: list[dict[str, Any]] = []
    dropped = 0
    for proposal in proposals:
        if not isinstance(proposal, dict) or proposal.get("kind") != "reward":
            kept.append(proposal)
            continue
        reason = str(proposal.get("reason") or "").casefold()
        if any(title in reason or reason in title for title in completed_titles):
            # Explicit same-turn or previously completed quest state is
            # accepted as the completion evidence.
            kept.append(proposal)
            continue
        if _COMPLETION_EVIDENCE_RE.search(text) and not _CONDITIONAL_REWARD_RE.search(text):
            kept.append(proposal)
        else:
            dropped += 1
    state_update["economy_proposals"] = kept
    return dropped


def unearned_reward_notice(language: str) -> str:
    return localized_text(language, {
        "en": "Reward pending: the task must be confirmed complete before it is awarded.",
        "zh-CN": "奖励待确认：任务确认完成前不会发放奖励。",
        "ja": "報酬保留中：任務の完了が確認されるまで報酬は付与されません。",
    })


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
    return localized_text(language, {
        "en": "No payment was charged: the GM must issue an explicit payment order.",
        "zh-CN": "本次未扣款：需要由 GM 明确发起支付订单。",
        "ja": "支払いは実行されていません。GM が明示的な支払い注文を発行してください。",
    })


def guard_unbacked_payment_narration(narration: str, data: dict[str, Any], language: str, *, currency_labels: Any = None) -> str:
    text = str(narration or "").strip()
    if not should_warn_unbacked_payment(
        text, data, language, currency_labels=currency_labels,
    ):
        return text
    return f"{text}\n\n{unbacked_payment_notice(language)}"


def unbacked_purchase_notice(language: str) -> str:
    return localized_text(language, {
        "en": "The item was not granted because its payment is not confirmed yet.",
        "zh-CN": "支付尚未确认，本次购买的物品未发放。",
        "ja": "支払いが確認されていないため、購入品は付与されませんでした。",
    })


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
    for key in _DEFERRED_DATA_KEYS:
        value = data.get(key)
        if _meaningful(value):
            deferred[key] = deepcopy(value)
        if key == "memory_delta":
            data[key] = {"add": [], "update": [], "forget": []}
        elif key == "plot_update":
            data[key] = {"quests": [], "relations": [], "decisions": []}
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
    return localized_text(language, {
        "en": "Settlement pending: dependent results are not effective yet.",
        "zh-CN": "结算待确认：关联结果尚未生效。",
        "ja": "決済確認待ち：関連結果はまだ発効していません。",
    })
