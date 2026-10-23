from __future__ import annotations

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import adventure_runtime_state
from src.gameplays import builtin as gameplay_builtin
from src.gameplays.contracts import BOT_SURFACE_NONE, GameplayMode, GameplayRegistry
from src.webui.services import bot_access


class FakeRegistry:
    def __init__(self, inst: GameInstance) -> None:
        self.inst = inst
        self.saved = 0

    def get(self, key):
        return self.inst if key == self.inst.game_key else None

    async def save(self, inst):
        assert inst is self.inst
        self.saved += 1


def _service(inst: GameInstance) -> bot_access.BotAccessService:
    return bot_access.BotAccessService(
        bot_access.BotAccessDependencies(
            registry=FakeRegistry(inst),
            parse_game_key=lambda game_key: tuple(game_key.split("|")),
        )
    )


async def _bind_token(service: bot_access.BotAccessService, game_key: str) -> str:
    created = await service.get_bind_token(game_key)
    assert created["ok"] is True
    return created["bind_token"]


def test_derivation_keeps_known_gal_mode() -> None:
    assert adventure_runtime_state.derive_play_mode("gal", None) == "gal"
    assert adventure_runtime_state.derive_play_mode("gal", {"adventure_id": "core:x"}) == "gal"


@pytest.mark.asyncio
async def test_full_surface_modes_bind_as_before():
    inst = GameInstance(game_key=("web", "room", "bot"), gm_uid="gm")
    service = _service(inst)
    token = await _bind_token(service, "web|room|bot")

    bound = await service.verify_bind_game("web|room|bot", token)

    assert bound["ok"] is True
    assert bound["bot_surface"] == "full"


@pytest.mark.asyncio
async def test_read_only_surface_binds_but_declares_surface():
    inst = GameInstance(game_key=("web", "room", "bot"), gm_uid="gm")
    adventure_runtime_state.replace_play_mode(inst, "gal")
    service = _service(inst)
    token = await _bind_token(service, "web|room|bot")

    bound = await service.verify_bind_game("web|room|bot", token)

    assert bound["ok"] is True
    assert bound["bot_surface"] == "read_only"


@pytest.mark.asyncio
async def test_none_surface_refuses_binding(monkeypatch):
    inst = GameInstance(game_key=("web", "room", "bot"), gm_uid="gm")
    monkeypatch.setattr(gameplay_builtin, "DEFAULT_GAMEPLAYS", GameplayRegistry([
        GameplayMode("free", BOT_SURFACE_NONE, lambda _request: None, "narrative_round"),
    ]))
    service = _service(inst)
    token = await _bind_token(service, "web|room|bot")

    refused = await service.verify_bind_game("web|room|bot", token)

    assert refused["ok"] is False
    assert "玩法模式" in refused["error"]
