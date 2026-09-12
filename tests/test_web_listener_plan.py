"""监听拓扑：HTTP/HTTPS 同时监听、多地址展开与真实双协议连通性。

需求来自社区 PR #242（IPv4/IPv6 下 HTTP 与 HTTPS 同时运行）。这里按仓库既有
transport 契约实现：TLS 上下文仍由 build_server_transport 提供，本模块只做
"哪个地址、哪个端口、要不要 TLS" 的展开与启动。
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from src.web_transport.config import TLS_MODE_OFF, TLS_MODE_SELF_SIGNED, WebTransportConfig
from src.web_transport.listeners import (
    ListenerPlan,
    build_listener_plan,
    internal_api_base,
    internal_api_listener,
    internal_loopback_host,
    normalize_hosts,
    parse_port,
    resolve_internal_listener,
    split_hosts,
    start_listeners,
)
from src.web_transport.transport import ServerTransport, build_server_transport


def _http_transport() -> ServerTransport:
    return ServerTransport(
        scheme="http",
        tls_mode=TLS_MODE_OFF,
        ssl_context=None,
        endpoint=build_server_transport(
            WebTransportConfig(), Path("."), 18000
        ).endpoint,
    )


def _tls_transport(tmp_path: Path, port: int = 18000) -> ServerTransport:
    transport = build_server_transport(
        WebTransportConfig(tls_mode=TLS_MODE_SELF_SIGNED), tmp_path, port
    )
    assert transport.ssl_context is not None, transport.degraded_error
    return transport


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_single_host_plan_keeps_the_previous_behaviour() -> None:
    plan, warnings = build_listener_plan(
        hosts=["0.0.0.0"], port=18000, transport=_http_transport(),
    )

    assert plan == [ListenerPlan(host="0.0.0.0", port=18000, tls=False)]
    assert warnings == []
    assert plan[0].url() == "http://127.0.0.1:18000"


def test_http_and_https_listen_side_by_side(tmp_path: Path) -> None:
    """社区 PR #242 的核心诉求：TLS 开着的同时再开一个 HTTP 端口。"""

    transport = _tls_transport(tmp_path)
    plan, warnings = build_listener_plan(
        hosts=["0.0.0.0"], port=18000, transport=transport, http_port=18080,
    )

    assert warnings == []
    assert [(item.port, item.scheme) for item in plan] == [(18000, "https"), (18080, "http")]


def test_https_extra_port_requires_a_tls_context() -> None:
    plan, warnings = build_listener_plan(
        hosts=["0.0.0.0"], port=18000, transport=_http_transport(), https_port=18443,
    )

    assert [(item.port, item.scheme) for item in plan] == [(18000, "http")]
    assert len(warnings) == 1
    assert "TRPG_WEB_HTTPS_PORT=18443" in warnings[0]
    assert "未启用 HTTPS" in warnings[0]


def test_degraded_transport_reports_why_https_is_unavailable() -> None:
    transport = _http_transport()
    transport.degraded_error = "本地 HTTPS 启用失败，已暂时回退 HTTP：证书缺失"

    _plan, warnings = build_listener_plan(
        hosts=["0.0.0.0"], port=18000, transport=transport, https_port=18443,
    )

    assert "证书缺失" in warnings[0]


def test_multiple_hosts_expand_to_one_listener_each() -> None:
    plan, warnings = build_listener_plan(
        hosts=["0.0.0.0", "::"], port=18000, transport=_http_transport(),
        http_port=18080,
    )

    assert warnings == []
    assert [(item.host, item.port) for item in plan] == [
        ("0.0.0.0", 18000), ("::", 18000), ("0.0.0.0", 18080), ("::", 18080),
    ]
    assert plan[1].address == "[::]:18000"
    assert plan[1].url("::") == "http://[::]:18000"


def test_duplicate_addresses_and_ports_collapse_without_duplicating_sites() -> None:
    plan, warnings = build_listener_plan(
        hosts=["0.0.0.0", "0.0.0.0", "[::]"], port=18000,
        transport=_http_transport(), http_port=18000, https_port=18000,
    )

    assert [(item.host, item.port) for item in plan] == [("0.0.0.0", 18000), ("::", 18000)]
    assert warnings == [
        "忽略 TRPG_WEB_HTTP_PORT=18000：与主端口相同",
        "忽略 TRPG_WEB_HTTPS_PORT=18000：与主端口相同",
    ]


def test_invalid_hosts_and_ports_are_skipped_with_warnings() -> None:
    plan, warnings = build_listener_plan(
        hosts=["http://example.com", "host:8080", "good.example", "/tmp/x"],
        port=18000, transport=_http_transport(),
        http_port="70000", https_port="abc",
    )

    assert [item.host for item in plan] == ["good.example"]
    assert len(warnings) == 2
    assert "TRPG_WEB_HTTP_PORT='70000'" in warnings[0]
    assert "TRPG_WEB_HTTPS_PORT='abc'" in warnings[1]


def test_all_invalid_hosts_fall_back_to_the_default_bind() -> None:
    plan, _warnings = build_listener_plan(
        hosts=["http://x", ""], port=18000, transport=_http_transport(),
    )

    assert [item.host for item in plan] == ["0.0.0.0"]


def test_host_helpers() -> None:
    assert split_hosts("0.0.0.0, ::  127.0.0.1") == ["0.0.0.0", "::", "127.0.0.1"]
    assert split_hosts(None) == []
    assert normalize_hosts(["127.0.0.1", "127.0.0.1", " :: "]) == ["127.0.0.1", "::"]
    assert parse_port("18080") == 18080
    assert parse_port(0) is None
    assert parse_port("65536") is None
    assert parse_port("") is None


def test_startup_url_uses_the_same_address_family() -> None:
    """启动日志不能对 IPv6-only 监听打印一个不可用的 IPv4 回环地址。"""

    ipv6_plan, _warnings = build_listener_plan(
        hosts=["::"], port=18000, transport=_http_transport(),
    )
    assert ipv6_plan[0].url() == "http://[::1]:18000"

    ipv4_plan, _warnings = build_listener_plan(
        hosts=["0.0.0.0"], port=18000, transport=_http_transport(),
    )
    assert ipv4_plan[0].url() == "http://127.0.0.1:18000"

    explicit, _warnings = build_listener_plan(
        hosts=["10.0.0.9"], port=18000, transport=_http_transport(),
    )
    assert explicit[0].url() == "http://10.0.0.9:18000"


def test_internal_loopback_follows_the_bound_family() -> None:
    """插件用的 TRPG_API_BASE 必须指向真的在监听的地址族。"""

    assert internal_loopback_host(["0.0.0.0"]) == "127.0.0.1"
    assert internal_loopback_host(["0.0.0.0", "::"]) == "127.0.0.1"
    assert internal_loopback_host(["127.0.0.1"]) == "127.0.0.1"
    assert internal_loopback_host(["::"]) == "::1"
    assert internal_loopback_host(["::", "::1"]) == "::1"
    assert internal_loopback_host([]) == "127.0.0.1"


def test_internal_api_listener_prefers_a_reachable_listener() -> None:
    """内部 API 必须挑一个本机真的连得上的监听器，而不是只看配置字符串。"""

    plan, _warnings = build_listener_plan(
        hosts=["10.0.0.9"], port=18000, transport=_http_transport(),
    )
    assert internal_api_listener(plan) is plan[0]
    assert internal_api_base(plan[0]) == "http://10.0.0.9:18000"

    wildcard, _warnings = build_listener_plan(
        hosts=["0.0.0.0", "::"], port=18000, transport=_http_transport(),
    )
    chosen = internal_api_listener(wildcard)
    assert chosen is wildcard[0]
    assert internal_api_base(chosen) == "http://127.0.0.1:18000"

    ipv6_only, _warnings = build_listener_plan(
        hosts=["::"], port=18000, transport=_http_transport(),
    )
    assert internal_api_base(internal_api_listener(ipv6_only)) == "http://[::1]:18000"

    explicit_loopback, _warnings = build_listener_plan(
        hosts=["192.168.1.5", "127.0.0.1"], port=18000, transport=_http_transport(),
    )
    assert internal_api_base(internal_api_listener(explicit_loopback)) == "http://127.0.0.1:18000"

    assert internal_api_listener([]) is None


def test_internal_api_uses_an_extra_listener_when_the_primary_failed() -> None:
    """主端口没起来、附加端口起来了：内部地址要跟着实际成功的那个走。"""

    plan, _warnings = build_listener_plan(
        hosts=["127.0.0.1"], port=18000, transport=_http_transport(),
        http_port=18080,
    )
    primary, extra = plan

    chosen, warning = resolve_internal_listener(plan, [primary])
    assert chosen == primary and warning == ""

    chosen, warning = resolve_internal_listener(plan, [extra])
    assert chosen == extra
    assert internal_api_base(chosen) == "http://127.0.0.1:18080"
    assert "未成功监听" in warning and str(primary.port) in warning

    chosen, warning = resolve_internal_listener(plan, [])
    assert chosen is None and "没有其它可用监听器" in warning


@pytest.mark.asyncio
async def test_dual_listeners_actually_serve_http_and_https(tmp_path: Path) -> None:
    """真实起两个监听器：HTTPS 主端口 + HTTP 附加端口，两边都要能连上。"""

    https_port = _free_port()
    http_port = _free_port()
    transport = _tls_transport(tmp_path, https_port)
    plan, warnings = build_listener_plan(
        hosts=["127.0.0.1"], port=https_port, transport=transport, http_port=http_port,
    )
    assert warnings == []

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api/system/update/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        started, failures = await start_listeners(runner, plan, transport)
        assert failures == []
        assert {(item.port, item.scheme) for item in started} == {
            (https_port, "https"), (http_port, "http"),
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{http_port}/api/system/update/health") as response:
                assert response.status == 200
                assert await response.json() == {"ok": True}
            async with session.get(
                f"https://127.0.0.1:{https_port}/api/system/update/health", ssl=False,
            ) as response:
                assert response.status == 200
                assert await response.json() == {"ok": True}
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_one_failed_address_does_not_stop_the_others(tmp_path: Path) -> None:
    """多地址里某个地址被占用时，其余监听器仍要能用（失败原因显式返回）。"""

    busy_port = _free_port()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", busy_port))
    listener.listen(1)
    try:
        plan, _warnings = build_listener_plan(
            hosts=["127.0.0.1"], port=busy_port, transport=_http_transport(),
            http_port=_free_port(),
        )
        app = web.Application()
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            started, failures = await start_listeners(runner, plan, _http_transport())
            assert [item.port for item in started] == [plan[1].port]
            assert len(failures) == 1
            assert str(busy_port) in failures[0]
        finally:
            await runner.cleanup()
    finally:
        listener.close()


@pytest.mark.asyncio
async def test_plan_start_is_idempotent_for_a_single_site() -> None:
    """单监听器计划（默认配置）行为与改造前一致：只起一个 http site。"""

    port = _free_port()
    transport = _http_transport()
    plan, _warnings = build_listener_plan(hosts=["127.0.0.1"], port=port, transport=transport)

    app = web.Application()

    async def root(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", root)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        started, failures = await start_listeners(runner, plan, transport)
        assert failures == [] and len(started) == 1
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/") as response:
                assert response.status == 200
                assert await response.text() == "ok"
    finally:
        await runner.cleanup()
        await asyncio.sleep(0)
