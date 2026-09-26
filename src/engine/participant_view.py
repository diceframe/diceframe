"""Participant identity for read projections, independent of transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ViewerKind = Literal["gm", "seat", "outsider"]


@dataclass(frozen=True)
class Viewer:
    kind: ViewerKind
    uid: str = ""

    @property
    def is_gm(self) -> bool:
        return self.kind == "gm"

    @property
    def is_seat(self) -> bool:
        return self.kind == "seat"

    @property
    def is_member(self) -> bool:
        return self.kind in ("gm", "seat")


def resolve_viewer(
    instance: Any,
    *,
    user_id: str,
    owner_authenticated: bool,
    player_preview: bool,
) -> Viewer:
    """Resolve in priority order; preview never inherits owner GM authority."""
    uid = str(user_id or "")
    gm_uid = str(getattr(instance, "gm_uid", "") or "")
    players = getattr(instance, "players", {}) or {}
    if player_preview:
        return Viewer("seat", uid) if uid in players else Viewer("outsider", uid)
    if not uid and owner_authenticated:
        return Viewer("gm", gm_uid)
    if uid and uid == gm_uid:
        return Viewer("gm", uid)
    if uid and owner_authenticated:
        return Viewer("gm", uid)
    if uid in players:
        return Viewer("seat", uid)
    return Viewer("outsider", uid)


__all__ = ["Viewer", "ViewerKind", "resolve_viewer"]
