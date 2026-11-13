"""Gameplay mode contracts.

A gameplay mode is what kind of game a run plays (the tabletop narrative game, a
visual novel, later others).  Each concrete mode lives in its own package under
``src/gameplays/`` and declares itself through this contract; generic code
only talks to the registry and never branches on a concrete mode id.

This module must not import concrete modes, the engine or the web layer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
import inspect
from typing import Any, Protocol

BOT_SURFACE_FULL = "full"
BOT_SURFACE_READ_ONLY = "read_only"
BOT_SURFACE_NONE = "none"
BOT_SURFACES = frozenset({BOT_SURFACE_FULL, BOT_SURFACE_READ_ONLY, BOT_SURFACE_NONE})

# The tabletop narrative round pipeline (``RoundProcessor``).
TURN_DRIVER_NARRATIVE_ROUND = "narrative_round"


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
    cannot run with; ``turn_driver`` names the driver that advances its runs
    (drivers need runtime services, so they are bound by id at the handler).
    """

    mode_id: str
    bot_surface: str
    validate_create: CreateValidator
    turn_driver: str
    progression_mode: str = "narrative_round"


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
        if mode.progression_mode not in {"narrative_round", "scene"}:
            raise ValueError(
                f"invalid progression mode for {mode.mode_id!r}: {mode.progression_mode!r}"
            )
        if not mode.turn_driver:
            raise ValueError(f"gameplay mode {mode.mode_id!r} declares no turn driver")
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


class TurnDriver(Protocol):
    """Advances one run by one step and returns ``(narration, private messages)``."""

    def process_round(
        self, instance: Any, *, on_delta: Any = None, on_reset: Any = None,
    ) -> Awaitable[tuple[str, dict | None]]: ...


class TurnDriverRegistry:
    """Runtime-bound turn drivers, keyed by the id modes declare."""

    def __init__(self) -> None:
        self._drivers: dict[str, TurnDriver] = {}

    def bind(self, driver_id: str, driver: TurnDriver) -> None:
        if not driver_id:
            raise ValueError("turn driver id must not be empty")
        self._drivers[driver_id] = driver

    def for_mode(self, gameplays: GameplayRegistry, mode_id: Any) -> TurnDriver | None:
        """Driver of a known mode; unknown modes or unbound drivers fail closed."""
        mode = gameplays.get(mode_id)
        return self._drivers.get(mode.turn_driver) if mode is not None else None


@dataclass(frozen=True)
class GameplayProvider:
    """Owner-supplied gameplay bundle kept independent of TRPG mechanics."""

    mode: GameplayMode
    turn_driver: TurnDriver
    project_view: Callable[[Any, str, bool], dict[str, Any]]
    validate_instance: Callable[[Any], None] | None = None
    handle_action: Callable[[Any, str, bool, str, dict[str, Any]], Any] | None = None
    project_slot: Callable[[Any, str, bool, str], dict[str, Any]] | None = None


class GameplayProviderRegistry:
    """Register a gameplay mode and its driver/projection as one lifecycle unit.

    The registry deliberately does not know how a provider stores state or
    whether it uses a ruleset runtime.  Missing providers/drivers remain
    observable as ``None`` so callers can fail closed instead of entering the
    tabletop round pipeline by accident.
    """

    def __init__(self) -> None:
        self.gameplays = GameplayRegistry()
        self.drivers = TurnDriverRegistry()
        self._providers: dict[str, GameplayProvider] = {}

    def register(self, provider: GameplayProvider) -> None:
        if not isinstance(provider, GameplayProvider):
            raise ValueError("gameplay provider must use the explicit contract")
        mode_id = provider.mode.mode_id
        if mode_id in self._providers:
            raise ValueError(f"duplicate gameplay provider: {mode_id!r}")
        if not callable(provider.project_view):
            raise ValueError("gameplay provider must declare a view projection")
        self.gameplays.register(provider.mode)
        self.drivers.bind(provider.mode.turn_driver, provider.turn_driver)
        self._providers[mode_id] = provider

    def get(self, mode_id: Any) -> GameplayProvider | None:
        return self._providers.get(str(mode_id or ""))

    def driver_for(self, mode_id: Any) -> TurnDriver | None:
        return self.drivers.for_mode(self.gameplays, mode_id)

    def validate_instance(self, mode_id: Any, instance: Any) -> None:
        provider = self.get(mode_id)
        if provider is None:
            raise ValueError(f"gameplay provider is unavailable: {mode_id}")
        if provider.validate_instance is not None:
            provider.validate_instance(instance)

    def project_view(self, mode_id: Any, instance: Any, viewer_id: str, viewer_is_gm: bool = False) -> dict[str, Any]:
        provider = self.get(mode_id)
        if provider is None:
            raise ValueError(f"gameplay provider is unavailable: {mode_id}")
        result = provider.project_view(instance, viewer_id, viewer_is_gm)
        if not isinstance(result, dict):
            raise ValueError("gameplay provider projection must be an object")
        return result

    async def dispatch_action(
        self,
        mode_id: Any,
        instance: Any,
        viewer_id: str,
        viewer_is_gm: bool,
        action: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        provider = self.get(mode_id)
        if provider is None:
            raise ValueError(f"gameplay provider is unavailable: {mode_id}")
        handler = provider.handle_action
        if handler is None:
            raise ValueError("gameplay provider has no action handler")
        result = handler(instance, viewer_id, viewer_is_gm, action, arguments)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise ValueError("gameplay provider action result must be an object")
        return result

    def project_slot(
        self,
        mode_id: Any,
        instance: Any,
        viewer_id: str,
        viewer_is_gm: bool,
        slot: str,
    ) -> dict[str, Any]:
        provider = self.get(mode_id)
        if provider is None:
            raise ValueError(f"gameplay provider is unavailable: {mode_id}")
        if provider.project_slot is None:
            raise ValueError("gameplay provider has no slot projection")
        result = provider.project_slot(instance, viewer_id, viewer_is_gm, slot)
        if not isinstance(result, dict):
            raise ValueError("gameplay provider slot projection must be an object")
        return result


def project_gameplay_view(
    registry: GameplayProviderRegistry,
    mode_id: Any,
    instance: Any,
    viewer_id: str,
    *,
    viewer_is_gm: bool = False,
) -> dict[str, Any]:
    """Project a bound gameplay through its owner, failing closed.

    Kept beside the registry contract so generic web/orchestrator code does
    not import a concrete gameplay package or silently fall back to TRPG.
    """

    mode = str(mode_id or "")
    provider = registry.get(mode)
    if provider is None:
        return {
            "ok": False,
            "code": "UNSUPPORTED_GAMEPLAY",
            "mode_id": mode,
            "read_only": True,
            "diagnostic": "gameplay provider is unavailable",
        }
    try:
        registry.validate_instance(mode, instance)
        view = registry.project_view(mode, instance, viewer_id, viewer_is_gm)
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "code": "GAMEPLAY_VIEW_UNAVAILABLE",
            "mode_id": mode,
            "read_only": True,
            "diagnostic": str(exc),
        }
    return {
        "ok": True,
        "mode_id": mode,
        "read_only": False,
        "view": view,
    }
