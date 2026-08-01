"""LLM 客户端 —— 通过 aiohttp 直连 OpenAI 兼容 API。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger("trpg")

MAX_RETRIES = 3          # 总共尝试次数（含首次）
BASE_DELAY = 2.0         # 基础重试间隔（秒）
LENGTH_RETRY_FACTOR = 2    # finish_reason=length 时重试放大 max_tokens 的倍数
LENGTH_RETRY_MAX_MULT = 4  # 最大放大到原始 max_tokens 的多少倍


def length_retry_budgets(base_max_tokens: int) -> tuple[int, ...]:
    """返回输出截断时共用的 token 预算序列。"""
    if base_max_tokens <= 0:
        raise ValueError("base_max_tokens 必须大于 0")

    budgets = [base_max_tokens]
    limit = base_max_tokens * LENGTH_RETRY_MAX_MULT
    while len(budgets) < MAX_RETRIES:
        bumped = min(budgets[-1] * LENGTH_RETRY_FACTOR, limit)
        if bumped <= budgets[-1]:
            break
        budgets.append(bumped)
    return tuple(budgets)


# 按 HTTP 状态码的退避策略
_RETRY_BACKOFF: dict[int, float] = {
    429: 5.0,     # Rate Limit → 较长等待
    503: 3.0,     # Service Unavailable → 中等等待
    502: 2.0,     # Bad Gateway
    504: 2.0,     # Gateway Timeout
}

_RETRYABLE_STATUSES = frozenset(_RETRY_BACKOFF) | {408}  # 408 Timeout 也可重试


class OutputTruncatedError(ValueError):
    """模型输出被 max_tokens 截断（finish_reason=length / stop_reason=max_tokens）。

    即使已经返回部分正文也不能当作完整结果；call() 据此在重试时自动提高
    max_tokens，而不是保存缺少结尾或结构化状态的半截回复。
    """

    def __init__(self, finish_reason: str = "length"):
        self.finish_reason = finish_reason
        super().__init__(f"模型未返回最终正文 (finish_reason={finish_reason})")


@dataclass
class ProviderConfig:
    """模型供应配置。"""
    provider_name: str
    base_url: str
    api_key: str
    model_name: str
    api_format: str = "openai"
    fallback: bool = False


@dataclass
class LLMResponse:
    """LLM 调用结果。"""
    content: str                # 完整响应文本
    narration: str              # 叙事文本（JSON 块之前的部分）
    state_update: dict | None   # 解析出的状态更新
    memory_delta: dict | None   # 解析出的记忆变更
    info_asymmetry: dict | None  # 解析出的信息不对称
    plot_update: dict | None    # 解析出的剧情推进
    total_tokens: int           # 实际消耗的 token 数
    is_narration_only: bool     # JSON 解析失败，仅叙事
    provider_used: str          # 实际使用的供应商名称
    token_budget_initial: int = 0  # 本次调用最初配置的最大输出 token
    token_budget_used: int = 0     # 成功响应实际使用的最大输出 token 档位


class LLMClient:
    """OpenAI 兼容 API 的异步 HTTP 客户端。

    支持多供应商配置、自动重试、失败降级到备用模型。
    """

    def __init__(self, providers: list[ProviderConfig], default: str, proxy_url: str = ""):
        if not providers:
            raise ValueError("至少需要配置一个模型供应商")
        self.providers = {p.provider_name: p for p in providers}
        self.default = default if default in self.providers else providers[0].provider_name
        self.proxy_url = proxy_url.strip()
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或懒创建复用的 HTTP session。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def call(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        force_provider: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """调用 LLM，失败时自动降级到 fallback 模型。

        Args:
            system_prompt: GM 系统提示词
            user_message: 拼接好的上下文
            temperature: 生成温度
            max_tokens: 最大输出 token 数
            force_provider: 强制使用指定供应商（跳过低负载均衡）
            json_mode: 启用 JSON 模式（DeepSeek/OpenAI 兼容的 structured output）

        Returns:
            LLMResponse

        Raises:
            RuntimeError: 所有供应商均失败
        """
        primary = self.providers[force_provider or self.default]
        ordered = [primary] + [
            p for p in self.providers.values()
            if p.fallback and p.provider_name != primary.provider_name
        ]

        last_error = None
        last_backoff = BASE_DELAY
        retry_budgets = length_retry_budgets(max_tokens)
        current_max_tokens = retry_budgets[0]
        for attempt_num in range(1, MAX_RETRIES + 1):
            for provider in ordered:
                try:
                    response = await self._call_one(
                        provider, system_prompt, user_message,
                        temperature, current_max_tokens, json_mode,
                    )
                    response.token_budget_initial = max_tokens
                    response.token_budget_used = current_max_tokens
                    return response
                except OutputTruncatedError as exc:
                    last_error = exc
                    logger.warning(
                        "LLM 输出被 max_tokens 截断 (attempt=%d, provider=%s, max_tokens=%d): %s",
                        attempt_num, provider.provider_name, current_max_tokens, exc,
                    )
                    continue
                except aiohttp.ClientResponseError as exc:
                    last_error = exc
                    status = exc.status
                    if status in _RETRYABLE_STATUSES:
                        last_backoff = _RETRY_BACKOFF.get(status, BASE_DELAY) * attempt_num
                        logger.warning(
                            "LLM HTTP %d (attempt=%d, provider=%s): %s, %0.1fs后重试",
                            status, attempt_num, provider.provider_name, exc, last_backoff,
                        )
                    else:
                        logger.warning(
                            "LLM HTTP %d (attempt=%d, provider=%s): %s (不可重试，跳过该供应商)",
                            status, attempt_num, provider.provider_name, exc,
                        )
                        continue
                except asyncio.TimeoutError as exc:
                    last_error = exc
                    last_backoff = BASE_DELAY * attempt_num * 0.5
                    logger.warning(
                        "LLM 超时 (attempt=%d, provider=%s): %0.1fs后重试",
                        attempt_num, provider.provider_name, last_backoff,
                    )
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "LLM 调用失败 (attempt=%d, provider=%s): %s",
                        attempt_num, provider.provider_name, exc,
                    )
                    continue
            if attempt_num < MAX_RETRIES:
                if isinstance(last_error, OutputTruncatedError):
                    bumped = retry_budgets[
                        min(attempt_num, len(retry_budgets) - 1)
                    ]
                    if bumped > current_max_tokens:
                        logger.info(
                            "输出截断，下次重试提高 max_tokens: %d -> %d",
                            current_max_tokens, bumped,
                        )
                        current_max_tokens = bumped
                    await asyncio.sleep(BASE_DELAY * 0.5)
                else:
                    await asyncio.sleep(last_backoff)

        raise RuntimeError(f"所有模型供应商均调用失败: {last_error}") from last_error

    async def call_stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        force_provider: str | None = None,
        json_mode: bool = False,
        on_delta=None,
    ) -> LLMResponse:
        """流式调用 LLM，逐段文本通过 on_delta 回调推送，返回与 call() 相同的 LLMResponse。

        on_delta 为可选的 async callable，签名为 ``async def on_delta(text: str) -> None``；
        每收到一段正文就调用一次，供上层做实时打字机展示。返回值与 call() 一致，
        内部累积完整正文后走同一个 _to_response 解析，保证流式/非流式结果同构。

        供应商在开始流式前失败（连接/HTTP 错误/超时）时按 fallback 顺序与退避重试，
        行为与 call() 一致；流式过程中途失败则抛出，由调用方决定是否重试。
        finish_reason=length 时即使已有部分正文也抛 OutputTruncatedError，不在内部
        放大预算，由调用方提高 max_tokens 后重新调用（与 call() 的截断处理对齐）。
        """
        primary = self.providers[force_provider or self.default]
        ordered = [primary] + [
            p for p in self.providers.values()
            if p.fallback and p.provider_name != primary.provider_name
        ]

        last_error = None
        last_backoff = BASE_DELAY
        for attempt_num in range(1, MAX_RETRIES + 1):
            for provider in ordered:
                try:
                    content, total_tokens, _finish_reason = await self._stream_one(
                        provider, system_prompt, user_message,
                        temperature, max_tokens, json_mode, on_delta,
                    )
                    response = self._to_response(content, total_tokens, provider.provider_name)
                    response.token_budget_initial = max_tokens
                    response.token_budget_used = max_tokens
                    return response
                except OutputTruncatedError:
                    raise
                except aiohttp.ClientResponseError as exc:
                    last_error = exc
                    status = exc.status
                    if status in _RETRYABLE_STATUSES:
                        last_backoff = _RETRY_BACKOFF.get(status, BASE_DELAY) * attempt_num
                        logger.warning(
                            "LLM 流式 HTTP %d (attempt=%d, provider=%s): %s, %0.1fs后重试",
                            status, attempt_num, provider.provider_name, exc, last_backoff,
                        )
                    else:
                        logger.warning(
                            "LLM 流式 HTTP %d (attempt=%d, provider=%s): %s (不可重试，跳过该供应商)",
                            status, attempt_num, provider.provider_name, exc,
                        )
                        continue
                except asyncio.TimeoutError as exc:
                    last_error = exc
                    last_backoff = BASE_DELAY * attempt_num * 0.5
                    logger.warning(
                        "LLM 流式超时 (attempt=%d, provider=%s): %0.1fs后重试",
                        attempt_num, provider.provider_name, last_backoff,
                    )
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "LLM 流式调用失败 (attempt=%d, provider=%s): %s",
                        attempt_num, provider.provider_name, exc,
                    )
                    continue
            if attempt_num < MAX_RETRIES:
                await asyncio.sleep(last_backoff)

        raise RuntimeError(f"所有模型供应商均流式调用失败: {last_error}") from last_error

    async def _call_one(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> LLMResponse:
        api_format = (provider.api_format or "openai").strip().lower()
        if api_format == "anthropic":
            return await self._call_anthropic(
                provider, system_prompt, user_message,
                temperature, max_tokens, json_mode,
            )
        return await self._call_openai_compatible(
            provider, system_prompt, user_message,
            temperature, max_tokens, json_mode,
        )

    async def _call_openai_compatible(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> LLMResponse:
        url = provider.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider.api_key}",
        }
        body = {
            "model": provider.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # JSON 模式：DeepSeek/OpenAI 兼容的 structured output
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        session = await self._get_session()
        request_kwargs = {"proxy": self.proxy_url} if self.proxy_url else {}
        async with session.post(url, json=body, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=120),
                                **request_kwargs) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history,
                    status=resp.status, message=error_text[:300],
                    headers=resp.headers,
                )
            data = await resp.json()

        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        finish_reason = str(choice.get("finish_reason") or "unknown")
        if finish_reason == "length":
            raise OutputTruncatedError(finish_reason)
        if not content.strip():
            raise ValueError(
                f"模型未返回最终正文 (finish_reason={finish_reason})"
            )
        total_tokens = data.get("usage", {}).get("total_tokens", 0)
        return self._to_response(content, total_tokens, provider.provider_name)

    async def _call_anthropic(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> LLMResponse:
        url = _anthropic_messages_url(provider.base_url)
        system = system_prompt
        if json_mode:
            system = f"{system_prompt}\n\nReturn only valid JSON. Do not wrap it in Markdown."

        headers = {
            "Content-Type": "application/json",
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": provider.model_name,
            "system": system,
            "messages": [
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens,
        }

        session = await self._get_session()
        request_kwargs = {"proxy": self.proxy_url} if self.proxy_url else {}
        async with session.post(url, json=body, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=120),
                                **request_kwargs) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history,
                    status=resp.status, message=error_text[:300],
                    headers=resp.headers,
                )
            data = await resp.json()

        content = _anthropic_text_content(data)
        if data.get("stop_reason") == "max_tokens":
            raise OutputTruncatedError("max_tokens")
        if not content.strip():
            raise ValueError(
                f"模型未返回最终正文 (stop_reason={data.get('stop_reason') or 'unknown'})"
            )
        usage = data.get("usage", {})
        total_tokens = int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
        return self._to_response(content, total_tokens, provider.provider_name)

    async def _stream_one(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        on_delta=None,
    ) -> tuple[str, int, str]:
        api_format = (provider.api_format or "openai").strip().lower()
        if api_format == "anthropic":
            return await self._stream_anthropic(
                provider, system_prompt, user_message,
                temperature, max_tokens, json_mode, on_delta,
            )
        return await self._stream_openai_compatible(
            provider, system_prompt, user_message,
            temperature, max_tokens, json_mode, on_delta,
        )

    async def _stream_openai_compatible(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
        on_delta=None,
    ) -> tuple[str, int, str]:
        url = provider.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider.api_key}",
        }
        body = {
            "model": provider.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # JSON 模式：DeepSeek/OpenAI 兼容的 structured output
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        session = await self._get_session()
        request_kwargs = {"proxy": self.proxy_url} if self.proxy_url else {}
        content_parts: list[str] = []
        finish_reason = "stop"
        total_tokens = 0
        async with session.post(url, json=body, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=120),
                                **request_kwargs) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history,
                    status=resp.status, message=error_text[:300],
                    headers=resp.headers,
                )
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if choices:
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    if text:
                        content_parts.append(text)
                        if on_delta:
                            await on_delta(text)
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                usage = chunk.get("usage")
                if isinstance(usage, dict):
                    total_tokens = int(usage.get("total_tokens", 0) or 0)

        content = "".join(content_parts)
        if finish_reason == "length":
            raise OutputTruncatedError(finish_reason)
        if not content.strip():
            raise ValueError(f"模型未返回最终正文 (finish_reason={finish_reason})")
        return content, total_tokens, finish_reason

    async def _stream_anthropic(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
        on_delta=None,
    ) -> tuple[str, int, str]:
        url = _anthropic_messages_url(provider.base_url)
        system = system_prompt
        if json_mode:
            system = f"{system_prompt}\n\nReturn only valid JSON. Do not wrap it in Markdown."

        headers = {
            "Content-Type": "application/json",
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": provider.model_name,
            "system": system,
            "messages": [
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens,
            "stream": True,
        }

        session = await self._get_session()
        request_kwargs = {"proxy": self.proxy_url} if self.proxy_url else {}
        content_parts: list[str] = []
        stop_reason = "end_turn"
        input_tokens = 0
        output_tokens = 0
        async with session.post(url, json=body, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=120),
                                **request_kwargs) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history,
                    status=resp.status, message=error_text[:300],
                    headers=resp.headers,
                )
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                etype = chunk.get("type")
                if etype == "message_start":
                    message = chunk.get("message") or {}
                    usage = message.get("usage") or {}
                    input_tokens = int(usage.get("input_tokens", 0) or 0)
                elif etype == "content_block_delta":
                    delta = chunk.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        if text:
                            content_parts.append(text)
                            if on_delta:
                                await on_delta(text)
                elif etype == "message_delta":
                    delta = chunk.get("delta") or {}
                    if delta.get("stop_reason"):
                        stop_reason = delta["stop_reason"]
                    usage = chunk.get("usage") or {}
                    output_tokens = int(usage.get("output_tokens", 0) or 0)

        content = "".join(content_parts)
        if stop_reason == "max_tokens":
            raise OutputTruncatedError("max_tokens")
        if not content.strip():
            raise ValueError(f"模型未返回最终正文 (stop_reason={stop_reason})")
        total_tokens = input_tokens + output_tokens
        return content, total_tokens, stop_reason

    @staticmethod
    def _to_response(content: str, total_tokens: int, provider_used: str) -> LLMResponse:

        # 解析输出
        from .parser import parse_llm_response
        result = parse_llm_response(content)

        return LLMResponse(
            content=content,
            narration=result.narration,
            state_update=result.state_update,
            memory_delta=result.memory_delta,
            info_asymmetry=result.info_asymmetry,
            plot_update=result.plot_update,
            total_tokens=total_tokens,
            is_narration_only=result.is_narration_only,
            provider_used=provider_used,
        )


def _anthropic_messages_url(base_url: str) -> str:
    url = (base_url or "https://api.anthropic.com").strip().rstrip("/")
    if url.endswith("/v1/messages"):
        return url
    if url.endswith("/v1"):
        return f"{url}/messages"
    return f"{url}/v1/messages"


def _anthropic_text_content(data: dict) -> str:
    parts: list[str] = []
    for block in data.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(part for part in parts if part).strip()
