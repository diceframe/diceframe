"""Synchronous SDK for DiceFrame ``bot-extension`` process plugins."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, TextIO

BridgeHandler = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class _BridgeExtension:
    name: str
    title: str
    description: str
    stages: tuple[str, ...]
    priority: int
    timeout_sec: float
    platforms: tuple[str, ...]
    kinds: tuple[str, ...]
    handler: BridgeHandler


class BridgeExtensionRuntime:
    """Registers Bridge hooks/renderers and serves JSON-RPC over stdio."""

    protocol_version = 1

    def __init__(self, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._extensions: dict[str, _BridgeExtension] = {}

    def extension(
        self,
        *,
        name: str,
        title: str,
        description: str,
        stages: list[str] | tuple[str, ...],
        priority: int = 0,
        timeout_sec: float = 5,
        platforms: list[str] | tuple[str, ...] = (),
        kinds: list[str] | tuple[str, ...] = (),
    ) -> Callable[[BridgeHandler], BridgeHandler]:
        def register(handler: BridgeHandler) -> BridgeHandler:
            if name in self._extensions:
                raise ValueError(f"Bot Bridge 扩展名称重复：{name}")
            self._extensions[name] = _BridgeExtension(
                name=name,
                title=title,
                description=description,
                stages=tuple(stages),
                priority=int(priority),
                timeout_sec=float(timeout_sec),
                platforms=tuple(platforms),
                kinds=tuple(kinds),
                handler=handler,
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
                "bridge_extensions": [
                    {
                        "name": item.name,
                        "title": item.title,
                        "description": item.description,
                        "stages": list(item.stages),
                        "priority": item.priority,
                        "timeout_sec": item.timeout_sec,
                        "platforms": list(item.platforms),
                        "kinds": list(item.kinds),
                    }
                    for item in self._extensions.values()
                ],
            }
        if method == "bridge.apply":
            name = str(params.get("name") or "")
            stage = str(params.get("stage") or "")
            payload = params.get("payload")
            extension = self._extensions.get(name)
            if not extension:
                raise ValueError(f"Bot Bridge 扩展不存在：{name}")
            if stage not in extension.stages:
                raise ValueError(f"扩展 {name} 不支持阶段：{stage}")
            if not isinstance(payload, dict):
                raise ValueError("payload 必须是对象")
            result = extension.handler(stage, payload)
            if not isinstance(result, dict):
                raise ValueError("Bot Bridge 扩展必须返回 JSON 对象")
            return result
        raise ValueError(f"不支持的 RPC 方法：{method}")
