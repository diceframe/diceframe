"""Turn dispatch by gameplay mode (ADR-0012 §2, track P0.5c)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.commands import game_handler as game_handler_module
from src.commands.game_handler import GameHandler
from src.commands.round_processor import RoundNotProcessed
from src.engine.game_instance import GameInstance
from src.engine.modules import adventure_runtime_state
from src.gameplays.builtin import DEFAULT_GAMEPLAYS
from src.gameplays.contracts import (
    TURN_DRIVER_NARRATIVE_ROUND, GameplayMode, GameplayRegistry, TurnDriverRegistry,
)


class FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[GameInstance, object, object]] = []

    async def process_round(self, instance, *, on_delta=None, on_reset=None):
        self.calls.append((instance, on_delta, on_reset))
        return "narration", None


def _handler(driver: FakeDriver, completed: list[GameInstance]) -> SimpleNamespace:
    drivers = TurnDriverRegistry()
    drivers.bind(TURN_DRIVER_NARRATIVE_ROUND, driver)
    return SimpleNamespace(_turn_drivers=drivers, _after_round_completed=completed.append)


def test_every_builtin_mode_resolves_to_a_bound_driver() -> None:
    driver = FakeDriver()
    drivers = TurnDriverRegistry()
    drivers.bind(TURN_DRIVER_NARRATIVE_ROUND, driver)
    for mode_id in DEFAULT_GAMEPLAYS.mode_ids():
        assert drivers.for_mode(DEFAULT_GAMEPLAYS, mode_id) is driver
    assert drivers.for_mode(DEFAULT_GAMEPLAYS, "werewolf") is None
    assert TurnDriverRegistry().for_mode(DEFAULT_GAMEPLAYS, "free") is None


def test_peek_play_mode_does_not_materialize_the_slot() -> None:
    inst = GameInstance(game_key=("web", "room", "bot"), gm_uid="gm")
    inst.modules.pop(adventure_runtime_state.MODULE_NAME, None)
    assert adventure_runtime_state.peek_play_mode(inst) == "free"
    assert adventure_runtime_state.MODULE_NAME not in inst.modules
    inst.modules[adventure_runtime_state.MODULE_NAME] = {"schema_version": 99, "play_mode": "gal"}
    assert adventure_runtime_state.peek_play_mode(inst) == "free"
    assert inst.modules[adventure_runtime_state.MODULE_NAME] == {"schema_version": 99, "play_mode": "gal"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["free", "gal", "GAL"])
async def test_process_round_dispatches_to_the_mode_driver(mode: str) -> None:
    inst = GameInstance(game_key=("web", "room", "bot"), gm_uid="gm")
    adventure_runtime_state.replace_play_mode(inst, mode)
    driver, completed = FakeDriver(), []
    handler = _handler(driver, completed)

    result = await GameHandler.process_round(handler, inst, on_delta="d", on_reset="r")

    assert result == ("narration", None)
    assert driver.calls == [(inst, "d", "r")]
    assert completed == [inst]


@pytest.mark.asyncio
async def test_process_round_fails_closed_without_a_driver(monkeypatch) -> None:
    inst = GameInstance(game_key=("web", "room", "bot"), gm_uid="gm")
    monkeypatch.setattr(game_handler_module, "DEFAULT_GAMEPLAYS", GameplayRegistry([
        GameplayMode("free", "full", lambda _request: None, "calendar"),
    ]))
    driver, completed = FakeDriver(), []

    with pytest.raises(RoundNotProcessed) as exc:
        await GameHandler.process_round(_handler(driver, completed), inst)

    assert exc.value.reason == "unsupported_gameplay"
    assert driver.calls == [] and completed == []
