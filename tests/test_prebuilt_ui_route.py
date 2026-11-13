from __future__ import annotations

from pathlib import Path

import pytest

from src.webui.routes.plugins import api_plugin_prebuilt_ui


class FakeApi:
    def __init__(self, path: Path):
        self.path = path

    def prebuilt_plugin_ui_descriptor(self, plugin_id):
        return {"ok": True, "plugin_id": plugin_id, "entrypoint": "ui/index.html", "assets": ["ui/app.js"]}

    def prebuilt_plugin_ui_asset_path(self, plugin_id, relative_path):
        assert plugin_id == "demo"
        assert relative_path == "ui/index.html"
        return self.path


class FakeRequest:
    scheme = "https"
    host = "localhost:1234"

    def __init__(self, api):
        self.app = {"api": api}
        self.match_info = {"plugin_id": "demo", "path": "ui/index.html"}


@pytest.mark.asyncio
async def test_prebuilt_ui_route_applies_frame_security_headers(tmp_path: Path):
    path = tmp_path / "index.html"
    path.write_text("<html></html>", encoding="utf-8")
    response = await api_plugin_prebuilt_ui(FakeRequest(FakeApi(path)))
    assert response.headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
