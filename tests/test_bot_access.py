from __future__ import annotations

import pytest

from src.engine.game_instance import GameInstance
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


class FakeAPI:
    def __init__(self, inst: GameInstance) -> None:
        self._reg = FakeRegistry(inst)
        self.bot_access = bot_access.BotAccessService(
            bot_access.BotAccessDependencies(
                registry=self._reg,
                parse_game_key=lambda game_key: tuple(game_key.split("|")),
            )
        )


@pytest.mark.asyncio
async def test_bind_token_is_persisted_but_not_exposed_in_multiplayer_status():
    inst = GameInstance(game_key=("web", "room", "bot"), gm_uid="gm")
    api = FakeAPI(inst)

    created = await api.bot_access.get_bind_token("web|room|bot")

    assert created["ok"] is True
    assert len(created["bind_token"]) >= 18
    assert api._reg.saved == 1
    assert "bot_bind_token" not in inst.multiplayer_status()
    persisted = inst.to_dict()
    room = (persisted.get("modules") or {}).get("room_access")
    stored = room["bot_bind_token"] if isinstance(room, dict) and "bot_bind_token" in room else persisted["bot_bind_token"]
    assert stored == created["bind_token"]


@pytest.mark.asyncio
async def test_bind_verification_and_actor_authorization():
    inst = GameInstance(game_key=("web", "room", "bot"), gm_uid="gm")
    inst.language = "en"
    inst.players = {"gm": {"character_name": "GM"}, "player-1": {"character_name": "玩家"}}
    api = FakeAPI(inst)
    token = (await api.bot_access.get_bind_token("web|room|bot"))["bind_token"]

    assert (await api.bot_access.verify_bind_game("web|room|bot", "wrong"))["ok"] is False
    bound = await api.bot_access.verify_bind_game("web|room|bot", token)
    assert bound["gm_uid"] == "gm"
    assert bound["language"] == "en"
    assert inst.bot_bind_token == ""
    assert api._reg.saved == 2
    assert (await api.bot_access.verify_bind_game("web|room|bot", token))["ok"] is False
    assert api.bot_access.actor_allowed("web|room|bot", "player-1") is True
    assert api.bot_access.actor_allowed("web|room|bot", "stranger") is False


@pytest.mark.asyncio
async def test_rotating_bind_token_invalidates_previous_token():
    inst = GameInstance(game_key=("web", "room", "bot"), gm_uid="gm")
    api = FakeAPI(inst)
    old = (await api.bot_access.get_bind_token("web|room|bot"))["bind_token"]
    new = (await api.bot_access.get_bind_token("web|room|bot", rotate=True))["bind_token"]

    assert old != new
    assert (await api.bot_access.verify_bind_game("web|room|bot", old))["ok"] is False
    assert (await api.bot_access.verify_bind_game("web|room|bot", new))["ok"] is True
