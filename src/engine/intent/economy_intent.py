"""Purchase-intent recovery: intents + narration evidence → proposals.

Intent 层与既有 proposal 系统的对接点。``repair_missing_economy_proposals``
把每个 actor 的购买意图与其结构化 grant、叙事证据、持久化商家报价独立
配对，产出 pending 提案或结构化澄清：

```text
Intent(actor) → evidence ladder → pending proposal / clarification
```

价格证据阶梯（每个 actor 独立）：persisted merchant offer > 按句绑定的
叙事价格 > 全局唯一叙事金额 > 玩家行动自报金额 > clarification。玩家
自报金额只是缺失价格字段的证据，永远不能覆盖 persisted offer。
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable
from uuid import uuid4

from src.engine.economy import MAX_ECONOMY_AMOUNT
from src.engine.intent.evidence import collect_evidence_for_intent
from src.engine.intent.lexicon import instance_language
from src.engine.intent.matcher import match_open_merchant_offers
from src.engine.intent.parser import (
    charge_pattern,
    completed_payment_pattern,
    currency_amounts,
    deferred_payment_pattern,
    free_purchase_pattern,
    parse_purchase_intents,
)

AMOUNT_SOURCE_NARRATION = "narration"
AMOUNT_SOURCE_PLAYER_ACTION = "player_action"
AMOUNT_SOURCE_MERCHANT_OFFER = "merchant_offer"

_MAX_CLARIFICATION_HISTORY = 24


def bounded_economy_collection(instance: Any, key: str) -> list[dict[str, Any]]:
    economy = getattr(instance, "economy", None)
    if not isinstance(economy, dict):
        return []
    entries = economy.setdefault(key, [])
    if not isinstance(entries, list):
        economy[key] = []
    return economy[key]


def trim_open_history(entries: list[dict[str, Any]], *, max_history: int) -> None:
    open_entries = [
        entry for entry in entries
        if isinstance(entry, dict) and entry.get("status") == "open"
    ]
    resolved = [
        entry for entry in entries
        if not (isinstance(entry, dict) and entry.get("status") == "open")
    ]
    budget = max(0, max_history - len(open_entries))
    entries[:] = (resolved[-budget:] if budget else []) + open_entries


def has_economy_proposal(data: dict[str, Any]) -> bool:
    state_update = data.get("state_update")
    if not isinstance(state_update, dict):
        return False
    return bool(
        state_update.get("pending_payments")
        or state_update.get("economy_proposals")
    )


def record_purchase_clarification(
    instance: Any,
    *,
    reason: str,
    payer_uid: str = "",
    item_candidates: Iterable[str] = (),
    amount_candidates: Iterable[int] = (),
    evidence_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """Persist an unresolvable purchase intent as structured pending state.

    A clarification is a business state, not an error: it can never settle,
    charge, or deliver anything.  It keeps the structure of a failed
    fail-closed binding available for GM/player resolution instead of
    degrading it into a prose-only notice.
    """

    items: list[str] = []
    for candidate in item_candidates:
        name = str(candidate or "").strip()[:120]
        if name and name not in items:
            items.append(name)
    amounts: list[int] = []
    for amount_candidate in amount_candidates:
        try:
            parsed = int(amount_candidate)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in amounts:
            amounts.append(parsed)
    if not items and not amounts and not payer_uid:
        return None
    clarifications = bounded_economy_collection(instance, "clarifications")
    signature = (
        str(payer_uid or ""), tuple(items), tuple(amounts), str(reason)[:60],
    )
    for entry in clarifications:
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        entry_signature = (
            str(entry.get("payer_uid") or ""),
            tuple(str(item) for item in (entry.get("item_candidates") or [])),
            tuple(int(value) for value in (entry.get("amount_candidates") or [])),
            str(entry.get("reason") or ""),
        )
        if entry_signature == signature:
            return entry
    clarification = {
        "id": f"clarify_{uuid4().hex}",
        "run_id": str(getattr(instance, "run_id", "")),
        "origin_round": int(getattr(instance, "round_number", 0) or 0),
        "payer_uid": str(payer_uid or ""),
        "item_candidates": items,
        "amount_candidates": amounts,
        "reason": str(reason)[:60],
        "evidence_ids": list(evidence_ids or []),
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    clarifications.append(clarification)
    trim_open_history(clarifications, max_history=_MAX_CLARIFICATION_HISTORY)
    return clarification


def _collect_grants(state_update: dict[str, Any]) -> tuple[dict[str, list[str]], list[dict[str, Any]] | None]:
    """Collect AI-emitted item grants grouped by actor."""

    grants_by_uid: dict[str, list[str]] = {}
    players_update = state_update.get("players")
    if isinstance(players_update, dict):
        for uid, update in players_update.items():
            if not isinstance(update, dict):
                continue
            for key in ("equip_gain", "weapon_change"):
                item = str(update.get(key) or "").strip()
                if item:
                    grants_by_uid.setdefault(str(uid), []).append(item)
    loot = state_update.get("loot")
    loot_entries = loot if isinstance(loot, list) else None
    if loot_entries is not None:
        for entry in loot_entries:
            if isinstance(entry, dict):
                uid = str(entry.get("player") or "")
                name = str(entry.get("item") or "").strip()
                if uid and name:
                    grants_by_uid.setdefault(uid, []).append(name)
    return grants_by_uid, loot_entries


def _narration_amounts_for_items(
    language: str,
    narration_text: str,
    item_names: list[str],
    currency_labels: Iterable[str] | None,
) -> list[int]:
    bound: list[int] = []
    for sentence in re.split(r"[。！？.!?\n]+", narration_text):
        if not any(
            item and item.casefold() in sentence.casefold()
            for item in item_names
        ):
            continue
        bound.extend(currency_amounts(language, sentence, currency_labels))
    return sorted(set(bound))


def repair_missing_economy_proposals(
    instance: Any,
    data: dict[str, Any],
    narration: str,
    *,
    actions: Iterable[dict[str, Any]] | None = None,
    currency_labels: Iterable[str] | None = None,
) -> tuple[int, bool]:
    """Recover purchases the model narrated without structured payment tags.

    每个 actor 独立一条证据链：merchant offer > 按句绑定叙事价格 > 全局唯一
    叙事金额 > 行动自报金额 > clarification。有 grant 且证据唯一的 actor 合成
    pending 提案（扣款发货仍需付款人确认）；无 grant 的意图按 actor 记为
    结构化澄清，不再静默丢失。返回 (dropped_grant_count, clarified_any)。
    """

    state_update = data.get("state_update")
    if not isinstance(state_update, dict) or has_economy_proposal(data):
        return 0, False
    language = instance_language(instance)
    action_records = (
        list(actions) if actions is not None
        else list(getattr(instance, "action_queue", []))
    )
    intents = parse_purchase_intents(
        action_records, getattr(instance, "players", {}), language, currency_labels,
    )
    narrative_text = str(narration or "")
    priced_narrative = bool(
        charge_pattern(language, currency_labels).search(narrative_text)
        or completed_payment_pattern(language, currency_labels).search(narrative_text)
    )
    if not intents and not priced_narrative:
        return 0, False
    if free_purchase_pattern(language).search(narrative_text) and not priced_narrative:
        return 0, False

    players_update = state_update.get("players")
    grants_by_uid, loot_entries = _collect_grants(state_update)
    narration_amounts = currency_amounts(language, narrative_text, currency_labels)
    context_text = (
        narrative_text
        + "\n"
        + "\n".join(intent.action_text for intent in intents)
    )

    def _drop_grants(actor_uid: str, items: list[str]) -> None:
        if isinstance(players_update, dict):
            update = players_update.get(actor_uid)
            if isinstance(update, dict):
                for key in ("equip_gain", "weapon_change"):
                    if str(update.get(key) or "").strip() in items:
                        update.pop(key, None)
        current_loot = state_update.get("loot")
        if isinstance(current_loot, list):
            state_update["loot"] = [
                entry for entry in current_loot
                if not (
                    isinstance(entry, dict)
                    and str(entry.get("player") or "") == actor_uid
                    and str(entry.get("item") or "").strip() in items
                )
            ]

    def _clarify(
        *,
        reason: str,
        payer_uid: str,
        item_candidates: list[str],
        amount_candidates: list[int],
        evidence_ids: list[str] | None = None,
    ) -> None:
        record_purchase_clarification(
            instance,
            reason=reason,
            payer_uid=payer_uid,
            item_candidates=item_candidates,
            amount_candidates=amount_candidates,
            evidence_ids=evidence_ids,
        )

    dropped = 0
    clarified_any = False
    proposals: list[dict[str, Any]] = []

    for intent in intents:
        actor_grants = grants_by_uid.get(intent.actor_uid, [])
        named = [
            item for item in actor_grants
            if item.casefold() in context_text.casefold()
        ]
        if named:
            bound_items = named
        elif actor_grants and len(grants_by_uid) == 1:
            # 唯一有 grant 的 actor：叙事未点名时沿用保守兜底。
            bound_items = list(actor_grants)
        else:
            bound_items = []

        evidence_ids = collect_evidence_for_intent(
            instance,
            intent_actor_uid=intent.actor_uid,
            intent_item_context=intent.item_context,
            intent_amounts=intent.amount_candidates,
            grant_items=actor_grants,
        )

        if deferred_payment_pattern(language).search(intent.action_text):
            # 赊账/延期付款：交易条款未当场谈定，不合成立即结算的提案。
            # 证据与意图留档，待 GM/玩家安排后再走标准确认链。
            if bound_items:
                _drop_grants(intent.actor_uid, bound_items)
                dropped += len(bound_items)
            _clarify(
                reason="DEFERRED_PAYMENT",
                payer_uid=intent.actor_uid,
                item_candidates=bound_items if bound_items else [intent.item_context],
                amount_candidates=[
                    *intent.amount_candidates,
                    *narration_amounts,
                ],
                evidence_ids=evidence_ids,
            )
            clarified_any = True
            continue

        if not bound_items:
            # 无卖方接受证据（AI 未发 grant）：意图按 actor 记为澄清，
            # 不再静默丢失（round-12 结构图问题的恢复入口）。
            _clarify(
                reason="MISSING_SELLER_PRICE_CONFIRMATION",
                payer_uid=intent.actor_uid,
                item_candidates=[intent.item_context] if intent.item_context else [],
                amount_candidates=[
                    *intent.amount_candidates,
                    *narration_amounts,
                ],
                evidence_ids=evidence_ids,
            )
            clarified_any = True
            continue

        offers = match_open_merchant_offers(instance, bound_items)
        offer_amount = (
            int(offers[0].get("amount") or 0) if len(offers) == 1 else None
        )
        item_bound_amounts = _narration_amounts_for_items(
            language, narrative_text, bound_items, currency_labels,
        )

        amount = 0
        amount_source = ""
        if len(offers) > 1:
            _drop_grants(intent.actor_uid, bound_items)
            dropped += len(bound_items)
            _clarify(
                reason="AMBIGUOUS_OFFER",
                payer_uid=intent.actor_uid,
                item_candidates=bound_items,
                amount_candidates=[*intent.amount_candidates, *narration_amounts],
                evidence_ids=evidence_ids,
            )
            clarified_any = True
            continue
        if offer_amount is not None:
            offer_evidence = collect_evidence_for_intent(
                instance,
                intent_actor_uid=intent.actor_uid,
                intent_item_context=intent.item_context,
                offer=offers[0],
            )
            evidence_ids = [*evidence_ids, *offer_evidence]
            # 冲突检测同样只认有归属的证据：按句叙事绑定 / 行动自报，
            # 全局叙事金额（可能与商品无关，如任务悬赏）不参与冲突判定。
            evidence = (
                item_bound_amounts[0]
                if len(item_bound_amounts) == 1
                else intent.amount_candidates[0] if len(intent.amount_candidates) == 1
                else 0
            )
            if evidence and evidence != offer_amount:
                _drop_grants(intent.actor_uid, bound_items)
                dropped += len(bound_items)
                _clarify(
                    reason="OFFER_PRICE_CONFLICT",
                    payer_uid=intent.actor_uid,
                    item_candidates=bound_items,
                    amount_candidates=sorted({evidence, offer_amount}),
                    evidence_ids=evidence_ids,
                )
                clarified_any = True
                continue
            amount, amount_source = offer_amount, AMOUNT_SOURCE_MERCHANT_OFFER
        elif len(item_bound_amounts) == 1:
            # 叙事价格必须与商品同句绑定。此处刻意没有"全局唯一叙事金额"兜底：
            # 叙事里无关的数字（任务悬赏、NPC 薪水、其它商品报价）只要恰好唯一，
            # 就会被错误归属为商品价格（真实案例：影狼悬赏 40 金被绑给 15 金的剑）。
            # 价格归属比价格准确更重要——绑定失败回落行动自报金额或澄清。
            amount, amount_source = item_bound_amounts[0], AMOUNT_SOURCE_NARRATION
        elif len(intent.amount_candidates) == 1:
            amount, amount_source = (
                intent.amount_candidates[0], AMOUNT_SOURCE_PLAYER_ACTION,
            )

        if not amount or amount > MAX_ECONOMY_AMOUNT:
            _drop_grants(intent.actor_uid, bound_items)
            dropped += len(bound_items)
            _clarify(
                reason=(
                    "INVALID_AMOUNT"
                    if amount > MAX_ECONOMY_AMOUNT
                    else "AMBIGUOUS_PRICE"
                ),
                payer_uid=intent.actor_uid,
                item_candidates=bound_items if bound_items else [intent.item_context],
                amount_candidates=[
                    *intent.amount_candidates,
                    *narration_amounts,
                ],
                evidence_ids=evidence_ids,
            )
            clarified_any = True
            continue

        proposal_evidence_ids = collect_evidence_for_intent(
            instance,
            intent_actor_uid=intent.actor_uid,
            intent_item_context=intent.item_context,
            intent_amounts=intent.amount_candidates,
            narration_amount=(
                amount if amount_source == AMOUNT_SOURCE_NARRATION else None
            ),
            grant_items=bound_items,
        )
        evidence_ids = [*evidence_ids, *proposal_evidence_ids]

        proposals.append({
            "kind": "purchase",
            "uid": intent.actor_uid,
            "amount": amount,
            "recipient_uid": intent.actor_uid,
            "items": bound_items,
            "reason": f"购买 {'、'.join(bound_items)}",
            "approval_policy": "payer",
            "source": "server_purchase_guard",
            "amount_source": amount_source,
            "evidence_ids": evidence_ids,
        })
        # 合成提案消费该 actor 的 grant 作为唯一发货路径；这不是丢弃，
        # 不计入 dropped（dropped 只统计进澄清的 grant）。
        _drop_grants(intent.actor_uid, bound_items)

    for proposal in proposals:
        data.setdefault("state_update", {}).setdefault(
            "economy_proposals", [],
        ).append(proposal)
    return dropped, clarified_any
