"""HTTP contracts for the user-facing content-module API (API-00)."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.routes.modules import register_modules


class FakeAPI:
    def list_modules(self) -> dict[str, object]:
        return {"ok": True, "modules": [{"id": "castle-module"}]}

    def module_detail(self, module_id: str) -> dict[str, object]:
        if module_id == "castle-module":
            return {"ok": True, "module": {"id": module_id}}
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}

    def module_adventures(self, module_id: str) -> dict[str, object]:
        if module_id == "castle-module":
            return {"ok": True, "module_id": module_id, "adventures": [{"adventure_id": "castle"}]}
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}

    def module_content(
        self, module_id: str, kind: str, key: str, language: str = "",
    ) -> dict[str, object]:
        if (module_id, kind, key, language) == ("castle-module", "npc", "keeper", "zh-CN"):
            return {"ok": True, "content": {"id": "keeper"}}
        return {"ok": False, "error_code": "CONTENT_NOT_FOUND"}


def _app() -> web.Application:
    app = web.Application()
    app["api"] = FakeAPI()
    register_modules(app)
    return app


@pytest.mark.asyncio
async def test_module_list_and_detail_routes_delegate_to_public_api() -> None:
    async with TestClient(TestServer(_app())) as client:
        list_response = await client.get("/api/modules")
        assert list_response.status == 200
        assert await list_response.json() == {
            "ok": True,
            "modules": [{"id": "castle-module"}],
        }

        detail_response = await client.get("/api/modules/castle-module")
        assert detail_response.status == 200
        assert await detail_response.json() == {
            "ok": True,
            "module": {"id": "castle-module"},
        }


@pytest.mark.asyncio
async def test_module_detail_route_returns_404_for_unknown_module() -> None:
    async with TestClient(TestServer(_app())) as client:
        response = await client.get("/api/modules/missing")
        assert response.status == 404
        assert await response.json() == {
            "ok": False,
            "error_code": "MODULE_NOT_FOUND",
        }


@pytest.mark.asyncio
async def test_module_adventures_route_has_a_dedicated_read_model() -> None:
    async with TestClient(TestServer(_app())) as client:
        response = await client.get("/api/modules/castle-module/adventures")
        assert response.status == 200
        assert await response.json() == {
            "ok": True,
            "module_id": "castle-module",
            "adventures": [{"adventure_id": "castle"}],
        }


@pytest.mark.asyncio
async def test_module_content_route_preserves_module_and_language_scope() -> None:
    async with TestClient(TestServer(_app())) as client:
        response = await client.get(
            "/api/modules/castle-module/content/npc/keeper?language=zh-CN",
        )
        assert response.status == 200
        assert await response.json() == {"ok": True, "content": {"id": "keeper"}}

        missing = await client.get("/api/modules/castle-module/content/npc/missing")
        assert missing.status == 404
