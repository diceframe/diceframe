"""Affection: how a character feels about the party (persisted module slot).

One bounded integer score per subject, from ``MIN_SCORE`` to ``MAX_SCORE``.
A subject is the key a character is known by in this run: an ``instance.npcs``
key (narrator NPCs are keyed by display name, so any script is allowed) or a
world entity id.  The score is the
attitude toward the party / protagonist; per-seat attitudes would be a new
slot schema, never guessed from this one.

This module owns only the authoritative numbers and their invariants (range,
subject key shape, subject count).  Balance (tiers, per-round caps, decay) is
gameplay configuration and stays out of the engine.  Narrator output never
writes here directly: a later settlement step validates model proposals and
calls ``adjust`` (ENGINEERING_RULES §13).

Scores belong to the run: reset / restart starts empty, and round abort,
rollback and swipe restore the scores as of the round's start (the log entry
carries them as ``pre_affection``).  The core flow reaches this module only
through ``module_hooks``; without it a game simply has no affection.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.engine import module_hooks
from src.engine.module_state import (
    ModuleStateError, ModuleStateSpec, get_module_state, register_module_state,
)

MODULE_NAME = "affection"
SCHEMA_VERSION = 1
MIN_SCORE = -100
MAX_SCORE = 100
MAX_SUBJECTS = 64
# Matches the NPC tag's name truncation.
MAX_SUBJECT_CHARS = 80
LOG_FIELD = "pre_affection"
_BEFORE_ROUND = "_before_round"


def fresh() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "scores": {}}


def _valid_subject(subject: Any) -> bool:
    return (
        isinstance(subject, str)
        and 0 < len(subject) <= MAX_SUBJECT_CHARS
        and subject == subject.strip()
        and subject.isprintable()
    )


def _valid_score(score: Any) -> bool:
    return type(score) is int and MIN_SCORE <= score <= MAX_SCORE


def _clean_scores(raw: Any) -> dict[str, int]:
    """Keep well-formed entries only; a malformed score is dropped, not clamped."""
    if not isinstance(raw, dict):
        return {}
    scores = {key: value for key, value in raw.items() if _valid_subject(key) and _valid_score(value)}
    return dict(list(scores.items())[:MAX_SUBJECTS])


def ensure(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fresh()
    if raw.get("schema_version") != SCHEMA_VERSION:
        return raw
    raw["scores"] = _clean_scores(raw.get("scores"))
    before = raw.get(_BEFORE_ROUND)
    if before is not None and not isinstance(before, dict):
        raw.pop(_BEFORE_ROUND)
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
        raise ModuleStateError(f"unsupported affection module schema: {slot.get('schema_version')!r}")


def scores(instance: Any) -> dict[str, int]:
    """Projection: every subject's score (a copy).  Unreadable slots raise."""
    return dict(get_module_state(instance, MODULE_NAME)["scores"])


def score(instance: Any, subject: str) -> int | None:
    """One subject's score; ``None`` when it has none or the slot is unreadable.

    Callers deciding a branch (gates) must treat ``None`` as "no evidence".
    """
    try:
        return get_module_state(instance, MODULE_NAME)["scores"].get(str(subject or ""))
    except ModuleStateError:
        return None


def adjust(instance: Any, subject: str, delta: int) -> int:
    """Apply ``delta`` clamped to the score range; return the applied change.

    A subject without a score starts at 0.  Only the settlement step may call
    this (architecture guard); it validates proposals before getting here.
    """
    if not _valid_subject(subject):
        raise ValueError(f"invalid affection subject: {subject!r}")
    if type(delta) is not int:
        raise ValueError(f"affection delta must be an integer: {delta!r}")
    table = get_module_state(instance, MODULE_NAME)["scores"]
    if subject not in table and len(table) >= MAX_SUBJECTS:
        raise ValueError("too many affection subjects")
    before = table.get(subject, 0)
    after = max(MIN_SCORE, min(MAX_SCORE, before + delta))
    table[subject] = after
    return after - before


def reset(instance: Any) -> None:
    require_writable(instance)
    instance.modules[MODULE_NAME] = fresh()


def _begin(instance: Any) -> None:
    slot = get_module_state(instance, MODULE_NAME)
    slot[_BEFORE_ROUND] = {"scores": dict(slot["scores"])}


def _commit(instance: Any, entry: Any, _pre: Any) -> None:
    slot = get_module_state(instance, MODULE_NAME)
    before = slot.pop(_BEFORE_ROUND, None)
    if not isinstance(before, dict):
        before = {"scores": dict(slot["scores"])}
    # Rounds that started with no scores carry nothing; rewinding to one
    # without the field restores the empty table it started with.
    if before["scores"]:
        entry[LOG_FIELD] = deepcopy(before)


def _abort(instance: Any) -> bool:
    slot = get_module_state(instance, MODULE_NAME)
    before = slot.pop(_BEFORE_ROUND, None)
    if not isinstance(before, dict):
        return False
    slot["scores"] = _clean_scores(before.get("scores"))
    return True


def _rewind(instance: Any, entry: Any, _round: int, _source: str) -> None:
    require_writable(instance)
    before = entry.get(LOG_FIELD) if isinstance(entry, dict) else None
    state = fresh()
    if isinstance(before, dict):
        state["scores"] = _clean_scores(before.get("scores"))
    instance.modules[MODULE_NAME] = state


register_module_state(ModuleStateSpec(MODULE_NAME, SCHEMA_VERSION, fresh, ensure))
module_hooks.register(module_hooks.ROUND_PROCESSING_BEGIN, MODULE_NAME, _begin)
module_hooks.register(module_hooks.ROUND_COMMIT, MODULE_NAME, _commit)
module_hooks.register(module_hooks.ROUND_ABORT, MODULE_NAME, _abort)
module_hooks.register(module_hooks.ROUND_REWIND_RESTORE, MODULE_NAME, _rewind)
module_hooks.register(module_hooks.ROUND_RECOVERY_PREFLIGHT, MODULE_NAME, require_writable)
module_hooks.register(module_hooks.INSTANCE_RESET_PREFLIGHT, MODULE_NAME, require_writable)
module_hooks.register(module_hooks.INSTANCE_RESET, MODULE_NAME, reset)
