# -*- coding: utf-8 -*-
"""监听器拓扑：把主机地址、端口与传输配置展开成 "每个地址一个 site" 的计划。

web_server 只负责执行计划；解析与校验都在这里，因此可以脱离 aiohttp 运行时
单测。与 transport 一致：构建失败不抛异常，而是返回 warning，由调用方写入
启动日志，绝不静默丢弃用户显式配置的监听地址。
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from aiohttp import web

from src.web_transport.transport import ServerTransport

logger = logging.getLogger("trpg.web_transport")

MAX_PORT = 65535
_HOST_SEPARATORS = re.compile(r"[,\s]+")
DEFAULT_HOST = "0.0.0.0"


@dataclass(frozen=True)
class ListenerPlan:
    """一个监听器：地址 + 端口 + 是否使用 TLS。"""

    host: str
    port: int
    tls: bool

    @property
    def scheme(self) -> str:
        return "https" if self.tls else "http"

    @property
    def address(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def display_host(self) -> str:
        """启动日志里给人看的地址：通配地址按同族回环展示。"""

        if self.host in {"", "*", "0.0.0.0", "::"}:
            return "::1" if ":" in self.host else "127.0.0.1"
        return self.host

    def url(self, display_host: str | None = None) -> str:
        host = str(display_host) if display_host else self.display_host()
        host = f"[{host}]" if ":" in host else host
        return f"{self.scheme}://{host}:{self.port}"


def internal_loopback_host(hosts: Sequence[str]) -> str:
    """内部客户端（插件、健康检查）应连接的回环地址。

    只要绑定地址里存在 IPv4（含 0.0.0.0）就用 127.0.0.1；纯 IPv6 监听时用 ::1，
    否则插件会去连一个根本没在监听的 IPv4 localhost。
    """

    normalized = normalize_hosts(hosts)
    if normalized and all(":" in host for host in normalized):
        return "::1"
    return "127.0.0.1"


def split_hosts(raw: object) -> list[str]:
    """把 "a,b c" 形式的主机串拆成列表；空白项与重复项会被丢弃。"""

    return [item for item in _HOST_SEPARATORS.split(str(raw or "").strip()) if item]


def normalize_hosts(raw: Iterable[object]) -> list[str]:
    """规范化监听地址：只接受裸主机名或 IP（不接受 scheme / 端口 / 路径）。"""

    hosts: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item or "").strip()
        if not value:
            continue
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1].strip()
        if not value:
            continue
        if "://" in value or "/" in value:
            logger.warning("忽略无效的监听地址 %r：只接受主机名或 IP，不要带协议或路径", value)
            continue
        if ":" in value:
            try:
                ipaddress.IPv6Address(value)
            except ValueError:
                logger.warning("忽略无效的监听地址 %r：包含冒号但不是合法 IPv6 地址", value)
                continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        hosts.append(value)
    return hosts


def parse_port(value: object) -> int | None:
    """解析可选端口：非法或越界返回 None（由调用方给出 warning）。"""

    if value is None or value == "":
        return None
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= MAX_PORT else None


def build_listener_plan(
    *,
    hosts: Sequence[str],
    port: int,
    transport: ServerTransport,
    http_port: int | None = None,
    https_port: int | None = None,
) -> tuple[list[ListenerPlan], list[str]]:
    """展开监听计划。返回 (计划, 警告列表)；警告必须由调用方记录，不静默。"""

    warnings: list[str] = []
    primary_hosts = normalize_hosts(hosts) or [DEFAULT_HOST]
    primary_tls = transport.ssl_context is not None
    plan: list[ListenerPlan] = []

    def add(host: str, item_port: int, tls: bool) -> bool:
        if any(existing.host == host and existing.port == item_port for existing in plan):
            return False
        plan.append(ListenerPlan(host=host, port=item_port, tls=tls))
        return True

    for host in primary_hosts:
        add(host, port, primary_tls)

    def add_extra(raw_port: object, *, tls: bool, env_name: str) -> None:
        if raw_port is None or raw_port == "":
            return
        extra_port = parse_port(raw_port)
        if extra_port is None:
            warnings.append(f"忽略 {env_name}={raw_port!r}：端口必须是 1-{MAX_PORT} 的整数")
            return
        if extra_port == port:
            warnings.append(f"忽略 {env_name}={extra_port}：与主端口相同")
            return
        if tls and transport.ssl_context is None:
            detail = f"（{transport.degraded_error}）" if transport.degraded_error else ""
            warnings.append(
                f"忽略 {env_name}={extra_port}：当前未启用 HTTPS，没有可用的 TLS 上下文{detail}"
            )
            return
        added = [host for host in primary_hosts if add(host, extra_port, tls)]
        if not added:
            warnings.append(f"忽略 {env_name}={extra_port}：该端口已由其它监听器占用")

    add_extra(http_port, tls=False, env_name="TRPG_WEB_HTTP_PORT")
    add_extra(https_port, tls=True, env_name="TRPG_WEB_HTTPS_PORT")
    return plan, warnings


async def start_listeners(
    runner: web.AppRunner,
    plan: Sequence[ListenerPlan],
    transport: ServerTransport,
) -> tuple[list[ListenerPlan], list[str]]:
    """按计划启动监听器。单个地址失败不影响其它地址，失败原因原样返回。"""

    started: list[ListenerPlan] = []
    failures: list[str] = []
    for item in plan:
        site = web.TCPSite(
            runner,
            host=item.host,
            port=item.port,
            ssl_context=transport.ssl_context if item.tls else None,
        )
        try:
            await site.start()
        except OSError as exc:
            failure = f"{item.scheme}://{item.address} 监听失败：{exc}"
            failures.append(failure)
            logger.error("%s", failure)
            continue
        started.append(item)
    return started, failures
