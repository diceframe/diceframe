"""Versioned JSON-RPC transport for managed process plugins."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from src.localization.messages import t

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
            raise PluginProtocolError(t("plugin_host.runtime_protocol.schema_too_deep"))
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
                raise PluginProtocolError(t("plugin_host.runtime_protocol.argument_wrong_type", path=path, expected=expected))
        if "enum" in current_schema and isinstance(current_schema["enum"], list) and value not in current_schema["enum"]:
            raise PluginProtocolError(t("plugin_host.runtime_protocol.argument_not_in_enum", path=path))
        if isinstance(value, dict):
            properties = current_schema.get("properties") if isinstance(current_schema.get("properties"), dict) else {}
            required = current_schema.get("required") if isinstance(current_schema.get("required"), list) else []
            for key in required:
                if str(key) not in value:
                    raise PluginProtocolError(t("plugin_host.runtime_protocol.argument_required_missing", path=path, field=key))
            if current_schema.get("additionalProperties") is False:
                extras = sorted(set(value) - set(properties))
                if extras:
                    raise PluginProtocolError(t("plugin_host.runtime_protocol.argument_unknown_field", path=path, field=extras[0]))
            for key, item in value.items():
                if key in properties:
                    validate(properties[key], item, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            if "minItems" in current_schema and len(value) < int(current_schema["minItems"]):
                raise PluginProtocolError(t("plugin_host.runtime_protocol.argument_too_few_items", path=path))
            if "maxItems" in current_schema and len(value) > int(current_schema["maxItems"]):
                raise PluginProtocolError(t("plugin_host.runtime_protocol.argument_too_many_items", path=path))
            item_schema = current_schema.get("items")
            for index, item in enumerate(value):
                validate(item_schema, item, f"{path}[{index}]", depth + 1)
        elif isinstance(value, str):
            if "minLength" in current_schema and len(value) < int(current_schema["minLength"]):
                raise PluginProtocolError(t("plugin_host.runtime_protocol.argument_too_short", path=path))
            if "maxLength" in current_schema and len(value) > int(current_schema["maxLength"]):
                raise PluginProtocolError(t("plugin_host.runtime_protocol.argument_too_long", path=path))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in current_schema and value < float(current_schema["minimum"]):
                raise PluginProtocolError(t("plugin_host.runtime_protocol.argument_below_minimum", path=path))
            if "maximum" in current_schema and value > float(current_schema["maximum"]):
                raise PluginProtocolError(t("plugin_host.runtime_protocol.argument_above_maximum", path=path))

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
            raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_pipe_unavailable"))
        async with self._lock:
            if self.process.returncode is not None:
                raise PluginProtocolError(t("plugin_host.runtime_protocol.process_exited", returncode=self.process.returncode))
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
                raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_request_too_large"))
            self.process.stdin.write(encoded)
            try:
                await asyncio.wait_for(self.process.stdin.drain(), timeout=timeout)
                raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_timeout", timeout=format(timeout, 'g'))) from exc
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_disconnected")) from exc
            except ValueError as exc:
                raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_response_too_large")) from exc
            if not raw:
                raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_no_response"))
            if len(raw) > MAX_RPC_MESSAGE_BYTES:
                raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_response_too_large"))
            try:
                response = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_invalid_json")) from exc
            if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
                raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_invalid_response"))
            if response.get("id") != request_id:
                raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_id_mismatch"))
            if "error" in response:
                error = response.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or "插件调用失败")
                else:
                    message = str(error or "插件调用失败")
                raise PluginInvocationError(message)
            if "result" not in response:
                raise PluginProtocolError(t("plugin_host.runtime_protocol.rpc_result_missing"))
            return response["result"]
