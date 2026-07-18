"""Versioned JSON-RPC transport for managed process plugins."""

from __future__ import annotations

import asyncio
import json
from typing import Any

PLUGIN_PROTOCOL_VERSION = 1
MAX_RPC_MESSAGE_BYTES = 256 * 1024
DEFAULT_RPC_TIMEOUT = 30.0


class PluginProtocolError(RuntimeError):
    """Raised when a managed plugin violates the runtime protocol."""


class PluginInvocationError(RuntimeError):
    """Raised when a valid plugin rejects one specific invocation."""


def validate_tool_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    """Validate the practical JSON-Schema subset used by tool inputs."""

    def validate(current_schema: Any, value: Any, path: str, depth: int) -> None:
        if depth > 16:
            raise PluginProtocolError("工具输入 Schema 嵌套过深")
        if not isinstance(current_schema, dict):
            return
        expected = current_schema.get("type")
        if expected:
            matches = {
                "object": isinstance(value, dict),
                "array": isinstance(value, list),
                "string": isinstance(value, str),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "boolean": isinstance(value, bool),
                "null": value is None,
            }.get(str(expected), True)
            if not matches:
                raise PluginProtocolError(f"工具参数 {path} 类型应为 {expected}")
        if "enum" in current_schema and isinstance(current_schema["enum"], list) and value not in current_schema["enum"]:
            raise PluginProtocolError(f"工具参数 {path} 不在允许选项中")
        if isinstance(value, dict):
            properties = current_schema.get("properties") if isinstance(current_schema.get("properties"), dict) else {}
            required = current_schema.get("required") if isinstance(current_schema.get("required"), list) else []
            for key in required:
                if str(key) not in value:
                    raise PluginProtocolError(f"工具参数缺少必填字段：{path}.{key}")
            if current_schema.get("additionalProperties") is False:
                extras = sorted(set(value) - set(properties))
                if extras:
                    raise PluginProtocolError(f"工具参数包含未知字段：{path}.{extras[0]}")
            for key, item in value.items():
                if key in properties:
                    validate(properties[key], item, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            if "minItems" in current_schema and len(value) < int(current_schema["minItems"]):
                raise PluginProtocolError(f"工具参数 {path} 数量不足")
            if "maxItems" in current_schema and len(value) > int(current_schema["maxItems"]):
                raise PluginProtocolError(f"工具参数 {path} 数量过多")
            item_schema = current_schema.get("items")
            for index, item in enumerate(value):
                validate(item_schema, item, f"{path}[{index}]", depth + 1)
        elif isinstance(value, str):
            if "minLength" in current_schema and len(value) < int(current_schema["minLength"]):
                raise PluginProtocolError(f"工具参数 {path} 长度不足")
            if "maxLength" in current_schema and len(value) > int(current_schema["maxLength"]):
                raise PluginProtocolError(f"工具参数 {path} 过长")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in current_schema and value < float(current_schema["minimum"]):
                raise PluginProtocolError(f"工具参数 {path} 小于最小值")
            if "maximum" in current_schema and value > float(current_schema["maximum"]):
                raise PluginProtocolError(f"工具参数 {path} 超过最大值")

    validate(schema, arguments, "$", 0)


class JsonRpcStdioClient:
    """A serialized JSON-RPC 2.0 client over a child process' stdio."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self._lock = asyncio.Lock()
        self._next_id = 1

    async def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = DEFAULT_RPC_TIMEOUT,
    ) -> Any:
        if not self.process.stdin or not self.process.stdout:
            raise PluginProtocolError("插件进程没有可用的 RPC 管道")
        async with self._lock:
            if self.process.returncode is not None:
                raise PluginProtocolError(f"插件进程已退出，code={self.process.returncode}")
            request_id = self._next_id
            self._next_id += 1
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(encoded) > MAX_RPC_MESSAGE_BYTES:
                raise PluginProtocolError("插件 RPC 请求超过 256 KB")
            self.process.stdin.write(encoded)
            try:
                await asyncio.wait_for(self.process.stdin.drain(), timeout=timeout)
                raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise PluginProtocolError(f"插件调用超时（{timeout:g} 秒）") from exc
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise PluginProtocolError("插件 RPC 连接已断开") from exc
            except ValueError as exc:
                raise PluginProtocolError("插件 RPC 响应超过 256 KB") from exc
            if not raw:
                raise PluginProtocolError("插件未返回 RPC 响应")
            if len(raw) > MAX_RPC_MESSAGE_BYTES:
                raise PluginProtocolError("插件 RPC 响应超过 256 KB")
            try:
                response = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PluginProtocolError("插件返回了无效 JSON；stdout 只能输出协议消息") from exc
            if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
                raise PluginProtocolError("插件返回了无效 JSON-RPC 响应")
            if response.get("id") != request_id:
                raise PluginProtocolError("插件 RPC 响应 ID 不匹配")
            if "error" in response:
                error = response.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or "插件调用失败")
                else:
                    message = str(error or "插件调用失败")
                raise PluginInvocationError(message)
            if "result" not in response:
                raise PluginProtocolError("插件 RPC 响应缺少 result")
            return response["result"]
