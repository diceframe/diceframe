"""Post-roll Push decisions owned by the round-check domain.

Callers mutate under aggregate authority/state locks. The saved threshold and
mode, never client dice or a translated skill lookup, determine the second roll.
"""
from __future__ import annotations
from src.engine.modules import legacy_combat, progression_state

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from src.engine.dice_rules_coc import (
    apply_required_level,
    check_d100_bonus,
    coc_success_level,
    normalize_difficulty,
    project_percentile_dice,
)
from src.engine.game_state import GameState
from src.engine.modules import checks, session_stats
from src.engine import module_hooks
from src.engine.player_control import control_mode

OPEN = frozenset({"pending", "requested", "offered"})
STATUSES = OPEN | {"declined", "rejected", "resolved"}


def _state(check: dict) -> dict | None:
    push = check.get("push")
    if push is None:
        return None
    if (not isinstance(push, dict) or push.get("schema_version") != 1
            or push.get("status") not in STATUSES or type(push.get("revision")) is not int
            or push["revision"] < 0 or not isinstance(push.get("run_id"), str)
            or type(push.get("round")) is not int):
        raise ValueError("unsupported Push decision")
    return push


def _in_scope(inst: Any, push: dict) -> bool:
    return push["run_id"] == inst.run_id and push["round"] == progression_state.round_value(inst)


def validate(check: dict) -> None:
    _state(check)


def validate_all(inst: Any) -> None:
    for check in checks.last_checks(inst):
        validate(check)


def prepare(inst: Any, rule: Any) -> None:
    """Offer newly failed ordinary checks; never reopen restored decisions."""
    if (not module_hooks.ask(module_hooks.STATE_READ, inst, module_hooks.PUSH_ENABLED, default=True)
            or rule is None or not rule.template.get("pushed_rolls")
            or rule.advantage_mechanic.get("type") != "coc_bonus_penalty"):
        return
    checks.require_writable(inst)
    # 规则声明大失败不可孤注一掷时（CoC 7e 通行做法），只给普通失败提供推骰。
    pushable_verdicts = {"失败"} if rule.pushed_roll_excludes_fumble else {"失败", "大失败"}
    for check in checks.last_checks(inst):
        if check.get("push") is not None:
            _state(check)
            continue
        uid = str(check.get("actor_uid") or "")
        attribute = str(check.get("attribute_key") or "")
        ordinary = bool(check.get("skill")) or attribute in rule.attribute_keys
        if (uid not in inst.players or control_mode(inst, uid) != "human"
                or check.get("actor_ref") not in (None, "", f"player:{uid}")
                or check.get("kind") != "check" or not ordinary
                or check.get("opponent") or legacy_combat.combat_state(inst) != "none"
                or check.get("dice") != "d100" or check.get("dice_mechanic") != "coc_bonus_penalty"
                or check.get("verdict") not in pushable_verdicts
                or check.get("luck_decision") in {"spent", "declined"}):
            continue
        check["push"] = {"schema_version": 1, "revision": 0, "status": "pending",
                         "run_id": inst.run_id, "round": progression_state.round_value(inst),
                         "reason": "", "consequence": ""}
    _sync(inst)


def pending(inst: Any) -> list[dict]:
    if inst.state != GameState.ACTIVE_JUDGMENT or not checks.round_checks_prepared(inst):
        return []
    return [deepcopy(check) for check in checks.last_checks(inst)
            if (push := _state(check)) is not None and _in_scope(inst, push) and push["status"] in OPEN]


def pending_any(inst: Any) -> bool:
    return bool(inst.pending_luck_checks() or pending(inst))


def close(check: dict, *, status: str = "declined", reason: str = "") -> None:
    """Luck, timeout and forced advancement share the same terminal decision."""
    push = _state(check)
    if push is not None and push["status"] in OPEN:
        push.update(status=status, decision_reason=reason, resolved_at=datetime.now(timezone.utc).isoformat())
        push["revision"] += 1


def close_disabled(inst: Any) -> list[str]:
    """Accept original results for unfinished choices; never replay resolved rolls."""
    checks.require_writable(inst)
    validate_all(inst)
    closed = []
    for check in checks.last_checks(inst):
        push = _state(check)
        if push is not None and _in_scope(inst, push) and push['status'] in OPEN:
            close(check, reason='push_disabled_by_gm')
            closed.append(str(check.get('check_id') or ''))
    _sync(inst)
    return closed


def close_timed_out(inst: Any, check_id: str) -> bool:
    """Accept the original result of one unanswered Push; never re-roll.

    A check whose Luck choice is still open is left to the Luck timeout, which
    closes its Push together with the Luck decision.
    """
    checks.require_writable(inst)
    validate_all(inst)
    if inst.state != GameState.ACTIVE_JUDGMENT or not checks.round_checks_prepared(inst):
        return False
    check = next((c for c in checks.last_checks(inst) if c.get("check_id") == check_id), None)
    push = _state(check) if check is not None else None
    if (push is None or push["status"] not in OPEN or not _in_scope(inst, push)
            or check.get("luck_decision") == "pending"):
        return False
    close(check, reason="push_timeout")
    _sync(inst)
    return True


def _sync(inst: Any) -> None:
    if checks.last_check(inst):
        target = next((c for c in checks.last_checks(inst) if c.get("check_id") == checks.last_check(inst).get("check_id")), None)
        if target is not None:
            inst.sync_last_check(target)


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValueError("a concrete reason or consequence is required")
    return value.strip()


def change(inst: Any, check_id: str, uid: str, operation: str, *, run_id: str,
           round_number: int, expected_revision: int, reason: Any = None,
           consequence: Any = None, rng: Any = None) -> dict:
    if not module_hooks.ask(module_hooks.STATE_READ, inst, module_hooks.PUSH_ENABLED, default=True):
        raise ValueError('Push is disabled for this table')
    checks.require_writable(inst)
    session_stats.require_writable(inst)
    validate_all(inst)
    check = next((c for c in checks.last_checks(inst) if c.get("check_id") == check_id), None)
    push = _state(check) if check is not None else None
    if (check is None or push is None or push["status"] not in OPEN or not _in_scope(inst, push)
            or run_id != inst.run_id or type(round_number) is not int or round_number != progression_state.round_value(inst)
            or type(expected_revision) is not int or expected_revision != push["revision"]
            or inst.state != GameState.ACTIVE_JUDGMENT or not checks.round_checks_prepared(inst)):
        raise ValueError("Push decision unavailable or stale")
    owner = str(check.get("actor_uid") or "")
    if owner not in inst.players:
        raise ValueError("seat unavailable")
    if operation in {"offer", "reject"}:
        if uid != inst.gm_uid:
            raise PermissionError("only the Keeper can adjudicate a Push request")
    elif uid != owner:
        raise PermissionError("only the check owner can choose Push")

    if operation == "request" and push["status"] == "pending":
        push.update(status="requested", reason=_text(reason))
    elif operation == "offer" and push["status"] in {"requested", "offered"}:
        push.update(status="offered", consequence=_text(consequence))
    elif operation == "reject" and push["status"] in {"requested", "offered"}:
        push.update(status="rejected", decision_reason=_text(reason))
    elif operation == "decline":
        push.update(status="declined")
    elif operation == "confirm" and push["status"] == "offered":
        # Preserve the original record before replacing the current outcome.
        original = deepcopy({k: v for k, v in check.items() if k != "push"})
        mode = str(check.get("advantage_mode") or "")
        threshold = check.get("threshold")
        if type(threshold) is not int or not 1 <= threshold <= 99 or mode not in {"", "advantage", "disadvantage"}:
            raise ValueError("invalid original check parameters")
        if rng is None:
            rolled, verdict = check_d100_bonus(threshold, int(mode == "advantage"), int(mode == "disadvantage"))
            value, rolls = rolled.natural, rolled.rolls
        else:
            value = rng()
            if type(value) is not int or not 1 <= value <= 100 or mode:
                raise ValueError("invalid injected percentile roll")
            rolls, verdict = [value], coc_success_level(value, threshold)
        achieved = verdict
        if check.get("difficulty"):
            # 推骰沿用原检定的难度要求：困难检定推骰后也必须达到困难成功。
            verdict = apply_required_level(verdict, normalize_difficulty(check.get("difficulty")))
        check.pop("achieved_verdict", None)
        if verdict != achieved:
            check["achieved_verdict"] = achieved
        push.update(status="resolved", original=original, failed=verdict in {"失败", "大失败"})
        check.update(roll=value, rolls=rolls, verdict=verdict, percentile_dice=project_percentile_dice(rolls, value, mode),
                     is_critical=verdict == "大成功", is_fumble=verdict == "大失败", luck_cost=None)
    else:
        raise ValueError("invalid Push transition")
    # Requesting Push irrevocably chooses it over Luck; declining accepts failure.
    if check.get("luck_decision") == "pending":
        check.update(luck_decision="declined", luck_spend_available=False)
    push["revision"] += 1
    push["updated_at"] = datetime.now(timezone.utc).isoformat()
    _sync(inst)
    session_stats.touch(inst)
    return deepcopy(check)
