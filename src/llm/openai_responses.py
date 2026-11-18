"""Stateless Responses transport for supported models on the official OpenAI API.

The application owns prompts, history, tools and game state. This adapter only
translates HTTP contracts; it neither executes tools nor retains remote state.
"""
from __future__ import annotations

import codecs
from copy import deepcopy
from dataclasses import dataclass
import json
import logging
import re
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import aiohttp

from src.llm.tool_usage import observed_post

logger = logging.getLogger("trpg")

_MODELS = re.compile(r"gpt-6\.2-(?:astra|sol)(?:-\d{4}-\d{2}-\d{2})?\Z")
_PATHS = {"", "/v1", "/v1/chat/completions", "/v1/responses"}


def responses_url(base_url: str, model: str, api_format: str = "openai") -> str | None:
    """Select only an explicitly supported official endpoint/model combination."""
    if api_format.strip().lower() != "openai" or not _MODELS.fullmatch(model.strip()):
        return None
    try:
        parsed = urlsplit(base_url.strip())
        if (parsed.scheme != "https" or parsed.hostname != "api.openai.com"
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path.rstrip("/") not in _PATHS):
            return None
    except ValueError:
        return None
    return "https://api.openai.com/v1/responses"


class ResponsesIncompleteError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"OpenAI Responses did not complete: {reason}")


class ResponsesStreamInterruptedError(RuntimeError):
    """A visible stream cannot be retried internally without duplicating output."""


@dataclass
class ResponsesResult:
    content: str
    tool_calls: list[dict[str, Any]]
    total_tokens: int


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    """Translate closed schemas without closing maps or altering the local contract.

    Responses strict tools require all object fields. Originally optional fields
    become nullable on the wire and are restored to omission when received.
    Open/dynamic schemas retain their original shape in explicit non-strict mode.
    """
    result = deepcopy(schema)
    if any(key in result for key in ("$ref", "$defs", "oneOf", "allOf", "not", "if", "then", "else", "patternProperties")):
        return None
    if isinstance(result.get("type"), list) and {"object", "array"}.intersection(result["type"]):
        return None
    if result.get("type") == "object":
        if result.get("additionalProperties") is not False:
            return None
        properties = result.get("properties", {})
        original_required = set(result.get("required", []))
        for key, child in properties.items():
            converted = _strict_schema(child)
            if converted is None:
                return None
            if key not in original_required:
                converted = {"anyOf": [converted, {"type": "null"}]}
            properties[key] = converted
        result["required"] = list(properties)
    elif result.get("type") == "array":
        child = _strict_schema(result.get("items", {}))
        if child is None:
            return None
        result["items"] = child
    elif "anyOf" in result:
        if any(child.get("type") in ("object", "array") for child in result["anyOf"]):
            return None
        branches = [_strict_schema(child) for child in result["anyOf"]]
        if any(child is None for child in branches):
            return None
        result["anyOf"] = branches
    elif "type" not in result and "enum" not in result:
        return None
    return result


def _allows_null(schema: dict[str, Any]) -> bool:
    kind = schema.get("type")
    if kind == "null" or isinstance(kind, list) and "null" in kind:
        return True
    if "enum" in schema and None in schema["enum"]:
        return True
    return any(_allows_null(branch) for branch in schema.get("anyOf", []))


def _restore_optional_fields(value: Any, schema: dict[str, Any]) -> Any:
    if isinstance(value, dict) and schema.get("type") == "object":
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        return {
            key: _restore_optional_fields(child, properties.get(key, {}))
            for key, child in value.items()
            if not (child is None and key in properties and key not in required
                    and not _allows_null(properties[key]))
        }
    if isinstance(value, list) and schema.get("type") == "array":
        return [_restore_optional_fields(child, schema.get("items", {})) for child in value]
    return value


def _check_complete(data: dict[str, Any]) -> None:
    status = data.get("status")
    if status != "completed":
        reason = (data.get("incomplete_details") or {}).get("reason") if status == "incomplete" else status
        # Never include provider output/arguments in protocol error messages.
        raise ResponsesIncompleteError(str(reason or "missing completion status"))
    if data.get("error"):
        raise ResponsesIncompleteError("error in completed response")


def _message_phase(item: dict[str, Any]) -> str | None:
    if item.get("role") != "assistant":
        raise ValueError("Unexpected OpenAI Responses message role")
    phase = item.get("phase")
    if phase not in (None, "commentary", "final_answer"):
        raise ValueError("Unexpected OpenAI Responses message phase")
    return phase


def parse_response(data: dict[str, Any], tools: list[dict[str, Any]] | None = None) -> ResponsesResult:
    _check_complete(data)
    originals = {tool["function"]["name"]: tool["function"].get("parameters", {}) for tool in tools or []}
    texts: list[str] = []
    calls: list[dict[str, Any]] = []
    output = data.get("output")
    if not isinstance(output, list):
        raise ValueError("OpenAI Responses output must be an array")
    for item in output:
        if not isinstance(item, dict):
            raise ValueError("Invalid OpenAI Responses output item")
        if item.get("type") == "message":
            if item.get("status") != "completed":
                raise ResponsesIncompleteError("message incomplete")
            if _message_phase(item) == "commentary":
                continue
            parts: list[str] = []
            for part in item.get("content", []):
                if part.get("type") == "refusal":
                    raise ValueError("OpenAI Responses refused the request")
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            texts.append("".join(parts))
        elif item.get("type") == "function_call":
            if item.get("status") != "completed":
                raise ResponsesIncompleteError("function call incomplete")
            name = item.get("name")
            call_id = item.get("call_id")
            if not isinstance(name, str) or not name or not isinstance(call_id, str) or not call_id:
                raise ValueError("OpenAI Responses function call lacks identity")
            try:
                arguments = json.loads(item.get("arguments", ""))
            except (TypeError, ValueError) as exc:
                raise ValueError("OpenAI Responses function arguments are invalid JSON") from exc
            if not isinstance(arguments, dict):
                raise ValueError("OpenAI Responses function arguments must be an object")
            if name not in originals:
                raise ValueError("OpenAI Responses returned an unregistered function call")
            schema = originals[name]
            if _strict_schema(schema) is not None:
                arguments = _restore_optional_fields(arguments, schema)
            calls.append({"name": name, "arguments": arguments, "call_id": call_id})
        elif item.get("type") != "reasoning":
            raise ValueError("Unexpected OpenAI Responses output type")
    usage = data.get("usage") or {}
    total = int(usage.get("total_tokens") or (int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)))
    return ResponsesResult("\n".join(texts).strip(), calls, total)


async def _events(content: Any) -> AsyncIterator[dict[str, Any]]:
    """Decode SSE frames, including split UTF-8 bytes and multi-line data fields."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    pending = ""
    data_lines: list[str] = []
    async for chunk in content:
        pending += decoder.decode(chunk)
        if len(pending) + sum(map(len, data_lines)) > 16 * 1024 * 1024:
            raise ValueError("OpenAI Responses stream event exceeds size limit")
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            line = line.rstrip("\r")
            if not line:
                if data_lines:
                    payload = "\n".join(data_lines)
                    data_lines.clear()
                    if payload != "[DONE]":
                        event = json.loads(payload)
                        if not isinstance(event, dict):
                            raise ValueError("OpenAI Responses stream event must be an object")
                        yield event
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
    decoder.decode(b"", final=True)
    # EOF is never completion. The caller requires response.completed.


class OpenAIResponsesTransport:
    def __init__(self, generation_options: dict[str, float] | None = None):
        self.generation_options = dict(generation_options or {})
        self._default_sampling_noticed = False

    def body(self, model: str, system: str, user: str, max_tokens: int, *, json_mode: bool = False,
             tools: list[dict[str, Any]] | None = None, force_tool_choice: bool = True,
             reasoning_effort: str | None = None) -> dict[str, Any]:
        unsupported = {key for key, value in self.generation_options.items() if not (key == "temperature" and value == 1)}
        if unsupported:
            raise ValueError("GPT-6 Responses does not support configured sampling options: " + ", ".join(sorted(unsupported)))
        if self.generation_options and not self._default_sampling_noticed:
            logger.info("GPT-6 Responses uses default sampling; configured temperature=1 is represented by omitting temperature")
            self._default_sampling_noticed = True
        body: dict[str, Any] = {
            "model": model, "store": False, "reasoning": {"effort": reasoning_effort if reasoning_effort is not None else "medium"},
            "input": [{"role": "developer", "content": system}, {"role": "user", "content": user}],
            "max_output_tokens": max_tokens,
        }
        if json_mode:
            body["text"] = {"format": {"type": "json_object"}}
        if tools is not None:
            converted = []
            for tool in tools:
                function = tool["function"]
                schema = function.get("parameters", {})
                strict = _strict_schema(schema)
                converted.append({"type": "function", "name": function["name"],
                                  "description": function.get("description", ""),
                                  "parameters": strict if strict is not None else deepcopy(schema),
                                  "strict": strict is not None})
            body["tools"] = converted
            body["parallel_tool_calls"] = False
            body["tool_choice"] = {"type": "function", "name": converted[0]["name"]} if force_tool_choice else "auto"
        return body

    @staticmethod
    async def _check_http(response: Any, api_key: str) -> None:
        if response.status != 200:
            message = (await response.text()).replace(api_key, "[redacted]") if api_key else await response.text()
            raise aiohttp.ClientResponseError(response.request_info, response.history,
                                             status=response.status, message=message[:300], headers=response.headers)

    async def call(self, session: Any, url: str, api_key: str, body: dict[str, Any], *,
                   timeout: Any, proxy: str = "", tools: list[dict[str, Any]] | None = None) -> ResponsesResult:
        kwargs = {"proxy": proxy} if proxy else {}
        # 与 OpenAIChatTransport 相同的账本钩子：DF Agent 工具规划的每次
        # HTTP 尝试都入 usage ledger；无活动账本时（叙事流/连接测试）直通。
        async with observed_post(session, url, json=body,
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                 timeout=timeout, **kwargs) as response:
            await self._check_http(response, api_key)
            result = parse_response(await response.json(), tools)
            choice = body.get("tool_choice")
            if isinstance(choice, dict):
                names = [call["name"] for call in result.tool_calls]
                if names != [choice["name"]]:
                    raise ValueError("OpenAI Responses did not return the selected function exactly once")
            return result

    async def stream(self, session: Any, url: str, api_key: str, body: dict[str, Any], *,
                     timeout: Any, proxy: str = "", on_delta=None) -> ResponsesResult:
        kwargs = {"proxy": proxy} if proxy else {}
        visible = False
        items: dict[int, dict[str, Any]] = {}
        text_parts: dict[int, list[str]] = {}
        async with observed_post(session, url, json={**body, "stream": True},
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                 timeout=timeout, **kwargs) as response:
            await self._check_http(response, api_key)
            try:
                async for event in _events(response.content.iter_any()):
                    kind = event.get("type")
                    if kind == "response.output_item.added":
                        item = event.get("item") or {}
                        if item.get("type") == "message":
                            _message_phase(item)
                        items[event["output_index"]] = item
                    elif kind == "response.output_text.delta":
                        index = event.get("output_index")
                        item = items.get(index)
                        if not item or item.get("type") != "message":
                            raise ValueError("Text delta has no matching message item")
                        if _message_phase(item) == "commentary":
                            continue
                        delta = event.get("delta")
                        if not isinstance(delta, str):
                            raise ValueError("Invalid OpenAI Responses text delta")
                        separator = "\n" if index not in text_parts and text_parts else ""
                        text_parts.setdefault(index, []).append(delta)
                        if delta and on_delta:
                            visible = True
                            await on_delta(separator + delta)
                    elif kind == "response.completed":
                        result = parse_response(event.get("response") or {})
                        if result.tool_calls or not result.content:
                            raise ValueError("OpenAI Responses returned no final text or unexpected tools")
                        streamed = "\n".join("".join(text_parts[index]) for index in sorted(text_parts)).strip()
                        if streamed != result.content:
                            raise ValueError("OpenAI Responses terminal text does not match streamed text")
                        return result
                    elif kind in {"response.incomplete", "response.failed"}:
                        _check_complete(event.get("response") or {})
                        raise ResponsesIncompleteError("invalid terminal event")
                    elif kind == "error":
                        raise ResponsesIncompleteError("stream error event")
                raise ResponsesIncompleteError("stream ended before response.completed")
            except ResponsesIncompleteError as exc:
                if exc.reason == "max_output_tokens":
                    raise
                if visible:
                    raise ResponsesStreamInterruptedError("OpenAI Responses stream interrupted after visible output") from exc
                raise
            except Exception as exc:
                if visible:
                    raise ResponsesStreamInterruptedError("OpenAI Responses stream interrupted after visible output") from exc
                raise
