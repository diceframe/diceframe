from __future__ import annotations

import json

import pytest

from src.webui.routes import plugins
from src.engine.game_instance import GameInstance


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.instance = GameInstance(game_key=("web", "room", "main"), run_id="run-1")
        self.instance.gm_uid = "gm"
        self.instance.players["player"] = {"user_id": "player", "character_name": "Player"}

    def list_plugin_rulebooks(self, plugin_id: str, *, viewer_is_gm: bool = False):
        self.calls.append(("list", plugin_id, viewer_is_gm))
        return {"ok": True, "plugin_id": plugin_id, "rulebooks": []}

    def read_plugin_rulebook(self, plugin_id: str, book_id: str, *, viewer_is_gm: bool = False):
        self.calls.append(("read", plugin_id, book_id, viewer_is_gm))
        return {"ok": True, "plugin_id": plugin_id, "book_id": book_id, "content": "ok"}

    def get_game_instance(self, _game_key):
        return self.instance


class FakeRequest:
    def __init__(self, api: FakeApi, match_info: dict[str, str], *, is_gm: bool = False):
        self.app = {"api": api}
        self.match_info = match_info
        self._values = {"plugin_viewer_is_gm": is_gm}

    def get(self, key: str, default=None):
        return self._values.get(key, default)


def response_json(response):
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_rulebook_list_uses_trusted_viewer_context_only():
    api = FakeApi()
    response = await plugins.api_plugin_rulebooks(
        FakeRequest(api, {"plugin_id": "demo"}, is_gm=True)
    )
    assert response.status == 200
    assert response_json(response)["plugin_id"] == "demo"
    assert api.calls == [("list", "demo", True)]


@pytest.mark.asyncio
async def test_rulebook_read_does_not_accept_query_parameter_as_gm():
    api = FakeApi()
    request = FakeRequest(api, {"plugin_id": "demo", "book_id": "secret"})
    request.query = {"gm": "true"}
    response = await plugins.api_plugin_rulebook(request)
    assert response.status == 200
    assert api.calls == [("read", "demo", "secret", False)]


@pytest.mark.asyncio
async def test_game_scoped_rulebook_route_derives_gm_from_membership():
    api = FakeApi()
    request = FakeRequest(
        api, {"game_key": "web|room|main", "plugin_id": "demo"}, is_gm=False,
    )
    request._values["user_id"] = "gm"
    response = await plugins.api_game_plugin_rulebooks(request)
    assert response.status == 200
    assert api.calls == [("list", "demo", True)]
