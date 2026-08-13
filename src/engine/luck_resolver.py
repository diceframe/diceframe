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
from src.engine.game_instance import GameInstance, GameState

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
            (check for check in instance.last_checks if str(check.get("check_id") or "") == check_id),
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
        if instance.state != GameState.ACTIVE_JUDGMENT or not instance.round_checks_prepared:
            return {"ok": False, "code": "LUCK_NOT_PENDING", "error": "当前没有等待处理的幸运选择"}
        if current_decision != "pending":
            return {"ok": False, "code": "LUCK_NOT_AVAILABLE", "error": "该检定不能消耗幸运"}

        if spend:
            if str(target.get("dice") or "").lower() != "d100" or str(target.get("verdict") or "") != "失败":
                return {"ok": False, "code": "LUCK_NOT_AVAILABLE", "error": "该检定不能消耗幸运"}
            roll_value = int(target.get("roll", 0) or 0)
            threshold = int(target.get("threshold", 0) or 0)
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
            target["verdict"] = "成功"
            target["luck_spent"] = cost
            target["luck_remaining"] = remaining

        target["luck_decision"] = desired
        target["luck_spend_available"] = False
        target["luck_resolved_at"] = datetime.now(timezone.utc).isoformat()
        _cancel_luck_timer(instance, str(target.get("check_id") or ""))
        if instance.last_check and str(instance.last_check.get("check_id") or "") == check_id:
            instance.sync_last_check(target)
        instance.last_activity = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "check_result": dict(target)}


async def decline_pending_luck(instance: GameInstance) -> list[dict]:
    """GM 强制推进时将所有未选择的幸运检定按失败继续。"""
    async with instance._lock:
        declined: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()
        for check in instance.last_checks:
            if check.get("luck_decision") != "pending":
                continue
            check["luck_decision"] = "declined"
            check["luck_spend_available"] = False
            check["luck_resolved_at"] = now
            _cancel_luck_timer(instance, str(check.get("check_id") or ""))
            declined.append(dict(check))
        if declined:
            if instance.last_check:
                last_id = str(instance.last_check.get("check_id") or "")
                replacement = next(
                    (check for check in instance.last_checks if str(check.get("check_id") or "") == last_id),
                    None,
                )
                if replacement:
                    instance.sync_last_check(replacement)
            instance.last_activity = now
        return declined


async def system_decline_luck(instance: GameInstance, check_id: str) -> dict:
    """幸运超时定时器触发：按失败继续单条幸运检定（系统发起，不需 actor）。

    玩家已手动决定时返回 LUCK_ALREADY_RESOLVED，不重复改判。
    返回 declined_all=True 表示这是最后一条 pending，调用方应重新推进回合。
    """
    async with instance._lock:
        target = next(
            (c for c in instance.last_checks if str(c.get("check_id") or "") == check_id),
            None,
        )
        if not target:
            return {"ok": False, "code": "CHECK_NOT_FOUND", "error": "检定不存在或已过期"}
        if str(target.get("luck_decision") or "") != "pending":
            return {"ok": False, "code": "LUCK_ALREADY_RESOLVED", "error": "该检定的幸运选择已经处理"}
        target["luck_decision"] = "declined"
        target["luck_spend_available"] = False
        target["luck_timeout"] = True
        target["luck_resolved_at"] = datetime.now(timezone.utc).isoformat()
        if instance.last_check and str(instance.last_check.get("check_id") or "") == check_id:
            instance.sync_last_check(target)
        instance.last_activity = target["luck_resolved_at"]
        declined_all = not any(
            c.get("luck_decision") == "pending"
            for c in instance.last_checks
        )
        return {"ok": True, "check_result": dict(target), "declined_all": declined_all}


def _cancel_luck_timer(instance: GameInstance, check_id: str) -> None:
    """取消并移除某条检定的幸运超时定时器（若已挂）。手动决议先于超时到达时调用。"""
    task = instance._luck_timers.pop(check_id, None)
    if task and not task.done():
        task.cancel()
