"""Small synchronous SDK for DiceFrame ``tool`` process plugins."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, TextIO

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class _Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


class ToolRuntime:
    """Registers tools and serves the DiceFrame JSON-RPC stdio protocol."""

    protocol_version = 1

    def __init__(self, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._tools: dict[str, _Tool] = {}

    def tool(
        self,
        *,
        name: str,
        title: str,
        description: str,
        input_schema: dict[str, Any] | None = None,
    ) -> Callable[[ToolHandler], ToolHandler]:
        def register(handler: ToolHandler) -> ToolHandler:
            if name in self._tools:
                raise ValueError(f"工具名称重复：{name}")
            self._tools[name] = _Tool(
                name=name,
                title=title,
                description=description,
                input_schema=input_schema or {"type": "object", "properties": {}},
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
                "tools": [
                    {
                        "name": tool.name,
                        "title": tool.title,
                        "description": tool.description,
                        "input_schema": tool.input_schema,
                    }
                    for tool in self._tools.values()
                ],
            }
        if method == "tool.call":
            name = str(params.get("name") or "")
            tool = self._tools.get(name)
            if not tool:
                raise ValueError(f"工具不存在：{name}")
            arguments = params.get("arguments")
            context = params.get("context")
            if not isinstance(arguments, dict) or not isinstance(context, dict):
                raise ValueError("arguments 和 context 必须是对象")
            result = tool.handler(arguments, context)
            if not isinstance(result, dict):
                raise ValueError("工具必须返回 JSON 对象")
            return result
        raise ValueError(f"不支持的 RPC 方法：{method}")
