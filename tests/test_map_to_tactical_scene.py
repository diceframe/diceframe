"""map-01: explicit GM conversion of the location-map background into a Scene."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.engine.game_instance import GameInstance
from src.engine.modules import media, tactical_scene
from src.engine.participant_view import resolve_viewer
from src.webui.routes.maps import register_maps
from src.webui.services import map_backgrounds
from src.webui.services import tactical_scene_ops as tactical_scene_ops_service
from src.webui.services import map_scene_conversion

GAME_KEY = "g"
UPLOAD_BYTES = b"normalized-webp-payload"
UPLOAD_ASSET_ID = hashlib.sha256(UPLOAD_BYTES).hexdigest()
OTHER_ASSET_ID = hashlib.sha256(b"another-background").hexdigest()
GENERATED_ASSET_ID = hashlib.sha256(b"generated-background").hexdigest()


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", GAME_KEY, "u"), players={"p1": {}})
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


def _upload_file(tmp_path: Path, asset_id: str = UPLOAD_ASSET_ID) -> Path:
    backgrounds_dir = tmp_path / "map-backgrounds"
    backgrounds_dir.mkdir(parents=True, exist_ok=True)
    path = backgrounds_dir / f"{asset_id}.webp"
    path.write_bytes(UPLOAD_BYTES)
    return path


def _upload_resolver(tmp_path: Path):
    service = map_backgrounds.MapBackgroundService(
        tmp_path / "map-backgrounds", lambda _asset_id: None,
    )
    return service.resolve_file


def _generated_resolver(tmp_path: Path):
    images_dir = tmp_path / "generated-images"
    images_dir.mkdir(parents=True, exist_ok=True)
    path = images_dir / f"{GENERATED_ASSET_ID}.webp"
    path.write_bytes(b"generated")

    def resolve(selection):
        if isinstance(selection, dict) and selection.get("kind") == "generated":
            normalized = selection.get("asset_id")
            if normalized == GENERATED_ASSET_ID:
                return path
        return None

    return resolve


_DEFAULT_INTENT = object()


async def _convert(instance, resolver, *, intent=_DEFAULT_INTENT, viewer=None, saver=None):
    resolved_intent = {"confirm": True} if intent is _DEFAULT_INTENT else intent
    return await map_scene_conversion.convert_map_background_to_scene(
        _deps(instance, saver), resolver, GAME_KEY,
        viewer or _viewer(instance, "gm1"),
        intent=resolved_intent,
    )


@pytest.mark.asyncio
async def test_conversion_creates_scene_with_background_and_default_grid(tmp_path):
    _upload_file(tmp_path)
    instance = _instance()
    selection = {"kind": "upload", "asset_id": UPLOAD_ASSET_ID}
    instance.set_map_background(selection)
    saver = _Saver()

    result = await _convert(instance, _upload_resolver(tmp_path), saver=saver)

    assert result["ok"] is True
    assert result["created"] is True
    assert saver.calls == 1
    scene = result["scene"]
    expected_scene_id = map_scene_conversion.map_background_scene_id("upload", UPLOAD_ASSET_ID)
    assert scene["canonical_id"] == expected_scene_id
    assert scene["background"] == {
        "asset_id": UPLOAD_ASSET_ID,
        "digest": UPLOAD_ASSET_ID,
        "source_path": f"map-backgrounds/{UPLOAD_ASSET_ID}.webp",
        "mime": "image/webp",
    }
    assert scene["grid"] == map_scene_conversion.DEFAULT_GRID
    assert scene["grid"]["type"] == "square"
    assert scene["dimensions"]["width"] == map_scene_conversion.DEFAULT_DIMENSIONS["width"]
    assert scene["dimensions"]["height"] == map_scene_conversion.DEFAULT_DIMENSIONS["height"]
    assert scene["placeables"] == {
        "tokens": {}, "tiles": {}, "walls": {}, "lights": {}, "sounds": {},
        "notes": {}, "drawings": {}, "templates": {}, "regions": {},
    }
    # Creating a scene never changes the existing map background or gameplay binding.
    assert media.map_background(instance) == selection
    assert tactical_scene.active_scene_ref(instance) is None
    assert tactical_scene.revision(instance) == 1


@pytest.mark.asyncio
async def test_generated_background_uses_generated_source_path(tmp_path):
    instance = _instance()
    instance.set_map_background({"kind": "generated", "asset_id": GENERATED_ASSET_ID})

    result = await _convert(instance, _generated_resolver(tmp_path))

    assert result["ok"] is True
    assert result["scene"]["background"] == {
        "asset_id": GENERATED_ASSET_ID,
        "digest": GENERATED_ASSET_ID,
        "source_path": f"generated-images/{GENERATED_ASSET_ID}.webp",
        "mime": "image/webp",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [{}, {"confirm": False}, {"confirm": "yes"}, None])
async def test_conversion_requires_explicit_confirmation(tmp_path, intent):
    _upload_file(tmp_path)
    instance = _instance()
    instance.set_map_background({"kind": "upload", "asset_id": UPLOAD_ASSET_ID})
    before = deepcopy(tactical_scene.slot(instance))

    result = await _convert(instance, _upload_resolver(tmp_path), intent=intent)

    assert result["ok"] is False
    assert result["error_code"] == "map_background_unconfirmed"
    assert tactical_scene.slot(instance) == before


@pytest.mark.asyncio
async def test_explicit_grid_payload_counts_as_confirmation(tmp_path):
    _upload_file(tmp_path)
    instance = _instance()
    instance.set_map_background({"kind": "upload", "asset_id": UPLOAD_ASSET_ID})
    grid = {"type": "square", "size": 140, "units": "ft", "distance": 5.0}

    result = await _convert(
        instance, _upload_resolver(tmp_path),
        intent={"grid": grid},
    )

    assert result["ok"] is True
    assert result["scene"]["grid"] == {
        "type": "square", "size": 140, "scale": 1.0, "units": "ft",
        "color": "", "alpha": 0.0, "distance": 5.0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [
    {},
    {"kind": "auto"},
    {"kind": "none"},
    {"kind": "builtin", "id": "fantasy-region-v1"},
    {"kind": "plugin", "map_id": "plugin:demo:map:demo-map"},
    {"kind": "upload", "asset_id": OTHER_ASSET_ID},
    {"kind": "url", "url": "https://example.com/map.png"},
])
async def test_unresolvable_background_is_refused_without_side_effects(tmp_path, selection):
    _upload_file(tmp_path)
    instance = _instance()
    if selection:
        instance.set_map_background(selection)
    before_map = deepcopy(media.map_background(instance))
    before_slot = deepcopy(tactical_scene.slot(instance))

    result = await _convert(instance, _upload_resolver(tmp_path))

    assert result["ok"] is False
    assert result["error_code"] == "map_background_unresolvable"
    assert media.map_background(instance) == before_map
    assert tactical_scene.slot(instance) == before_slot


@pytest.mark.asyncio
async def test_save_failure_leaves_map_and_scene_state_untouched(tmp_path):
    _upload_file(tmp_path)
    instance = _instance()
    selection = {"kind": "upload", "asset_id": UPLOAD_ASSET_ID}
    instance.set_map_background(selection)
    before_slot = deepcopy(tactical_scene.slot(instance))

    result = await _convert(instance, _upload_resolver(tmp_path), saver=_Saver(fail=True))

    assert result["ok"] is False
    assert result["error_code"] == "save_failed"
    assert media.map_background(instance) == selection
    assert tactical_scene.slot(instance) == before_slot


@pytest.mark.asyncio
@pytest.mark.parametrize("uid", ["p1", "stranger"])
async def test_conversion_is_gm_only(tmp_path, uid):
    _upload_file(tmp_path)
    instance = _instance()
    instance.set_map_background({"kind": "upload", "asset_id": UPLOAD_ASSET_ID})
    before_slot = deepcopy(tactical_scene.slot(instance))

    result = await _convert(
        instance, _upload_resolver(tmp_path), viewer=_viewer(instance, uid),
    )

    assert result["ok"] is False
    assert result["error_code"] == "map_background_forbidden"
    assert tactical_scene.slot(instance) == before_slot


@pytest.mark.asyncio
async def test_repeated_conversion_is_idempotent(tmp_path):
    _upload_file(tmp_path)
    instance = _instance()
    instance.set_map_background({"kind": "upload", "asset_id": UPLOAD_ASSET_ID})
    resolver = _upload_resolver(tmp_path)
    saver = _Saver()

    first = await _convert(instance, resolver, saver=saver)
    second = await _convert(instance, resolver, saver=saver)

    assert first["created"] is True
    assert second["ok"] is True
    assert second["created"] is False
    assert second["scene_id"] == first["scene_id"]
    assert second["revision"] == first["revision"] == 1
    assert saver.calls == 1
    assert list(tactical_scene.scenes(instance)) == [first["scene_id"]]


@pytest.mark.asyncio
async def test_stale_expected_revision_is_rejected_without_creating_scene(tmp_path):
    _upload_file(tmp_path)
    instance = _instance()
    instance.set_map_background({"kind": "upload", "asset_id": UPLOAD_ASSET_ID})
    before_slot = deepcopy(tactical_scene.slot(instance))

    result = await _convert(
        instance, _upload_resolver(tmp_path), intent={"confirm": True, "expected_revision": 7},
    )

    assert result["ok"] is False
    assert result["error_code"] == "tactical_scene_stale_revision"
    assert tactical_scene.slot(instance) == before_slot


@pytest.mark.asyncio
async def test_scene_at_same_identity_with_other_background_fails_closed(tmp_path):
    _upload_file(tmp_path)
    instance = _instance()
    instance.set_map_background({"kind": "upload", "asset_id": UPLOAD_ASSET_ID})
    scene_id = map_scene_conversion.map_background_scene_id("upload", UPLOAD_ASSET_ID)
    other_ref = {
        "asset_id": OTHER_ASSET_ID,
        "digest": OTHER_ASSET_ID,
        "source_path": f"map-backgrounds/{OTHER_ASSET_ID}.webp",
        "mime": "image/webp",
    }
    tactical_scene.apply_op(
        instance,
        {"kind": "tactical_scene_create", "scene": {
            "schema_version": 1, "kind": "scene", "canonical_id": scene_id,
            "background": other_ref,
        }},
        expected_revision=0,
    )
    before_slot = deepcopy(tactical_scene.slot(instance))

    result = await _convert(instance, _upload_resolver(tmp_path))

    assert result["ok"] is False
    assert result["error_code"] == "map_background_conversion_conflict"
    assert tactical_scene.slot(instance) == before_slot


@pytest.mark.asyncio
async def test_successful_conversion_broadcasts_scene_projection(tmp_path):
    _upload_file(tmp_path)
    instance = _instance()
    instance.set_map_background({"kind": "upload", "asset_id": UPLOAD_ASSET_ID})
    events: list[tuple[dict, dict]] = []

    async def broadcast(game_key, payload, **kwargs):
        events.append((payload, kwargs))

    result = await map_scene_conversion.convert_map_background_to_scene(
        _deps(instance), _upload_resolver(tmp_path), GAME_KEY,
        _viewer(instance, "gm1"), intent={"confirm": True}, broadcast=broadcast,
    )

    assert result["ok"] is True
    assert len(events) == 2
    assert {payload["event_type"] for payload, _ in events} == {"tactical_scene_updated"}
    assert events[0][1].get("exclude_uid") == "gm1"
    assert events[1][1].get("private_uid") == "gm1"


# ---- route -----------------------------------------------------------------


class _FakeApi:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result if result is not None else {
            "ok": True, "created": True, "scene_id": "mapbg.upload.x",
        }
        self.calls: list[dict] = []

    def get_game_instance(self, game_key):
        return None

    async def convert_map_background_to_tactical_scene(self, game_key, viewer, *, intent, broadcast=None):
        self.calls.append({"game_key": game_key, "intent": intent, "broadcast": broadcast})
        return self.result


def _route_app(api) -> web.Application:
    app = web.Application()
    app["api"] = api
    register_maps(app)
    return app


@pytest.mark.asyncio
async def test_route_forwards_intent_and_returns_created_scene():
    api = _FakeApi()
    body = {"confirm": True, "grid": {"type": "square", "size": 100}}

    async with TestClient(TestServer(_route_app(api))) as client:
        response = await client.post(
            f"/api/games/{GAME_KEY}/tactical-scenes/from-map-background", json=body,
        )
        payload = await response.json()

    assert response.status == 200
    assert payload["created"] is True
    assert api.calls[0]["game_key"] == GAME_KEY
    assert api.calls[0]["intent"] == body


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "status"), [
    ("map_background_unconfirmed", 400),
    ("map_background_unresolvable", 422),
    ("map_background_forbidden", 403),
    ("map_background_not_found", 404),
    ("map_background_conversion_conflict", 409),
    ("tactical_scene_stale_revision", 409),
])
async def test_route_maps_conversion_errors_to_status(code, status):
    api = _FakeApi({"ok": False, "error_code": code, "error": "x"})

    async with TestClient(TestServer(_route_app(api))) as client:
        response = await client.post(f"/api/games/{GAME_KEY}/tactical-scenes/from-map-background", json={})

    assert response.status == status


@pytest.mark.asyncio
async def test_route_rejects_malformed_body_before_the_service():
    api = _FakeApi()

    async with TestClient(TestServer(_route_app(api))) as client:
        response = await client.post(
            f"/api/games/{GAME_KEY}/tactical-scenes/from-map-background",
            data="{not json",
        )
        payload = await response.json()

    assert response.status == 400
    assert payload["error_code"] == "map_background_unconfirmed"
    assert api.calls == []


def test_route_is_registered_on_the_maps_app():
    app = _route_app(_FakeApi())
    paths = {route.resource.canonical for route in app.router.routes()}
    assert "/api/games/{game_key}/tactical-scenes/from-map-background" in paths