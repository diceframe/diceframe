"""Evidence layer: persist what was observed, never what is decided.

证据层回答"系统看到了什么"。证据不是事实：不能直接修改状态、不能扣款、
不能发货，只用于创建 proposal、辅助 GM 确认、恢复遗漏事件。任何证据都
不携带权威性（``authority`` 恒为 False），权威状态只能由 proposal 经
settlement 产生。

```text
Intent → collect evidence → evidence records → proposal / clarification
```
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

MAX_EVIDENCE_HISTORY = 100

# 证据可信度（方案约定）：merchant_offer 最高，player_action 次之，
# narration 只作辅助。⚠️ 数值仅供 GM 界面排序展示——DO NOT use confidence
# for authorization / auto-approval；任何授权判定必须走 proposal 状态机。
EVIDENCE_CONFIDENCE = {
    "merchant_offer": 0.9,
    "player_action": 0.8,
    "narration": 0.7,
    "grant": 0.6,
}


def evidence_collection(instance: Any) -> list[dict[str, Any]]:
    economy = getattr(instance, "economy", None)
    if not isinstance(economy, dict):
        return []
    entries = economy.setdefault("evidence", [])
    if not isinstance(entries, list):
        economy["evidence"] = []
    return economy["evidence"]


def _referenced_evidence_ids(instance: Any) -> set[str]:
    """Evidence ids referenced by proposals or clarifications.

    被引用的证据是审计链的一部分：GM 打开历史提案/澄清时依赖它们还原
    价格来源，因此永不参与 trim 淘汰。
    """

    economy = getattr(instance, "economy", {})
    if not isinstance(economy, dict):
        return set()
    referenced: set[str] = set()
    for collection in ("proposals", "clarifications"):
        for entry in economy.get(collection, []) or []:
            if isinstance(entry, dict):
                referenced.update(
                    str(item) for item in (entry.get("evidence_ids") or []) if str(item)
                )
    return referenced


def _trim_evidence(instance: Any, entries: list[dict[str, Any]]) -> None:
    if len(entries) <= MAX_EVIDENCE_HISTORY:
        return
    referenced = _referenced_evidence_ids(instance)
    kept: list[dict[str, Any]] = []
    freed = 0
    # 从旧到新扫描：未引用的旧证据先淘汰，被引用的与配额内的新证据保留。
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if entry_id in referenced:
            kept.append(entry)
        elif freed < len(entries) - MAX_EVIDENCE_HISTORY:
            freed += 1
        else:
            kept.append(entry)
    entries[:] = kept


def record_evidence(
    instance: Any,
    *,
    evidence_type: str,
    source: str,
    actor_uid: str = "",
    item: str = "",
    amount: int | None = None,
    note: str = "",
    dedupe: bool = False,
) -> dict[str, Any] | None:
    """Append one evidence record and return it (audit-only, bounded).

    ``dedupe=True`` 时同一轮内 type/actor/item/amount 完全相同的记录只保留
    一条（供可重入的规划阶段使用，避免重试路径重复留痕）。
    """

    entries = evidence_collection(instance)
    if dedupe:
        run_id = str(getattr(instance, "run_id", ""))
        try:
            round_number = int(getattr(instance, "round_number", 0) or 0)
        except (TypeError, ValueError):
            round_number = -1
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                entry_round = int(entry.get("round", -1))
            except (TypeError, ValueError):
                entry_round = -1
            if (
                entry.get("type") == evidence_type
                and entry.get("source") == source
                and str(entry.get("actor_uid") or "") == str(actor_uid or "")
                and str(entry.get("item") or "") == str(item or "")
                and entry.get("amount") == amount
                and str(entry.get("run_id") or "") == run_id
                and entry_round == round_number
            ):
                return entry
    evidence = {
        "id": f"ev_{uuid4().hex}",
        "type": str(evidence_type or "")[:40],
        "run_id": str(getattr(instance, "run_id", "")),
        "round": int(getattr(instance, "round_number", 0) or 0),
        "actor_uid": str(actor_uid or "")[:64],
        "item": str(item or "")[:120],
        "amount": int(amount) if amount is not None else None,
        "source": str(source or "")[:40],
        "confidence": EVIDENCE_CONFIDENCE.get(str(source or ""), 0.5),
        "authority": False,
        "note": str(note or "")[:200],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    entries.append(evidence)
    _trim_evidence(instance, entries)
    return evidence


def collect_evidence_for_intent(
    instance: Any,
    *,
    intent_actor_uid: str,
    intent_item_context: str,
    intent_amounts: Iterable[int] | None = None,
    narration_amount: int | None = None,
    offer: dict[str, Any] | None = None,
    grant_items: Iterable[str] | None = None,
) -> list[str]:
    """Persist every evidence piece found for one purchase intent.

    返回证据 id 列表，供 proposal / clarification 挂 ``evidence_ids``，让
    GM 处理时能看到完整证据链（玩家行动、叙事绑定、商家报价、AI grant
    各自独立留痕）。
    """

    ids: list[str] = []
    amounts = list(intent_amounts or [])
    intent_amount = amounts[0] if len(amounts) == 1 else None
    evidence = record_evidence(
        instance,
        evidence_type="purchase_intent",
        source="player_action",
        actor_uid=intent_actor_uid,
        item=intent_item_context,
        amount=intent_amount,
        note=(
            "行动自报金额多个候选" if len(amounts) > 1
            else "" if intent_amount is not None
            else "行动未含金额"
        ),
    )
    if evidence is not None:
        ids.append(evidence["id"])
    if offer is not None:
        offer_evidence = record_evidence(
            instance,
            evidence_type="merchant_offer_price",
            source="merchant_offer",
            actor_uid=intent_actor_uid,
            item=str(offer.get("item_display") or ""),
            amount=int(offer.get("amount", 0) or 0),
            note=f"offer {offer.get('id') or ''}",
        )
        if offer_evidence is not None:
            ids.append(offer_evidence["id"])
    if narration_amount is not None:
        narration_evidence = record_evidence(
            instance,
            evidence_type="narration_price",
            source="narration",
            actor_uid=intent_actor_uid,
            item=intent_item_context,
            amount=narration_amount,
        )
        if narration_evidence is not None:
            ids.append(narration_evidence["id"])
    for item in grant_items or ():
        grant_evidence = record_evidence(
            instance,
            evidence_type="seller_grant",
            source="grant",
            actor_uid=intent_actor_uid,
            item=str(item or ""),
        )
        if grant_evidence is not None:
            ids.append(grant_evidence["id"])
    return ids
