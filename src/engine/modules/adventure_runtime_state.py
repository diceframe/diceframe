"""Per-run Adventure state: v2 graph progress and the free/adventure play mode.

Adventure identity is not stored here: ``GameInstance.adventure_binding`` stays
the single authority for which Adventure a game is bound to, and the
``content_binding`` slot never mirrors it.  This slot only keeps what the run
has done with that binding:

- ``progress``: active/completed nodes, objectives, milestones and history of
  an Adventure v2 graph.  It is written in the same authoritative transaction
  as world truth, so round snapshots and rollbacks carry it alongside
  ``world_state``.
- ``play_mode``: a gameplay mode id from ``src.gameplays`` chosen at creation.
  An empty or unknown value is derived from the binding when a save is
  decoded, exactly as the codec did when the field lived at the top level.
  This slot only stores the id (transitional location, ADR-0012); the modes
  themselves, their bot surface and creation rules belong to the gameplay
  registry.

Run transitions: the in-place reset clears ``progress`` together with world
truth and keeps ``play_mode``; a new run (reset/restart/seed) keeps the play
mode and re-initializes v2 progress through the Adventure runtime.
"""

from __future__ import annotations

from copy import deepcopy
import uuid
from typing import Any

from src.engine import module_hooks
from src.gameplays import builtin as gameplay_builtin
from src.engine.module_state import (
    ModuleStateError, ModuleStateSpec, get_module_state, register_module_state,
)

MODULE_NAME = "adventure_runtime"
SCHEMA_VERSION = 1
MAX_COMPLETION_SUGGESTIONS = 64
MAX_IGNORED_COMPLETION_NODES = 256


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "progress": {}, "play_mode": ""}


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    if not isinstance(raw.get("progress"), dict):
        raw["progress"] = {}
    if not isinstance(raw.get("play_mode"), str):
        raw["play_mode"] = ""
    return raw


def require_writable(instance: Any) -> None:
    """Read-only transaction preflight; never repair or materialize a slot."""
    modules = getattr(instance, "modules", None)
    if not isinstance(modules, dict):
        raise ModuleStateError("instance has no module state container")
    slot = modules.get(MODULE_NAME)
    if not isinstance(slot, dict):
        return  # Missing/corrupt slots have the documented fresh default.
    if slot.get("schema_version") != SCHEMA_VERSION:
        raise ModuleStateError(f"unsupported adventure_runtime module schema: {slot.get('schema_version')!r}")


def derive_play_mode(value: Any, adventure_binding: Any) -> str:
    """The decode rule: keep a known mode verbatim, otherwise derive it."""
    text = str(value or "")
    if text.casefold() in gameplay_builtin.DEFAULT_GAMEPLAYS.mode_ids():
        return text
    if isinstance(adventure_binding, dict) and adventure_binding.get("adventure_id"):
        return "adventure"
    return "free"


def progress(instance: Any) -> dict[str, Any]:
    return get_module_state(instance, MODULE_NAME)["progress"]


def progress_snapshot(instance: Any) -> dict[str, Any]:
    """Read progress without repairing or materializing missing stored slots."""
    require_writable(instance)
    slot = instance.modules.get(MODULE_NAME)
    value = slot.get("progress") if isinstance(slot, dict) else None
    return deepcopy(value) if isinstance(value, dict) else {}


def replace_progress(instance: Any, value: Any) -> None:
    require_writable(instance)
    get_module_state(instance, MODULE_NAME)["progress"] = value if isinstance(value, dict) else {}


def record_completion_suggestion(instance: Any, candidate: Any) -> bool:
    """Store a bounded, non-authoritative GM suggestion against server facts."""
    binding = getattr(instance, "adventure_binding", None)
    if not isinstance(binding, dict) or binding.get("format") != "diceframe:adventure-graph-v2":
        return False
    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"format_version", "node_id", "reason", "evidence_rounds"}
        or not isinstance(candidate.get("format_version"), int)
        or isinstance(candidate.get("format_version"), bool)
        or candidate.get("format_version") != 1
    ):
        return False
    node_id = candidate.get("node_id")
    reason = candidate.get("reason")
    evidence = candidate.get("evidence_rounds")
    if (
        not isinstance(node_id, str) or not node_id or len(node_id) > 128
        or not isinstance(reason, str) or not reason.strip() or len(reason) > 500
        or not isinstance(evidence, list) or not evidence or len(evidence) > 5
        or any(not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 0
               for round_number in evidence)
    ):
        return False

    progress_value = progress(instance)
    active_nodes = progress_value.get("active_nodes")
    if not isinstance(active_nodes, list) or node_id not in active_nodes:
        return False
    ignored = progress_value.get("ignored_completion_nodes")
    if isinstance(ignored, list) and node_id in ignored:
        return False

    logged_rounds = {
        entry.get("round")
        for entry in (getattr(instance, "log", None) or [])
        if isinstance(entry, dict) and isinstance(entry.get("gm_response"), str)
        and entry["gm_response"].strip() and isinstance(entry.get("round"), int)
        and not isinstance(entry.get("round"), bool)
    }
    evidence_rounds = list(dict.fromkeys(evidence))
    if any(round_number not in logged_rounds for round_number in evidence_rounds):
        return False

    suggestions = progress_value.get("completion_suggestions")
    if not isinstance(suggestions, list):
        suggestions = []
    if any(isinstance(row, dict) and row.get("node_id") == node_id for row in suggestions):
        return False
    if len(suggestions) >= MAX_COMPLETION_SUGGESTIONS:
        return False

    suggestions.append({
        "proposal_id": uuid.uuid4().hex,
        "node_id": node_id,
        "reason": reason.strip(),
        "evidence_rounds": evidence_rounds,
    })
    progress_value["completion_suggestions"] = suggestions
    return True


def dismiss_completion_suggestion(instance: Any, proposal_id: str) -> str | None:
    """Dismiss one suggestion and suppress repeats for that active node."""
    progress_value = progress(instance)
    suggestions = progress_value.get("completion_suggestions")
    if not isinstance(suggestions, list):
        return None
    match = next((row for row in suggestions
                  if isinstance(row, dict) and row.get("proposal_id") == proposal_id), None)
    if match is None or not isinstance(match.get("node_id"), str) or not match["node_id"]:
        return None
    node_id = match.get("node_id")
    progress_value["completion_suggestions"] = [
        row for row in suggestions
        if not isinstance(row, dict) or row.get("proposal_id") != proposal_id
    ]
    ignored = progress_value.get("ignored_completion_nodes")
    if not isinstance(ignored, list):
        ignored = []
    if isinstance(node_id, str) and node_id not in ignored:
        ignored.append(node_id)
    progress_value["ignored_completion_nodes"] = ignored[-MAX_IGNORED_COMPLETION_NODES:]
    return node_id if isinstance(node_id, str) else None


def clear_completion_suggestion(instance: Any, node_id: str) -> None:
    """Forget advisory state when the authoritative node completion commits."""
    progress_value = progress(instance)
    suggestions = progress_value.get("completion_suggestions")
    if isinstance(suggestions, list):
        progress_value["completion_suggestions"] = [
            row for row in suggestions
            if not isinstance(row, dict) or row.get("node_id") != node_id
        ]
    ignored = progress_value.get("ignored_completion_nodes")
    if isinstance(ignored, list):
        progress_value["ignored_completion_nodes"] = [
            value for value in ignored if value != node_id
        ]


def clear_completion_suggestions_for_round(instance: Any, round_number: int) -> None:
    """Drop suggestions whose cited narration changed during swipe selection."""
    progress_value = progress(instance)
    suggestions = progress_value.get("completion_suggestions")
    if isinstance(suggestions, list):
        progress_value["completion_suggestions"] = [
            row for row in suggestions
            if not isinstance(row, dict)
            or not isinstance(row.get("evidence_rounds"), list)
            or round_number not in row["evidence_rounds"]
        ]


def play_mode(instance: Any) -> str:
    return get_module_state(instance, MODULE_NAME)["play_mode"]


def replace_play_mode(instance: Any, value: Any) -> None:
    require_writable(instance)
    get_module_state(instance, MODULE_NAME)["play_mode"] = value if isinstance(value, str) else ""


def normalize_decoded_play_mode(instance: Any) -> None:
    """Re-apply the decode-time derivation; unknown slot schemas stay opaque."""
    slot = getattr(instance, "modules", {}).get(MODULE_NAME)
    if not isinstance(slot, dict) or slot.get("schema_version") != SCHEMA_VERSION:
        return
    slot["play_mode"] = derive_play_mode(slot.get("play_mode"), getattr(instance, "adventure_binding", None))


def effective_bot_surface(instance: Any) -> str:
    """Bot surface of the run's effective play mode (decode-time derivation)."""
    return gameplay_builtin.DEFAULT_GAMEPLAYS.bot_surface(
        derive_play_mode(play_mode(instance), getattr(instance, "adventure_binding", None))
    )


SPEC = ModuleStateSpec(name=MODULE_NAME, schema_version=SCHEMA_VERSION, fresh=fresh, ensure=ensure)
register_module_state(SPEC)


def _restore_swipe_progress(instance: Any, entry: dict[str, Any], _round: int, source: str) -> None:
    """Restore this owner's facts before the staged rewrite builds context."""
    if source != module_hooks.REWIND_SOURCE_SWIPE:
        return
    snapshot = entry.get("pre_adventure_progress")
    if isinstance(snapshot, dict):
        replace_progress(instance, deepcopy(snapshot))


module_hooks.register(module_hooks.ROUND_REWIND_RESTORE, MODULE_NAME, _restore_swipe_progress)
