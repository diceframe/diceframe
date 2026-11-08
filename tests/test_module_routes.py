"""HTTP contracts for the user-facing content-module API (API-00)."""

from __future__ import annotations

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.routes.modules import register_modules


class FakeAPI:
    def __init__(self) -> None:
        # 记录路由传进来的视角判定（issue #708）。
        self.detail_views: list[bool] = []
        self.lorebook_views: list[bool] = []
        self.content_views: list[bool] = []

    def list_modules(self) -> dict[str, object]:
        return {"ok": True, "modules": [{"id": "castle-module"}]}

    def module_detail(self, module_id: str, *, viewer_is_gm: bool = True) -> dict[str, object]:
        self.detail_views.append(viewer_is_gm)
        if module_id == "castle-module":
            return {"ok": True, "module": {"id": module_id}}
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}

    def module_adventures(self, module_id: str) -> dict[str, object]:
        if module_id == "castle-module":
            return {"ok": True, "module_id": module_id, "adventures": [{"adventure_id": "castle"}]}
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}

    def module_lorebooks(self, module_id: str, *, viewer_is_gm: bool = True) -> dict[str, object]:
        self.lorebook_views.append(viewer_is_gm)
        if module_id == "castle-module":
            return {"ok": True, "module_id": module_id, "lorebooks": [{"id": "castle-book"}]}
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}

    def module_content(
        self, module_id: str, kind: str, key: str, language: str = "",
        *, viewer_is_gm: bool = True,
    ) -> dict[str, object]:
        self.content_views.append(viewer_is_gm)
        if (module_id, kind, key, language) == ("castle-module", "npc", "keeper", "zh-CN"):
            return {"ok": True, "content": {"id": "keeper"}}
        return {"ok": False, "error_code": "CONTENT_NOT_FOUND"}

    def module_compatibility(self, module_id: str) -> dict[str, object]:
        if module_id == "castle-module":
            return {"ok": True, "module_id": module_id, "blockers": [], "warnings": []}
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}

    def module_usages(self, module_id: str) -> dict[str, object]:
        if module_id == "castle-module":
            return {"ok": True, "module_id": module_id, "usages": [], "total_games": 0}
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}

    def preview_module_import(self, payload: bytes) -> dict[str, object]:
        assert payload == b"module-zip"
        return {"ok": True, "blockers": [], "warnings": ["catalog_mode_content_not_autoloaded"]}


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
async def test_module_lorebooks_route_has_a_dedicated_read_model() -> None:
    async with TestClient(TestServer(_app())) as client:
        response = await client.get("/api/modules/castle-module/lorebooks")
        assert response.status == 200
        assert await response.json() == {
            "ok": True,
            "module_id": "castle-module",
            "lorebooks": [{"id": "castle-book"}],
        }


@pytest.mark.asyncio
async def test_module_content_route_preserves_module_and_language_scope() -> None:
    api = FakeAPI()
    app = web.Application()
    app["api"] = api
    register_modules(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get(
            "/api/modules/castle-module/content/npc/keeper?language=zh-CN",
        )
        assert response.status == 200
        assert await response.json() == {"ok": True, "content": {"id": "keeper"}}

        missing = await client.get("/api/modules/castle-module/content/npc/missing")
        assert missing.status == 404

    # 无访问密码 + 无 share 标记 ⇒ 主机视角（GM）；404 探测也走同一判定。
    assert api.content_views == [True, True]


@pytest.mark.asyncio
async def test_module_read_routes_treat_share_marked_requests_as_player_view() -> None:
    """issue #708：带 share 身份（?user= / 席位令牌）的请求按玩家视角。"""

    api = FakeAPI()
    app = web.Application()
    app["api"] = api
    register_modules(app)
    async with TestClient(TestServer(app)) as client:
        plain = await client.get("/api/modules/castle-module/content/npc/keeper?language=zh-CN")
        assert plain.status == 200
        shared = await client.get("/api/modules/castle-module/content/npc/keeper?language=zh-CN&user=seat-1")
        assert shared.status == 200
        seated = await client.get(
            "/api/modules/castle-module/content/npc/keeper?language=zh-CN",
            headers={"X-Seat-Token": "seat-token"},
        )
        assert seated.status == 200
        await client.get("/api/modules/castle-module/lorebooks?user=seat-1")
        await client.get("/api/modules/castle-module?user=seat-1")

    assert api.content_views == [True, False, False]
    assert api.lorebook_views == [False]
    assert api.detail_views == [False]


@pytest.mark.asyncio
async def test_module_compatibility_routes_expose_installed_and_import_previews() -> None:
    async with TestClient(TestServer(_app())) as client:
        installed = await client.get("/api/modules/castle-module/compatibility")
        assert installed.status == 200
        assert await installed.json() == {
            "ok": True,
            "module_id": "castle-module",
            "blockers": [],
            "warnings": [],
        }

        form = FormData()
        form.add_field("file", b"module-zip", filename="castle.dfplugin")
        imported = await client.post("/api/modules/import/preview", data=form)
        assert imported.status == 200
        assert await imported.json() == {
            "ok": True,
            "blockers": [],
            "warnings": ["catalog_mode_content_not_autoloaded"],
        }


@pytest.mark.asyncio
async def test_module_usage_route_returns_the_bound_game_index() -> None:
    async with TestClient(TestServer(_app())) as client:
        response = await client.get("/api/modules/castle-module/usages")
        assert response.status == 200
        assert await response.json() == {
            "ok": True,
            "module_id": "castle-module",
            "usages": [],
            "total_games": 0,
        }
