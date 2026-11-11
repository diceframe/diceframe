"""便携版启动器握手（services/launcher_link + routes/launcher）的服务端契约。

启动器侧（C#）与这里是同一份契约；两边真正对接由 tests/test_launcher_lifecycle.py
在 Windows 上用真实编译的启动器验证。
"""

from __future__ import annotations

import json
import os
import secrets
import signal

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.access_control import WebAccessControl
from src.webui.routes import launcher as launcher_routes
from src.webui.routes.launcher import LAUNCHER_DATA_DIR_KEY, register_launcher
from src.webui.services import launcher_link


def _write_key(data_dir, key: bytes | None = None) -> bytes:
    key = key or secrets.token_bytes(32)
    folder = data_dir / "_launcher"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "instance.json").write_text(
        json.dumps({"schema": 1, "key": key.hex(), "data_dir": str(data_dir)}),
        encoding="utf-8",
    )
    return key


def _app(data_dir) -> web.Application:
    # 设了访问密码：证明两个握手端点是在鉴权中间件之后仍然匿名可达，而别的接口照旧受保护。
    access = WebAccessControl({"access_token": "owner-password"})
    app = web.Application(middlewares=[access.middleware])
    app["runtime_control"] = {
        "boot_id": "boot-1",
        "restart_requested": False,
        "stop_requested": False,
        "restart_task": None,
    }
    app[LAUNCHER_DATA_DIR_KEY] = data_dir
    register_launcher(app)

    async def protected(request):
        return web.json_response({"ok": True})

    app.router.add_get("/api/games", protected)
    return app


@pytest.fixture
def no_signal(monkeypatch):
    raised = []
    monkeypatch.setattr(launcher_routes.signal, "raise_signal", raised.append)
    return raised


@pytest.mark.asyncio
async def test_identity_proves_access_to_this_install_without_revealing_the_key(tmp_path):
    key = _write_key(tmp_path)
    nonce = secrets.token_hex(16)
    async with TestClient(TestServer(_app(tmp_path))) as client:
        response = await client.get("/api/system/launcher/identity", params={"nonce": nonce})
        payload = await response.json()
        protected = await client.get("/api/games")

    assert response.status == 200
    assert payload["boot_id"] == "boot-1"
    assert payload["pid"] == os.getpid()
    assert payload["proof"] == launcher_link.identity_proof(key, nonce, "boot-1", os.getpid())
    assert key.hex() not in json.dumps(payload)
    # 别的接口仍然需要房主凭据。
    assert protected.status == 401


@pytest.mark.asyncio
async def test_identity_proof_does_not_verify_under_another_install_key(tmp_path):
    _write_key(tmp_path)
    nonce = secrets.token_hex(16)
    async with TestClient(TestServer(_app(tmp_path))) as client:
        payload = await (await client.get("/api/system/launcher/identity", params={"nonce": nonce})).json()

    other = secrets.token_bytes(32)
    assert payload["proof"] != launcher_link.identity_proof(other, nonce, payload["boot_id"], payload["pid"])
    # 证明绑定 pid：同一把密钥下改报 pid 也对不上。
    key = launcher_link.read_instance_key(tmp_path)
    assert payload["proof"] != launcher_link.identity_proof(key, nonce, payload["boot_id"], payload["pid"] + 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("nonce", ["", "short", "Z" * 32, "a" * 129])
async def test_identity_rejects_malformed_nonces(tmp_path, nonce):
    _write_key(tmp_path)
    async with TestClient(TestServer(_app(tmp_path))) as client:
        response = await client.get("/api/system/launcher/identity", params={"nonce": nonce})
    assert response.status == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "{not json", json.dumps({"key": "abc"})])
async def test_without_a_valid_instance_key_the_handshake_is_unavailable(tmp_path, content):
    # Docker / 源码运行没有启动器、没有密钥：启动器无法证明归属，也就不会接管。
    if content is not None:
        (tmp_path / "_launcher").mkdir()
        (tmp_path / "_launcher" / "instance.json").write_text(content, encoding="utf-8")
    async with TestClient(TestServer(_app(tmp_path))) as client:
        identity = await client.get(
            "/api/system/launcher/identity", params={"nonce": secrets.token_hex(16)}
        )
        payload = await identity.json()
        stop = await client.post("/api/system/launcher/stop", json={})
    assert identity.status == 404
    assert payload["error_code"] == launcher_link.LAUNCHER_LINK_UNAVAILABLE
    assert stop.status == 404


@pytest.mark.asyncio
async def test_a_signed_stop_request_shuts_down_without_restarting(tmp_path, no_signal, monkeypatch):
    monkeypatch.setattr(launcher_routes.asyncio, "sleep", _instant)
    key = _write_key(tmp_path)
    app = _app(tmp_path)
    nonce = secrets.token_hex(16)
    body = {"nonce": nonce, "boot_id": "boot-1", "proof": launcher_link.stop_proof(key, nonce, "boot-1")}
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/system/launcher/stop", json=body)
        await app["runtime_control"]["restart_task"]

    assert response.status == 202
    control = app["runtime_control"]
    assert control["stop_requested"] is True
    assert control["restart_requested"] is False
    assert no_signal == [signal.SIGINT]


@pytest.mark.asyncio
async def test_a_stop_during_a_pending_restart_cancels_the_execv_without_a_second_interrupt(
    tmp_path, no_signal
):
    key = _write_key(tmp_path)
    app = _app(tmp_path)
    app["runtime_control"]["restart_requested"] = True
    nonce = secrets.token_hex(16)
    body = {"nonce": nonce, "boot_id": "boot-1", "proof": launcher_link.stop_proof(key, nonce, "boot-1")}
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/system/launcher/stop", json=body)

    assert response.status == 202
    assert app["runtime_control"]["stop_requested"] is True
    # 已经在走重启的清理路径：不再排第二个中断。
    assert app["runtime_control"]["restart_task"] is None
    assert no_signal == []


@pytest.mark.asyncio
async def test_stop_requests_that_are_not_signed_for_this_boot_are_refused(tmp_path, no_signal):
    key = _write_key(tmp_path)
    other = secrets.token_bytes(32)
    nonce = secrets.token_hex(16)
    cases = [
        # 另一份安装的密钥。
        (403, {"nonce": nonce, "boot_id": "boot-1", "proof": launcher_link.stop_proof(other, nonce, "boot-1")}),
        # 身份证明不能被拿来当停止签名。
        (
            403,
            {
                "nonce": nonce,
                "boot_id": "boot-1",
                "proof": launcher_link.identity_proof(key, nonce, "boot-1", os.getpid()),
            },
        ),
        # 签给上一次启动的请求：后端已经重启过，不能误停新进程。
        (409, {"nonce": nonce, "boot_id": "boot-0", "proof": launcher_link.stop_proof(key, nonce, "boot-0")}),
        (400, {"boot_id": "boot-1", "proof": "0" * 64}),
    ]
    app = _app(tmp_path)
    async with TestClient(TestServer(app)) as client:
        statuses = [
            (await client.post("/api/system/launcher/stop", json=body)).status for _expected, body in cases
        ]
        not_json = await client.post("/api/system/launcher/stop", data=b"nope")

    assert statuses == [expected for expected, _body in cases]
    assert not_json.status == 400
    assert app["runtime_control"]["stop_requested"] is False
    assert no_signal == []


def test_restart_intent_is_recorded_only_for_launcher_managed_installs(tmp_path):
    intent = tmp_path / "_launcher" / launcher_link.RESTART_INTENT_FILE_NAME

    launcher_link.record_restart_intent(tmp_path, boot_id="boot-1", pid=1234)
    assert not intent.exists()

    _write_key(tmp_path)
    launcher_link.record_restart_intent(tmp_path, boot_id="boot-1", pid=1234)
    payload = json.loads(intent.read_text(encoding="utf-8"))
    assert payload["pid"] == 1234
    assert payload["boot_id"] == "boot-1"
    assert not list(intent.parent.glob("*.tmp"))


async def _instant(_seconds):
    return None
