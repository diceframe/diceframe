from __future__ import annotations

import json

import pytest

from src.engine.game_instance import GameInstance
from src.webui.routes.game_query_routes import api_gameplay_action, api_gameplay_slot, api_gameplay_view


class FakeAPI:
    def __init__(self, result):
        self.instance = GameInstance(game_key=("web", "room", "main"), run_id="run-1")
        self.instance.gm_uid = "gm"
        self.instance.players["player"] = {"user_id": "player", "character_name": "Player"}
        self.result = result
        self.calls = []
        self.action_calls = []
        self.slot_calls = []

    def get_game_instance(self, _key):
        return self.instance

    def gameplay_view(self, game_key, viewer_uid, viewer_is_gm):
        self.calls.append((game_key, viewer_uid, viewer_is_gm))
        return self.result

    async def gameplay_action(self, game_key, viewer_uid, viewer_is_gm, action, arguments):
        self.action_calls.append((game_key, viewer_uid, viewer_is_gm, action, arguments))
        return {"ok": True, "result": {"accepted": True}}

    def gameplay_slot(self, game_key, viewer_uid, viewer_is_gm, slot):
        self.slot_calls.append((game_key, viewer_uid, viewer_is_gm, slot))
        return {"ok": True, "slot": slot, "view": {"ready": True}}


class FakeRequest:
    def __init__(self, api, *, user_id="player"):
        self.app = {"api": api}
        self.match_info = {"game_key": "web|room|main"}
        self.values = {"user_id": user_id}

    def get(self, key, default=None):
        return self.values.get(key, default)


def payload(response):
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_gameplay_view_route_returns_owner_projection():
    api = FakeAPI({"ok": True, "mode_id": "board", "read_only": False, "view": {"slots": []}})
    response = await api_gameplay_view(FakeRequest(api))
    assert response.status == 200
    assert payload(response)["view"]["slots"] == []
    assert api.calls == [("web|room|main", "player", False)]


@pytest.mark.asyncio
async def test_gameplay_view_route_rejects_non_member_before_owner_call():
    api = FakeAPI({"ok": True})
    response = await api_gameplay_view(FakeRequest(api, user_id="outsider"))
    assert response.status == 403
    assert payload(response)["code"] == "GAME_MEMBER_REQUIRED"
    assert api.calls == []


@pytest.mark.asyncio
async def test_gameplay_view_route_surfaces_unknown_mode_as_recoverable_conflict():
    api = FakeAPI({"ok": False, "code": "UNSUPPORTED_GAMEPLAY", "read_only": True})
    response = await api_gameplay_view(FakeRequest(api))
    assert response.status == 409
    assert payload(response)["read_only"] is True


@pytest.mark.asyncio
async def test_gameplay_action_route_uses_member_identity_and_owner_handler():
    api = FakeAPI({"ok": True})
    request = FakeRequest(api)
    request.match_info["action"] = "cards.play"
    request.json = lambda: None
    async def body():
        return {"card_id": "c1"}
    request.json = body
    request.content_length = 20
    response = await api_gameplay_action(request)
    assert response.status == 200
    assert api.action_calls == [("web|room|main", "player", False, "cards.play", {"card_id": "c1"})]


@pytest.mark.asyncio
async def test_gameplay_slot_route_uses_member_identity_and_rejects_unknown_slot():
    api = FakeAPI({"ok": True})
    request = FakeRequest(api)
    request.match_info["slot"] = "character"
    response = await api_gameplay_slot(request)
    assert response.status == 200
    assert payload(response)["slot"] == "character"
    assert api.slot_calls == [("web|room|main", "player", False, "character")]
    request.match_info["slot"] = "settings"
    response = await api_gameplay_slot(request)
    assert response.status == 400
    assert payload(response)["code"] == "INVALID_GAMEPLAY_SLOT"
