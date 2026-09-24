"""Pure, ordered admission checks shared by the three action submission paths.

The gate returns codes only; transports own messages and status codes. Callers
retain authority and locks. Economy predicates must be synchronous read-only
queries: the human service awaits outbox retry between the two policy segments,
never inside a check. No check repairs or writes instance state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.engine.game_state import GameState
from src.engine.player_control import get_control, submission_block

PLAYER_NOT_IN_GAME = "PLAYER_NOT_IN_GAME"
ACTOR_DECEASED = "ACTOR_DECEASED"
STRUCTURED_INTENT_REQUIRED = "STRUCTURED_INTENT_REQUIRED"
ECONOMY_DECISION_PENDING = "ECONOMY_DECISION_PENDING"
ROUND_PROCESSING = "ROUND_PROCESSING"

RUN_CHANGED = "run_changed"
ROUND_CHANGED = "round_changed"
SEAT_REMOVED = "seat_removed"
CONTROL_CHANGED = "control_changed"
PHASE_CHANGED = "phase_changed"
HUMAN_GATE_CHANGED = "human_gate_changed"
DUPLICATE = "duplicate"

SOURCE_HUMAN = "human"
SOURCE_AI_SEAT = "ai_seat"
SOURCE_INTENT = "intent"

# A supplied predicate must synchronously read current state without mutation.
# AI commits evaluate it under their existing authority/state locks, after awaits.
StructuredIntentRequirement = bool | Callable[[], bool] | None


@dataclass(frozen=True)
class GateRequest:
    actor_uid: str
    source: str
    requester_is_gm: bool = False
    expected_run_id: str | None = None
    expected_round_number: int | None = None
    expected_control_revision: int | None = None
    # Runtime resolution stays with the caller, outside the engine.
    requires_structured_intent: StructuredIntentRequirement = False
    # Lazy read only. In particular this must not retry the async outbox.
    economy_blocked: Callable[[], bool] | None = None
    action_source: str = ""


Check = Callable[[Any, GateRequest], str]


def check_seat_exists(instance: Any, req: GateRequest) -> str:
    if req.requester_is_gm:
        return ""
    return "" if req.actor_uid in instance.players else PLAYER_NOT_IN_GAME


def check_human_control(instance: Any, req: GateRequest) -> str:
    return submission_block(instance, req.actor_uid)


def check_structured_intent(instance: Any, req: GateRequest) -> str:
    requirement = req.requires_structured_intent
    required = requirement() if callable(requirement) else requirement
    return STRUCTURED_INTENT_REQUIRED if required else ""


def check_actor_deceased(instance: Any, req: GateRequest) -> str:
    return ACTOR_DECEASED if instance.is_dead(req.actor_uid) else ""


def check_economy(instance: Any, req: GateRequest) -> str:
    if req.economy_blocked is None:
        return ""
    return ECONOMY_DECISION_PENDING if req.economy_blocked() else ""


def check_not_judging(instance: Any, req: GateRequest) -> str:
    return ROUND_PROCESSING if instance.state == GameState.ACTIVE_JUDGMENT else ""


def check_run_unchanged(instance: Any, req: GateRequest) -> str:
    if req.expected_run_id is None:
        return ""
    current = str(getattr(instance, "run_id", "") or "")
    return "" if current == req.expected_run_id else RUN_CHANGED


def check_round_unchanged(instance: Any, req: GateRequest) -> str:
    if req.expected_round_number is None:
        return ""
    return "" if int(instance.round_number or 0) == req.expected_round_number else ROUND_CHANGED


def check_seat_present(instance: Any, req: GateRequest) -> str:
    return "" if req.actor_uid in instance.players else SEAT_REMOVED


def check_ai_control_current(instance: Any, req: GateRequest) -> str:
    record = get_control(instance, req.actor_uid)
    if record["mode"] != "ai":
        return CONTROL_CHANGED
    if req.expected_control_revision is not None and int(record["revision"]) != req.expected_control_revision:
        return CONTROL_CHANGED
    return ""


def check_phase_active_action(instance: Any, req: GateRequest) -> str:
    return "" if instance.state == GameState.ACTIVE_ACTION else PHASE_CHANGED


def check_human_gate_open(instance: Any, req: GateRequest) -> str:
    # All-AI tables have no humans to wait for (human_actions_ready is False).
    if instance.active_human_players and not instance.human_actions_ready():
        return HUMAN_GATE_CHANGED
    return ""


def check_not_duplicate_from_source(instance: Any, req: GateRequest) -> str:
    if instance.has_action_from_source(req.actor_uid, req.expected_round_number, req.action_source):
        return DUPLICATE
    return ""


# Order is a contract. The service awaits retry at this boundary, only once the
# prefix passes, then evaluates the suffix without repeating earlier checks.
HUMAN_FREE_TEXT_PRE_RETRY_POLICY: tuple[Check, ...] = (
    check_seat_exists,
    check_human_control,
    check_structured_intent,
    check_actor_deceased,
)
HUMAN_FREE_TEXT_POST_RETRY_POLICY: tuple[Check, ...] = (
    check_economy,
    check_not_judging,
)
HUMAN_FREE_TEXT_POLICY: tuple[Check, ...] = (
    HUMAN_FREE_TEXT_PRE_RETRY_POLICY + HUMAN_FREE_TEXT_POST_RETRY_POLICY
)
AI_SEAT_STALE_POLICY: tuple[Check, ...] = (
    check_run_unchanged,
    check_round_unchanged,
    check_seat_present,
    check_ai_control_current,
    check_phase_active_action,
)
# The aggregate keeps its public stale-reason facade as the prefix entry point.
# Both segments run synchronously under the same authority/state lock boundary.
AI_SEAT_COMMIT_POLICY: tuple[Check, ...] = (
    check_human_gate_open,
    check_not_duplicate_from_source,
    check_structured_intent,
)
AI_SEAT_POLICY: tuple[Check, ...] = AI_SEAT_STALE_POLICY + AI_SEAT_COMMIT_POLICY
STRUCTURED_INTENT_POLICY: tuple[Check, ...] = (check_seat_exists, check_not_judging)


def evaluate(instance: Any, request: GateRequest, policy: tuple[Check, ...]) -> str:
    """Return the first rejection in policy order, or an empty string."""
    for check in policy:
        code = check(instance, request)
        if code:
            return code
    return ""
