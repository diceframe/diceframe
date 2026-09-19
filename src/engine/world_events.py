"""Deterministic settlement of scheduled world events on logical time advance.

The world already stores scheduled events (see :mod:`src.engine.world_state`);
this module answers "what happens when logical time passes a planned moment?".

```text
advance_world_time(+N)
    -> new world clock
    -> due pending events (ordered by due moment, then canonical event id)
    -> apply each event's ops
    -> mark it applied (or failed when its ops no longer apply)
    -> one authoritative commit on GameInstance.world_state
```

Boundaries:

- **No background work.** Time moves only when the authoritative round flow
  calls this function; there is no timer, thread, real-time tick, or cron.
- **Deterministic.** Ordering never depends on dict iteration order, insertion
  order, or client input: due events are sorted by ``(day, minute, event_id)``.
- **Idempotent.** An event is settled exactly once through the persisted
  ``pending -> applied | failed`` transition, so a retry, a duplicate save, or a
  reloaded page can never execute it twice.
- **Isolated failures.** An event whose ops no longer apply (for example it
  removes a fact that no longer exists) is marked ``failed`` with its error,
  while the clock and the remaining events still settle.  It is never retried
  silently and never blocks the game forever.
- **Revertable.** Everything lives in ``GameInstance.world_state``, so the
  existing whole-round rollback / swipe / reset semantics apply unchanged.
"""

from __future__ import annotations

from typing import Any

from src.engine.world_state import (
    WorldStateError,
    apply_ops_to_state,
    clock_to_minutes,
    ensure_world_state,
    world_clock,
    world_processes,
    world_revision,
    world_scheduled_events,
)

# 单次推进上限：一天。世界状态本身允许更大跨度（见 world_state 的时钟上限），
# 但「一轮推进好几年」不是回合流程能解释的语义，因此在这里收紧。
MAX_ADVANCE_MINUTES = 1440


def advance_world_time(
    instance: Any, minutes: int, *, source_round: int | None = None,
) -> dict[str, Any]:
    """Move logical time forward and settle every event that becomes due.

    Raises :class:`WorldStateError` for an unusable amount or a world container
    that cannot be interpreted; a rejected call writes nothing.
    """

    amount = _validated_minutes(minutes)
    round_number = source_round if isinstance(source_round, int) and not isinstance(
        source_round, bool,
    ) and source_round >= 0 else 0
    state = ensure_world_state(getattr(instance, "world_state", None))
    target = clock_to_minutes(world_clock(state)) + amount
    # 时钟推进同样是世界 ops：单批提交，失败则整次推进不生效。
    draft, _ = apply_ops_to_state(
        state, [{"op": "advance_time", "minutes": amount}],
        source_round=round_number,
    )
    settled: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for event in due_events(draft, target_minutes=target):
        event_id = str(event["event_id"])
        label = str(event.get("label") or "")
        due_at = dict(event.get("due_at") or {})
        # 持久化状态在 apply_ops_to_state 入口已按 op shape contract 校验过：这里
        # 不再静默过滤非法 op（那会把「事件没执行任何东西」伪装成 applied）。
        nested = [dict(op) for op in (event.get("ops") or [])]
        try:
            draft, _ = apply_ops_to_state(
                draft,
                [*nested, {
                    "op": "complete_event", "event_id": event_id, "status": "applied",
                }],
                source_round=round_number,
            )
        except WorldStateError as exc:
            message = str(exc)[:160]
            draft, _ = apply_ops_to_state(
                draft,
                [{
                    "op": "complete_event", "event_id": event_id,
                    "status": "failed", "error": message,
                }],
                source_round=round_number,
            )
            failed.append({
                "event_id": event_id, "label": label, "due_at": due_at,
                "error": message,
            })
            continue
        settled.append({"event_id": event_id, "label": label, "due_at": due_at})
    # Process 的到期结算同样只发生在权威时间推进里（母方案 §16/§80：无后台
    # tick）。到期的 running 进程确定性转为 completed；事件先结算、进程后结算，
    # 顺序固定。后果（fact / 通知 / 记忆）由上层从 WorldEvent / 进程状态读取，
    # 进程本身不携带可执行 ops。
    settled_processes: list[dict[str, Any]] = []
    for process in due_processes(draft, target_minutes=target):
        process_id = str(process["process_id"])
        draft, _ = apply_ops_to_state(
            draft,
            [{"op": "complete_process", "process_id": process_id}],
            source_round=round_number,
        )
        settled_processes.append({
            "process_id": process_id,
            "kind": str(process.get("kind") or ""),
            "due_at": dict(process.get("due_at") or {}),
        })
    instance.world_state = draft
    return {
        "clock": dict(draft["clock"]),
        "minutes": amount,
        "revision": world_revision(draft),
        "applied": settled,
        "failed": failed,
        "processes": settled_processes,
    }


def due_events(state: Any, *, target_minutes: int) -> list[dict[str, Any]]:
    """Pending events due at or before ``target_minutes``, in stable order."""

    due: list[dict[str, Any]] = []
    for event in world_scheduled_events(state).values():
        if str(event.get("status") or "") != "pending":
            continue
        try:
            moment = clock_to_minutes(event.get("due_at"))
        except WorldStateError:
            # 结构损坏的事件不会被悄悄执行，也不会拖垮其它事件。
            continue
        if moment <= target_minutes:
            due.append(event)
    due.sort(key=lambda event: (
        clock_to_minutes(event.get("due_at")), str(event.get("event_id") or ""),
    ))
    return due


def due_processes(state: Any, *, target_minutes: int) -> list[dict[str, Any]]:
    """Running processes whose ``due_at`` has passed, in stable order."""

    due: list[dict[str, Any]] = []
    for process in world_processes(state).values():
        if str(process.get("status") or "") != "running":
            continue
        raw_due = process.get("due_at")
        if raw_due is None:
            continue
        try:
            moment = clock_to_minutes(raw_due)
        except WorldStateError:
            continue
        if moment <= target_minutes:
            due.append(process)
    due.sort(key=lambda process: (
        clock_to_minutes(process.get("due_at")), str(process.get("process_id") or ""),
    ))
    return due


def _validated_minutes(minutes: Any) -> int:
    if (
        isinstance(minutes, bool) or not isinstance(minutes, int)
        or not 0 < minutes <= MAX_ADVANCE_MINUTES
    ):
        raise WorldStateError(
            f"world time advance needs 1..{MAX_ADVANCE_MINUTES} minutes"
        )
    return minutes


__all__ = ["MAX_ADVANCE_MINUTES", "advance_world_time", "due_events", "due_processes"]
