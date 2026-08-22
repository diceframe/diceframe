"""Small synchronous SDK for DiceFrame ``provider`` process plugins.

Provider 插件以 capability 为单位对外提供能力（如 ``text-transform``）。
宿主通过 ``provider.request``（capability + 方法别名）调用；插件进程内同步
处理请求，适合封装独立的外部服务能力。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, TextIO

ProviderHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class _Capability:
    kind: str
    version: int
    title: str
    description: str
    methods: dict[str, str]
    handlers: dict[str, ProviderHandler]


class ProviderRuntime:
    """Registers capabilities and serves the DiceFrame JSON-RPC stdio protocol."""

    protocol_version = 1

    def __init__(self, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._capabilities: dict[str, _Capability] = {}

    def capability(
        self,
        *,
        kind: str,
        version: int = 1,
        title: str = "",
        description: str = "",
    ) -> Callable[[ProviderHandler], ProviderHandler]:
        """注册 capability 的 generate 处理器。

        methods 固定为 ``{"generate": "provider.<kind>.generate"}``，与宿主
        descriptors 校验（``provider.`` 前缀）保持一致。
        """
        if kind in self._capabilities:
            raise ValueError(f"capability kind 重复：{kind}")

        def register(handler: ProviderHandler) -> ProviderHandler:
            self._capabilities[kind] = _Capability(
                kind=kind,
                version=version,
                title=title or kind,
                description=description,
                methods={"generate": f"provider.{kind}.generate"},
                handlers={"generate": handler},
            )
            return handler

        return register

    def run(self) -> None:
        for line in self.stdin:
            if not line.strip():
                continue
            request_id: Any = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                    raise ValueError("无效 JSON-RPC 请求")
                request_id = request.get("id")
                result = self._dispatch(str(request.get("method") or ""), request.get("params"))
                response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
            self.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.stdout.flush()

    def _dispatch(self, method: str, raw_params: Any) -> dict[str, Any]:
        params = raw_params if isinstance(raw_params, dict) else {}
        if method == "initialize":
            requested = int(params.get("protocol_version") or 0)
            if requested != self.protocol_version:
                raise ValueError(f"不支持的协议版本：{requested}")
            return {
                "protocol_version": self.protocol_version,
                "capabilities": [
                    {
                        "kind": capability.kind,
                        "version": capability.version,
                        "title": capability.title,
                        "description": capability.description,
                        "methods": dict(capability.methods),
                    }
                    for capability in self._capabilities.values()
                ],
            }
        if method == "provider.request":
            kind = str(params.get("capability") or "")
            alias = str(params.get("method") or "")
            capability = self._capabilities.get(kind)
            if not capability:
                raise ValueError(f"capability 不存在：{kind}")
            handler = capability.handlers.get(alias)
            if not handler:
                raise ValueError(f"capability {kind} 不支持方法：{alias}")
            arguments = params.get("arguments")
            context = params.get("context") or {}
            if not isinstance(arguments, dict) or not isinstance(context, dict):
                raise ValueError("arguments 和 context 必须是对象")
            result = handler(arguments, context)
            if not isinstance(result, dict):
                raise ValueError("capability 处理器必须返回 JSON 对象")
            return result
        raise ValueError(f"不支持的 RPC 方法：{method}")
