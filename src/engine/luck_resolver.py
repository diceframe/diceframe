"""幸运结算逻辑 —— 从 GameInstance 拆出的 luck 相关方法（P2-G Step 1）。

GameInstance 保留薄委托方法（见 game_instance.py 对应方法），调用方零改动。
本模块依赖 game_instance 的 GameState/GameInstance：循环 import 由
game_instance.py 文件末尾 `from src.engine import luck_resolver` 触发（届时
GameInstance/GameState 均已定义），函数内才访问这些名字。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.engine.character_utils import apply_resource_delta, get_resource
from src.engine.dice_rules_coc import REQUIRED_LEVEL_VERDICT
from src.engine.game_instance import GameInstance, GameState
from src.engine import progression
from src.engine import module_hooks
from src.engine import check_decisions
from src.engine.modules import checks, session_stats

logger = logging.getLogger("trpg")


async def resolve_luck_decision(
    instance: GameInstance,
    check_id: str,
    actor_uid: str,
    spend: bool,
    *,
    rule=None,
    allow_gm: bool = False,
) -> dict:
    """原子处理一次幸运选择，重复请求不会重复扣除资源。"""
    async with instance._lock:
        target = next(
            (check for check in checks.last_checks(instance) if str(check.get("check_id") or "") == check_id),
            None,
        )
        if not target:
            return {"ok": False, "code": "CHECK_NOT_FOUND", "error": "检定不存在或已过期"}
        owner_uid = str(target.get("actor_uid") or "")
        if actor_uid != owner_uid and not (allow_gm and actor_uid == instance.gm_uid):
            return {"ok": False, "code": "LUCK_FORBIDDEN", "error": "只能处理自己的幸运选择"}

        desired = "spent" if spend else "declined"
        current_decision = str(target.get("luck_decision") or "")
        if current_decision and current_decision != "pending":
            if current_decision == desired:
                return {
                    "ok": True,
                    "already_resolved": True,
                    "check_result": dict(target),
                }
            return {"ok": False, "code": "LUCK_ALREADY_RESOLVED", "error": "该检定的幸运选择已经处理"}
        if instance.state != GameState.ACTIVE_JUDGMENT or not checks.round_checks_prepared(instance):
            return {"ok": False, "code": "LUCK_NOT_PENDING", "error": "当前没有等待处理的幸运选择"}
        if current_decision != "pending":
            return {"ok": False, "code": "LUCK_NOT_AVAILABLE", "error": "该检定不能消耗幸运"}
        progression.require_writable(instance)
        session_stats.require_writable(instance)
        checks.require_writable(instance)
        module_hooks.run(module_hooks.SEAT_PREFLIGHT, instance)
        check_decisions.validate_all(instance)

        if spend:
            if str(target.get("dice") or "").lower() != "d100" or str(target.get("verdict") or "") != "失败":
                return {"ok": False, "code": "LUCK_NOT_AVAILABLE", "error": "该检定不能消耗幸运"}
            roll_value = int(target.get("roll", 0) or 0)
            # 规则要求困难/极难成功时，幸运要补到所需档位的上限，而不是技能值。
            threshold = int(target.get("required_threshold") or target.get("threshold", 0) or 0)
            cost = roll_value - threshold
            if cost <= 0:
                return {"ok": False, "code": "LUCK_NOT_AVAILABLE", "error": "该检定不需要消耗幸运"}
            character_sheet = instance.get_character_sheet(owner_uid)
            resource = get_resource(character_sheet, "luck")
            current_luck = int((resource or {}).get("current", character_sheet.get("luck", 0)) or 0)
            if current_luck < cost:
                return {
                    "ok": False,
                    "code": "LUCK_INSUFFICIENT",
                    "error": f"幸运不足：需要 {cost} 点，当前只有 {current_luck} 点",
                }
            remaining = apply_resource_delta(character_sheet, "luck", -cost, rule)
            target["original_verdict"] = target.get("verdict")
            target["verdict"] = REQUIRED_LEVEL_VERDICT.get(str(target.get("difficulty") or ""), "成功")
            if target["verdict"] == "普通成功":
                target["verdict"] = "成功"
            target["luck_spent"] = cost
            target["luck_remaining"] = remaining
            if actor_uid == owner_uid and owner_uid in instance.players:
                # The seat spent its own luck: it is in play (see seat_activity).
                module_hooks.run(module_hooks.SEAT_ACTED, instance, owner_uid, module_hooks.SEAT_SOURCE_LUCK)

        if spend:
            # 花了幸运就不能再孤注一掷；拒绝花幸运时仍可以选择孤注一掷。
            check_decisions.close(target)
        target["luck_decision"] = desired
        target["luck_spend_available"] = False
        target["luck_resolved_at"] = datetime.now(timezone.utc).isoformat()
        _cancel_luck_timer(instance, str(target.get("check_id") or ""))
        if checks.last_check(instance) and str(checks.last_check(instance).get("check_id") or "") == check_id:
            instance.sync_last_check(target)
        session_stats.touch(instance)
        return {"ok": True, "check_result": dict(target)}


async def decline_pending_luck(instance: GameInstance) -> list[dict]:
    """GM 强制推进时将所有未选择的幸运检定按失败继续。"""
    async with instance._lock:
        declined: list[dict] = []
        for check in checks.last_checks(instance):
            check_decisions.validate(check)
        if any(check.get("luck_decision") == "pending" for check in checks.last_checks(instance)) or check_decisions.pending(instance):
            progression.require_writable(instance)
            session_stats.require_writable(instance)
            checks.require_writable(instance)
        now = datetime.now(timezone.utc).isoformat()
        for check in checks.last_checks(instance):
            push_open = check.get("push", {}).get("status") in check_decisions.OPEN
            if check.get("luck_decision") != "pending" and not push_open:
                continue
            check_decisions.close(check, reason="gm_forced_advance")
            check["luck_decision"] = "declined"
            check["luck_spend_available"] = False
            check["luck_resolved_at"] = now
            _cancel_luck_timer(instance, str(check.get("check_id") or ""))
            declined.append(dict(check))
        if declined:
            if checks.last_check(instance):
                last_id = str(checks.last_check(instance).get("check_id") or "")
                replacement = next(
                    (check for check in checks.last_checks(instance) if str(check.get("check_id") or "") == last_id),
                    None,
                )
                if replacement:
                    instance.sync_last_check(replacement)
            session_stats.touch(instance, at=now)
        return declined


async def system_decline_luck(instance: GameInstance, check_id: str) -> dict:
    """幸运超时定时器触发：按失败继续单条幸运检定（系统发起，不需 actor）。

    玩家已手动决定时返回 LUCK_ALREADY_RESOLVED，不重复改判。
    返回 declined_all=True 表示这是最后一条 pending，调用方应重新推进回合。
    """
    async with instance._lock:
        target = next(
            (c for c in checks.last_checks(instance) if str(c.get("check_id") or "") == check_id),
            None,
        )
        if not target:
            return {"ok": False, "code": "CHECK_NOT_FOUND", "error": "检定不存在或已过期"}
        if str(target.get("luck_decision") or "") != "pending":
            return {"ok": False, "code": "LUCK_ALREADY_RESOLVED", "error": "该检定的幸运选择已经处理"}
        progression.require_writable(instance)
        session_stats.require_writable(instance)
        checks.require_writable(instance)
        check_decisions.close(target, reason="luck_timeout")
        target["luck_decision"] = "declined"
        target["luck_spend_available"] = False
        target["luck_timeout"] = True
        target["luck_resolved_at"] = datetime.now(timezone.utc).isoformat()
        if checks.last_check(instance) and str(checks.last_check(instance).get("check_id") or "") == check_id:
            instance.sync_last_check(target)
        session_stats.touch(instance, at=target["luck_resolved_at"])
        declined_all = not check_decisions.pending_any(instance)
        return {"ok": True, "check_result": dict(target), "declined_all": declined_all}


def _cancel_luck_timer(instance: GameInstance, check_id: str) -> None:
    """取消并移除某条检定的幸运超时定时器（若已挂）。手动决议先于超时到达时调用。"""
    task = instance._luck_timers.pop(check_id, None)
    if task and not task.done():
        task.cancel()
