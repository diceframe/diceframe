"""Scene runtime persisted slot (workplan §6.1 / §6.8 / §6.13).

This module owns the slot: pure operation logic and validation live in
``src.tactical_scene``, while the write service holds authority, locking and save
rollback. Unknown future slot schemas are preserved verbatim and rejected on
access, so this runtime never guesses how to interpret them.

The whole slot (scenes, active scene binding, revision) also rides the round
snapshot: judgment entry captures it, the log entry carries it as
``pre_tactical_scene_snapshot``, and abort / rollback / swipe restore it through
the owning module. The core flow reaches this module only through
``module_hooks``; without it rounds simply carry no scene state.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from src.engine import module_hooks
from src.engine.module_state import (
    ModuleStateError,
    ModuleStateSpec,
    get_module_state,
    register_module_state,
    set_module_state,
)
from src.tactical_scene.contract import is_canonical_id
from src.tactical_scene.ops import apply_tactical_scene_op

MODULE_NAME = "tactical_scene"
SCHEMA_VERSION = 1
LOG_FIELD = "pre_tactical_scene_snapshot"
_BEFORE_ROUND = "_before_round"


def fresh() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "scenes": {},
        "active_scene_ref": None,
        "revision": 0,
    }


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    if not isinstance(raw.get("scenes"), dict):
        raw["scenes"] = {}
    if "active_scene_ref" not in raw or not (
        raw["active_scene_ref"] is None or is_canonical_id(raw["active_scene_ref"])
    ):
        # A missing or non-canonical binding is unbound; names are never matched.
        raw["active_scene_ref"] = None
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raw["revision"] = 0
    return raw


def slot(instance: Any) -> dict[str, Any]:
    return get_module_state(instance, MODULE_NAME)


def scenes(instance: Any) -> dict[str, Any]:
    return slot(instance)["scenes"]


def active_scene_ref(instance: Any) -> str | None:
    return slot(instance)["active_scene_ref"]


def revision(instance: Any) -> int:
    return slot(instance)["revision"]


def snapshot(instance: Any) -> dict[str, Any]:
    """Capture the owning slot for save-failure rollback."""
    return deepcopy(slot(instance))


def restore(instance: Any, value: dict[str, Any]) -> None:
    """Restore a previously captured slot snapshot."""
    set_module_state(instance, MODULE_NAME, deepcopy(value))


def apply_op(instance: Any, op: Any, *, expected_revision: Any) -> dict[str, Any]:
    """Apply one validated write operation to the live slot.

    Callers hold the authority gate and instance lock; this module performs no
    IO and no locking of its own. The live slot is replaced only after the
    whole operation succeeds.
    """
    new_slot, summary = apply_tactical_scene_op(slot(instance), op, expected_revision=expected_revision)
    set_module_state(instance, MODULE_NAME, new_slot)
    return summary


def require_writable(instance: Any) -> None:
    """Read-only transaction preflight; never repair or materialize a slot."""
    modules = getattr(instance, "modules", None)
    if not isinstance(modules, dict):
        raise ModuleStateError("instance has no module state container")
    slot = modules.get(MODULE_NAME)
    if not isinstance(slot, dict):
        return  # Missing/corrupt slots have the documented fresh default.
    if slot.get("schema_version") != SCHEMA_VERSION:
        raise ModuleStateError(f"unsupported tactical_scene module schema: {slot.get('schema_version')!r}")


# ---------- round snapshots (workplan §6.13) -------------------------------


def _valid_round_snapshot(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    version = value.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        return False
    return isinstance(value.get("scenes"), dict)


def _apply_round_snapshot(instance: Any, value: Any) -> bool:
    """Restore a captured slot through the owning module; never guess."""
    if not _valid_round_snapshot(value):
        return False
    restored = deepcopy(value)
    restored.pop(_BEFORE_ROUND, None)
    set_module_state(instance, MODULE_NAME, restored)
    return True


def begin_round(instance: Any) -> None:
    """Capture the slot at judgment entry for abort / commit."""
    slot = get_module_state(instance, MODULE_NAME)
    before = deepcopy(slot)
    before.pop(_BEFORE_ROUND, None)
    slot[_BEFORE_ROUND] = before


def abort_round(instance: Any) -> bool:
    """Failed judgment: restore the scene as it was at judgment entry."""
    before = get_module_state(instance, MODULE_NAME).pop(_BEFORE_ROUND, None)
    return _apply_round_snapshot(instance, before)


def rewind_begin(instance: Any, _target_round: int) -> None:
    """Drop the discarded branch's live judgment-entry capture."""
    get_module_state(instance, MODULE_NAME).pop(_BEFORE_ROUND, None)


def rewind_restore(
    instance: Any, entry: Mapping[str, Any], _target_round: int, _source: str,
) -> None:
    """Rollback / swipe: restore the entry's judgment-entry scene snapshot.

    An entry without the field (historical log) keeps the live slot: older
    rounds never recorded scene state, so there is nothing to guess from.
    """
    if not isinstance(entry, Mapping):
        return
    before = entry.get(LOG_FIELD)
    if before is None:
        return
    _apply_round_snapshot(instance, before)


def commit_round(instance: Any, entry: dict[str, Any], _pre_snapshot: Any) -> None:
    """Judgment commit: record the round's judgment-entry scene snapshot."""
    slot = get_module_state(instance, MODULE_NAME)
    before = slot.pop(_BEFORE_ROUND, None)
    if _valid_round_snapshot(before):
        entry[LOG_FIELD] = deepcopy(before)
    elif slot.get("scenes") or slot.get("active_scene_ref") is not None or slot.get("revision"):
        # No judgment-entry capture (commit outside the round flow): record the
        # live slot rather than inventing an earlier state.
        entry[LOG_FIELD] = deepcopy(slot)


def reset(instance: Any) -> None:
    """In-place reset: a new run starts with an empty scene workspace."""
    require_writable(instance)
    set_module_state(instance, MODULE_NAME, fresh())


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)

module_hooks.register(module_hooks.ROUND_PROCESSING_BEGIN, MODULE_NAME, begin_round)
module_hooks.register(module_hooks.ROUND_COMMIT, MODULE_NAME, commit_round)
module_hooks.register(module_hooks.ROUND_ABORT, MODULE_NAME, abort_round)
module_hooks.register(module_hooks.ROUND_REWIND_BEGIN, MODULE_NAME, rewind_begin)
module_hooks.register(module_hooks.ROUND_REWIND_RESTORE, MODULE_NAME, rewind_restore)
module_hooks.register(module_hooks.ROUND_RECOVERY_PREFLIGHT, MODULE_NAME, require_writable)
module_hooks.register(module_hooks.INSTANCE_RESET_PREFLIGHT, MODULE_NAME, require_writable)
module_hooks.register(module_hooks.INSTANCE_RESET, MODULE_NAME, reset)