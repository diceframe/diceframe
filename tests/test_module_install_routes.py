"""HTTP contracts for content-module import and marketplace install (API-01)."""

from __future__ import annotations

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.routes.modules import register_modules
from src.webui.api import WebAPI
from src.webui.services import plugins


class FakeAPI:
    def __init__(self) -> None:
        self.imports: list[tuple[bytes, bool]] = []
        self.installs: list[tuple[str, bool]] = []

    async def import_module(self, payload: bytes, overwrite: bool = False) -> dict[str, object]:
        self.imports.append((payload, overwrite))
        return {"ok": True, "id": "castle-module"}

    async def install_marketplace_module(
        self, module_id: str, overwrite: bool = False,
    ) -> dict[str, object]:
        self.installs.append((module_id, overwrite))
        return {"ok": True, "id": module_id}


def _app(api: FakeAPI) -> web.Application:
    app = web.Application()
    app["api"] = api
    register_modules(app)
    return app


@pytest.mark.asyncio
async def test_module_import_requires_confirmation_and_delegates_zip() -> None:
    api = FakeAPI()
    async with TestClient(TestServer(_app(api))) as client:
        form = FormData()
        form.add_field("file", b"module-zip", filename="castle.dfplugin")
        denied = await client.post("/api/modules/import", data=form)
        assert denied.status == 403

        form = FormData()
        form.add_field("file", b"module-zip", filename="castle.dfplugin")
        form.add_field("overwrite", "true")
        response = await client.post(
            "/api/modules/import",
            data=form,
            headers={"X-TRPG-Confirm": "true"},
        )
        assert response.status == 200
        assert await response.json() == {"ok": True, "id": "castle-module"}
        assert api.imports == [(b"module-zip", True)]


@pytest.mark.asyncio
async def test_marketplace_module_install_requires_confirmation_and_delegates() -> None:
    api = FakeAPI()
    async with TestClient(TestServer(_app(api))) as client:
        denied = await client.post("/api/modules/castle-module/install", json={})
        assert denied.status == 403

        response = await client.post(
            "/api/modules/castle-module/install",
            json={"overwrite": True},
            headers={"X-TRPG-Confirm": "true"},
        )
        assert response.status == 200
        assert await response.json() == {"ok": True, "id": "castle-module"}
        assert api.installs == [("castle-module", True)]


@pytest.mark.asyncio
async def test_successful_plugin_install_refreshes_module_read_models(monkeypatch) -> None:
    api = object.__new__(WebAPI)
    api._plugin_lifecycle_dependencies = object()
    refreshed: list[str] = []
    api._sync_plugin_adventure_sources = lambda: refreshed.append("adventures")
    api._sync_module_catalogs = lambda: refreshed.append("catalogs")

    async def install(_dependencies, _payload, _overwrite, **_kwargs):
        return {"ok": True, "id": "castle-module"}

    monkeypatch.setattr(plugins, "install_plugin", install)

    result = await api.import_module(b"module-zip")

    assert result["ok"] is True
    assert refreshed == ["adventures", "catalogs"]
