"""静态资源缓存策略：哈希产物 immutable，根级与 index 保持 no-cache。

用户实测：更新重启后偶发无样式页面（原生按钮 + Markdown 裸文本），重启
才恢复。根因是全部静态文件 no-cache——启动时每个 chunk 都要回源重验证，
更新窗口里一次失败即断样式。哈希产物改 immutable 后不再有重验证窗口。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.routes.pages import register_pages


@pytest.fixture
def client(tmp_path: Path):
    static = tmp_path / "static-v2"
    (static / "assets").mkdir(parents=True)
    (static / "assets" / "app-Bme8bERS.js").write_text("console.log(1)", encoding="utf-8")
    (static / "assets" / "style-0AfFvExl.css").write_text("body{}", encoding="utf-8")
    (static / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (static / "_redirects").write_text("/* /index.html 200", encoding="utf-8")
    app = web.Application()
    app["static_v2_dir"] = static
    register_pages(app)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["assets/app-Bme8bERS.js", "assets/style-0AfFvExl.css"])
async def test_hashed_assets_are_immutable(client, asset):
    async with TestClient(TestServer(client)) as http:
        response = await http.get("/v2-assets/" + asset)
        assert response.status == 200
        assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"


@pytest.mark.asyncio
@pytest.mark.parametrize("root_file", ["favicon.svg", "_redirects"])
async def test_root_files_stay_no_cache(client, root_file):
    async with TestClient(TestServer(client)) as http:
        response = await http.get("/v2-assets/" + root_file)
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-cache"


@pytest.mark.asyncio
async def test_traversal_outside_static_dir_is_forbidden(client):
    async with TestClient(TestServer(client)) as http:
        response = await http.get("/v2-assets/..%2Fsecret.txt")
        assert response.status in (403, 404)
