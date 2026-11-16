"""Authority and persistence boundary for declarative character growth."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from src.engine.character_progress import apply_attribute_step, assign_class_level, spend_skill_point
from src.engine.game_state import GameState
from src.engine.staged_transaction import StagedGameTransaction

logger = logging.getLogger("trpg")
_OPERATIONS = {
    "class-level": assign_class_level,
    "skill-point": spend_skill_point,
    "attribute-step": apply_attribute_step,
}


async def update_character_progress(
    *, game_key: str, uid: str, requester_id: str, expected_run_id: str,
    operation: str, selection: str, get_instance: Callable[[str], Any],
    load_rule: Callable[[Any], Any], save_instance: Callable[[Any], Awaitable[None]],
) -> tuple[dict[str, Any], int]:
    """Revalidate under the state lock, plan on a copy, undo failed publication.

    The authority gate is task-reentrant (including production middleware).
    Progression is intentionally not part of historical swipe; no growth write
    may enter during a judgment or a rewrite.
    """
    def error(code: str, status: int, message: str = ""):
        return {"ok": False, "error_code": code, **({"error": message} if message else {})}, status

    instance = get_instance(game_key)
    if instance is None:
        return error("game_missing", 404)
    async with instance.authoritative_write() as entered, instance._lock:
        if not entered:
            return error("rewrite_in_progress", 409)
        if get_instance(game_key) is not instance or expected_run_id != instance.run_id:
            return error("run_changed", 409)
        if not instance.run_writable():
            return error("read_only", 409)
        if instance.state == GameState.ACTIVE_JUDGMENT or instance.round_processing_in_flight():
            return error("round_processing", 409)
        if not requester_id or (requester_id != uid and requester_id != instance.gm_uid):
            return error("forbidden", 403)
        if uid not in instance.players:
            return error("player_missing", 404)
        try:
            rule = load_rule(instance)
            if rule is None:
                return error("rule_unavailable", 409)
        except Exception:
            return error("rule_unavailable", 409)
        try:
            transaction = StagedGameTransaction(instance)
            result = _OPERATIONS[operation](transaction.staged, uid, selection, rule)
            transaction.publish(instance)
            try:
                await save_instance(instance)
            except BaseException as exc:
                transaction.rollback(instance)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if not isinstance(exc, Exception):
                    raise
                logger.exception("Saving character progress failed")
                return error("save_failed", 500)
        except asyncio.CancelledError:
            raise
        except ValueError as exc:
            return error("invalid", 400, str(exc))
        except Exception:
            logger.exception("Saving character progress failed")
            return error("save_failed", 500)
    return {"ok": True, **result}, 200
