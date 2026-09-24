"""Character roster projections must withhold all NPCs from non-GM viewers."""

from copy import deepcopy
import json

from aiohttp.test_utils import TestClient, TestServer
import pytest

from src.webui.services import characters
from test_viewer_routes import (
    _owner_password,  # noqa: F401
    play_env,  # noqa: F401
    viewer_env,  # noqa: F401
)


@pytest.fixture
def characters_env(viewer_env, play_env):
    app, key, instance, headers = viewer_env
    for player in instance.players.values():
        player["character_sheet"] = {"race": "人类", "class": "冒险者"}
    instance.npcs = {
        "encountered-guard": {
            "character_name": "Encountered Guard",
            "hp": 17,
            "max_hp": 23,
            "tier": "hostile",
            "secret_plan": "guard-betrayal",
        },
    }
    dependencies = play_env.api._character_dependencies
    lorebook = dependencies.assets.lorebook
    lorebook.create_world(instance.world_id, "Test World")
    lorebook.add_entry({
        "id": "unseen-villain",
        "world_id": instance.world_id,
        "name": "Unseen Villain",
        "type": "npc",
        "tier": "core",
        "content": "secret-villain-identity",
        "visible_to": [instance.gm_uid],
    })
    return app, key, instance, headers, dependencies


def test_service_projects_npc_roster_without_changing_other_fields(characters_env):
    _, key, instance, _, dependencies = characters_env
    before_npcs = deepcopy(instance.npcs)

    gm = deepcopy(characters.list_characters(dependencies, key, viewer_is_gm=True))
    player = characters.list_characters(dependencies, key, viewer_is_gm=False)

    assert {npc["npc_id"] for npc in gm["npcs"]} == {
        "encountered-guard", "unseen-villain",
    }
    guard = next(npc for npc in gm["npcs"] if npc["npc_id"] == "encountered-guard")
    assert guard == {
        "npc_id": "encountered-guard",
        **before_npcs["encountered-guard"],
        "name": "Encountered Guard",
    }
    assert player["npcs"] == []
    assert player["players"] == gm["players"]
    assert player == {**gm, "npcs": []}
    assert instance.npcs == before_npcs
    assert "secret-villain-identity" not in json.dumps(player)
    assert "guard-betrayal" not in json.dumps(player)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix, use_owner",
    [
        ("?user=p1&share=1", False),
        ("?user=p1&share=1", True),
        ("?user=p1&share=1&delegate=1", True),
    ],
    ids=["shared-player", "owner-preview", "owner-delegate"],
)
async def test_l7_non_gm_characters_withholds_npc_roster(characters_env, suffix, use_owner):
    app, key, _, headers, _ = characters_env

    async with TestClient(TestServer(app)) as client:
        response = await client.get(
            f"/api/games/{key}/characters{suffix}",
            headers=headers if use_owner else {},
        )
        body = await response.json()

    assert response.status == 200
    assert body["npcs"] == []
    assert {player["user_id"] for player in body["players"]} == {"p1", "p2"}
    assert "Unseen Villain" not in json.dumps(body)
    assert "guard-betrayal" not in json.dumps(body)


@pytest.mark.asyncio
async def test_owner_characters_retains_complete_roster(characters_env, play_env):
    app, key, _, headers, _ = characters_env
    expected = json.loads(json.dumps(play_env.api.list_characters(key, viewer_is_gm=True)))

    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/games/{key}/characters", headers=headers)
        body = await response.json()

    assert response.status == 200
    assert body == expected
    assert {npc["npc_id"] for npc in body["npcs"]} == {
        "encountered-guard", "unseen-villain",
    }


@pytest.mark.parametrize("viewer_is_gm", [True, False])
def test_missing_game_characters_is_unchanged(characters_env, viewer_is_gm):
    _, _, _, _, dependencies = characters_env

    assert characters.list_characters(
        dependencies, "web|missing|web", viewer_is_gm=viewer_is_gm,
    ) == {"players": [], "npcs": [], "rule_attrs": []}


def test_character_service_requires_explicit_viewer(characters_env):
    _, key, _, _, dependencies = characters_env

    with pytest.raises(TypeError, match="viewer_is_gm"):
        characters.list_characters(dependencies, key)


def test_character_api_requires_explicit_viewer(characters_env, play_env):
    _, key, _, _, _ = characters_env

    with pytest.raises(TypeError, match="viewer_is_gm"):
        play_env.api.list_characters(key)
