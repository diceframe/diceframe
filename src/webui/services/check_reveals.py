"""Shared click-to-reveal markers for resolved checks.

Presentation-only companion of the checks module: the roll result is already
authoritative and public, the reveal marker only records the shared ritual.
The reveal intent carries a check id, never a dice value; the actor of the
check and the GM may reveal, and a repeat click is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.engine.modules import check_reveals, checks
from src.localization.messages import t

GameKey = tuple[str, ...]


@dataclass(frozen=True)
class CheckRevealDependencies:
    parse_game_key: Callable[[str], GameKey]
    get_instance: Callable[[GameKey], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]


def _find_check(instance: Any, check_id: str) -> dict[str, Any] | None:
    """Locate one resolved check by id in current-round or timeline records."""
    for check in checks.last_checks(instance) or []:
        if isinstance(check, dict) and str(check.get("check_id") or "") == check_id:
            return check
    for entry in reversed(instance.log or []):
        if not isinstance(entry, dict):
            continue
        results = entry.get("check_results")
        if not isinstance(results, list):
            continue
        for check in results:
            if isinstance(check, dict) and str(check.get("check_id") or "") == check_id:
                return check
    return None


class CheckRevealService:
    def __init__(self, deps: CheckRevealDependencies):
        self.d = deps

    async def reveal(self, game_key: str, check_id: str, uid: str) -> dict[str, Any]:
        key = self.d.parse_game_key(game_key)
        inst = self.d.get_instance(key)
        if not inst:
            return {"ok": False, "error": "游戏不存在", "status": 404}
        uid = str(uid or "")
        if not uid:
            return {"ok": False, "error": t("check_reveals.not_logged_in"), "status": 403}
        check_id = str(check_id or "")
        async with inst.authoritative_write() as entered:
            if not entered:
                return {"ok": False, "error_code": "REWRITE_IN_PROGRESS", "error": t("check_reveals.rewriting_wait_and_retry"), "status": 409}
            if self.d.get_instance(key) is not inst:
                return {"ok": False, "error_code": "STALE_RUN", "error": t("check_reveals.game_restarted_refresh_and_retry"), "status": 409}
            assert inst is not None
            check = _find_check(inst, check_id)
            if check is None:
                return {"ok": False, "error": t("check_reveals.check_missing"), "status": 404}
            if uid != str(inst.gm_uid) and uid != str(check.get("actor_uid") or ""):
                return {"ok": False, "error": t("check_reveals.actor_or_gm_only"), "status": 403}
            existing = check_reveals.reveal_record(inst, check_id)
            already = existing is not None
            if not already:
                existing = check_reveals.mark_revealed(inst, check_id, uid)
                await self.d.save_instance(inst)
        return {"ok": True, "check_id": check_id, "reveal": existing, "already": already}
