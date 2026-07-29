from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.routes.pages import (
    NOINDEX_HEADER_VALUE,
    add_response_security_headers,
    robots_txt,
)


async def _ok(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _stream(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse()
    await response.prepare(request)
    await response.write(b"ok")
    await response.write_eof()
    return response


@pytest.mark.asyncio
async def test_security_headers_are_added_to_success_error_and_stream_responses():
    app = web.Application()
    app.on_response_prepare.append(add_response_security_headers)
    app.router.add_get("/ok", _ok)
    app.router.add_get("/stream", _stream)

    async with TestClient(TestServer(app)) as client:
        success = await client.get("/ok")
        missing = await client.get("/missing")
        stream = await client.get("/stream")

    for response in (success, missing, stream):
        assert response.headers["X-Robots-Tag"] == NOINDEX_HEADER_VALUE
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"


@pytest.mark.asyncio
async def test_robots_txt_allows_crawlers_to_observe_noindex():
    app = web.Application()
    app.on_response_prepare.append(add_response_security_headers)
    app.router.add_get("/robots.txt", robots_txt)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/robots.txt")
        body = await response.text()

    assert response.status == 200
    assert response.content_type == "text/plain"
    assert body == "User-agent: *\nDisallow:\n"
    assert response.headers["X-Robots-Tag"] == NOINDEX_HEADER_VALUE


def test_frontend_html_declares_noindex():
    index_html = (
        Path(__file__).resolve().parents[1] / "frontend-v2" / "index.html"
    ).read_text(encoding="utf-8")

    assert '<meta name="robots" content="noindex, nofollow, noarchive">' in index_html
