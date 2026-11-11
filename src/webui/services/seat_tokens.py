"""Per-seat share credentials: issuing tokens to the right holder.

The ``room_access`` module owns storage (hash only) and verification; this
service decides *who* may receive a seat's plaintext token:

- the GM / owner, for a takeover link (re-issuing rotates the credential, so
  older links of that seat stop working);
- a session already bound to a non-GM seat that has no credential yet (the
  one-time migration of players who joined before seat tokens existed).

The plaintext is returned once and never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.engine.modules import room_access
from src.localization.messages import t

GameKey = tuple[str, ...]


@dataclass(frozen=True)
class SeatTokenDependencies:
    parse_game_key: Callable[[str], GameKey]
    get_instance: Callable[[GameKey], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]


class SeatTokenService:
    def __init__(self, deps: SeatTokenDependencies):
        self.d = deps

    async def issue_for_gm(
        self, game_key: str, uid: str, *, requester_uid: str, owner: bool, rotate: bool = False,
        bound_sessions: int = 0, check_token: str = "",
    ) -> dict[str, Any]:
        """Issue a seat's token for the GM's takeover link.

        A seat that already holds a credential is only re-issued with
        ``rotate`` (it logs the seat's current devices out), so a plain click
        never silently cuts a player off.  The same holds for a seat without a
        credential that devices are still bound to (pre-token players): the
        first link logs them out too, so it also needs ``rotate``.

        ``check_token``: the link the GM already holds. If it is still the
        seat's credential it is returned unchanged (nothing rotates); a stale
        one falls through to the normal rules.
        """
        key = self.d.parse_game_key(game_key)
        inst = self.d.get_instance(key)
        if not inst:
            return {"ok": False, "error": "游戏不存在", "status": 404}
        uid = str(uid or "")
        # Credential delivery stays possible on a frozen or ended run: players
        # still need to reach their settled characters and the archive.
        async with inst.authoritative_write(administrative=True) as entered:
            if not entered:
                return {"ok": False, "error_code": "REWRITE_IN_PROGRESS", "error": t("seat_tokens.rewriting_wait_and_retry"), "status": 409}
            if self.d.get_instance(key) is not inst:
                return {"ok": False, "error_code": "STALE_RUN", "error": t("seat_tokens.game_restarted_refresh_and_retry"), "status": 409}
            gm_uid = str(inst.gm_uid or "")
            if not owner and (not requester_uid or requester_uid != gm_uid):
                return {"ok": False, "error": t("seat_tokens.seat_link_gm_only"), "status": 403}
            if uid not in inst.players:
                return {"ok": False, "error": t("seat_tokens.seat_missing"), "status": 404}
            if uid == gm_uid:
                return {"ok": False, "error_code": "GM_SEAT_REQUIRES_OWNER", "error": t("seat_tokens.gm_seat_not_shareable"), "status": 400}
            if check_token and room_access.verify_seat_token(inst, check_token) == uid:
                return {"ok": True, "user_id": uid, "seat_token": check_token, "reused": True}
            has_link = room_access.has_seat_token(inst, uid)
            if (has_link or bound_sessions > 0) and not rotate:
                return {
                    "ok": False, "error_code": "SEAT_TOKEN_EXISTS",
                    "error": t("seat_tokens.seat_in_use"),
                    "has_link": has_link, "bound_sessions": int(bound_sessions),
                    "status": 409,
                }
            token = room_access.issue_seat_token(inst, uid)
            await self.d.save_instance(inst)
        return {"ok": True, "user_id": uid, "seat_token": token}

    async def claim(self, game_key: str, *, session_uid: str) -> dict[str, Any]:
        """Give a session already bound to a seat that seat's first token."""
        key = self.d.parse_game_key(game_key)
        inst = self.d.get_instance(key)
        if not inst:
            return {"ok": False, "error": "游戏不存在", "status": 404}
        session_uid = str(session_uid or "")
        async with inst.authoritative_write(administrative=True) as entered:
            if not entered:
                return {"ok": False, "error_code": "REWRITE_IN_PROGRESS", "error": t("seat_tokens.rewriting_wait_and_retry"), "status": 409}
            if self.d.get_instance(key) is not inst:
                return {"ok": False, "error_code": "STALE_RUN", "error": t("seat_tokens.game_restarted_refresh_and_retry"), "status": 409}
            if not session_uid or session_uid not in inst.players:
                return {"ok": False, "error_code": "SEAT_NOT_BOUND", "error": t("seat_tokens.session_without_seat"), "status": 403}
            if session_uid == str(inst.gm_uid or ""):
                return {"ok": False, "error_code": "GM_SEAT_REQUIRES_OWNER", "error": t("seat_tokens.gm_seat_requires_host_login"), "status": 403}
            # Never rotate an existing credential from a session: that would
            # silently cut off the seat's other devices.  Those use the link.
            if room_access.has_seat_token(inst, session_uid):
                return {"ok": False, "error_code": "SEAT_TOKEN_EXISTS", "error": t("seat_tokens.seat_has_credential"), "status": 409}
            token = room_access.issue_seat_token(inst, session_uid)
            await self.d.save_instance(inst)
        return {"ok": True, "user_id": session_uid, "seat_token": token}
