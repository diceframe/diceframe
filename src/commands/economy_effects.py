"""Narrative economy gates that do not create charges.

Purchases are created only by the explicit GM purchase-order service. Model
output can describe a payment, but it cannot create a payment proposal.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.engine.intent.economy_intent import has_economy_proposal
from src.engine.intent.parser import completed_payment_pattern, currency_labels_for_rule
from src.engine.language import localized_text

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
