"""Deterministic WorldEvent → World Memory projection (母方案 §21/§22, WR-06).

``WorldEvent receipt`` → **确定性晋升策略** → memory delta（携带 provenance）
→ 既有 memory outbox（``queue_memory_delivery``）→ MemoryStore。

硬边界：

- **确定性晋升**：哪些 receipt 值得进入长期记忆由白名单决定，第一版不用
  LLM 判断事实（§22）。LLM 之后可以把已确认事件摘要得更自然，但永远不能
  决定"什么真实发生过"。
- **Memory NEVER writes current WorldState**（§21）：本模块只读 receipts、
  只写 memory 侧，不触碰 ``GameInstance.world_state``。
- **幂等**：outbox 投递身份 = ``worldevent:{event_id}``，重试/重复推进不会
  产生重复记忆（幂等投递由既有 outbox / MemoryEconomyDelivery 保证）。
- fact_set / time_advanced 不晋升：当前事实的 authority 是 WorldState 本身；
  把每次事实变更复制进记忆只会制造第二真相。权威 Rules outcome /
  Adventure milestone / GM confirmed event 的直连投影由后续 PR 按
  §21 的来源清单逐个接入。
"""

from __future__ import annotations

from typing import Any

from src.engine.memory_outbox import queue_memory_delivery

# 记忆来源 kind：world event receipts 的直连投影。
WORLD_MEMORY_SOURCE_KIND = "worldevent"

# 确定性晋升白名单（§22）：世界结构 / 生命周期的故事级变化。
PROMOTED_EVENT_KINDS = (
    "entity_registered",
    "entity_retired",
    "relation_added",
    "relation_status_changed",
    "relation_removed",
    "process_settled",
)


def world_memory_delta(receipt: dict[str, Any]) -> dict[str, Any] | None:
    """Turn one WorldEvent receipt into a provenance-stamped memory delta.

    FIX-05 §7.5：权威记忆必须保留**确定性语义**，而不是只存
    ``relation_added @ evt``。value 由四段组成（全部来自 receipt 本身，确定性）：

    ```text
    <kind> <subject> [· <summary>] · rev <revision> @ <event_id>
    ```

    其中 ``summary`` 是 receipt 携带的结构化语义（relation kind / status、
    process status、fact key/value 等，见 ``world/receipts.py``）。后续 LLM 可以
    把这些字段润色成叙事，但"什么真实发生过"永远由这里的字段决定。

    Returns ``None`` for receipts the deterministic policy does not promote.
    """

    kind = str(receipt.get("kind") or "")
    if kind not in PROMOTED_EVENT_KINDS:
        return None
    subject = receipt.get("subject")
    if not isinstance(subject, str) or not subject:
        return None
    event_id = str(receipt.get("event_id") or "")
    revision = receipt.get("revision")
    summary = str(receipt.get("summary") or "").strip()
    parts = [kind, subject]
    if summary:
        parts.append(summary)
    parts.append(f"rev {revision}")
    parts.append(f"@ {event_id}")
    return {
        "add": [{
            "entity": subject,
            "relation": "world_event",
            "value": " · ".join(parts),
        }],
        "memory_kind": "authoritative_world",
        "source_kind": WORLD_MEMORY_SOURCE_KIND,
        "source_id": event_id,
        "world_revision": revision,
        "visibility": receipt.get("visibility") or "public",
    }


def queue_world_memory(
    instance: Any,
    receipts: list[dict[str, Any]] | None,
    *,
    round_number: int,
) -> list[dict[str, Any]]:
    """Queue memory deliveries for promotable receipts; idempotent per event."""

    queued: list[dict[str, Any]] = []
    for receipt in receipts or []:
        if not isinstance(receipt, dict):
            continue
        delta = world_memory_delta(receipt)
        if delta is None:
            continue
        delivery = queue_memory_delivery(
            instance,
            effect_group_id=f"worldevent:{receipt.get('event_id') or ''}",
            memory_delta=delta,
            round_number=round_number,
        )
        if delivery is not None:
            queued.append(delivery)
    return queued


__all__ = [
    "PROMOTED_EVENT_KINDS",
    "WORLD_MEMORY_SOURCE_KIND",
    "queue_world_memory",
    "world_memory_delta",
]
