"""Canonical participant identity projection for read-side requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ViewerKind = Literal["gm", "seat", "outsider"]

__all__ = ["Viewer", "ViewerKind", "resolve_viewer"]


@dataclass(frozen=True)
class Viewer:
    """The server-derived read identity, never a client-supplied role."""

    kind: ViewerKind
    uid: str = ""

    @property
    def is_gm(self) -> bool:
        return self.kind == "gm"

    @property
    def user_id(self) -> str:
        return self.uid

    @property
    def is_seat(self) -> bool:
        return self.kind == "seat"

    @property
    def is_member(self) -> bool:
        return self.kind in {"gm", "seat"}


def resolve_viewer(
    instance: Any,
    *,
    user_id: str = "",
    owner_authenticated: bool = False,
    player_preview: bool = False,
) -> Viewer:
    """Resolve a request to GM, a player seat, or an outsider.

    Preview is deliberately evaluated first: an owner viewing a player seat
    must receive that seat's projection and can never regain GM visibility.
    """

    if instance is None:
        return Viewer("outsider", str(user_id or "").strip())
    uid = str(user_id or "").strip()
    players = getattr(instance, "players", {}) or {}
    if player_preview:
        return Viewer("seat", uid) if uid and uid in players else Viewer("outsider", uid)
    if uid and uid == str(getattr(instance, "gm_uid", "") or ""):
        return Viewer("gm", uid)
    if owner_authenticated:
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        return Viewer("gm", gm_uid or uid)
    if uid and uid in players:
        return Viewer("seat", uid)
    return Viewer("outsider", uid)
