"""B2 Scene read service, HTTP route and ApplicationAction tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.application_actions import ActionRegistry
from src.engine.game_instance import GameInstance
from src.engine.module_state import set_module_state
from src.engine.modules import tactical_scene
from src.engine.participant_view import resolve_viewer
from src.tactical_scene.contract import validate_tactical_scene_payload
from src.webui.routes.tactical_scene import register_tactical_scene
from src.webui.services import tactical_scene_ops as tactical_scene_ops_service
from src.webui.services import tactical_scene_read as tactical_scene_read_service
from src.webui.services.assistant_tactical_scene_tools import TacticalSceneToolDependencies, tactical_scene_tools

GAME_KEY = "g"


def _asset(asset_id: str) -> dict[str, str]:
    return {
        "asset_id": asset_id,
        "digest": "sha256:abc123",
        "source_path": f"assets/{asset_id}.png",
        "mime": "image/png",
    }


def _wall(wall_id: str = "wall_1") -> dict:
    return {
        "schema_version": 1,
        "kind": "wall",
        "canonical_id": wall_id,
        "endpoints": {"x0": 0.0, "y0": 0.0, "x1": 400.0, "y1": 0.0},
        "direction": 0,
        "door": 0,
        "door_state": 0,
        "restrictions": {"move": 1, "sight": 1, "sound": 1, "light": 1},
        "extensions": {"threshold": {"light": 10}},
    }


def _token(canonical_id: str, *, hidden: bool = False, disposition: int = 1) -> dict:
    return {
        "schema_version": 1,
        "kind": "token",
        "canonical_id": canonical_id,
        "actor_ref": {
            "source_kind": "module", "source_id": "example-module",
            "kind": "npc", "id": "hero", "digest": "",
        },
        "position": {"x": 0.0, "y": 0.0, "elevation": 0.0, "level": 0},
        "size": {"width": 1.0, "height": 1.0},
        "rotation": 0.0,
        "texture": _asset(f"texture_{canonical_id}"),
        "ring": {"private": "gm"},
        "alpha": 1.0,
        "display": {"name": canonical_id},
        "disposition": disposition,
        "hidden": hidden,
        "locked": False,
        "sight": {"enabled": True},
        "detection_modes": [],
        "light": {"dim": 20},
        "bars": {"bar1": {"attribute": "hp"}},
        "delta": {"conditions": []},
        "external_ids": {"foundry_v14": canonical_id},
        "flags": {"dnd2024": {"initiative": 2}},
        "extensions": {},
        "provenance": {"import": "foundry"},
    }


def _scene(canonical_id: str, *, sort: int = 0) -> dict:
    return {
        "schema_version": 1,
        "kind": "scene",
        "canonical_id": canonical_id,
        "display_name": canonical_id,
        "navigation_name": canonical_id,
        "source_ref": None,
        "external_ids": {"foundry_v14": canonical_id},
        "ownership": {"default": 0, "gm": 3},
        "sort": sort,
        "background": _asset("background"),
        "foreground": None,
        "thumbnail": None,
        "dimensions": {
            "width": 1000, "height": 1000, "padding": 0.0,
            "offset_x": 0, "offset_y": 0,
            "initial_view": {"x": 0.0, "y": 0.0, "zoom": 1.0},
        },
        "grid": {
            "type": "square", "size": 100, "scale": 1.0, "units": "ft",
            "color": "#000000", "alpha": 0.5, "distance": 5.0,
        },
        "environment": {
            "darkness": 0.0, "global_illumination": True,
            "fog": {"exploration": False, "reset_policy": ""}, "weather": "",
        },
        "placeables": {
            "tokens": {
                "token_hero": _token("token_hero"),
                "token_hidden": _token("token_hidden", hidden=True),
                "token_secret": _token("token_secret", disposition=-2),
            },
            "tiles": {"tile_1": {"canonical_id": "tile_1", "private": "gm"}},
            "walls": {"wall_1": _wall("wall_1")},
            "lights": {}, "sounds": {}, "notes": {}, "drawings": {},
            "templates": {}, "regions": {},
        },
        "levels": [],
        "flags": {"dnd2024": {"secret": True}},
        "extensions": {"vendor": {"private": True}},
        "provenance": {"import": "foundry"},
        "revision": 4,
    }


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "g", "u"), players={"p1": {}})
    instance.gm_uid = "gm1"
    scenes = tactical_scene.scenes(instance)
    scenes["scene_castle"] = validate_tactical_scene_payload(_scene("scene_castle", sort=10))
    scenes["scene_forest"] = validate_tactical_scene_payload(_scene("scene_forest", sort=20))
    set_module_state(instance, "tactical_scene", {
        "schema_version": 1,
        "scenes": scenes,
        "active_scene_ref": "scene_castle",
        "revision": 7,
    })
    return instance


def _dependencies(instance: GameInstance) -> tactical_scene_read_service.TacticalSceneReadDependencies:
    return tactical_scene_read_service.TacticalSceneReadDependencies(
        get_instance=lambda key: instance if key == GAME_KEY else None,
    )


def _viewer(instance: GameInstance, uid: str):
    return resolve_viewer(
        instance, user_id=uid, owner_authenticated=False, player_preview=False,
    )


class _SceneApi:
    def __init__(self, instance: GameInstance):
        self.instance = instance
        self.dependencies = _dependencies(instance)

    def get_game_instance(self, game_key: str):
        return self.instance if game_key == GAME_KEY else None

    def tactical_scene_read(self, game_key: str, viewer, scene_id=None):
        return tactical_scene_read_service.tactical_scene_read(self.dependencies, game_key, viewer, scene_id)

    def tactical_scene_list(self, game_key: str, viewer):
        return tactical_scene_read_service.tactical_scene_list(self.dependencies, game_key, viewer)


@web.middleware
async def _identity(request: web.Request, handler):
    request["user_id"] = request.headers.get("X-Test-User", "")
    request["owner_authenticated"] = False
    request["player_preview"] = False
    return await handler(request)


async def _client(instance: GameInstance):
    app = web.Application(middlewares=[_identity])
    app["api"] = _SceneApi(instance)
    register_tactical_scene(app)
    return TestClient(TestServer(app))


@pytest.mark.asyncio
async def test_gm_reads_full_scene_and_seat_reads_filtered_scene():
    instance = _instance()
    async with await _client(instance) as client:
        gm = await client.get(
            "/api/games/g/tactical-scenes/scene_castle", headers={"X-Test-User": "gm1"},
        )
        assert gm.status == 200
        gm_scene = (await gm.json())["scene"]
        assert gm_scene["ownership"] == {"default": 0, "gm": 3}
        assert gm_scene["flags"] == {"dnd2024": {"secret": True}}
        assert "token_hidden" in gm_scene["placeables"]["tokens"]
        assert "tile_1" in gm_scene["placeables"]["tiles"]

        seat = await client.get(
            "/api/games/g/tactical-scenes/scene_castle", headers={"X-Test-User": "p1"},
        )
        assert seat.status == 200
        seat_scene = (await seat.json())["scene"]
        assert "ownership" not in seat_scene
        assert "flags" not in seat_scene
        assert "extensions" not in seat_scene
        assert "provenance" not in seat_scene
        assert "source_ref" not in seat_scene
        assert "external_ids" not in seat_scene
        assert set(seat_scene["placeables"]) == {"tokens", "walls", "lights"}
        assert set(seat_scene["placeables"]["walls"]["wall_1"]) == {
            "canonical_id", "endpoints", "direction", "door", "door_state", "restrictions",
        }
        assert "token_hidden" not in seat_scene["placeables"]["tokens"]
        assert seat_scene["placeables"]["tokens"]["token_secret"]["actor_ref"] is None


@pytest.mark.asyncio
async def test_tactical_scene_read_rejects_outsider_and_missing_scene():
    instance = _instance()
    async with await _client(instance) as client:
        outsider = await client.get(
            "/api/games/g/tactical-scenes/scene_castle", headers={"X-Test-User": "stranger"},
        )
        assert outsider.status == 403
        outsider_body = await outsider.json()
        assert outsider_body["error_code"] == "tactical_scene_forbidden"

        missing = await client.get(
            "/api/games/g/tactical-scenes/scene_missing", headers={"X-Test-User": "p1"},
        )
        assert missing.status == 404
        assert (await missing.json())["error_code"] == "tactical_scene_not_found"


@pytest.mark.asyncio
async def test_active_scene_reference_is_resolved_explicitly_or_fails_closed():
    instance = _instance()
    async with await _client(instance) as client:
        active = await client.get("/api/games/g/tactical-scene", headers={"X-Test-User": "p1"})
        assert active.status == 200
        body = await active.json()
        assert body["scene_id"] == "scene_castle"
        assert body["active_scene_ref"] == "scene_castle"

    tactical_scene.slot(instance)["active_scene_ref"] = "scene_stale"
    async with await _client(instance) as client:
        stale = await client.get("/api/games/g/tactical-scene", headers={"X-Test-User": "p1"})
        assert stale.status == 404
        assert (await stale.json())["error_code"] == "tactical_scene_not_found"

    tactical_scene.slot(instance)["active_scene_ref"] = None
    async with await _client(instance) as client:
        unbound = await client.get("/api/games/g/tactical-scene", headers={"X-Test-User": "p1"})
        assert unbound.status == 404
        assert (await unbound.json())["error_code"] == "tactical_scene_not_found"


@pytest.mark.asyncio
async def test_tactical_scene_list_projects_every_scene_without_writing():
    instance = _instance()
    before = deepcopy(instance.modules)
    revision = tactical_scene.revision(instance)

    api = _SceneApi(instance)
    gm = api.tactical_scene_list(GAME_KEY, _viewer(instance, "gm1"))
    seat = api.tactical_scene_list(GAME_KEY, _viewer(instance, "p1"))

    assert gm["ok"] is True
    assert [scene["canonical_id"] for scene in gm["scenes"]] == [
        "scene_castle", "scene_forest",
    ]
    assert "ownership" not in seat["scenes"][0]
    assert instance.modules == before
    assert tactical_scene.revision(instance) == revision == 7
    async with await _client(instance) as client:
        response = await client.get(
            "/api/games/g/tactical-scenes", headers={"X-Test-User": "p1"},
        )
        assert response.status == 200
        assert len((await response.json())["scenes"]) == 2
    assert instance.modules == before
    assert tactical_scene.revision(instance) == revision


def test_scene_actions_are_discoverable_read_only_and_reuse_the_service():
    instance = _instance()
    read_dependencies = _dependencies(instance)
    write_dependencies = tactical_scene_ops_service.TacticalSceneOpDependencies(
        get_instance=lambda key: instance if key == GAME_KEY else None,
        save_instance=lambda instance: asyncio.sleep(0),
    )
    dependencies = TacticalSceneToolDependencies(
        get_game=lambda key: instance if key == GAME_KEY else None,
        viewer_for=lambda game, owner: _viewer(game, owner),
        read_tactical_scene=lambda game_key, viewer, scene_id=None: tactical_scene_read_service.tactical_scene_read(
            read_dependencies, game_key, viewer, scene_id,
        ),
        apply_op=lambda game_key, viewer, op, expected_revision: tactical_scene_ops_service.apply_tactical_scene_op(
            write_dependencies, game_key, viewer, op, expected_revision=expected_revision,
        ),
    )
    registry = ActionRegistry(tactical_scene_tools(dependencies))

    read = registry.get("tactical_scene_read")
    visibility = registry.get("tactical_scene_visibility_read")
    assert read is not None and visibility is not None
    assert read.domain == "tactical_scene" and read.effect == "read" and read.mutation is False
    assert visibility.effect == "read" and visibility.mutation is False
    assert read.descriptor()["effect"] == "read"

    delete = registry.get("tactical_scene_delete")
    assert delete is not None
    assert delete.effect == "destructive" and delete.mutation is True
    assert delete.authorize is not None

    gm_result = read.handler({"game_key": GAME_KEY}, "gm1")
    assert gm_result["ok"] is True
    assert gm_result["scene"]["ownership"] == {"default": 0, "gm": 3}

    seat_result = read.handler({"game_key": GAME_KEY, "scene_id": "scene_castle"}, "p1")
    assert seat_result["ok"] is True
    assert "ownership" not in seat_result["scene"]

    visibility_result = visibility.handler({"game_key": GAME_KEY}, "p1")
    assert visibility_result["visibility"] == {
        "viewer_kind": "seat", "full_projection": False,
    }
    assert tactical_scene.revision(instance) == 7