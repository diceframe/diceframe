"""LLM 客户端 —— 通过 aiohttp 直连 OpenAI 兼容 API，绕过 MaiBot 性格层。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger("trpg")

MAX_RETRIES = 3          # 总共尝试次数（含首次）
BASE_DELAY = 2.0         # 基础重试间隔（秒）


# 按 HTTP 状态码的退避策略
_RETRY_BACKOFF: dict[int, float] = {
    429: 5.0,     # Rate Limit → 较长等待
    503: 3.0,     # Service Unavailable → 中等等待
    502: 2.0,     # Bad Gateway
    504: 2.0,     # Gateway Timeout
}

_RETRYABLE_STATUSES = frozenset(_RETRY_BACKOFF) | {408}  # 408 Timeout 也可重试


@dataclass
class ProviderConfig:
    """模型供应配置（对应 plugin.py 中 ModelProviderConfig 的运行时表示）。"""
    provider_name: str
    base_url: str
    api_key: str
    model_name: str
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
        for attempt_num in range(1, MAX_RETRIES + 1):
            for provider in ordered:
                try:
                    return await self._call_one(
                        provider, system_prompt, user_message,
                        temperature, max_tokens, json_mode,
                    )
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
                await asyncio.sleep(last_backoff)

        raise RuntimeError(f"所有模型供应商均调用失败: {last_error}") from last_error

    async def _call_one(
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

        content = data["choices"][0]["message"]["content"] or ""
        if not content.strip():
            content = data["choices"][0]["message"].get("reasoning_content", "") or ""
        total_tokens = data.get("usage", {}).get("total_tokens", 0)

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
            provider_used=provider.provider_name,
        )
