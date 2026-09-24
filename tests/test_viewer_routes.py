"""Track R6-a real HTTP regressions for participant viewer isolation."""

from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer
import pytest

from src.webui.session import SessionManager, session_middleware
from test_game_query_routes_http import (
    _make_app,
    _make_game,
    _owner,
    _owner_password,  # noqa: F401
    play_env,  # noqa: F401
)


@pytest.fixture
def viewer_env(play_env, tmp_path):
    game_key, instance = _make_game(play_env, "viewer")
    instance.players["p2"] = {"character_name": "乙", "character_sheet": {}}
    instance.private_log = {
        "p1": [{"round": 1, "text": "A private", "source": "gm"}],
        "p2": [{"round": 1, "text": "B private", "source": "gm"}],
    }
    instance.log = [{
        "round": 1, "gm_response": "Public narration",
        "actions": [
            {"user_id": "p1", "text": "Look around"},
            {"user_id": "system", "text": "[GM Directive] secret"},
        ],
    }]
    app = _make_app(play_env)
    sessions = SessionManager(tmp_path / "sessions")
    token, _ = sessions.get_or_create(None)
    sessions.rebind(token, instance.gm_uid)
    app["session_manager"] = sessions
    app.middlewares.insert(0, session_middleware)
    return app, game_key, instance, {**_owner(), "Cookie": f"trpg_session={token}"}


@pytest.mark.asyncio
async def test_l1_owner_preview_private_log_only_returns_own_messages(viewer_env):
    app, key, _, headers = viewer_env
    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/games/{key}/private-log?user=p1&share=1", headers=headers)
        body = await response.json()
    assert response.status == 200
    assert [message["text"] for message in body["messages"]] == ["A private"]


@pytest.mark.asyncio
async def test_l2_owner_delegate_log_filters_gm_directive(viewer_env):
    app, key, instance, headers = viewer_env
    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/games/{key}/log?user=p1&share=1&delegate=1", headers=headers)
        body = await response.json()
    assert response.status == 200
    assert body["log"][0]["actions"] == [instance.log[0]["actions"][0]]


@pytest.mark.asyncio
async def test_l3_owner_preview_detail_hides_gm_style(viewer_env):
    app, key, _, headers = viewer_env
    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/games/{key}?user=p1&share=1", headers=headers)
        body = await response.json()
    assert response.status == 200
    assert body["gm_style_override"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [True, False], ids=["owner", "shared-player"])
async def test_existing_owner_and_shared_player_read_projections(viewer_env, play_env, owner):
    app, key, instance, headers = viewer_env
    suffix = "" if owner else "?user=p1&share=1"
    uid = instance.gm_uid if owner else "p1"
    expected = {
        "": play_env.api.game_detail(key, uid, viewer_is_gm=owner),
        "/log": play_env.api.get_log(key, 1, 50, owner),
        "/private-log": (play_env.api.private_log(key) if owner else play_env.api.private_log_for_user(key, uid)),
        "/table-talk": play_env.api.table_talk(key),
        "/adventure": play_env.api.game_adventure_projection(key, viewer_is_gm=owner),
    }
    async with TestClient(TestServer(app)) as client:
        for path, payload in expected.items():
            response = await client.get(f"/api/games/{key}{path}{suffix}", headers=headers if owner else {})
            assert response.status == 200
            assert await response.json() == payload


@pytest.mark.asyncio
async def test_owner_preview_adventure_hides_secret_nodes(viewer_env):
    app, key, _, headers = viewer_env
    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/games/{key}/adventure?user=p1&share=1", headers=headers)
        body = await response.json()
    assert response.status == 200
    assert {node["id"] for node in body["adventure"]["projection"]["nodes"]} == {"gate"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["private-log", "table-talk"])
async def test_owner_preview_outsider_cannot_read_member_channels(viewer_env, path):
    app, key, _, headers = viewer_env
    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/games/{key}/{path}?user=outsider&share=1", headers=headers)
        body = await response.json()
    assert response.status == 403
    assert body["error"] == "未加入本局"
