import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.abuse_guard import (
    ABUSE_GUARD_KEY,
    AbuseGuard,
    SlidingWindowLimiter,
    _is_ai_request,
    abuse_guard_middleware,
)


async def _ok(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def _app(guard: AbuseGuard) -> web.Application:
    app = web.Application(middlewares=[abuse_guard_middleware])
    app[ABUSE_GUARD_KEY] = guard
    app.router.add_post("/api/login", _ok)
    app.router.add_post("/api/games/room/action", _ok)
    app.router.add_get("/api/games/room", _ok)
    return app


@pytest.mark.asyncio
async def test_login_is_limited_before_reaching_handler():
    guard = AbuseGuard(
        login_per_ip_limit=2,
        login_per_ip_window=600,
        login_global_limit=100,
        ai_concurrency=10,
    )
    app = _app(guard)

    async with TestClient(TestServer(app)) as client:
        first = await client.post("/api/login")
        second = await client.post("/api/login")
        blocked = await client.post("/api/login")
        body = await blocked.json()

    assert first.status == 200
    assert second.status == 200
    assert blocked.status == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert blocked.headers["Cache-Control"] == "no-store"
    assert f"{body['retry_after']} 秒" in body["error"]
    assert "影响游戏" in body["error"]


@pytest.mark.asyncio
async def test_only_api_writes_use_the_general_request_limit():
    guard = AbuseGuard(
        write_per_ip_limit=2,
        write_global_limit=100,
        ai_concurrency=10,
    )
    app = _app(guard)

    async with TestClient(TestServer(app)) as client:
        assert (await client.post("/api/games/room/action")).status == 200
        assert (await client.post("/api/games/room/action")).status == 200
        assert (await client.post("/api/games/room/action")).status == 429
        assert (await client.get("/api/games/room")).status == 200


@pytest.mark.asyncio
async def test_ai_requests_have_a_small_waiting_window_before_429():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_handler(request: web.Request) -> web.Response:
        entered.set()
        await release.wait()
        return web.json_response({"ok": True})

    guard = AbuseGuard(
        write_per_ip_limit=100,
        write_global_limit=100,
        ai_concurrency=1,
        ai_wait_seconds=0.01,
    )
    app = web.Application(middlewares=[abuse_guard_middleware])
    app[ABUSE_GUARD_KEY] = guard
    app.router.add_post("/api/generate-text", slow_handler)

    async with TestClient(TestServer(app)) as client:
        active = asyncio.create_task(client.post("/api/generate-text"))
        await entered.wait()
        blocked = await client.post("/api/generate-text")
        release.set()
        completed = await active

    assert blocked.status == 429
    assert completed.status == 200


def test_limiter_evicts_old_identities_instead_of_growing_forever():
    now = 0.0
    limiter = SlidingWindowLimiter(max_buckets=3, clock=lambda: now)

    for index in range(10):
        now += 1
        assert limiter.check("write", f"ip-{index}", 10, 60).allowed

    assert limiter.bucket_count == 3


def test_story_recap_is_counted_as_an_ai_request():
    request = type("Request", (), {
        "path": "/api/games/room/story-recap",
        "method": "POST",
    })()

    assert _is_ai_request(request) is True


def test_server_speech_is_counted_as_an_ai_request():
    request = type("Request", (), {
        "path": "/api/games/room/speech",
        "method": "POST",
    })()

    assert _is_ai_request(request) is True
