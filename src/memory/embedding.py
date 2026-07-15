"""Embedding 客户端 —— 通过 aiohttp 调用 OpenAI 兼容 embedding API。"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import List

import aiohttp

logger = logging.getLogger("trpg")


class EmbeddingClient:
    """异步 embedding API 客户端，支持 OpenAI 兼容接口和 Ollama 本地服务。"""

    # 模型最大输入长度（字符），中文 1 字 ≈ 1 token
    _MODEL_MAX_INPUT: dict[str, int] = {
        "bge-large-zh": 500,
        "bge-base-zh": 500,
        "bge-small-zh": 500,
        "bge-m3": 8000,
        "nomic-embed-text": 8000,
        "mxbai-embed-large": 500,
        "snowflake-arctic-embed": 500,
        "all-minilm": 250,
        "qwen2.5-embed": 8000,
        "qwen3-embed": 8000,
        "text-embedding-3-small": 8000,
        "text-embedding-3-large": 8000,
        "text-embedding-ada-002": 8000,
    }
    _DEFAULT_MAX_INPUT = 500

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "nomic-embed-text",
        max_input_override: int = 0,
        proxy_url: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.proxy_url = proxy_url.strip()
        self.max_input_chars = max_input_override if max_input_override > 0 else self._detect_max_input(model)
        self._session: aiohttp.ClientSession | None = None

    def _detect_max_input(self, model: str) -> int:
        """根据模型名推断最大输入字符数，匹配不到时用保守默认值。"""
        m = model.lower()
        for key, limit in self._MODEL_MAX_INPUT.items():
            if key in m:
                return limit
        return self._DEFAULT_MAX_INPUT

    def _truncate(self, text: str) -> str:
        if len(text) > self.max_input_chars:
            return text[:self.max_input_chars]
        return text


    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _build_url(self) -> str:
        """构建 embedding endpoint URL，兼容 OpenAI 和 Ollama。"""
        url = self.base_url
        # OpenAI 格式：/v1/embeddings
        if url.endswith("/v1"):
            return url + "/embeddings"
        if "/v1/" in url and not url.endswith("/embeddings"):
            return url + "/embeddings" if not url.endswith("/") else url + "embeddings"
        # Ollama 原生格式：/api/embeddings
        if not url.endswith("/embeddings"):
            if not url.endswith("/api"):
                return url + "/api/embeddings"
            return url + "/embeddings"
        return url

    async def embed(self, text: str) -> List[float] | None:
        """将文本转换为向量。"""
        text = self._truncate(text)
        url = self._build_url()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "input": text,
        }

        try:
            session = await self._get_session()
            request_kwargs = {"proxy": self.proxy_url} if self.proxy_url else {}
            async with session.post(
                url, json=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
                **request_kwargs,
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.warning("Embedding API 失败: HTTP %d %s", resp.status, error_text[:200])
                    return None
                data = await resp.json()
                return data["data"][0]["embedding"]
        except Exception:
            logger.exception("Embedding API 调用失败")
            return None

    async def embed_batch(self, texts: List[str]) -> List[List[float]] | None:
        """批量转换文本为向量。"""
        texts = [self._truncate(t) for t in texts]
        url = self._build_url()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "input": texts,
        }

        try:
            session = await self._get_session()
            request_kwargs = {"proxy": self.proxy_url} if self.proxy_url else {}
            async with session.post(
                url, json=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
                **request_kwargs,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return [item["embedding"] for item in data["data"]]
        except Exception:
            logger.exception("批量 embedding 调用失败")
            return None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算两个向量的余弦相似度。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
