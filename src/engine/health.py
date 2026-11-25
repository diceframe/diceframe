"""System health / degradation events for a running game instance."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from src.engine import module_hooks, progression

_MAX_HEALTH_EVENTS = 100
_SEVERITIES = {"info", "warning", "error", "critical"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_health_event(
    instance,
    component: str,
    code: str,
    severity: str,
    title: str,
    message: str = "",
    impact: str = "",
    fallback: str = "",
    repair_hint: str = "",
    details: dict | None = None,
) -> dict:
    """Append a GM-visible degradation event to a GameInstance-like object."""
    severity = severity if severity in _SEVERITIES else "warning"
    event = {
        "id": "evt_" + uuid4().hex[:12],
        "time": _now_iso(),
        "round": progression.current_round(instance),
        "severity": severity,
        "component": component,
        "code": code,
        "title": title,
        "message": message,
        "impact": impact,
        "fallback": fallback,
        "repair_hint": repair_hint,
        "details": details or {},
        "resolved": False,
        "ignored": False,
    }
    events = [*module_hooks.read(instance, module_hooks.HEALTH_EVENTS), event]
    module_hooks.record(
        instance, module_hooks.HEALTH_EVENTS, events[-_MAX_HEALTH_EVENTS:],
    )

    status = dict(module_hooks.read_value(instance, module_hooks.HEALTH_STATUS) or {})
    status[component] = severity if severity != "info" else "ok"
    status["last_event_at"] = event["time"]
    module_hooks.record(instance, module_hooks.HEALTH_STATUS, status)
    return event


def mark_health_event(instance, event_id: str, *, resolved: bool = False, ignored: bool = False) -> bool:
    """Mark a health event as resolved or ignored."""
    for event in module_hooks.read(instance, module_hooks.HEALTH_EVENTS):
        if event.get("id") == event_id:
            if resolved:
                event["resolved"] = True
                event["resolved_at"] = _now_iso()
            if ignored:
                event["ignored"] = True
                event["ignored_at"] = _now_iso()
            return True
    return False


def health_payload(instance, include_resolved: bool = False) -> dict:
    """Build the API payload for a GameInstance health view."""
    events = list(module_hooks.read(instance, module_hooks.HEALTH_EVENTS))
    if not include_resolved:
        events = [
            e for e in events
            if not e.get("resolved") and not e.get("ignored")
        ]
    from src.engine.modules import narrative_quality_metrics

    return {
        "ok": True,
        "status": module_hooks.read_value(instance, module_hooks.HEALTH_STATUS) or {},
        "events": events[-_MAX_HEALTH_EVENTS:],
        "total": len(events),
        "narrative_quality": narrative_quality_metrics.metrics(instance),
    }
