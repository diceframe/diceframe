"""Lobby-reachable data that must not cross games or the room password:
the character-card library, and the scene text in the visitor lobby view."""

from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer
import pytest
import pytest_asyncio

from src.engine.modules import narrative_notes
from src.engine.modules import room_access
from src.webui.routes.character_cards import register_character_cards as register_character_cards
from test_game_query_routes_http import (
    ROOM_HEADER,
    ROOM_TOKEN,
    _make_game,
    _owner,
    _owner_password,  # noqa: F401
    play_env,  # noqa: F401
)
from test_share_gm_seat_guard import CONFIRM, _cookie, share_env  # noqa: F401


def _names(body: dict) -> set[str]:
    return {str(card.get("character_name")) for card in body.get("cards", [])}


@pytest_asyncio.fixture
async def cards_env(share_env, play_env):
    env = share_env
    register_character_cards(env.app)
    # A card shipped by an installed plugin: meant for any table.
    assert env.api.save_character_card({
        "character_name": "Plugin Hero", "source_plugin": "starter-pack",
        "attributes": {"str": 12},
    })["ok"]
    # Someone joins another game: the join saves that character server-wide.
    other_key, _other = _make_game(play_env, "cards-other-game", bind_adventure=False)
    joined = await env.api.create_player(
        other_key, {"character_name": "Other Game Hero", "attributes": {"str": 11}},
        assign_new_id=True,
    )
    assert joined["ok"]
    return env


def _cards_url(env):
    return f"/api/games/{env.key}/character-cards?share=1"


@pytest.mark.asyncio
async def test_visitor_sees_only_plugin_cards(cards_env):
    env = cards_env
    stranger, _ = env.sessions.get_or_create(None)
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        response = await client.get(_cards_url(env), headers=_cookie(stranger))
        body = await response.json()
    assert response.status == 200
    assert _names(body) == {"Plugin Hero"}


@pytest.mark.asyncio
async def test_seated_player_cannot_read_cards_from_another_game(cards_env):
    env = cards_env
    seat = {"X-Seat-Token": room_access.issue_seat_token(env.instance, "p1")}
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        response = await client.get(_cards_url(env), headers=seat)
        body = await response.json()
    assert response.status == 200
    assert "Other Game Hero" not in _names(body)
    assert _names(body) == {"Plugin Hero"}


@pytest.mark.asyncio
async def test_owner_keeps_the_full_card_library(cards_env):
    env = cards_env
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        response = await client.get(f"/api/games/{env.key}/character-cards", headers=_owner())
        body = await response.json()
    assert {"Plugin Hero", "Other Game Hero"} <= _names(body)


@pytest.mark.asyncio
async def test_seated_player_cannot_adopt_a_card_it_cannot_list(cards_env):
    env = cards_env
    hidden = next(
        card for card in env.api.list_character_cards()["cards"]
        if card.get("character_name") == "Other Game Hero"
    )
    seat = {"X-Seat-Token": room_access.issue_seat_token(env.instance, "p1"), **CONFIRM}
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        response = await client.post(
            f"/api/games/{env.key}/character/p1/adopt-card?share=1",
            headers=seat, json={"card_id": hidden["id"]},
        )
        body = await response.json()
    assert response.status == 404, body
    assert body["error_code"] == "CARD_NOT_AVAILABLE"
    assert "Other Game Hero" not in str(body)


# ---- scene behind the room password -----------------------------------------


@pytest.mark.asyncio
async def test_password_room_lobby_hides_scene_without_room_token(share_env):
    env = share_env
    narrative_notes.replace_scene(env.instance, "The harbor at midnight")
    stranger, _ = env.sessions.get_or_create(None)
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        hidden = await (await client.get(
            f"/api/games/{env.key}?share=1", headers={**_cookie(stranger), "X-Room-Token": ""},
        )).json()
        wrong = await (await client.get(
            f"/api/games/{env.key}?share=1", headers={**_cookie(stranger), "X-Room-Token": "wrong"},
        )).json()
        shown = await (await client.get(
            f"/api/games/{env.key}?share=1", headers=_cookie(stranger),
        )).json()
    assert "scene" not in hidden and "scene" not in wrong
    assert hidden["has_room_password"] is True
    assert shown["scene"] == "The harbor at midnight"


@pytest.mark.asyncio
async def test_open_room_lobby_shows_scene(share_env, play_env):
    env = share_env
    open_key, open_game = _make_game(play_env, "open-room-scene", bind_adventure=False)
    narrative_notes.replace_scene(open_game, "Market square")
    stranger, _ = env.sessions.get_or_create(None)
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        body = await (await client.get(
            f"/api/games/{open_key}?share=1", headers=_cookie(stranger),
        )).json()
    assert body["viewer"] == {"kind": "outsider"}
    assert body["scene"] == "Market square"


@pytest.mark.asyncio
async def test_seated_player_and_owner_keep_scene(share_env):
    env = share_env
    narrative_notes.replace_scene(env.instance, "The harbor at midnight")
    seat = {"X-Seat-Token": room_access.issue_seat_token(env.instance, "p1")}
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        seated = await (await client.get(f"/api/games/{env.key}?share=1", headers=seat)).json()
        owner = await (await client.get(f"/api/games/{env.key}", headers=_owner())).json()
    assert seated["scene"] == owner["scene"] == "The harbor at midnight"


# ---- review of #468: plugin provenance cannot be forged from a table ---------


def _seat_put(env, uid="p1"):
    return {"X-Seat-Token": room_access.issue_seat_token(env.instance, uid), **CONFIRM}


def _library(env):
    return env.api.list_character_cards()["cards"]


@pytest.mark.asyncio
async def test_spoofed_plugin_marker_does_not_make_a_card_shareable(cards_env):
    env = cards_env
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        put = await client.put(
            f"/api/games/{env.key}/character/p1?share=1",
            headers=_seat_put(env),
            json={"character_name": "Forged Hero", "source_plugin": "starter-pack",
                  "plugin_content_id": "x", "background": "forged"},
        )
        assert put.status == 200, await put.json()
        listed = await (await client.get(_cards_url(env), headers=_seat_put(env, "p2"))).json()
    forged = [c for c in _library(env) if c.get("character_name") == "Forged Hero"]
    assert forged and all(not c.get("source_plugin") for c in forged)
    assert "Forged Hero" not in _names(listed)


@pytest.mark.asyncio
async def test_table_save_cannot_overwrite_a_plugin_card(cards_env):
    env = cards_env
    plugin_before = next(c for c in _library(env) if c.get("character_name") == "Plugin Hero")
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        # The GM editor (owner) may set attributes; the save is still a table save.
        put = await client.put(
            f"/api/games/{env.key}/character/p1",
            headers={**_owner(), **CONFIRM},
            # Same identity as the plugin card, plus its id and markers.
            json={**{k: plugin_before[k] for k in ("character_name", "race", "class", "background")},
                  "id": plugin_before["id"], "card_id": plugin_before["id"],
                  "source_plugin": "starter-pack", "attributes": {"str": 3}},
        )
        assert put.status == 200
    plugin_after = next(c for c in _library(env) if c.get("id") == plugin_before["id"])
    assert plugin_after["attributes"] == plugin_before["attributes"]
    assert plugin_after["source_plugin"] == "starter-pack"
    copies = [c for c in _library(env) if c.get("character_name") == "Plugin Hero"]
    assert len(copies) == 2  # the table's version is a separate, non-plugin card
    assert sum(1 for c in copies if c.get("source_plugin")) == 1


@pytest.mark.asyncio
async def test_plugin_card_survives_a_player_adopting_it(cards_env):
    """A classic-ruleset player adopts a library card by id; the server applies it."""
    env = cards_env
    plugin_card = next(c for c in _library(env) if c.get("character_name") == "Plugin Hero")
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        adopted = await client.post(
            f"/api/games/{env.key}/character/p1/adopt-card?share=1",
            headers=_seat_put(env), json={"card_id": plugin_card["id"]},
        )
        assert adopted.status == 200, await adopted.json()
    survivor = next(c for c in _library(env) if c.get("id") == plugin_card["id"])
    assert survivor["source_plugin"] == "starter-pack"
    for key in ("character_name", "race", "class", "background", "attributes", "skills"):
        assert survivor.get(key) == plugin_card.get(key), key
    assert env.instance.players["p1"]["character_name"] == "Plugin Hero"
    sheet = env.instance.players["p1"]["character_sheet"]
    assert sheet["attributes"] == plugin_card["attributes"]
    assert "source_plugin" not in sheet


@pytest.mark.asyncio
async def test_character_put_drops_unknown_keys(cards_env):
    env = cards_env
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        put = await client.put(
            f"/api/games/{env.key}/character/p1",
            headers={**_owner(), **CONFIRM},
            json={"attributes": {"str": 14}, "evil_key": 1, "source": "forged",
                  "card_id": "x", "deceased": True, "rule_binding": {"runtime_id": "x"}},
        )
        assert put.status == 200
    sheet = env.instance.players["p1"]["character_sheet"]
    assert sheet["attributes"] == {"str": 14}
    for key in ("evil_key", "source", "card_id", "rule_binding"):
        assert key not in sheet
    assert sheet.get("deceased") is not True


# ---- bot / plugin tokens are table participants; unknown means restricted -----


@pytest.mark.asyncio
async def test_bot_token_sees_only_plugin_cards(cards_env, monkeypatch):
    import web_server

    env = cards_env
    monkeypatch.setitem(web_server.STATE, "bot_token", "bot-secret")
    async with TestClient(TestServer(env.app), headers=ROOM_HEADER) as client:
        response = await client.get(
            f"/api/games/{env.key}/character-cards",
            headers={"X-Bot-Token": "bot-secret", "X-Bot-Actor": "p1"},
        )
        body = await response.json()
    assert response.status == 200, body
    assert _names(body) == {"Plugin Hero"}


def test_card_library_check_fails_closed_when_password_state_is_unknown():
    from src.webui.routes.character_cards import sees_full_card_library

    assert sees_full_card_library({}) is False
    assert sees_full_card_library({"owner_authenticated": True}) is True
    assert sees_full_card_library({"bot_authenticated": True}) is False
