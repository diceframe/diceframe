"""Gameplay mode contracts.

A gameplay mode is what kind of game a run plays (the tabletop narrative game, a
visual novel, later others).  Each concrete mode lives in its own package under
``src/gameplays/`` and declares itself through this contract; generic code
only talks to the registry and never branches on a concrete mode id.

This module must not import concrete modes, the engine or the web layer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

BOT_SURFACE_FULL = "full"
BOT_SURFACE_READ_ONLY = "read_only"
BOT_SURFACE_NONE = "none"
BOT_SURFACES = frozenset({BOT_SURFACE_FULL, BOT_SURFACE_READ_ONLY, BOT_SURFACE_NONE})


@dataclass(frozen=True)
class GameplayCreateRequest:
    """The creation inputs a mode may constrain, already trimmed."""

    solo: bool
    rule_id: str
    adventure_id: str


@dataclass(frozen=True)
class GameplayCreateRejection:
    error: str
    error_code: str = ""

    def as_response(self) -> dict[str, Any]:
        response: dict[str, Any] = {"ok": False, "error": self.error}
        if self.error_code:
            response["error_code"] = self.error_code
        return response


CreateValidator = Callable[[GameplayCreateRequest], "GameplayCreateRejection | None"]


@dataclass(frozen=True)
class GameplayMode:
    """One registered gameplay mode.

    ``bot_surface`` is the transport capability (enforced only at the bind
    admission point); ``validate_create`` rejects creation inputs the mode
    cannot run with.
    """

    mode_id: str
    bot_surface: str
    validate_create: CreateValidator


class GameplayRegistry:
    def __init__(self, modes: Iterable[GameplayMode] = ()) -> None:
        self._modes: dict[str, GameplayMode] = {}
        for mode in modes:
            self.register(mode)

    def register(self, mode: GameplayMode) -> None:
        if not mode.mode_id or not mode.mode_id.isidentifier() or mode.mode_id != mode.mode_id.casefold():
            raise ValueError(f"invalid gameplay mode id: {mode.mode_id!r}")
        if mode.bot_surface not in BOT_SURFACES:
            raise ValueError(f"invalid bot surface for {mode.mode_id!r}: {mode.bot_surface!r}")
        if mode.mode_id in self._modes:
            raise ValueError(f"duplicate gameplay mode: {mode.mode_id!r}")
        self._modes[mode.mode_id] = mode

    def get(self, mode_id: Any) -> GameplayMode | None:
        return self._modes.get(str(mode_id or ""))

    def mode_ids(self) -> frozenset[str]:
        return frozenset(self._modes)

    def bot_surface(self, mode_id: Any) -> str:
        """Declared surface of a known mode; unknown modes fail closed."""
        mode = self.get(mode_id)
        return mode.bot_surface if mode is not None else BOT_SURFACE_NONE
