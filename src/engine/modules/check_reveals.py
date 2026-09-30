"""Per-check reveal markers for the shared click-to-reveal dice presentation.

The mechanical roll result is authoritative, already saved and already public
in the timeline; this slot only records who performed the shared reveal
ritual and when, so replay can restore masked / revealed card states. Pure
presentation state: it never gates progression (``dice_pending`` keeps that
job for genuine mechanical waits) and is trimmed to a bounded window of
recent checks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.engine.module_state import ModuleStateSpec, get_module_state, register_module_state

MODULE_NAME = "check_reveals"
SCHEMA_VERSION = 1
#: Presentation-only history; old markers may drop off without any notice.
MAX_RECORDS = 200


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "records": {}}


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    if not isinstance(raw.get("records"), dict):
        raw["records"] = {}
    return raw


def records(instance: Any) -> dict[str, dict[str, Any]]:
    return get_module_state(instance, MODULE_NAME)["records"]


def reveal_record(instance: Any, check_id: str) -> dict[str, Any] | None:
    record = records(instance).get(str(check_id))
    return record if isinstance(record, dict) else None


def mark_revealed(instance: Any, check_id: str, by: str) -> dict[str, Any]:
    """Idempotent: the first reveal wins and later calls return it unchanged."""
    key = str(check_id)
    slot = records(instance)
    existing = slot.get(key)
    if isinstance(existing, dict) and existing.get("by"):
        return existing
    record = {"by": str(by or ""), "at": datetime.now(timezone.utc).isoformat()}
    slot[key] = record
    while len(slot) > MAX_RECORDS:
        slot.pop(next(iter(slot)))
    return record


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
