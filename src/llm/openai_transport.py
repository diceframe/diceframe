"""Bounded Chat Completions parameter negotiation at the HTTP boundary.

Only an explicit unsupported-parameter response may change a request. No model
name heuristics are used, and a rejected request never expands its token budget.
"""
from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator
from src.llm.generation_options import validate_generation_options

logger = logging.getLogger("trpg")


@dataclass
class _ChatCapabilities:
    token_parameter: str = "max_tokens"
    default_temperature_only: bool = False
    reasoning_effort_unsupported: bool = False
    # Provider-reported inclusive ceiling for the token parameter (0 = unknown).
    # Learned from a 400 whose error message echoes the valid range; every
    # later request for this (url, model) is clamped to it.
    max_tokens_ceiling: int = 0


class OpenAIChatTransport:
    def __init__(self, generation_options: dict[str, Any] | None = None) -> None:
        self._capabilities: dict[tuple[str, str], _ChatCapabilities] = {}
        self._generation_options = validate_generation_options({} if generation_options is None else generation_options)

    @asynccontextmanager
    async def post(self, session: Any, url: str, *, json: dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        capabilities = self._capabilities.setdefault((url, str(json.get("model") or "")), _ChatCapabilities())
        for attempt in range(3):
            body = dict(json)
            body.update(self._generation_options)
            if capabilities.token_parameter != "max_tokens" and "max_tokens" in body:
                body[capabilities.token_parameter] = body.pop("max_tokens")
            if capabilities.max_tokens_ceiling:
                token_key = capabilities.token_parameter
                if token_key in body and isinstance(body[token_key], int) \
                        and body[token_key] > capabilities.max_tokens_ceiling:
                    logger.warning(
                        "模型 %s 的 %s 上限为 %d，请求值 %d 已被自动钳制；"
                        "请到 设置 → DF Agent 调低「单次回复上限 / 任务预算」以匹配该模型。",
                        json.get("model") or "?", token_key,
                        capabilities.max_tokens_ceiling, body[token_key],
                    )
                    body[token_key] = capabilities.max_tokens_ceiling
            if capabilities.default_temperature_only and "temperature" not in self._generation_options:
                body.pop("temperature", None)
            if capabilities.reasoning_effort_unsupported:
                body.pop("reasoning_effort", None)
            async with session.post(url, json=body, **kwargs) as response:
                if response.status == 400 and attempt < 2:
                    if self._learn(capabilities, body, await response.text(), frozenset(self._generation_options)):
                        continue
                yield response
                return

    @staticmethod
    def _learn(capabilities: _ChatCapabilities, body: dict[str, Any], error_text: str, protected: frozenset[str] = frozenset()) -> bool:
        if len(error_text) > 16384:
            return False
        try:
            payload = json.loads(error_text)
        except (TypeError, ValueError):
            return False
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return False
        parameter = error.get("param")
        code = str(error.get("code") or "").lower()
        message = str(error.get("message") or "").lower()
        if parameter == "max_tokens" and "max_tokens" in body and (
            code == "unsupported_parameter" or "max_completion_tokens" in message
        ):
            capabilities.token_parameter = "max_completion_tokens"
            logger.info("Chat endpoint requires max_completion_tokens; preserving the requested output budget")
            return True
        if parameter in {"max_tokens", "max_completion_tokens"} and (
            "max_tokens" in body or "max_completion_tokens" in body
        ):
            match = re.search(
                r"valid range of (?:max_tokens|max_completion_tokens) is \[1,\s*(\d+)\]",
                message,
            )
            if match:
                ceiling = int(match.group(1))
                if ceiling >= 1 and ceiling != capabilities.max_tokens_ceiling:
                    capabilities.max_tokens_ceiling = ceiling
                    logger.warning(
                        "聊天端点拒绝超出范围的输出预算：该模型上限为 %d（原请求 %s）。"
                        "后续请求已自动钳制到该上限；建议同步调低 设置 → DF Agent 的预算配置。",
                        ceiling,
                        max(int(body.get("max_tokens") or 0), int(body.get("max_completion_tokens") or 0)),
                    )
                    return True
        if parameter == "temperature" and parameter not in protected and "temperature" in body and (
            code == "unsupported_parameter"
            or (code == "unsupported_value" and "default" in message)
        ):
            capabilities.default_temperature_only = True
            logger.info("Chat endpoint only accepts its default temperature")
            return True
        if parameter == "reasoning_effort" and "reasoning_effort" in body and (
            code in {"unsupported_parameter", "unknown_parameter", "extra_forbidden"}
            or "reasoning_effort" in message
        ):
            capabilities.reasoning_effort_unsupported = True
            logger.info("Chat endpoint does not support reasoning_effort; retrying without it")
            return True
        return False
