"""Real HTTP regressions for share identity and GM-seat isolation."""

from __future__ import annotations

from copy import deepcopy
import re
from types import SimpleNamespace
from uuid import UUID

from aiohttp.test_utils import TestClient, TestServer
import pytest

from src.engine.game_state import GameState
from src.webui.session import SessionManager, session_middleware
from test_game_query_routes_http import (
    ROOM_PASSWORD,
    ROOM_TOKEN,
    _make_app,
    _make_game,
    _owner,
    _owner_password,  # noqa: F401
    play_env,  # noqa: F401
)


@pytest.fixture
def share_env(play_env, tmp_path):
    key, instance = _make_game(
        play_env, "share-guard", bind_adventure=False, room_password=ROOM_PASSWORD,
    )
    instance.state = GameState.ACTIVE_ACTION
    instance.players[instance.gm_uid] = {"character_name": "GM", "character_sheet": {}}
    instance.players["p2"] = {"character_name": "乙", "character_sheet": {}}
    instance.private_log = {
        "p1": [{"round": 1, "text": "A private", "source": "gm"}],
        "p2": [{"round": 1, "text": "B private", "source": "gm"}],
    }
    sessions = SessionManager(tmp_path / "sessions")
    token, _ = sessions.get_or_create(None)
    sessions.rebind(token, "p1")
    # Existing sessions keep their identity, including a previously bound GM.
    gm_token = "existing-gm-session"
    sessions._sessions[gm_token] = {"user_id": instance.gm_uid, "name": ""}
    app = _make_app(play_env)
    app["session_manager"] = sessions
    app.middlewares.insert(0, session_middleware)
    return SimpleNamespace(
        app=app, key=key, instance=instance, sessions=sessions,
        token=token, gm_token=gm_token, api=play_env.api,
    )


def _cookie(token):
    return {"Cookie": f"trpg_session={token}"}


def _share_url(env, path, uid=None):
    url = f"/api/games/{env.key}/{path}?share=1&room_token={ROOM_TOKEN}"
    return url if uid is None else f"{url}&user={uid}"


def _assert_gm_denied(response, body):
    assert response.status == 403, body
    assert body == {
        "ok": False,
        "error_code": "GM_SEAT_REQUIRES_OWNER",
        "error": "GM 席位需要房主登录",
    }


@pytest.mark.asyncio
async def test_f2_share_query_cannot_read_gm_private_log(share_env):
    env = share_env
    async with TestClient(TestServer(env.app)) as client:
        response = await client.get(
            _share_url(env, "private-log", env.instance.gm_uid),
            headers=_cookie(env.token),
        )
        body = await response.json()
    _assert_gm_denied(response, body)


@pytest.mark.asyncio
async def test_f2_share_cookie_cannot_read_gm_private_log(share_env):
    env = share_env
    async with TestClient(TestServer(env.app)) as client:
        response = await client.get(
            _share_url(env, "private-log"), headers=_cookie(env.gm_token),
        )
        body = await response.json()
    _assert_gm_denied(response, body)


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_path", ["query", "cookie"])
async def test_share_gm_cannot_change_player_control(share_env, identity_path):
    env = share_env
    before = deepcopy(env.instance.players)
    uid = env.instance.gm_uid if identity_path == "query" else None
    token = env.token if identity_path == "query" else env.gm_token
    async with TestClient(TestServer(env.app)) as client:
        response = await client.post(
            _share_url(env, "players/p2/control", uid),
            headers=_cookie(token), json={"mode": "ai"},
        )
        body = await response.json()
    _assert_gm_denied(response, body)
    assert env.instance.players == before


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_path", ["query", "cookie"])
async def test_owner_gm_share_keeps_private_log_access(share_env, identity_path):
    env = share_env
    uid = env.instance.gm_uid if identity_path == "query" else None
    expected = env.api.private_log(env.key)
    async with TestClient(TestServer(env.app)) as client:
        response = await client.get(
            _share_url(env, "private-log", uid),
            headers={**_owner(), **_cookie(env.gm_token)},
        )
        body = await response.json()
    assert response.status == 200
    assert body == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("delegate", [False, True], ids=["preview", "delegate"])
async def test_owner_player_view_keeps_private_log_isolation(share_env, delegate):
    env = share_env
    url = _share_url(env, "private-log", "p1") + ("&delegate=1" if delegate else "")
    async with TestClient(TestServer(env.app)) as client:
        response = await client.get(
            url, headers={**_owner(), **_cookie(env.gm_token)},
        )
        body = await response.json()
    assert response.status == 200
    assert body == env.api.private_log_for_user(env.key, "p1")
    assert [message["text"] for message in body["messages"]] == ["A private"]


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_path", ["query", "cookie"])
async def test_regular_player_share_keeps_private_log_access(share_env, identity_path):
    env = share_env
    uid = "p1" if identity_path == "query" else None
    async with TestClient(TestServer(env.app)) as client:
        response = await client.get(
            _share_url(env, "private-log", uid), headers=_cookie(env.token),
        )
        body = await response.json()
    assert response.status == 200
    assert body == env.api.private_log_for_user(env.key, "p1")


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [False, True], ids=["shared-player", "owner"])
async def test_f4_join_gm_seat_does_not_rebind_session(share_env, owner):
    env = share_env
    original_uid = env.sessions._sessions[env.token]["user_id"]
    headers = {**_cookie(env.token), **(_owner() if owner else {})}
    async with TestClient(TestServer(env.app)) as client:
        response = await client.post(
            _share_url(env, "players"), headers=headers,
            json={"user_id": env.instance.gm_uid},
        )
        body = await response.json()
    assert response.status == 200
    assert body["ok"] is True
    assert body["user_id"] == env.instance.gm_uid
    assert body["reused"] is True
    assert env.sessions._sessions[env.token]["user_id"] == original_uid
    assert env.sessions._sessions[env.token]["user_id"] != env.instance.gm_uid
    reloaded = SessionManager(env.sessions._path.parent)
    assert reloaded.get_or_create(env.token) == (env.token, original_uid)


@pytest.mark.asyncio
async def test_regular_player_join_still_rebinds_session(share_env):
    env = share_env
    async with TestClient(TestServer(env.app)) as client:
        response = await client.post(
            _share_url(env, "players"), headers=_cookie(env.token),
            json={"user_id": "p2"},
        )
        body = await response.json()
    assert response.status == 200
    assert body["ok"] is True
    assert env.sessions._sessions[env.token]["user_id"] == "p2"


def test_new_sessions_have_distinct_eight_hex_user_ids(tmp_path):
    manager = SessionManager(tmp_path)
    first_token, first_uid = manager.get_or_create(None)
    second_token, second_uid = manager.get_or_create(None)
    assert first_token != second_token
    assert first_uid != second_uid
    for uid in (first_uid, second_uid):
        assert re.fullmatch(r"web_[0-9a-f]{8}", uid)


def test_new_session_retries_colliding_user_id(tmp_path, monkeypatch):
    manager = SessionManager(tmp_path)
    colliding = UUID("12345678" + "0" * 24)
    fresh = UUID("87654321" + "0" * 24)
    manager._sessions["existing-token"] = {
        "user_id": f"web_{colliding.hex[:8]}", "name": "",
    }
    generated = iter([colliding, fresh])
    monkeypatch.setattr("src.webui.session.uuid.uuid4", lambda: next(generated))

    token, uid = manager.get_or_create("new-token")
    assert (token, uid) == ("new-token", f"web_{fresh.hex[:8]}")
    assert manager.get_or_create("existing-token") == (
        "existing-token", f"web_{colliding.hex[:8]}",
    )
    assert SessionManager(tmp_path).get_or_create(token) == (token, uid)


def test_f5_new_session_uid_is_independent_of_client_token(tmp_path, monkeypatch):
    manager = SessionManager(tmp_path)
    # Fix server randomness so this regression cannot fail by random collision.
    monkeypatch.setattr("src.webui.session.uuid.uuid4", lambda: UUID("12345678" + "0" * 24))
    supplied_token = "deadbeef" + "0" * 24
    token, uid = manager.get_or_create(supplied_token)
    assert token == supplied_token
    assert uid != "web_deadbeef"
    assert uid == "web_12345678"
    assert manager.get_or_create(token) == (token, uid)
    assert SessionManager(tmp_path).get_or_create(token) == (token, uid)


def test_new_session_without_token_uses_separate_uid_randomness(tmp_path, monkeypatch):
    manager = SessionManager(tmp_path)
    generated = iter([UUID("deadbeef" + "0" * 24), UUID("12345678" + "0" * 24)])
    monkeypatch.setattr("src.webui.session.uuid.uuid4", lambda: next(generated))
    token, uid = manager.get_or_create(None)
    assert token == "deadbeef" + "0" * 24
    assert uid == "web_12345678"


def test_existing_session_keeps_legacy_uid(tmp_path, monkeypatch):
    manager = SessionManager(tmp_path)
    token = "deadbeef" + "0" * 24
    manager._sessions[token] = {"user_id": "web_deadbeef", "name": ""}

    def unexpected_randomness():
        pytest.fail("Existing sessions must not allocate a new identity")

    monkeypatch.setattr("src.webui.session.uuid.uuid4", unexpected_randomness)
    assert manager.get_or_create(token) == (token, "web_deadbeef")
