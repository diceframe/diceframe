"""m3-04a: HTTP routes for the per-game portable scene import (workplan §6.17)."""

from __future__ import annotations

from copy import deepcopy

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.content_modules.refs import PORTABLE_SCENE_SCHEMA
from src.engine.game_instance import GameInstance
from src.engine.modules import tactical_scene
from src.webui.routes.tactical_scene import register_tactical_scene

GAME_KEY = "g"


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", GAME_KEY, "u"), players={"p1": {}})
    instance.gm_uid = "gm1"
    return instance


def _definition(*, scene_id: str = "scene_import_camp", name: str = "Import Camp") -> dict:
    return {
        "spec": PORTABLE_SCENE_SCHEMA,
        "tactical_scene": {
            "schema_version": 1,
            "kind": "scene",
            "canonical_id": scene_id,
            "display_name": name,
        },
    }


class _Api:
    """Minimal facade with the public seams the route is allowed to use."""

    def __init__(self, instance: GameInstance | None) -> None:
        self.instance = instance
        self.saves = 0

    def get_game_instance(self, game_key: str):
        if self.instance is None or game_key != GAME_KEY:
            return None
        return self.instance

    async def save_game_instance(self, instance) -> None:
        self.saves += 1


@web.middleware
async def _identity(request: web.Request, handler):
    request["user_id"] = request.headers.get("X-Test-User", "")
    request["owner_authenticated"] = False
    request["player_preview"] = False
    return await handler(request)


def _client(api: _Api) -> TestClient:
    app = web.Application(middlewares=[_identity])
    app["api"] = api
    register_tactical_scene(app)
    return TestClient(TestServer(app))


GM = {"X-Test-User": "gm1"}
PLAYER = {"X-Test-User": "p1"}


def _import_path() -> str:
    return f"/api/games/{GAME_KEY}/tactical-scenes/import"


def _preview_path() -> str:
    return f"/api/games/{GAME_KEY}/tactical-scenes/import/preview"


@pytest.mark.asyncio
async def test_preview_reports_create_without_writing() -> None:
    instance = _instance()
    api = _Api(instance)

    async with _client(api) as client:
        response = await client.post(
            _preview_path(), json={"definition": _definition()}, headers=GM,
        )
        payload = await response.json()

    assert response.status == 200
    assert payload["action"] == "create"
    assert payload["scene_id"] == "scene_import_camp"
    assert payload["name_collision"] is False
    assert api.saves == 0
    assert tactical_scene.scenes(instance) == {}


@pytest.mark.asyncio
async def test_commit_creates_the_scene_once_and_preview_then_reports_noop() -> None:
    instance = _instance()
    api = _Api(instance)
    body = {"definition": _definition(), "confirm": True}

    async with _client(api) as client:
        first = await client.post(_import_path(), json=body, headers=GM)
        first_payload = await first.json()
        second = await client.post(_import_path(), json=body, headers=GM)
        second_payload = await second.json()
        preview = await client.post(
            _preview_path(), json={"definition": _definition()}, headers=GM,
        )
        preview_payload = await preview.json()

    assert first.status == 200
    assert first_payload["created"] is True
    assert second.status == 200
    assert second_payload["created"] is False
    assert api.saves == 1
    assert list(tactical_scene.scenes(instance)) == ["scene_import_camp"]
    assert tactical_scene.scenes(instance)["scene_import_camp"]["display_name"] == "Import Camp"
    assert preview_payload["action"] == "noop"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [_preview_path(), _import_path()])
async def test_import_routes_are_gm_only(path: str) -> None:
    instance = _instance()
    api = _Api(instance)

    async with _client(api) as client:
        response = await client.post(
            path, json={"definition": _definition(), "confirm": True}, headers=PLAYER,
        )
        payload = await response.json()

    assert response.status == 403
    assert payload["error_code"] == "scene_import_forbidden"
    assert api.saves == 0
    assert tactical_scene.scenes(instance) == {}


@pytest.mark.asyncio
async def test_conflicting_identity_and_name_map_to_409() -> None:
    instance = _instance()
    api = _Api(instance)

    async with _client(api) as client:
        created = await client.post(
            _import_path(),
            json={"definition": _definition(), "confirm": True},
            headers=GM,
        )
        assert created.status == 200

        changed = _definition(name="Import Camp II")
        conflict = await client.post(
            _import_path(), json={"definition": changed, "confirm": True}, headers=GM,
        )
        conflict_payload = await conflict.json()

        same_name = _definition(scene_id="scene_import_other", name="Import Camp")
        name_conflict = await client.post(
            _import_path(), json={"definition": same_name, "confirm": True}, headers=GM,
        )
        name_conflict_payload = await name_conflict.json()

        confirmed = await client.post(
            _import_path(),
            json={
                "definition": same_name,
                "confirm": True,
                "confirm_name_collision": True,
            },
            headers=GM,
        )
        confirmed_payload = await confirmed.json()

    assert conflict.status == 409
    assert conflict_payload["error_code"] == "scene_import_conflict"
    assert name_conflict.status == 409
    assert name_conflict_payload["error_code"] == "scene_import_name_conflict"
    assert confirmed.status == 200
    assert confirmed_payload["created"] is True
    assert set(tactical_scene.scenes(instance)) == {
        "scene_import_camp", "scene_import_other",
    }


@pytest.mark.asyncio
async def test_stale_expected_revision_is_rejected() -> None:
    instance = _instance()
    api = _Api(instance)
    before = deepcopy(tactical_scene.slot(instance))

    async with _client(api) as client:
        response = await client.post(
            _import_path(),
            json={"definition": _definition(), "confirm": True, "expected_revision": 7},
            headers=GM,
        )
        payload = await response.json()

    assert response.status == 409
    assert payload["error_code"] == "tactical_scene_stale_revision"
    assert tactical_scene.slot(instance) == before
    assert api.saves == 0


@pytest.mark.asyncio
async def test_unconfirmed_and_invalid_payloads_are_refused() -> None:
    instance = _instance()
    api = _Api(instance)

    async with _client(api) as client:
        unconfirmed = await client.post(
            _import_path(), json={"definition": _definition()}, headers=GM,
        )
        unconfirmed_payload = await unconfirmed.json()
        invalid = await client.post(
            _import_path(), json={"definition": None, "confirm": True}, headers=GM,
        )
        invalid_payload = await invalid.json()

    assert unconfirmed.status == 400
    assert unconfirmed_payload["error_code"] == "scene_import_unconfirmed"
    assert invalid.status == 422
    assert invalid_payload["error_code"] == "tactical_scene_invalid_payload"
    assert api.saves == 0


@pytest.mark.asyncio
async def test_game_vanishing_after_viewer_resolution_maps_to_404() -> None:
    instance = _instance()

    class _VanishingApi(_Api):
        def __init__(self) -> None:
            super().__init__(instance)
            self.lookups = 0

        def get_game_instance(self, game_key: str):
            self.lookups += 1
            return instance if self.lookups == 1 else None

    api = _VanishingApi()

    async with _client(api) as client:
        response = await client.post(
            _import_path(), json={"definition": _definition(), "confirm": True}, headers=GM,
        )
        payload = await response.json()

    assert response.status == 404
    assert payload["error_code"] == "scene_import_not_found"


@pytest.mark.asyncio
async def test_unknown_game_is_refused_fail_closed() -> None:
    api = _Api(None)

    async with _client(api) as client:
        response = await client.post(
            _import_path(), json={"definition": _definition(), "confirm": True}, headers=GM,
        )
        payload = await response.json()

    assert response.status == 403
    assert payload["error_code"] == "scene_import_forbidden"


def test_import_routes_are_registered() -> None:
    app = web.Application()
    app["api"] = _Api(None)
    register_tactical_scene(app)
    paths = {route.resource.canonical for route in app.router.routes()}
    assert "/api/games/{game_key}/tactical-scenes/import" in paths
    assert "/api/games/{game_key}/tactical-scenes/import/preview" in paths