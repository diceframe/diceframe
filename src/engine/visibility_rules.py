"""Seat visibility predicates for economy proposals and manual rolls (Track R6-a4).

The single source of truth for "may this viewer see this private record".
Pure; must not import webui, commands, rulesets or game_instance.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _uid(value: Any) -> str:
    return str(value or "")


def proposal_visible_to(proposal: Mapping[str, Any], *, viewer_uid: str, viewer_is_gm: bool) -> bool:
    if viewer_is_gm:
        return True
    if proposal.get("visibility") == "party":
        return True
    viewer = _uid(viewer_uid)
    if not viewer:
        return False
    stakeholders = {
        _uid(proposal.get("payer_uid")),
        _uid(proposal.get("uid")),
        _uid(proposal.get("recipient_uid")),
    }
    for item in proposal.get("contributors") or []:
        if isinstance(item, Mapping):
            stakeholders.add(_uid(item.get("uid")))
    stakeholders.discard("")
    return viewer in stakeholders


def manual_roll_visible_to(request: Mapping[str, Any], *, viewer_uid: str, viewer_is_gm: bool) -> bool:
    if viewer_is_gm:
        return True
    if str(request.get("visibility") or "party") != "private":
        return True
    viewer = _uid(viewer_uid)
    if not viewer:
        return False
    allowed = {_uid(request.get("created_by"))}
    allowed.update(_uid(uid) for uid in request.get("target_uids") or [])
    allowed.discard("")
    return viewer in allowed


__all__ = ["proposal_visible_to", "manual_roll_visible_to"]
