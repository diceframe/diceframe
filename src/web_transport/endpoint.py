# -*- coding: utf-8 -*-
"""ServerEndpoint：统一生成本机健康检查、浏览器与 API 使用的地址。

禁止各端自行拼接 scheme；scheme 只由 Web Transport 配置决定。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServerEndpoint:
    scheme: str
    port: int
    # 本机内部调用（插件、健康检查）使用的回环地址；纯 IPv6 监听时为 ::1。
    local_host: str = "127.0.0.1"

    def url(self, host: str | None = None, path: str = "") -> str:
        target = host or self.local_host
        if ":" in target and not target.startswith("["):
            # IPv6 字面量需要方括号。
            target = f"[{target}]"
        url = f"{self.scheme}://{target}:{self.port}"
        if path:
            url += path if path.startswith("/") else f"/{path}"
        return url

    def origin(self, host: str | None = None) -> str:
        return self.url(host)

    def local_health_url(self) -> str:
        return self.url(path="/api/system/update/health")
