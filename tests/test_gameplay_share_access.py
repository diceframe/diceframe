"""Registered gameplay/rulebook routes through the real password middleware."""

from unittest.mock import AsyncMock, Mock

from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
import pytest

from src.engine.modules import room_access
from src.webui.access_control import WebAccessControl
from src.webui.routes.plugins import register_plugins
from test_game_query_routes_http import (
    _make_app, _make_game, _owner_password, play_env, ROOM_HEADER,  # noqa: F401
)


ENDPOINTS = [
    ("GET", "gameplay-view", "gameplay_view", ()),
    ("POST", "gameplay-actions/choose", "gameplay_action", ("choose", {})),
    ("GET", "gameplay-slots/character", "gameplay_slot", ("character",)),
    ("GET", "plugins/pack-a/rulebooks", "list_plugin_rulebooks", ()),
    ("GET", "plugins/pack-a/rulebooks/player", "read_plugin_rulebook", ()),
]


@pytest.mark.parametrize("method,tail,kind", [
    *[(method, tail, "seat") for method, tail, *_ in ENDPOINTS],
    ("POST", "gameplay-view", ""),
    ("GET", "gameplay-view/extra", ""),
    ("GET", "gameplay-actions/choose", ""),
    ("POST", "gameplay-actions/choose/extra", ""),
    ("POST", "gameplay-slots/character", ""),
    ("GET", "gameplay-slots/character/extra", ""),
    ("POST", "plugins/pack-a/rulebooks", ""),
    ("GET", "plugins/pack-a/config", ""),
    ("GET", "plugins/pack-a/rulebooks/player/extra", ""),
])
def test_share_classifies_only_exact_player_operations(method, tail, kind):
    assert WebAccessControl.share_endpoint_kind(
        make_mocked_request(method, f"/api/games/key/{tail}"),
    ) == kind


@pytest.mark.asyncio
@pytest.mark.parametrize("method,tail,service,args", ENDPOINTS)
async def test_password_protected_player_endpoints_keep_seat_room_and_owner_boundaries(
    play_env, monkeypatch, method, tail, service, args,
):
    key, instance = _make_game(play_env, "gameplay-share", bind_adventure=False, room_password="secret")
    other_key, _ = _make_game(play_env, "other-game", bind_adventure=False)
    callback = (AsyncMock if method == "POST" else Mock)(return_value={"ok": True})
    monkeypatch.setattr(play_env.api, service, callback)
    seat_token = room_access.issue_seat_token(instance, "p1")
    seat = {"X-Seat-Token": seat_token, **ROOM_HEADER}
    app = _make_app(play_env)
    register_plugins(app)
    async with TestClient(TestServer(app)) as client:
        async def request(path, headers):
            return await client.request(method, path, headers=headers, **({"json": {}} if method == "POST" else {}))

        path = f"/api/games/{key}/{tail}?user=gm_user&share=1"
        response = await request(path, ROOM_HEADER)
        assert response.status == 401
        response = await request(path, {"X-Seat-Token": "invalid", **ROOM_HEADER})
        assert response.status == 401
        response = await request(path, {"X-Seat-Token": seat_token})
        assert response.status == 403  # Seat identity cannot bypass the room password.
        callback.assert_not_called()
        response = await request(path, seat)
        assert response.status == 200, await response.text()
        if service in {"gameplay_view", "gameplay_action", "gameplay_slot"}:
            callback.assert_called_once_with(key, "p1", False, *args)
        elif service == "list_plugin_rulebooks":
            callback.assert_called_once_with("pack-a", viewer_is_gm=False)
        else:
            callback.assert_called_once_with("pack-a", "player", viewer_is_gm=False)
        callback.reset_mock()
        assert (await request(f"/api/games/{other_key}/{tail}?share=1", seat)).status == 401
        room_access.issue_seat_token(instance, "p1")  # Rotation revokes the old link.
        assert (await request(path, seat)).status == 401
        seat["X-Seat-Token"] = room_access.issue_seat_token(instance, "p1")
        instance.set_player_access(False)
        assert (await request(path, seat)).status == 403
        callback.assert_not_called()
