"""Bounded Chat Completions parameter negotiation at the HTTP boundary.

Only an explicit unsupported-parameter response may change a request. No model
name heuristics are used, and a rejected request never expands its token budget.
"""
from __future__ import annotations

import json
import logging
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
