"""Provider-neutral system image-generation service."""

from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .assets import ImageAssetError, ImageAssetStore
from .contracts import IMAGE_PURPOSES, ImageGenerationRequest, ImageGenerationResult
from .providers import ImageProvider, ImageProviderError, OpenAICompatibleImageProvider


PURPOSE_PROMPT_SUFFIXES = {
    "scene": "Wide cinematic environment scene, no text, no interface elements.",
    "avatar": "Single character portrait, centered composition, clear face, no text, no frame.",
    "item": "Single isolated item illustration, centered composition, no text, no interface elements.",
    "map": "Detailed location map background, readable terrain and landmarks, no labels, no interface elements.",
    "freeform": "No text or interface elements unless explicitly requested.",
}


class ImageGenerationError(RuntimeError):
    pass


class ImageGenerationService:
    def __init__(self, config: dict[str, Any], assets_dir: Path, *, proxy_url: str = "") -> None:
        self.enabled = bool(config.get("imagegen_enabled", False))
        self.provider_id = str(config.get("imagegen_provider") or "openai-compatible").strip()
        self.base_url = str(config.get("imagegen_base_url") or "").strip()
        self.api_key = str(config.get("imagegen_api_key") or "").strip()
        self.model = str(config.get("imagegen_model") or "").strip()
        self.square_size = str(config.get("imagegen_square_size") or "1024x1024").strip()
        self.landscape_size = str(config.get("imagegen_landscape_size") or "1792x1024").strip()
        self.quality = str(config.get("imagegen_quality") or "").strip()
        self.style_prefix = str(config.get("imagegen_style_prefix") or "").strip()
        self.timeout_seconds = float(config.get("imagegen_timeout_seconds") or 120)
        self.auto_scene = bool(config.get("imagegen_auto_scene", True))
        self.proxy_url = "" if _is_local_endpoint(self.base_url) else str(proxy_url or "").strip()
        self.assets = ImageAssetStore(assets_dir)
        self._semaphore = asyncio.Semaphore(2)
        self._validate_config()

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.base_url and self.model)

    def public_config(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "provider": self.provider_id,
            "model": self.model,
            "auto_scene": self.auto_scene,
        }

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        if not self.available:
            raise ImageGenerationError("系统图像生成尚未配置或启用")
        purpose = str(request.purpose or "").strip().lower()
        if purpose not in IMAGE_PURPOSES:
            raise ImageGenerationError("不支持的图片用途")
        prompt = str(request.prompt or "").strip()
        if not prompt:
            raise ImageGenerationError("画面描述为空")
        if len(prompt) > 8000:
            raise ImageGenerationError("画面描述不能超过 8000 个字符")
        composed_prompt = self._compose_prompt(prompt, purpose, request.style)
        size = self._size_for(request, purpose)
        try:
            async with self._semaphore:
                generated = await self._provider().generate(
                    composed_prompt,
                    size=size,
                    quality=self.quality,
                )
            return self.assets.store(
                generated.body,
                purpose=purpose,
                prompt=prompt,
                revised_prompt=generated.revised_prompt,
                provider=self.provider_id,
                model=self.model,
                owner_type=request.owner_type,
                owner_id=request.owner_id,
                context=request.context,
            )
        except (ImageProviderError, ImageAssetError) as exc:
            raise ImageGenerationError(str(exc)) from exc

    def _provider(self) -> ImageProvider:
        kwargs = {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "proxy_url": self.proxy_url,
        }
        if self.provider_id == "openai-compatible":
            return OpenAICompatibleImageProvider(**kwargs)
        raise ImageGenerationError(f"不支持的图像生成 provider：{self.provider_id}")

    def _compose_prompt(self, prompt: str, purpose: str, request_style: str) -> str:
        parts = [self.style_prefix, str(request_style or "").strip(), prompt, PURPOSE_PROMPT_SUFFIXES[purpose]]
        return "\n\n".join(part for part in parts if part)[:12000]

    def _size_for(self, request: ImageGenerationRequest, purpose: str) -> str:
        ratio = str(request.aspect_ratio or "").strip()
        if ratio in {"1:1", "square"} or purpose in {"avatar", "item"}:
            return self.square_size
        return self.landscape_size

    def _validate_config(self) -> None:
        if self.provider_id != "openai-compatible":
            raise ValueError(f"不支持的图像生成 provider：{self.provider_id}")
        if not self.enabled:
            return
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("图像生成 Base URL 必须是无内嵌凭据的 http(s) 地址")
        if not self.model:
            raise ValueError("启用图像生成时必须选择模型")


def _is_local_endpoint(value: str) -> bool:
    hostname = (urlparse(value).hostname or "").strip().lower()
    if hostname in {"localhost", "host.docker.internal"} or hostname.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_private
    except ValueError:
        return False
