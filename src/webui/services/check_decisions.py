"""Atomic application boundary for post-roll decisions and guarded re-rolls."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.engine import check_decisions
from src.engine.game_state import GameState
from src.engine.participant_view import Viewer
from src.engine.staged_transaction import StagedGameTransaction

#: Push operations reserved for the table's Keeper. Every other operation in
#: ``check_decisions.change`` belongs to the seat that owns the check.
KEEPER_OPERATIONS = frozenset({"offer", "reject"})

#: Every refusal of this feature carries a message as well as a stable code:
#: the WebUI must never render a bare status line. ``src/webui/errors.py`` maps
#: the message to an ``error_code`` so the client can localize it.
_REFUSALS: dict[str, dict] = {
    "GAME_NOT_FOUND": {"ok": False, "code": "GAME_NOT_FOUND", "error": "游戏不存在"},
    "PREVIEW_READ_ONLY": {
        "ok": False,
        "code": "PREVIEW_READ_ONLY",
        "error": "当前是房主预览模式，请先开启允许代操作",
    },
    "PUSH_FORBIDDEN": {
        "ok": False,
        "code": "PUSH_FORBIDDEN",
        "error": "只能由该检定的行动者本人处理孤注一掷，主持人只能告知风险或拒绝申请",
    },
    "PUSH_INVALID_REQUEST": {
        "ok": False,
        "code": "PUSH_INVALID_REQUEST",
        "error": "孤注一掷请求格式无效",
    },
    "PUSH_INVALID_OR_STALE": {
        "ok": False,
        "code": "PUSH_INVALID_OR_STALE",
        "error": "检定选择已变化，请刷新后重试",
    },
    "REWRITE_IN_PROGRESS": {
        "ok": False,
        "code": "REWRITE_IN_PROGRESS",
        "error": "GM 正在重写历史回合，请等待完成后重试",
    },
    "STALE_RUN": {"ok": False, "code": "STALE_RUN", "error": "对局已重开，请刷新后重试"},
}


def refusal(code: str) -> dict:
    """One Push refusal payload; the transport only adds the HTTP status."""
    return {**_REFUSALS[code]}


@dataclass(frozen=True)
class CheckDecisionDependencies:
    get_instance: Callable[[str], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]


def decision_payload(inst: Any, result: dict) -> dict:
    luck = inst.pending_luck_checks()
    push = check_decisions.pending(inst)
    done = inst.state != GameState.ACTIVE_JUDGMENT
    return {**result, "phase": "done" if done else "luck" if luck or push else "ready",
            "pending_luck_decisions": luck, "pending_push_decisions": push,
            "ready_to_resolve": not done and not luck and not push, "round_already_resolved": done}


def keeper_uid(instance: Any) -> str:
    return str(getattr(instance, "gm_uid", "") or "")


def acting_uid(instance: Any, viewer: Viewer, operation: str) -> str:
    """The identity a Push command acts as.

    ``Viewer`` is the *read* identity: an owner-authenticated client that plays a
    seat resolves as GM there (docs/ARCHITECTURE_CN.md 12F), and player share
    keeps viewer identity and acting player identity apart (38.2). A Push
    decision is a command, so the seat the request names wins. The Keeper
    identity only applies to Keeper-only operations, or to a Keeper whose own
    seat owns the check.
    """
    gm_uid = keeper_uid(instance)
    if operation in KEEPER_OPERATIONS:
        return gm_uid if viewer.is_gm else ""
    seat_uid = str(viewer.uid or "")
    if seat_uid and seat_uid in (getattr(instance, "players", None) or {}):
        return seat_uid
    return gm_uid if viewer.is_gm else ""


class CheckDecisionService:
    def __init__(self, dependencies: CheckDecisionDependencies):
        self.deps = dependencies

    async def update(self, key: str, check_id: str, viewer: Viewer, operation: str, body: Any) -> dict:
        inst = self.deps.get_instance(key)
        if inst is None:
            return refusal("GAME_NOT_FOUND")
        gm_uid = keeper_uid(inst)
        uid = acting_uid(inst, viewer, operation)
        if not uid or (uid != gm_uid and uid not in inst.players):
            return refusal("PUSH_FORBIDDEN")
        if not isinstance(body, dict) or body.keys() - {"run_id", "round_number", "expected_revision", "reason", "consequence"}:
            return refusal("PUSH_INVALID_REQUEST")
        try:
            async with inst.authoritative_write() as entered:
                if not entered:
                    return refusal("REWRITE_IN_PROGRESS")
                async with inst._lock:
                    if self.deps.get_instance(key) is not inst:
                        return refusal("STALE_RUN")
                    transaction = StagedGameTransaction(inst)
                    result = check_decisions.change(transaction.staged, check_id, uid, operation,
                        run_id=body.get("run_id"), round_number=body.get("round_number"),
                        expected_revision=body.get("expected_revision"), reason=body.get("reason"), consequence=body.get("consequence"))
                    transaction.publish(inst)
                    try:
                        await self.deps.save_instance(inst)
                    except BaseException:
                        transaction.rollback(inst)
                        raise
                    # Runtime timers belong to the live instance; cancel after persistence.
                    inst._cancel_luck_timer(check_id)
                    return decision_payload(inst, {"ok": True, "check_result": result})
        except PermissionError:
            return refusal("PUSH_FORBIDDEN")
        except ValueError:
            return refusal("PUSH_INVALID_OR_STALE")
