"""Bound-module lifecycle errors are structured HTTP 409 responses."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.routes.plugins import register_plugins
from src.webui.services.modules import ModuleInUse


class FakeAPI:
    async def control_plugin(self, _plugin_id: str, _action: str) -> dict[str, object]:
        raise _in_use()

    async def update_marketplace_plugin(self, _plugin_id: str) -> dict[str, object]:
        raise _in_use()

    async def uninstall_plugin(self, _plugin_id: str, _delete_data: bool) -> dict[str, object]:
        raise _in_use()


def _in_use() -> ModuleInUse:
    return ModuleInUse(
        "castle-module",
        "uninstall",
        [{"game_key": "web|room|bot", "adventure_id": "castle"}],
    )


def _app() -> web.Application:
    app = web.Application()
    app["api"] = FakeAPI()
    register_plugins(app)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/plugins/castle-module/stop", None),
        ("post", "/api/plugins/castle-module/update", None),
        ("delete", "/api/plugins/castle-module", {}),
    ],
)
async def test_bound_module_lifecycle_actions_return_structured_conflict(
    method: str, path: str, payload,
) -> None:
    async with TestClient(TestServer(_app())) as client:
        response = await getattr(client, method)(
            path,
            json=payload,
            headers={"X-TRPG-Confirm": "true"},
        )
        assert response.status == 409
        assert await response.json() == {
            "ok": False,
            "error_code": "MODULE_IN_USE",
            "module_id": "castle-module",
            "action": "uninstall",
            "games": [{"game_key": "web|room|bot", "adventure_id": "castle"}],
        }
