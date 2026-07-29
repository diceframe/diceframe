import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import web_server
from src.webui.access_password import hash_access_password
from src.webui.login_audit import LOGIN_AUDIT_KEY, LoginAuditStore
from src.webui.routes.auth import register_auth


def _login_app(tmp_path) -> web.Application:
    app = web.Application(middlewares=[web_server.auth_middleware])
    app[LOGIN_AUDIT_KEY] = LoginAuditStore(tmp_path)
    register_auth(app)
    return app


@pytest.mark.asyncio
async def test_owner_login_records_success_and_failure(tmp_path, monkeypatch):
    monkeypatch.setitem(
        web_server.STATE,
        "access_token",
        hash_access_password("correct-password"),
    )
    app = _login_app(tmp_path)

    async with TestClient(TestServer(app)) as client:
        failed = await client.post(
            "/api/login",
            headers={"Authorization": "Bearer wrong-password"},
        )
        succeeded = await client.post(
            "/api/login",
            headers={"Authorization": "Bearer correct-password"},
        )
        unauthorized_history = await client.get("/api/login-history")
        history = await client.get(
            "/api/login-history",
            headers={"Authorization": "Bearer correct-password"},
        )
        history_body = await history.json()

    assert failed.status == 401
    assert succeeded.status == 200
    assert unauthorized_history.status == 401
    assert history.status == 200
    assert history.headers["Cache-Control"] == "no-store"
    entries = history_body["entries"]
    assert [entry["success"] for entry in entries] == [True, False]
    assert all(entry["ip"] for entry in entries)


@pytest.mark.asyncio
async def test_login_succeeds_when_access_password_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.setitem(web_server.STATE, "access_token", "")
    app = _login_app(tmp_path)

    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/login")

    assert response.status == 200
