"""Ruleset binding storage; ruleset packages own state and event ledger contents."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.engine.module_state import (
    ModuleStateError, ModuleStateSpec, get_module_state, register_module_state,
)

MODULE_NAME = "ruleset_runtime"
SCHEMA_VERSION = 1


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "binding": {}, "state": {}, "event_ledger": []}


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    for key in ("binding", "state"):
        if not isinstance(raw.get(key), dict):
            raw[key] = {}
    if not isinstance(raw.get("event_ledger"), list):
        raw["event_ledger"] = []
    return raw


def require_writable(instance: Any) -> None:
    """Read-only transaction preflight; never repair or materialize a slot."""
    modules = getattr(instance, "modules", None)
    if not isinstance(modules, dict):
        raise ModuleStateError("instance has no module state container")
    slot = modules.get(MODULE_NAME)
    if not isinstance(slot, dict):
        return
    if slot.get("schema_version") != SCHEMA_VERSION:
        raise ModuleStateError(f"unsupported ruleset_runtime module schema: {slot.get('schema_version')!r}")


class RulesetBindingError(ValueError):
    code = "RULESET_BINDING_MISMATCH"

    def __init__(self) -> None:
        super().__init__("存档未绑定当前权威规则运行时")


def bound_runtime_id(instance: Any) -> str:
    """Read the binding without repairing or materializing module state."""
    modules = getattr(instance, "modules", None)
    slot = modules.get(MODULE_NAME) if isinstance(modules, dict) else None
    raw = slot.get("binding") if isinstance(slot, dict) else None
    runtime_id = raw.get("id") if isinstance(raw, dict) else None
    return runtime_id if isinstance(runtime_id, str) else ""


def require_binding(instance: Any, runtime_id: str) -> None:
    """Reject writes whose runtime does not own the saved binding."""
    if not runtime_id or bound_runtime_id(instance) != runtime_id:
        raise RulesetBindingError()


def binding(instance: Any) -> dict[str, Any]:
    return get_module_state(instance, MODULE_NAME)["binding"]


def replace_binding(instance: Any, value: dict[str, Any]) -> None:
    get_module_state(instance, MODULE_NAME)["binding"] = value


def state(instance: Any) -> dict[str, Any]:
    return get_module_state(instance, MODULE_NAME)["state"]


def replace_state(instance: Any, value: dict[str, Any]) -> None:
    get_module_state(instance, MODULE_NAME)["state"] = value


def event_ledger(instance: Any) -> list[dict[str, Any]]:
    return get_module_state(instance, MODULE_NAME)["event_ledger"]


def replace_event_ledger(instance: Any, value: list[dict[str, Any]]) -> None:
    get_module_state(instance, MODULE_NAME)["event_ledger"] = value


def persisted_state(instance: Any) -> dict[str, Any]:
    """Preserve encode-only omission of unbound state without changing live data.

    Follow-up: rest sessions on unbound games still disappear on reload.
    Unknown slot versions remain opaque, just like other module slots.
    """
    slot = instance.modules[MODULE_NAME]
    if slot.get("schema_version") != SCHEMA_VERSION:
        return slot
    return slot if slot.get("binding") else fresh()


def bind(instance: Any, normalized: dict[str, Any]) -> None:
    require_writable(instance)
    replace_binding(instance, normalized)
    if not state(instance):
        replace_state(instance, {"state_schema_version": normalized["state_schema_version"]})


def copy_binding_for_new_run(candidate: Any, source: Any) -> None:
    require_writable(source)
    require_writable(candidate)
    replace_binding(candidate, deepcopy(binding(source)))
    replace_state(candidate, (
        {"state_schema_version": int(binding(source).get("state_schema_version", 1) or 1)}
        if binding(source) else {}
    ))


def reset(instance: Any, saved_binding: dict[str, Any]) -> None:
    require_writable(instance)
    replace_binding(instance, saved_binding)
    replace_state(instance, (
        {"state_schema_version": int(saved_binding.get("state_schema_version", 1) or 1)}
        if saved_binding else {}
    ))
    event_ledger(instance).clear()


def restore_from_transaction(instance: Any, snapshot: dict[str, Any]) -> None:
    require_writable(instance)
    replace_state(instance, deepcopy(snapshot["ruleset_state"]))
    replace_event_ledger(instance, deepcopy(snapshot["event_ledger"]))


def round_entry_snapshot(instance: Any) -> dict[str, Any]:
    """Judgment-entry record: full state copy, ledger as an append-only mark.

    The ledger only grows inside a round, so its length plus the last batch id
    identify the entry point without persisting a second copy of the ledger.
    """
    ledger = event_ledger(instance)
    last = ledger[-1] if ledger and isinstance(ledger[-1], dict) else {}
    return {
        "state": deepcopy(state(instance)),
        "ledger_length": len(ledger),
        "ledger_last_batch_id": str(last.get("batch_id") or ""),
    }


def restore_round_entry(instance: Any, entry: Any) -> bool:
    """Roll state and ledger back to a ``round_entry_snapshot``.

    The ledger is truncated only when it still extends the recorded entry;
    if it was replaced meanwhile it is left alone. Returns whether the ledger
    was rewound (state is always restored for a well-formed entry).
    """
    if not isinstance(entry, dict) or not isinstance(entry.get("state"), dict):
        return False
    require_writable(instance)
    replace_state(instance, deepcopy(entry["state"]))
    length = entry.get("ledger_length")
    ledger = event_ledger(instance)
    if type(length) is not int or length < 0 or length > len(ledger):
        return False
    # With an empty entry ledger there is no marker to distinguish an
    # append-only extension from a ledger that was replaced wholesale.  Keep
    # the current ledger rather than clearing unrelated events; the caller
    # still restores the ruleset state and logs the ambiguous ledger.
    if length == 0 and ledger:
        return False
    if length:
        last = ledger[length - 1]
        last_id = str(last.get("batch_id") or "") if isinstance(last, dict) else ""
        if last_id != str(entry.get("ledger_last_batch_id") or ""):
            return False
    del ledger[length:]
    return True


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)
