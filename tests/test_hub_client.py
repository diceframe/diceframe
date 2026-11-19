from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from src.hub_client import (
    HubClient,
    HubConnectionUnavailable,
    HubHTTPError,
    HubUnavailable,
    _PLUGIN_DETAIL_TIMEOUT,
    _platform_name,
    _validated_base_url,
)
from src.plugin_host.marketplace import PluginMarketplace
from src.plugin_host.mirrors import FetchResult, validate_public_http_url
from src.webui.routes.hub import (
    _ClientDisconnected,
    _await_hub_read,
    api_hub_rendezvous_config,
    api_hub_rendezvous_room_create,
)
from src.webui.services import legal
from src.webui.services.hub import HubDependencies, HubService
from src.version import __version__


class _LegalApi:
    _legal_documents: dict | None = None

    async def current_legal_documents(self):
        return self._legal_documents or legal.bundled_documents()

    def legal_bundle_version(self, documents):
        return legal.bundle_version(documents)

    def legal_acceptance_payload(self, documents, language):
        return legal.acceptance_payload(documents, language)

    def legal_accepted(self, state, documents=None):
        return legal.accepted(state, documents)

    def record_legal_acceptance(self, state, **kwargs):
        return legal.record_acceptance(state, **kwargs)


def _hub_service(api: _LegalApi) -> HubService:
    return HubService(HubDependencies(
        client=getattr(api, "_hub", None),
        plugin_host=getattr(api, "_plugins", None),
        config_state=lambda: getattr(api, "_config_state", {}),
        save_config=getattr(api, "_save_config", lambda: None),
        current_legal_documents=api.current_legal_documents,
        legal_bundle_version=api.legal_bundle_version,
        legal_acceptance_payload=api.legal_acceptance_payload,
        legal_accepted=api.legal_accepted,
        record_legal_acceptance=api.record_legal_acceptance,
    ))


async def _serve(routes: list[tuple[str, str, object]]):
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    socket = site._server.sockets[0]
    return runner, f"http://127.0.0.1:{socket.getsockname()[1]}"


def test_hub_url_requires_https_except_loopback():
    assert _validated_base_url("https://api.diceframe.com/") == "https://api.diceframe.com"
    assert _validated_base_url("http://127.0.0.1:18080") == "http://127.0.0.1:18080"
    with pytest.raises(ValueError):
        _validated_base_url("http://example.com")
    with pytest.raises(ValueError):
        _validated_base_url("https://user:pass@example.com")


@pytest.mark.asyncio
async def test_hub_preferences_default_telemetry_off_until_active_consent(tmp_path):
    class Client:
        identity_file = tmp_path / "hub-installation.json"

    class Api(_LegalApi):
        _config_state = {}
        _hub = Client()

    documents = legal.bundled_documents()

    Api._legal_documents = documents
    result = await _hub_service(Api()).preferences()

    assert result["telemetry_enabled"] is False
    assert result["choice_made"] is False
    assert result["legal_accepted"] is False


@pytest.mark.asyncio
async def test_first_start_records_legal_acceptance_without_requiring_hub():
    class Api(_LegalApi):
        _config_state = {}
        _hub = None

        def _save_config(self):
            return None

    documents = legal.bundled_documents()

    Api._legal_documents = documents
    api = Api()
    result = await _hub_service(api).update_preferences(
        False, legal.acceptance_payload(documents, "zh-CN"),
    )

    assert result["telemetry_enabled"] is False
    assert result["choice_made"] is True
    assert result["legal_accepted"] is True
    assert api._config_state["legal_terms_accepted_version"] == "1.1"


@pytest.mark.asyncio
async def test_telemetry_cannot_be_enabled_before_current_privacy_acceptance():
    class Api(_LegalApi):
        _config_state = {}
        _hub = None

        def _save_config(self):
            return None

    with pytest.raises(ValueError, match="隐私政策"):
        await _hub_service(Api()).update_preferences(True)


@pytest.mark.asyncio
async def test_catalog_is_public_and_uses_stale_disk_cache_offline(tmp_path):
    async def plugins(_request):
        return web.json_response(
            {
                "items": [{"id": "sample", "name": "Sample", "manifest": {}}],
                "pages": 1,
            }
        )

    runner, base_url = await _serve([("GET", "/v1/plugins", plugins)])
    client = HubClient(tmp_path, base_url=base_url)
    try:
        fresh = await client.catalog()
        assert fresh["ok"] is True
        assert fresh["items"][0]["id"] == "sample"
        assert not client.identity_file.exists()
        await runner.cleanup()

        stale = await client.catalog()
        assert stale["ok"] is True
        assert stale["source"]["stale"] is True
        assert stale["items"] == fresh["items"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rendezvous_room_creation_uses_pseudonymous_installation_identity(tmp_path):
    async def installation(request):
        assert await request.json() == {
            "app_version": __version__,
            "platform": _platform_name(),
            "telemetry_enabled": False,
        }
        return web.json_response(
            {"installation_id": "install-rendezvous", "installation_token": "room-token"},
            status=201,
        )

    async def create_room(request):
        assert request.headers.get("Authorization") == "Bearer room-token"
        assert await request.json() == {"peer_count": 2}
        return web.json_response(
            {
                "protocol_version": 2,
                "topology": "host-star",
                "room_code": "ABCDEFGH",
                "host_peer_id": "h_abcdefghijk",
                "host_token": "host-secret",
                "invitations": [
                    {"peer_id": "p_abcdefghijk", "token": "guest-secret"}
                ],
                "expires_at": "2026-08-20T12:05:00+00:00",
                "websocket_url": (
                    "ws://127.0.0.1:18080/v1/rendezvous/rooms/ABCDEFGH/ws"
                ),
            },
            status=201,
        )

    runner, base_url = await _serve(
        [
            ("POST", "/v1/installations", installation),
            ("POST", "/v1/rendezvous/rooms", create_room),
        ]
    )
    client = HubClient(tmp_path, base_url=base_url)
    try:
        room = await client.create_rendezvous_room(2)
        assert room["protocol_version"] == 2
        assert room["invitations"][0]["token"] == "guest-secret"
        assert client.identity_file.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rendezvous_retries_once_when_connection_was_never_established(tmp_path, monkeypatch):
    client = HubClient(tmp_path, base_url="http://127.0.0.1:9")
    calls = 0

    async def flaky_request(peer_count):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HubConnectionUnavailable(retry_safe=True)
        return {
            "protocol_version": 2,
            "topology": "host-star",
            "room_code": "ABCDEFGH",
            "host_peer_id": "h_abcdefghijk",
            "host_token": "host-secret",
            "invitations": [{"peer_id": "p_abcdefghijk", "token": "guest-secret"}],
            "expires_at": "2026-08-20T12:05:00+00:00",
            "websocket_url": "ws://127.0.0.1:18080/v1/rendezvous/rooms/ABCDEFGH/ws",
        }

    monkeypatch.setattr(client, "_create_rendezvous_room_request", flaky_request)

    room = await client.create_rendezvous_room(2)

    assert room["room_code"] == "ABCDEFGH"
    assert calls == 2


@pytest.mark.asyncio
async def test_rendezvous_does_not_retry_after_request_may_have_been_sent(tmp_path, monkeypatch):
    client = HubClient(tmp_path, base_url="http://127.0.0.1:9")
    calls = 0

    async def failed_request(_peer_count):
        nonlocal calls
        calls += 1
        raise HubConnectionUnavailable(retry_safe=False)

    monkeypatch.setattr(client, "_create_rendezvous_room_request", failed_request)

    with pytest.raises(HubConnectionUnavailable, match="暂时无法连接"):
        await client.create_rendezvous_room(2)
    assert calls == 1


@pytest.mark.asyncio
async def test_rendezvous_401_rotates_identity_only_after_successful_reregister(tmp_path):
    """瞬时 401 不销毁本地身份：只有重注册成功才落盘新身份。"""
    calls = {"rooms": 0, "installations": 0}

    async def installation(request):
        calls["installations"] += 1
        return web.json_response(
            {"installation_id": "install-new", "installation_token": "new-token"},
            status=201,
        )

    async def create_room(request):
        calls["rooms"] += 1
        auth = request.headers.get("Authorization") or ""
        if auth == "Bearer stale-token":
            # 第一次：旧 token 已被 Hub 收回 → 401
            return web.json_response({"error": "invalid token"}, status=401)
        assert auth == "Bearer new-token"
        return web.json_response(
            {
                "protocol_version": 2,
                "topology": "host-star",
                "room_code": "ABCDEFGH",
                "host_peer_id": "h_abcdefghijk",
                "host_token": "host-secret",
                "invitations": [
                    {"peer_id": "p_abcdefghijk", "token": "guest-secret"}
                ],
                "expires_at": "2026-08-20T12:05:00+00:00",
                "websocket_url": (
                    "ws://127.0.0.1:18080/v1/rendezvous/rooms/ABCDEFGH/ws"
                ),
            },
            status=201,
        )

    runner, base_url = await _serve(
        [
            ("POST", "/v1/installations", installation),
            ("POST", "/v1/rendezvous/rooms", create_room),
        ]
    )
    client = HubClient(tmp_path, base_url=base_url)
    client._atomic_write(
        client.identity_file,
        {"installation_id": "install-old", "installation_token": "stale-token"},
    )
    try:
        room = await client.create_rendezvous_room(2)
        assert room["room_code"] == "ABCDEFGH"
        assert calls == {"rooms": 2, "installations": 1}
        identity = json.loads(client.identity_file.read_text(encoding="utf-8"))
        assert identity["installation_token"] == "new-token"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rendezvous_transient_401_keeps_stored_identity(tmp_path):
    """重注册失败（如 Hub 异常）时，原身份文件必须原样保留。"""
    async def installation(request):
        return web.json_response({"error": "hub trouble"}, status=503)

    async def create_room(request):
        return web.json_response({"error": "invalid token"}, status=401)

    runner, base_url = await _serve(
        [
            ("POST", "/v1/installations", installation),
            ("POST", "/v1/rendezvous/rooms", create_room),
        ]
    )
    client = HubClient(tmp_path, base_url=base_url)
    client._atomic_write(
        client.identity_file,
        {"installation_id": "install-old", "installation_token": "stale-token"},
    )
    try:
        with pytest.raises(HubUnavailable):
            await client.create_rendezvous_room(2)
        identity = json.loads(client.identity_file.read_text(encoding="utf-8"))
        assert identity["installation_token"] == "stale-token"
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_rendezvous_config_is_anonymous(tmp_path):
    async def config(request):
        assert request.headers.get("Authorization") is None
        return web.json_response(
            {
                "enabled": True,
                "entry_visible": False,
                "message": "",
            }
        )

    runner, base_url = await _serve([("GET", "/v1/rendezvous/config", config)])
    client = HubClient(tmp_path, base_url=base_url)
    try:
        result = await client.rendezvous_config()
        assert result["entry_visible"] is False
        assert not client.identity_file.exists()
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_local_rendezvous_route_validates_count_and_confirmation():
    app = web.Application()
    denied = make_mocked_request(
        "POST",
        "/api/hub/rendezvous/rooms",
        app=app,
    )
    response = await api_hub_rendezvous_room_create(denied)
    assert response.status == 403

    class Api:
        async def create_rendezvous_room(self, peer_count):
            return {"ok": True, "protocol_version": 2, "peer_count": peer_count}

    app["api"] = Api()
    class JsonRequest:
        def __init__(self, body):
            self.app = app
            self.headers = {"X-TRPG-Confirm": "true"}
            self._body = body

        async def json(self):
            return self._body

    invalid = JsonRequest({"peer_count": 1})
    response = await api_hub_rendezvous_room_create(invalid)
    assert response.status == 400

    allowed = JsonRequest({"peer_count": 6})
    response = await api_hub_rendezvous_room_create(allowed)
    assert response.status == 201
    assert json.loads(response.text)["peer_count"] == 6

    class OfflineApi:
        async def create_rendezvous_room(self, _peer_count):
            raise HubConnectionUnavailable(retry_safe=False)

    app["api"] = OfflineApi()
    response = await api_hub_rendezvous_room_create(JsonRequest({"peer_count": 2}))
    assert response.status == 502
    payload = json.loads(response.text)
    assert payload["error_code"] == "hub_connection_unavailable"
    assert payload["error"] == "暂时无法连接 DiceFrame Hub，请检查网络后重试"


@pytest.mark.asyncio
async def test_local_rendezvous_config_route_is_public_and_proxies_visibility():
    class Api:
        async def rendezvous_config(self):
            return {
                "ok": True,
                "enabled": True,
                "entry_visible": False,
                "message": "",
            }

    app = web.Application()
    app["api"] = Api()
    request = make_mocked_request("GET", "/api/hub/rendezvous/config", app=app)

    response = await api_hub_rendezvous_config(request)

    assert response.status == 200
    assert json.loads(response.text)["entry_visible"] is False


@pytest.mark.asyncio
async def test_download_event_creates_identity_only_when_needed(tmp_path):
    received: list[dict] = []

    async def installation(request):
        assert await request.json() == {
            "app_version": __version__,
            "platform": _platform_name(),
            "telemetry_enabled": False,
        }
        return web.json_response(
            {"installation_id": "install-1", "installation_token": "secret-token"},
            status=201,
        )

    async def event(request):
        assert request.headers["Authorization"] == "Bearer secret-token"
        received.append(await request.json())
        return web.json_response({"accepted": True, "counted": True}, status=202)

    from src.version import __version__

    routes = [
        ("POST", "/v1/installations", installation),
        ("POST", "/v1/plugins/sample/download-events", event),
    ]
    runner, base_url = await _serve(routes)
    # 测试运行平台可能不是 Windows，断言动态平台；保持请求体其余字段严格。
    client = HubClient(tmp_path, base_url=base_url)
    try:
        assert not client.identity_file.exists()
        await client.start()
        assert client.queue_download_event(
            "sample",
            event_id="event_1234567890abcdef",
            kind="install_succeeded",
            plugin_version="1.0.0",
            artifact_hash="a" * 64,
        )
        await asyncio.wait_for(client._event_queue.join(), timeout=2)
        assert len(received) == 1
        identity = json.loads(client.identity_file.read_text(encoding="utf-8"))
        assert identity["installation_id"] == "install-1"
        assert identity["installation_token"] == "secret-token"
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_interaction_writes_allow_identity_creation_beyond_catalog_read_timeout(tmp_path):
    async def installation(_request):
        await asyncio.sleep(2.1)
        return web.json_response(
            {"installation_id": "install-1", "installation_token": "secret-token"},
            status=201,
        )

    async def like(request):
        assert request.headers["Authorization"] == "Bearer secret-token"
        return web.json_response({"liked": True})

    runner, base_url = await _serve(
        [
            ("POST", "/v1/installations", installation),
            ("PUT", "/v1/plugins/sample/like", like),
        ]
    )
    client = HubClient(tmp_path, base_url=base_url)
    try:
        assert await client.set_like("sample", True) == {"liked": True}
        assert client.identity_file.exists()
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_plugin_detail_allows_a_slow_user_requested_read(tmp_path):
    async def slow_detail(_request):
        await asyncio.sleep(2.1)
        return web.json_response({"id": "sample"})

    runner, base_url = await _serve([("GET", "/v1/plugins/sample", slow_detail)])
    client = HubClient(tmp_path, base_url=base_url)
    try:
        assert _PLUGIN_DETAIL_TIMEOUT.total == 60
        assert await client.plugin_detail("sample") == {"id": "sample"}
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_hub_read_is_cancelled_when_the_browser_disconnects():
    cancelled = asyncio.Event()

    class Transport:
        checks = 0

        def is_closing(self):
            self.checks += 1
            return self.checks > 1

    class Request:
        transport = Transport()

    async def slow_read():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(_ClientDisconnected):
        await _await_hub_read(Request(), slow_read())
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_three_failures(tmp_path):
    calls = 0

    async def unavailable(_request):
        nonlocal calls
        calls += 1
        return web.json_response({"detail": "down"}, status=503)

    runner, base_url = await _serve([("GET", "/v1/plugins/sample", unavailable)])
    client = HubClient(tmp_path, base_url=base_url)
    try:
        for _ in range(3):
            with pytest.raises(HubUnavailable):
                await client.plugin_detail("sample")
        with pytest.raises(HubUnavailable, match="熔断"):
            await client.plugin_detail("sample")
        assert calls == 3
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_client_errors_do_not_open_global_circuit_breaker(tmp_path):
    calls = 0

    async def missing(_request):
        nonlocal calls
        calls += 1
        return web.json_response({"detail": "missing"}, status=404)

    runner, base_url = await _serve([("GET", "/v1/plugins/missing", missing)])
    client = HubClient(tmp_path, base_url=base_url)
    try:
        for _ in range(4):
            with pytest.raises(HubUnavailable, match="404"):
                await client.plugin_detail("missing")
        assert calls == 4
        assert client._failures == 0
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_disabling_telemetry_stops_local_heartbeat_even_offline(tmp_path):
    client = HubClient(
        tmp_path,
        base_url="http://127.0.0.1:9",
        telemetry_enabled=True,
        telemetry_choice_made=True,
    )
    sleeper = asyncio.create_task(asyncio.sleep(3600))
    client._heartbeat_task = sleeper

    await client.set_telemetry(False)
    await asyncio.sleep(0)

    assert client.telemetry_enabled is False
    assert client._heartbeat_task is None
    assert sleeper.cancelled()


@pytest.mark.asyncio
async def test_failed_remote_identity_delete_keeps_local_token_for_retry(tmp_path):
    async def unavailable(_request):
        return web.json_response({"detail": "down"}, status=503)

    runner, base_url = await _serve([("DELETE", "/v1/installations/me", unavailable)])
    client = HubClient(tmp_path, base_url=base_url)
    client._atomic_write(
        client.identity_file,
        {"installation_id": "install-1", "installation_token": "secret-token"},
    )
    try:
        with pytest.raises(HubUnavailable):
            await client.delete_identity()
        assert client.identity_file.exists()
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_marketplace_prefers_hub_and_normalizes_security(tmp_path):
    class FakeHub:
        async def catalog(self):
            return {
                "ok": True,
                "items": [
                    {
                        "id": "sample",
                        "name": "Sample",
                        "version": "1.0.0",
                        "permissions": ["network.client"],
                        "approved_permissions": ["network.client"],
                        "security": {"install_allowed": False, "blocking_reasons": ["blocked"]},
                        "manifest": {"id": "sample", "plugin_type": "content-pack"},
                    }
                ],
                "source": {"mirror_name": "DiceFrame Hub", "hub": True, "stale": False},
            }

    class FailingMirrors:
        async def fetch_raw(self, *args, **kwargs):
            raise AssertionError("Hub 成功时不应访问仓库索引")

    result = await PluginMarketplace(FailingMirrors(), hub_client=FakeHub()).list_plugins()
    assert result["ok"] is True
    assert result["source"]["hub"] is True
    assert result["plugins"][0]["installable"] is False
    assert result["plugins"][0]["verification_error"] == "blocked"
    assert result["plugins"][0]["approved_permissions"] == ["network.client"]


class _FakeMirror:
    def __init__(self, result: FetchResult | None = None):
        self.result = result or FetchResult(ok=False, error="镜像不可用")

    async def fetch_github_url(self, url, *, mirror_id="", binary=False, max_bytes=None):
        return self.result


class _FakeMarketplace:
    def __init__(self, items):
        self.items = items

    async def list_plugins(self):
        return {"plugins": self.items, "ok": True}


class _FakePlugins:
    def __init__(self, item=None, *, mirrors: _FakeMirror | None = None, items=None):
        self.mirrors = mirrors or _FakeMirror()
        self.marketplace = _FakeMarketplace([item] if item is not None else (items or []))


class _FakeHubApi(_LegalApi):
    def __init__(self, hub_client, plugins):
        self._hub = hub_client
        self._plugins = plugins


def _sample_item(repository_url: str = "https://github.com/example/sample") -> dict:
    return {
        "id": "sample",
        "repository_url": repository_url,
        "latest": {"commit_sha": "a" * 40},
    }


@pytest.mark.asyncio
async def test_plugin_readme_hub_success_writes_disk_cache(tmp_path):
    async def readme(_request):
        return web.json_response({"html": "<p>hello</p>", "plugin_id": "sample"})

    runner, base_url = await _serve([("GET", "/v1/plugins/sample/readme", readme)])
    client = HubClient(tmp_path, base_url=base_url)
    api = _FakeHubApi(client, _FakePlugins(item=_sample_item()))
    try:
        result = await _hub_service(api).plugin_readme("sample")
        assert result["ok"] is True
        assert result["html"] == "<p>hello</p>"
        assert result["source"]["hub"] is True
        assert result["source"]["cached"] is False
        assert result["source"]["stale"] is False

        cached = client.read_readme_cache("sample")
        assert cached is not None
        assert cached["html"] == "<p>hello</p>"
        assert cached["repository_url"] == "https://github.com/example/sample"
        assert cached["commit_sha"] == "a" * 40
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_plugin_readme_hub_down_hits_stale_disk_cache(tmp_path):
    async def readme(_request):
        return web.json_response({"html": "<p>cached</p>"})

    runner, base_url = await _serve([("GET", "/v1/plugins/sample/readme", readme)])
    client = HubClient(tmp_path, base_url=base_url)
    api = _FakeHubApi(client, _FakePlugins(item=_sample_item()))
    try:
        fresh = await _hub_service(api).plugin_readme("sample")
        assert fresh["ok"] is True
        assert fresh["html"] == "<p>cached</p>"
        await runner.cleanup()

        # Hub 关闭后再次读取，命中磁盘缓存并标记 stale，不能冒充刚同步。
        stale = await _hub_service(api).plugin_readme("sample")
        assert stale["ok"] is True
        assert stale["html"] == "<p>cached</p>"
        assert stale["source"]["cached"] is True
        assert stale["source"]["stale"] is True
        assert stale["source"]["error"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_plugin_readme_falls_back_to_author_github_raw(tmp_path):
    mirror = _FakeMirror(FetchResult(ok=True, data="# Sample\nreadme from author"))
    api = _FakeHubApi(
        HubClient(tmp_path, base_url="http://127.0.0.1:9"),
        _FakePlugins(item=_sample_item(), mirrors=mirror),
    )
    try:
        result = await _hub_service(api).plugin_readme("sample")
        assert result["ok"] is True
        assert result["html"] == ""
        assert result["markdown"] == "# Sample\nreadme from author"
        assert result["source"]["github"] is True
        assert result["source"]["hub"] is False
    finally:
        await api._hub.close()


@pytest.mark.asyncio
async def test_plugin_readme_rejects_non_github_private_and_oversized(tmp_path):
    client = HubClient(tmp_path, base_url="http://127.0.0.1:9")
    try:
        # 非 GitHub 仓库地址：解析阶段直接拒绝，不发起任何 GitHub 请求。
        bad_api = _FakeHubApi(
            client,
            _FakePlugins(item=_sample_item(repository_url="https://example.com/plugin")),
        )
        result = await _hub_service(bad_api).plugin_readme("sample")
        assert result["ok"] is False
        assert result["source"]["github"] is False

        # 超大响应被镜像层拒绝后，兜底静默放弃。
        oversized = _FakeMirror(
            FetchResult(ok=False, error="下载内容超过限制：262144 bytes")
        )
        big_api = _FakeHubApi(
            client,
            _FakePlugins(item=_sample_item(), mirrors=oversized),
        )
        result = await _hub_service(big_api).plugin_readme("sample")
        assert result["ok"] is False
        assert result["source"]["github"] is False

        # 私网地址被 SSRF 防护拒绝。
        with pytest.raises(ValueError):
            validate_public_http_url("http://127.0.0.1:8000/secrets")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_plugin_readme_cancelled_when_detail_modal_closes():
    cancelled = asyncio.Event()

    class Transport:
        checks = 0

        def is_closing(self):
            self.checks += 1
            return self.checks > 1

    class Request:
        transport = Transport()

    async def slow_read():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    # README 路由使用同一取消机制（含 60 秒总时限包装）。
    with pytest.raises(_ClientDisconnected):
        await _await_hub_read(Request(), asyncio.wait_for(slow_read(), timeout=60))
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_accepting_privacy_does_not_wait_for_a_slow_hub_sync(tmp_path):
    """确认按钮不得被 Hub 遥测同步拖住（网络不通时此前会等满 30s 超时）。"""
    import asyncio
    import time

    release = asyncio.Event()
    synced = []

    class SlowClient:
        identity_file = tmp_path / "hub-installation.json"

        async def set_telemetry(self, enabled, *, choice_made=True):
            synced.append((enabled, choice_made))
            await release.wait()  # 模拟 Hub 挂起

    class Api(_LegalApi):
        _config_state = {}
        _hub = SlowClient()

        def _save_config(self):
            return None

    documents = legal.bundled_documents()
    Api._legal_documents = documents
    started = time.monotonic()
    result = await _hub_service(Api()).update_preferences(
        False, legal.acceptance_payload(documents, "zh-CN"),
    )
    elapsed = time.monotonic() - started
    assert result["choice_made"] is True
    assert elapsed < 1.0                     # 响应不被 Hub 阻塞
    await asyncio.sleep(0)                   # 后台任务已启动
    assert synced == [(False, True)]
    release.set()
