from __future__ import annotations

import json
import zipfile
from io import BytesIO
from types import SimpleNamespace

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.routes import game_package_routes
from src.webui.services import game_packages


class _Registry:
    def __init__(self, root):
        self.root = root
        self.instances = {}
        self.import_payload = b""

    def save_package_state_path(self, key):
        return self.root / "__".join(key) / "state.json"

    def get(self, key):
        return self.instances.get(key)

    async def import_save_zip(self, payload, scene_image_importer, map_background_importer):
        self.import_payload = payload
        assert scene_image_importer(b"scene")["ok"] is True
        assert map_background_importer(b"map")["ok"] is True
        return {"ok": True, "game_key": ["web", "imported", "bot"]}


def _service(registry, *, scene_path=None, map_path=None):
    uploads = []
    service = game_packages.GamePackageService(game_packages.GamePackageDependencies(
        parse_game_key=lambda game_key: tuple(game_key.split("|")),
        get_instance=registry.get,
        state_path_for=registry.save_package_state_path,
        import_save_zip=registry.import_save_zip,
        resolve_scene_image_file=lambda _reference: scene_path,
        resolve_map_background_file=lambda _reference: map_path,
        save_scene_image_upload=lambda payload: uploads.append(("scene", payload)) or {"ok": True},
        save_map_background_upload=lambda payload: uploads.append(("map", payload)) or {"ok": True},
    ))
    return service, uploads


def test_saved_game_access_and_export_use_package_boundary(tmp_path) -> None:
    registry = _Registry(tmp_path)
    service, _uploads = _service(registry)
    key = ("web", "room", "bot")
    registry.instances[key] = SimpleNamespace(gm_uid="gm", world_name="测试 世界")
    save_path = registry.save_package_state_path(key)
    save_path.parent.mkdir(parents=True)
    save_path.write_text(json.dumps({"gm_uid": "gm", "round_number": 3}), encoding="utf-8")
    save_path.with_name("chatlog.jsonl").write_text('{"role":"gm"}\n', encoding="utf-8")

    assert service.saved_game_access("web|room|bot") == {
        "exists": True,
        "gm_uid": "gm",
    }
    result = service.export_game_package("web|room|bot")

    assert result["ok"] is True
    assert result["filename"].startswith("save_测试_世界_")
    with zipfile.ZipFile(BytesIO(result["payload"])) as archive:
        assert set(archive.namelist()) == {"state.json", "chatlog.jsonl"}
        assert json.loads(archive.read("state.json"))["round_number"] == 3


def test_saved_game_access_reads_offline_backup(tmp_path) -> None:
    registry = _Registry(tmp_path)
    service, _uploads = _service(registry)
    save_path = registry.save_package_state_path(("web", "offline", "bot"))
    save_path.parent.mkdir(parents=True)
    save_path.with_name("state.backup.json").write_text(
        json.dumps({"gm_uid": "offline-gm"}), encoding="utf-8",
    )

    assert service.saved_game_access("web|offline|bot") == {
        "exists": True,
        "gm_uid": "offline-gm",
    }


@pytest.mark.asyncio
async def test_import_game_package_delegates_assets_and_publicizes_key(tmp_path) -> None:
    registry = _Registry(tmp_path)
    service, uploads = _service(registry)

    result = await service.import_game_package(b"zip-payload")

    assert result == {"ok": True, "game_key": "web|imported|bot"}
    assert registry.import_payload == b"zip-payload"
    assert [kind for kind, _payload in uploads] == ["scene", "map"]


@pytest.mark.asyncio
async def test_package_routes_report_unsupported_media_as_bad_request() -> None:
    failure = {
        "ok": False,
        "error_code": "UNSUPPORTED_MEDIA_SCHEMA",
        "error": "unsupported media module schema: 99",
        "status": 400,
    }

    async def import_package(_payload):
        return dict(failure)

    @web.middleware
    async def authenticate(request, handler):
        request["user_id"] = "gm"
        return await handler(request)

    app = web.Application(middlewares=[authenticate])
    app["api"] = SimpleNamespace(
        get_game_instance=lambda _key: SimpleNamespace(gm_uid="gm"),
        export_game_package=lambda _key: dict(failure),
        import_game_package=import_package,
    )
    app.router.add_get("/games/{game_key}/export", game_package_routes.api_export_game)
    app.router.add_post("/games/import", game_package_routes.api_import_game)
    async with TestClient(TestServer(app)) as client:
        exported = await client.get("/games/test/export")
        upload = FormData()
        upload.add_field("file", b"package", filename="save.zip", content_type="application/zip")
        imported = await client.post("/games/import", data=upload, headers={"X-TRPG-Confirm": "true"})
        for response in (exported, imported):
            assert response.status == 400
            body = await response.json()
            assert body["error_code"] == failure["error_code"]
            assert body["error"] == failure["error"]


def test_export_game_package_includes_resolved_scene_and_map_assets(tmp_path) -> None:
    registry = _Registry(tmp_path)
    scene_path = tmp_path / "scene.webp"
    map_path = tmp_path / "map.webp"
    scene_path.write_bytes(b"scene-bytes")
    map_path.write_bytes(b"map-bytes")
    service, _uploads = _service(registry, scene_path=scene_path, map_path=map_path)
    key = ("web", "assets", "bot")
    registry.instances[key] = SimpleNamespace(gm_uid="gm", world_name="assets")
    save_path = registry.save_package_state_path(key)
    save_path.parent.mkdir(parents=True)
    save_path.write_text(json.dumps({
        "scene_image": {"kind": "upload", "id": "scene"},
        "map_background": {"kind": "upload", "id": "map"},
    }), encoding="utf-8")

    result = service.export_game_package("web|assets|bot")

    assert result["ok"] is True
    with zipfile.ZipFile(BytesIO(result["payload"])) as archive:
        assert archive.read("scene-image.asset") == b"scene-bytes"
        assert archive.read("map-background.asset") == b"map-bytes"
        state = json.loads(archive.read("state.json"))
        assert state["scene_image"] == {"kind": "save_asset", "path": "scene-image.asset"}
        assert state["map_background"] == {"kind": "save_asset", "path": "map-background.asset"}
