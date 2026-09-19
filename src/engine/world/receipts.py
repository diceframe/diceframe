"""WorldEvent receipt factory for committed world transactions.

母方案 §18/§19（WR-05）：世界事务在提交时输出 **WorldEvent receipts**——
"服务器确认一件世界层事件真实发生"的凭据，携带稳定 ID、来源轮次、提交时的
world revision 与逻辑时钟、可见性。

边界（母方案 §19/§81）：

- receipt 是 **receipt / history source**，不是数据库唯一真相：authority
  仍然是当前 WorldState；启动不重放事件。
- receipt 不是 EventBus 消息：没有任何订阅者，不派发、不回调；它只是
  事务返回值里可 grep、可投影（WR-06 memory / WR-10 inspector）的结构化记录。
- event_id 是**确定性**的 ``evt:{revision}:{position}:{kind}``：同一批提交
  重试产生相同 receipt，跨批绝不碰撞（revision 单调）。
- receipt 不持久化进 world_state（母方案 §13 的 v2 容器集不含 events）；
  它随事务返回值交给消费方（memory outbox / 诊断 / 测试）。
"""

from __future__ import annotations

from typing import Any

from src.engine.world.contracts import validate_world_event_record

# op summary 的 op 名 → WorldEvent kind。新增 record op 时必须同时补这两处
# 映射与可见性来源，否则 receipt 构造会 fail closed（而不是悄悄漏记）。
_OP_EVENT_KINDS = {
    "set_fact": "fact_set",
    "remove_fact": "fact_removed",
    "advance_time": "time_advanced",
    "schedule_event": "event_scheduled",
    "cancel_event": "event_cancelled",
    "complete_event": "event_settled",
    "register_entity": "entity_registered",
    "retire_entity": "entity_retired",
    "add_relation": "relation_added",
    "set_relation_status": "relation_status_changed",
    "remove_relation": "relation_removed",
    "start_process": "process_started",
    "complete_process": "process_settled",
    "cancel_process": "process_settled",
    "fail_process": "process_settled",
}

# subject 取自 op summary 的哪个字段（key = **op 名**，与 applied list 一致）。
_OP_SUBJECT_FIELDS = {
    "set_fact": "key",
    "remove_fact": "key",
    "schedule_event": "event_id",
    "cancel_event": "event_id",
    "complete_event": "event_id",
    "register_entity": "entity_id",
    "retire_entity": "entity_id",
    "add_relation": "relation_id",
    "set_relation_status": "relation_id",
    "remove_relation": "relation_id",
    "start_process": "process_id",
    "complete_process": "process_id",
    "cancel_process": "process_id",
    "fail_process": "process_id",
}

# FIX-05 §7.5：receipt 的 ``summary`` 携带**确定性语义**（relation kind /
# status、process status 等），让权威记忆不必只存 "relation_added @ evt"。
# 只做 "k=v" 拼接，不做任何自然语言生成（deterministic，可断言）。
_SUMMARY_FIELDS = ("kind", "status", "key", "value")
_MAX_SUMMARY_CHARS = 400


def _receipt_summary(applied: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in _SUMMARY_FIELDS:
        if field not in applied:
            continue
        value = applied.get(field)
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value)
        if not text:
            continue
        parts.append(f"{field}={text}")
    summary = " ".join(parts)
    return summary[:_MAX_SUMMARY_CHARS]


def _receipt_visibility(applied: dict[str, Any]) -> str:
    """Derive a receipt's visibility from the op that produced it.

    Facts / entities / relations / processes carry their own visibility.
    Time and scheduled-event bookkeeping are world-level: time is public, and
    event scheduling/settlement stays GM-side because labels and ops may hide
    secrets.
    """

    visibility = str(applied.get("visibility") or "")
    if visibility in ("public", "gm"):
        return visibility
    op = str(applied.get("op") or "")
    if op == "advance_time":
        return "public"
    return "gm"


def receipts_from_applied(
    applied: list[dict[str, Any]],
    *,
    revision: int,
    clock: dict[str, int],
    source_round: int,
) -> list[dict[str, Any]]:
    """Build validated WorldEvent receipts for one committed op batch."""

    receipts: list[dict[str, Any]] = []
    for position, applied_op in enumerate(applied):
        op = str(applied_op.get("op") or "")
        kind = _OP_EVENT_KINDS.get(op)
        if kind is None:
            raise ValueError(f"world op {op!r} has no WorldEvent receipt mapping")
        subject_field = _OP_SUBJECT_FIELDS.get(op)
        subject = applied_op.get(subject_field) if subject_field else None
        receipt = {
            "event_id": f"evt:{revision:06d}:{position}:{kind}",
            "kind": kind,
            "revision": revision,
            "clock": dict(clock),
            "source_round": source_round,
            "visibility": _receipt_visibility(applied_op),
            "subject": subject,
            "summary": _receipt_summary(applied_op),
        }
        receipts.append(validate_world_event_record(receipt))
    return receipts


__all__ = ["receipts_from_applied"]
