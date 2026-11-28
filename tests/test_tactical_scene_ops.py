"""scene-03 Scene write operations: authority, stale guard, rollback, SSE."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.application_actions import ActionRegistry
from src.engine.game_instance import GameInstance
from src.engine.modules import tactical_scene
from src.engine.participant_view import resolve_viewer
from src.tactical_scene.ops import TacticalSceneOpError, apply_tactical_scene_op
from src.webui.api import WebAPI
from src.webui.routes.tactical_scene import register_tactical_scene
from src.webui.services import tactical_scene_ops as tactical_scene_ops_service
from src.webui.services import tactical_scene_read as tactical_scene_read_service
from src.webui.services.assistant_tactical_scene_tools import TacticalSceneToolDependencies, tactical_scene_tools

GAME_KEY = "g"


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "g", "u"), players={"p1": {}})
    instance.gm_uid = "gm1"
    return instance


def _viewer(instance: GameInstance, uid: str):
    return resolve_viewer(
        instance, user_id=uid, owner_authenticated=False, player_preview=False,
    )


class _Saver:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def __call__(self, instance: GameInstance) -> None:
        if self.fail:
            raise OSError("disk full")
        self.calls += 1


def _deps(instance: GameInstance, saver: _Saver | None = None):
    return tactical_scene_ops_service.TacticalSceneOpDependencies(
        get_instance=lambda key: instance if key == GAME_KEY else None,
        save_instance=saver or _Saver(),
    )


def _scene(scene_id: str, display_name: str | None = None) -> dict:
    return {
        "schema_version": 1,
        "kind": "scene",
        "canonical_id": scene_id,
        "display_name": display_name or scene_id,
        "ownership": {"default": 0, "gm": 3},
        "flags": {"dnd2024": {"secret": True}},
        "placeables": {},
    }


def _token(token_id: str, *, hidden: bool = False) -> dict:
    return {
        "schema_version": 1,
        "kind": "token",
        "canonical_id": token_id,
        "position": {"x": 10.0, "y": 20.0},
        "hidden": hidden,
        "disposition": 1,
    }


def _wall(wall_id: str = "wall_1", **overrides) -> dict:
    wall = {
        "schema_version": 1,
        "kind": "wall",
        "canonical_id": wall_id,
        "endpoints": {"x0": 0.0, "y0": 0.0, "x1": 400.0, "y1": 0.0},
        "direction": 0,
        "door": 1,
        "door_state": 0,
        "restrictions": {"move": 1, "sight": 1, "sound": 1, "light": 1},
    }
    wall.update(overrides)
    return wall


async def _apply(deps, viewer, op, expected_revision, broadcast=None) -> dict:
    return await tactical_scene_ops_service.apply_tactical_scene_op(
        deps, GAME_KEY, viewer, op,
        expected_revision=expected_revision, broadcast=broadcast,
    )


def test_apply_tactical_scene_op_is_pure_and_owns_the_committed_revision():
    slot = tactical_scene.fresh()
    original = deepcopy(slot)

    new_slot, summary = apply_tactical_scene_op(
        slot, {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, expected_revision=0,
    )

    assert slot == original
    assert new_slot is not slot
    assert new_slot["revision"] == 1
    assert new_slot["scenes"]["scene_a"]["revision"] == 1
    assert summary["operation"] == "tactical_scene_create"
    assert summary["scene_id"] == "scene_a"
    assert summary["revision"] == 1


def test_apply_tactical_scene_op_rejects_stale_revision_and_bad_slots():
    slot = tactical_scene.fresh()
    with pytest.raises(TacticalSceneOpError) as stale:
        apply_tactical_scene_op(slot, {"kind": "tactical_scene_create", "scene": _scene("s")}, expected_revision=3)
    assert stale.value.code == "tactical_scene_stale_revision"

    with pytest.raises(TacticalSceneOpError) as bad_slot:
        apply_tactical_scene_op({"schema_version": 99}, {"kind": "tactical_scene_create", "scene": _scene("s")},
                       expected_revision=0)
    assert bad_slot.value.code == "tactical_scene_invalid_payload"

    with pytest.raises(TacticalSceneOpError) as bad_revision:
        apply_tactical_scene_op(slot, {"kind": "tactical_scene_create", "scene": _scene("s")},
                       expected_revision="0")
    assert bad_revision.value.code == "tactical_scene_invalid_payload"


@pytest.mark.asyncio
async def test_gm_creates_updates_and_deletes_scene_with_revision_bumps():
    instance = _instance()
    saver = _Saver()
    deps = _deps(instance, saver)
    gm = _viewer(instance, "gm1")

    created = await _apply(deps, gm, {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, 0)
    assert created["ok"] is True
    assert created["revision"] == 1
    assert saver.calls == 1
    assert tactical_scene.revision(instance) == 1
    assert tactical_scene.scenes(instance)["scene_a"]["revision"] == 1

    updated = await _apply(
        deps, gm,
        {"kind": "tactical_scene_update", "scene_id": "scene_a",
         "scene": _scene("scene_a", "Renamed")},
        1,
    )
    assert updated["ok"] is True
    assert updated["revision"] == 2
    assert tactical_scene.scenes(instance)["scene_a"]["display_name"] == "Renamed"
    assert tactical_scene.scenes(instance)["scene_a"]["revision"] == 2

    read = tactical_scene_read_service.tactical_scene_read(
        tactical_scene_read_service.TacticalSceneReadDependencies(get_instance=lambda key: instance),
        GAME_KEY, gm, "scene_a",
    )
    assert read["scene"]["display_name"] == "Renamed"

    deleted = await _apply(
        deps, gm, {"kind": "tactical_scene_delete", "scene_id": "scene_a"}, 2,
    )
    assert deleted["ok"] is True
    assert deleted["revision"] == 3
    assert deleted["scene"] is None
    assert tactical_scene.scenes(instance) == {}


@pytest.mark.asyncio
async def test_seat_and_outsider_cannot_write_and_slot_stays_untouched():
    instance = _instance()
    before = deepcopy(tactical_scene.slot(instance))
    deps = _deps(instance)

    for uid in ("p1", "stranger"):
        result = await _apply(
            deps, _viewer(instance, uid),
            {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, 0,
        )
        assert result["ok"] is False
        assert result["error_code"] == "tactical_scene_forbidden"

    assert tactical_scene.slot(instance) == before


@pytest.mark.asyncio
async def test_stale_expected_revision_is_rejected_without_mutation():
    instance = _instance()
    deps = _deps(instance)
    gm = _viewer(instance, "gm1")
    await _apply(deps, gm, {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, 0)

    stale = await _apply(
        deps, gm,
        {"kind": "tactical_scene_update", "scene_id": "scene_a",
         "scene": _scene("scene_a", "Overwritten")},
        0,
    )

    assert stale["ok"] is False
    assert stale["error_code"] == "tactical_scene_stale_revision"
    assert tactical_scene.revision(instance) == 1
    assert tactical_scene.scenes(instance)["scene_a"]["display_name"] == "scene_a"


@pytest.mark.asyncio
async def test_duplicate_canonical_id_is_rejected():
    instance = _instance()
    deps = _deps(instance)
    gm = _viewer(instance, "gm1")
    await _apply(deps, gm, {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, 0)

    duplicate = await _apply(
        deps, gm, {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, 1,
    )

    assert duplicate["ok"] is False
    assert duplicate["error_code"] == "tactical_scene_invalid_payload"
    assert tactical_scene.revision(instance) == 1
    assert list(tactical_scene.scenes(instance)) == ["scene_a"]


@pytest.mark.asyncio
async def test_placeable_crud_validates_tokens_and_bumps_scene_revision():
    instance = _instance()
    deps = _deps(instance)
    gm = _viewer(instance, "gm1")
    await _apply(deps, gm, {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, 0)

    added = await _apply(
        deps, gm,
        {"kind": "tactical_scene_placeable_create", "scene_id": "scene_a",
         "collection": "tokens", "placeable": _token("token_hero")},
        1,
    )
    assert added["ok"] is True
    assert added["collection"] == "tokens"
    assert tactical_scene.scenes(instance)["scene_a"]["revision"] == 2
    assert "token_hero" in tactical_scene.scenes(instance)["scene_a"]["placeables"]["tokens"]

    wall = await _apply(
        deps, gm,
        {"kind": "tactical_scene_placeable_create", "scene_id": "scene_a",
         "collection": "walls", "placeable": _wall("wall_1")},
        2,
    )
    assert wall["ok"] is True
    assert tactical_scene.scenes(instance)["scene_a"]["placeables"]["walls"]["wall_1"]["door"] == 1

    # A wall outside the typed contract is rejected before anything is written.
    rejected = await _apply(
        deps, gm,
        {"kind": "tactical_scene_placeable_create", "scene_id": "scene_a",
         "collection": "walls", "placeable": {"canonical_id": "wall_bad", "door": 9}},
        3,
    )
    assert rejected["ok"] is False
    assert "wall_bad" not in tactical_scene.scenes(instance)["scene_a"]["placeables"]["walls"]
    assert tactical_scene.scenes(instance)["scene_a"]["revision"] == 3

    updated = await _apply(
        deps, gm,
        {"kind": "tactical_scene_placeable_update", "scene_id": "scene_a",
         "collection": "tokens", "placeable_id": "token_hero",
         "placeable": _token("token_hero")},
        3,
    )
    assert updated["ok"] is True
    assert tactical_scene.scenes(instance)["scene_a"]["revision"] == 4

    deleted = await _apply(
        deps, gm,
        {"kind": "tactical_scene_placeable_delete", "scene_id": "scene_a",
         "collection": "tokens", "placeable_id": "token_hero"},
        4,
    )
    assert deleted["ok"] is True
    assert tactical_scene.scenes(instance)["scene_a"]["placeables"]["tokens"] == {}
    assert tactical_scene.scenes(instance)["scene_a"]["revision"] == 5

    invalid = await _apply(
        deps, gm,
        {"kind": "tactical_scene_placeable_create", "scene_id": "scene_a",
         "collection": "tokens", "placeable": {"schema_version": 1, "kind": "token",
                                               "canonical_id": "bad", "position": {"x": "left"}}},
        5,
    )
    assert invalid["ok"] is False
    assert invalid["error_code"] == "tactical_scene_invalid_payload"
    assert tactical_scene.revision(instance) == 5

    missing = await _apply(
        deps, gm,
        {"kind": "tactical_scene_placeable_delete", "scene_id": "scene_a",
         "collection": "tokens", "placeable_id": "token_missing"},
        5,
    )
    assert missing["ok"] is False
    assert missing["error_code"] == "tactical_scene_placeable_not_found"


@pytest.mark.asyncio
async def test_set_active_requires_an_existing_explicit_scene_id():
    instance = _instance()
    deps = _deps(instance)
    gm = _viewer(instance, "gm1")
    await _apply(deps, gm, {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, 0)
    await _apply(deps, gm, {"kind": "tactical_scene_create", "scene": _scene("scene_b")}, 1)

    active = await _apply(deps, gm, {"kind": "tactical_scene_set_active", "scene_id": "scene_b"}, 2)
    assert active["ok"] is True
    assert tactical_scene.active_scene_ref(instance) == "scene_b"
    assert active["active_scene_ref"] == "scene_b"

    missing = await _apply(deps, gm, {"kind": "tactical_scene_set_active", "scene_id": "scene_missing"}, 3)
    assert missing["ok"] is False
    assert missing["error_code"] == "tactical_scene_not_found"
    assert tactical_scene.active_scene_ref(instance) == "scene_b"

    deleted = await _apply(deps, gm, {"kind": "tactical_scene_delete", "scene_id": "scene_b"}, 3)
    assert deleted["ok"] is True
    assert tactical_scene.active_scene_ref(instance) is None


@pytest.mark.asyncio
async def test_save_failure_rolls_back_the_slot():
    instance = _instance()
    failing = _Saver(fail=True)
    deps = _deps(instance, failing)
    gm = _viewer(instance, "gm1")
    before = deepcopy(tactical_scene.slot(instance))

    result = await _apply(deps, gm, {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, 0)

    assert result["ok"] is False
    assert result["error_code"] == "save_failed"
    assert tactical_scene.slot(instance) == before
    assert failing.calls == 0


@pytest.mark.asyncio
async def test_concurrent_writes_serialize_and_the_loser_is_stale():
    instance = _instance()
    deps = _deps(instance)
    gm = _viewer(instance, "gm1")

    first, second = await asyncio.gather(
        _apply(deps, gm, {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, 0),
        _apply(deps, gm, {"kind": "tactical_scene_create", "scene": _scene("scene_b")}, 0),
    )

    results = sorted([first, second], key=lambda item: item["ok"])
    assert [item["ok"] for item in results] == [False, True]
    assert results[0]["error_code"] == "tactical_scene_stale_revision"
    assert tactical_scene.revision(instance) == 1
    assert len(tactical_scene.scenes(instance)) == 1


@pytest.mark.asyncio
async def test_success_broadcasts_viewer_scoped_projection_without_the_save():
    instance = _instance()
    events: list[tuple[str, dict, dict]] = []

    async def broadcast(game_key: str, data: dict, **kwargs) -> None:
        events.append((game_key, deepcopy(data), kwargs))

    gm = _viewer(instance, "gm1")
    scene = _scene("scene_a")
    scene["placeables"] = {
        "tokens": {"token_hero": _token("token_hero"), "token_hidden": _token("token_hidden", hidden=True)},
    }
    result = await _apply(
        _deps(instance), gm, {"kind": "tactical_scene_create", "scene": scene}, 0, broadcast=broadcast,
    )
    assert result["ok"] is True

    seat = next(event for event in events if event[1]["visibility"] == "seat")
    gm_event = next(event for event in events if event[1]["visibility"] == "gm")

    assert seat[0] == GAME_KEY
    assert seat[2] == {"exclude_uid": "gm1"}
    assert gm_event[2] == {"private_uid": "gm1"}
    for _, data, _ in events:
        assert data["event_type"] == "tactical_scene_updated"
        assert data["scene_id"] == "scene_a"
        assert data["revision"] == 1
        assert data["source_operation"] == "tactical_scene_create"
        assert data["run_id"] == instance.run_id
        assert "save" not in data and "modules" not in data

    seat_projection = seat[1]["projection"]
    assert "ownership" not in seat_projection
    assert "flags" not in seat_projection
    assert "token_hidden" not in seat_projection["placeables"]["tokens"]
    assert "token_hero" in seat_projection["placeables"]["tokens"]

    gm_projection = gm_event[1]["projection"]
    assert gm_projection["ownership"] == {"default": 0, "gm": 3}
    assert "token_hidden" in gm_projection["placeables"]["tokens"]


@pytest.mark.asyncio
async def test_write_actions_are_discoverable_gm_only_and_delegate():
    instance = _instance()
    deps = tactical_scene_ops_service.TacticalSceneOpDependencies(
        get_instance=lambda key: instance if key == GAME_KEY else None,
        save_instance=_Saver(),
    )
    tools = TacticalSceneToolDependencies(
        get_game=lambda key: instance if key == GAME_KEY else None,
        viewer_for=lambda game, owner: _viewer(game, owner),
        read_tactical_scene=lambda game_key, viewer, scene_id=None: tactical_scene_read_service.tactical_scene_read(
            tactical_scene_read_service.TacticalSceneReadDependencies(get_instance=lambda key: instance),
            game_key, viewer, scene_id,
        ),
        apply_op=lambda game_key, viewer, op, expected_revision: tactical_scene_ops_service.apply_tactical_scene_op(
            deps, game_key, viewer, op, expected_revision=expected_revision,
        ),
    )
    registry = ActionRegistry(tactical_scene_tools(tools))

    write_names = {
        "tactical_scene_create", "tactical_scene_update", "tactical_scene_placeable_create",
        "tactical_scene_placeable_update", "tactical_scene_placeable_delete", "tactical_scene_set_active",
    }
    for name in write_names:
        action = registry.get(name)
        assert action is not None and action.mutation is True
        assert action.domain == "tactical_scene"
        assert action.authorize({"game_key": GAME_KEY}, "p1") is False
        assert action.authorize({"game_key": GAME_KEY}, "gm1") is True
    assert registry.get("tactical_scene_placeable_delete").effective_effect == "destructive"
    assert registry.get("tactical_scene_update").effective_effect == "write"

    created = await registry.get("tactical_scene_create").handler(
        {"game_key": GAME_KEY, "expected_revision": 0, "scene": _scene("scene_a")}, "gm1",
    )
    assert created["ok"] is True
    denied = await registry.get("tactical_scene_create").handler(
        {"game_key": GAME_KEY, "expected_revision": 1, "scene": _scene("scene_b")}, "p1",
    )
    assert denied["error_code"] == "tactical_scene_forbidden"
    assert tactical_scene.revision(instance) == 1


class _SceneApi:
    def __init__(self, instance: GameInstance):
        self.instance = instance
        self.read_deps = tactical_scene_read_service.TacticalSceneReadDependencies(
            get_instance=lambda key: instance if key == GAME_KEY else None,
        )
        self.op_deps = _deps(instance)

    def get_game_instance(self, game_key: str):
        return self.instance if game_key == GAME_KEY else None

    def tactical_scene_read(self, game_key, viewer, scene_id=None):
        return tactical_scene_read_service.tactical_scene_read(self.read_deps, game_key, viewer, scene_id)

    def tactical_scene_list(self, game_key, viewer):
        return tactical_scene_read_service.tactical_scene_list(self.read_deps, game_key, viewer)

    async def tactical_scene_apply(self, game_key, viewer, op, *, expected_revision, broadcast=None):
        return await tactical_scene_ops_service.apply_tactical_scene_op(
            self.op_deps, game_key, viewer, op,
            expected_revision=expected_revision, broadcast=broadcast,
        )


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
async def test_write_routes_are_thin_and_map_status_codes():
    instance = _instance()
    async with await _client(instance) as client:
        created = await client.post(
            "/api/games/g/tactical-scenes",
            json={"expected_revision": 0, "scene": _scene("scene_a")},
            headers={"X-Test-User": "gm1"},
        )
        assert created.status == 200
        assert (await created.json())["revision"] == 1

        stale = await client.patch(
            "/api/games/g/tactical-scenes/scene_a",
            json={"expected_revision": 0, "scene": _scene("scene_a", "Stale")},
            headers={"X-Test-User": "gm1"},
        )
        assert stale.status == 409
        assert (await stale.json())["error_code"] == "tactical_scene_stale_revision"

        forbidden = await client.post(
            "/api/games/g/tactical-scenes",
            json={"expected_revision": 1, "scene": _scene("scene_b")},
            headers={"X-Test-User": "p1"},
        )
        assert forbidden.status == 403
        assert (await forbidden.json())["error_code"] == "tactical_scene_forbidden"

        seat_read = await client.get(
            "/api/games/g/tactical-scenes/scene_a", headers={"X-Test-User": "p1"},
        )
        assert seat_read.status == 200
        assert "ownership" not in (await seat_read.json())["scene"]

        deleted = await client.delete(
            "/api/games/g/tactical-scenes/scene_a?expected_revision=1",
            headers={"X-Test-User": "gm1"},
        )
        assert deleted.status == 200
        assert (await deleted.json())["ok"] is True

        missing = await client.get(
            "/api/games/g/tactical-scenes/scene_a", headers={"X-Test-User": "p1"},
        )
        assert missing.status == 404


@pytest.mark.asyncio
async def test_agent_writes_broadcast_through_the_attached_connection_pool():
    """组合根接线后，Agent 写与 HTTP 写共用同一个 SSE 广播器（§0.5 遗留 3）。"""

    instance = _instance()
    sent: list[tuple[str, dict]] = []

    async def broadcast(
        game_key: str, data: dict, *, exclude_uid: str | None = None,
        private_uid: str | None = None,
    ) -> None:
        sent.append((game_key, data))

    api = WebAPI.__new__(WebAPI)
    api._connection_pool = SimpleNamespace(broadcast=broadcast)
    api._tactical_scene_ops_dependencies = _deps(instance)
    gm = _viewer(instance, "gm1")

    created = await api.tactical_scene_apply(
        GAME_KEY, gm,
        {"kind": "tactical_scene_create", "scene": _scene("scene_a")}, expected_revision=0,
    )
    assert created["ok"] is True
    assert [key for key, _event in sent] == [GAME_KEY, GAME_KEY]
    assert {event["visibility"] for _, event in sent} == {"seat", "gm"}
    assert all(event["event_type"] == "tactical_scene_updated" for _, event in sent)

    # 显式传入的广播器优先于接线的连接池。
    sent.clear()
    await api.tactical_scene_apply(
        GAME_KEY, gm,
        {"kind": "tactical_scene_delete", "scene_id": "scene_a"}, expected_revision=1,
        broadcast=broadcast,
    )
    assert len(sent) == 2

    # 未接线时静默落库，不广播。
    api._connection_pool = None
    sent.clear()
    created_b = await api.tactical_scene_apply(
        GAME_KEY, gm,
        {"kind": "tactical_scene_create", "scene": _scene("scene_b")}, expected_revision=2,
    )
    assert created_b["ok"] is True
    assert sent == []