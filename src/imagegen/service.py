"""Provider-neutral system image-generation service."""

from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .assets import ImageAssetError, ImageAssetStore
from .contracts import IMAGE_PROVIDER_IDS, IMAGE_PURPOSES, ImageGenerationRequest, ImageGenerationResult
from .providers import ImageProvider, ImageProviderError, create_image_provider
from .reference_sheet import ReferenceSheetError, combine_portrait_references
from .storyboards import StoryboardInferenceError, build_storyboard_prompt


PURPOSE_PROMPT_SUFFIXES = {
    "scene": "Wide cinematic environment scene, no text, no interface elements.",
    "avatar": "Single character portrait, centered composition, clear face, no text, no frame.",
    "item": "Single isolated item illustration, centered composition, no text, no interface elements.",
    "map": "Simple practical top-down tabletop map, clear terrain, rooms and paths, restrained colors, no labels, no interface elements.",
    "freeform": "No text or interface elements unless explicitly requested.",
}

DEFAULT_PROMPT_CHAR_LIMIT = 12_000
MINIMAX_STORYBOARD_CHAR_BUDGET = 1_000


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
        self.manual_scene = bool(config.get("imagegen_manual_scene", False))
        self.auto_storyboard = bool(config.get("imagegen_auto_storyboard", False))
        self.auto_use_manual_prompt = bool(config.get("imagegen_auto_use_manual_prompt", True))
        self.manual_rules = str(config.get("imagegen_manual_rules") or "").strip()
        self.manual_prompt = str(config.get("imagegen_manual_prompt") or "").strip()
        self.auto_rules = str(config.get("imagegen_auto_rules") or "").strip()
        self.auto_prompt = str(config.get("imagegen_auto_prompt") or "").strip()
        self.proxy_url = "" if _is_local_endpoint(self.base_url) else str(proxy_url or "").strip()
        self.assets = ImageAssetStore(assets_dir)
        self._semaphore = asyncio.Semaphore(2)
        self._validate_config()

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.base_url and self.model)

    def public_config(self) -> dict[str, Any]:
        provider = self._provider()
        return {
            "enabled": self.enabled,
            "available": self.available,
            "provider": provider.provider_id,
            "model": self.model,
            "auto_scene": self.auto_scene,
            "prompt_char_limit": int(
                getattr(provider, "prompt_char_limit", DEFAULT_PROMPT_CHAR_LIMIT)
            ),
        }

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        if not self.available:
            raise ImageGenerationError("系统图像生成尚未配置或启用")
        purpose = str(request.purpose or "").strip().lower()
        if purpose not in IMAGE_PURPOSES:
            raise ImageGenerationError("不支持的图片用途")
        prompt = str(request.prompt or "").strip()
        original_prompt = prompt
        if not prompt:
            raise ImageGenerationError("画面描述为空")
        if len(prompt) > 8000:
            raise ImageGenerationError("画面描述不能超过 8000 个字符")
        provider = self._provider()
        prompt_limit = int(
            getattr(provider, "prompt_char_limit", DEFAULT_PROMPT_CHAR_LIMIT)
        )
        storyboard = request.context.get("storyboard") if isinstance(request.context, dict) else None
        storyboard_metadata: dict[str, Any] = {}
        if purpose == "scene" and isinstance(storyboard, dict):
            try:
                storyboard_prompt, storyboard_metadata = build_storyboard_prompt(
                    storyboard.get("panels"),
                    global_prompt=prompt,
                    character_appearances=storyboard.get("character_appearances"),
                    max_chars=(
                        MINIMAX_STORYBOARD_CHAR_BUDGET
                        if prompt_limit <= 1_500
                        else 8_000
                    ),
                )
            except StoryboardInferenceError as exc:
                raise ImageGenerationError(str(exc)) from exc
            try:
                storyboard_metadata["compressed_count"] += max(0, int(storyboard.get("compressed_count") or 0))
            except (TypeError, ValueError):
                pass
            prompt = storyboard_prompt
        size = self._size_for(request, purpose)
        try:
            references = tuple(request.reference_images or ())
            if references and purpose != "scene":
                raise ImageGenerationError("头像参考图仅支持场景生图，地图生图不会上传头像")
            if references and not getattr(provider, "supports_reference_images", False):
                raise ImageGenerationError("当前图像服务商不支持头像参考图，请关闭该选项后重试")
            if len(references) > 8:
                raise ImageGenerationError("头像参考图最多支持 8 张")
            total_bytes = 0
            for reference in references:
                content = bytes(reference.content or b"")
                if not content or len(content) > 3 * 1024 * 1024:
                    raise ImageGenerationError("头像参考图无效或超过 3 MB")
                total_bytes += len(content)
            if total_bytes > 12 * 1024 * 1024:
                raise ImageGenerationError("头像参考图总大小不能超过 12 MB")
            if references:
                request.context["reference_character_ids"] = list(dict.fromkeys(
                    str(reference.character_id or "")[:80] for reference in references if str(reference.character_id or "").strip()
                ))
                request.context["reference_source_count"] = len(references)
                request.context["reference_combined"] = (
                    len(references) > 1 and request.context.get("combine_avatar_references", True) is True
                )
                # The numbered sheet and text mapping share the actual file
                # order, never roster order or unverified participant names.
                names = request.context.get("avatar_reference_names") or {}
                request.context["avatar_reference_names"] = {
                    reference.character_id: names.get(reference.character_id) or reference.character_id
                    for reference in references
                }
                if request.context["reference_combined"]:
                    references = (await asyncio.to_thread(combine_portrait_references, references),)
                request.context["reference_count"] = len(references)
            composed_prompt, prompt_budget = self._compose_prompt(
                prompt, purpose, request.style, request.context, prompt_limit,
            )
            request.context["prompt_budget"] = prompt_budget
            async with self._semaphore:
                if references:
                    generated = await provider.generate(composed_prompt, size=size, quality=self.quality, reference_images=references)
                else:
                    generated = await provider.generate(composed_prompt, size=size, quality=self.quality)
            body = generated.body
            if storyboard_metadata:
                request.context.setdefault("storyboard", {}).update(storyboard_metadata)
            return self.assets.store(
                body,
                purpose=purpose,
                prompt=original_prompt,
                revised_prompt=generated.revised_prompt,
                provider=getattr(provider, "provider_id", self.provider_id),
                model=self.model,
                owner_type=request.owner_type,
                owner_id=request.owner_id,
                context=request.context,
            )
        except (ImageProviderError, ImageAssetError, ReferenceSheetError, StoryboardInferenceError) as exc:
            raise ImageGenerationError(str(exc)) from exc

    def _provider(self) -> ImageProvider:
        kwargs = {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "proxy_url": self.proxy_url,
            "provider_id": self.provider_id,
        }
        try:
            return create_image_provider(**kwargs)
        except ImageProviderError as exc:
            raise ImageGenerationError(str(exc)) from exc

    def preview_prompt(self, prompt: str, purpose: str, context: dict[str, Any], request_style: str = "") -> tuple[str, dict[str, Any]]:
        provider = self._provider()
        return self._compose_prompt(prompt, purpose, request_style, context, int(getattr(provider, "prompt_char_limit", DEFAULT_PROMPT_CHAR_LIMIT)))

    def _compose_prompt(
        self,
        prompt: str,
        purpose: str,
        request_style: str,
        context: dict[str, Any],
        prompt_limit: int = DEFAULT_PROMPT_CHAR_LIMIT,
    ) -> tuple[str, dict[str, Any]]:
        configured_rules = self.manual_rules if context.get("manual") else self.auto_rules
        configured_prompt = self.manual_prompt if context.get("manual") else self.auto_prompt
        if not context.get("manual") and self.auto_use_manual_prompt:
            configured_rules = self.manual_rules or self.auto_rules
            configured_prompt = self.manual_prompt or self.auto_prompt
        replacements = {
            "scene": str(context.get("scene") or ""),
            "narration": str(context.get("narration") or ""),
            "actions": str(context.get("actions") or ""),
            "panels": str(context.get("panels") or context.get("storyboard") or ""),
        }
        def render(value: str) -> str:
            for key, replacement in replacements.items():
                value = value.replace("{" + key + "}", replacement)
            return value
        segments = {
            "style_prefix": self.style_prefix,
            "request_style": str(request_style or "").strip(),
            "rules": render(configured_rules),
            "template": render(configured_prompt),
            "scene": prompt,
            "purpose": PURPOSE_PROMPT_SUFFIXES[purpose],
        }
        reference_names = context.get("avatar_reference_names")
        if isinstance(reference_names, dict) and reference_names:
            combined = len(reference_names) > 1 and context.get("combine_avatar_references", True) is True
            segments["reference_labels"] = (
                "The single uploaded portrait sheet maps "
                if combined else "Uploaded portrait references correspond to these visible characters: "
            ) + ", ".join(
                (f"Ref {index}: " if combined else "") + f"{str(name)[:100]} ({str(uid)[:80]})"
                for index, (uid, name) in enumerate(reference_names.items(), 1) if str(name).strip()
            ) + ". Keep their identity and appearance consistent."
            if combined:
                segments["reference_labels"] += (
                    " The sheet is for character identity only, not composition. Do not copy its grid, "
                    "labels or portrait count into the output; use the requested scene panels instead."
                )
        reduced: list[str] = []

        def compose() -> str:
            return "\n\n".join(part for part in segments.values() if part)

        prompt_limit = max(
            len(segments["purpose"]),
            int(prompt_limit or DEFAULT_PROMPT_CHAR_LIMIT),
        )
        for key in ("style_prefix", "rules", "template", "request_style", "scene"):
            overflow = len(compose()) - prompt_limit
            if overflow <= 0:
                break
            current = segments.get(key, "")
            if not current:
                continue
            storyboard = context.get("storyboard")
            if key == "scene" and isinstance(storyboard, dict) and storyboard.get("panels"):
                available = len(current) - overflow
                if available < 256:
                    raise ImageGenerationError("提示词预算不足以保留全部分镜和头像标识，请缩短人物或地点名称")
                try:
                    segments[key], _ = build_storyboard_prompt(
                        storyboard["panels"], max_chars=available,
                        character_appearances=storyboard.get("character_appearances"),
                    )
                except StoryboardInferenceError as exc:
                    raise ImageGenerationError(str(exc)) from exc
            else:
                segments[key] = current[:max(0, len(current) - overflow)]
            reduced.append(key)

        composed = compose()
        if len(composed) > prompt_limit:
            raise ImageGenerationError("提示词预算不足以保留全部分镜和头像标识，请缩短人物或地点名称")
        return composed, {
            "limit": prompt_limit,
            "used": len(composed),
            "adjusted": bool(reduced),
            "reduced_segments": reduced,
        }

    def _size_for(self, request: ImageGenerationRequest, purpose: str) -> str:
        ratio = str(request.aspect_ratio or "").strip()
        if ratio in {"1:1", "square"} or purpose in {"avatar", "item"}:
            return self.square_size
        return self.landscape_size

    def _validate_config(self) -> None:
        if self.provider_id not in IMAGE_PROVIDER_IDS:
            raise ValueError(f"不支持的图像生成 provider：{self.provider_id}")
        if self.provider_id == "minimax" and self.model and self.model != "image-01":
            raise ValueError("MiniMax 图像生成目前仅支持 image-01")
        if not self.enabled:
            return
        if self.base_url:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("图像生成 Base URL 必须是无内嵌凭据的 http(s) 地址")


def _is_local_endpoint(value: str) -> bool:
    hostname = (urlparse(value).hostname or "").strip().lower()
    if hostname in {"localhost", "host.docker.internal"} or hostname.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_private
    except ValueError:
        return False
