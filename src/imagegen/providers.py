"""Built-in image-generation provider adapters."""

from __future__ import annotations

import base64
import binascii
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlparse, urlunparse

import aiohttp

from src.openai_endpoints import openai_endpoint_url

from .contracts import ImageReference


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_JSON_RESPONSE_BYTES = 30 * 1024 * 1024


class ImageProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderImage:
    body: bytes
    content_type: str
    revised_prompt: str = ""


class ImageProvider(ABC):
    provider_id: str
    supports_reference_images: bool = False
    prompt_char_limit: int = 12_000

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        proxy_url: str = "",
    ) -> None:
        self.base_url = str(base_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip()
        self.timeout_seconds = max(5.0, min(float(timeout_seconds), 300.0))
        self.proxy_url = str(proxy_url or "").strip() or None

    @abstractmethod
    async def generate(
        self, prompt: str, *, size: str, quality: str = "",
        reference_images: Sequence[ImageReference] = (),
    ) -> ProviderImage:
        raise NotImplementedError


class OpenAICompatibleImageProvider(ImageProvider):
    provider_id = "openai-compatible"
    supports_reference_images = True

    async def generate(
        self, prompt: str, *, size: str, quality: str = "",
        reference_images: Sequence[ImageReference] = (),
    ) -> ProviderImage:
        if reference_images:
            return await self._generate_with_references(
                prompt, size=size, quality=quality, reference_images=reference_images,
            )
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"model": self.model, "prompt": prompt, "n": 1, "size": size}
        if quality:
            payload["quality"] = quality
        attempts = [
            {**payload, "response_format": "b64_json"},
            payload,
            {key: value for key, value in payload.items() if key != "quality"},
        ]
        response_payload: dict | None = None
        last_error = ""
        for index, attempt in enumerate(attempts):
            try:
                response_payload = await self._post_json(attempt, headers)
                break
            except ImageProviderError as exc:
                last_error = str(exc)
                if "HTTP 400" not in last_error or index == len(attempts) - 1:
                    raise
        if response_payload is None:
            raise ImageProviderError(last_error or "图像生成服务没有返回响应")
        items = response_payload.get("data")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise ImageProviderError("图像生成服务响应缺少 data 数组")
        item = items[0]
        if item.get("b64_json"):
            try:
                body = base64.b64decode(str(item["b64_json"]), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ImageProviderError("图像生成服务返回了无效的 Base64 图片") from exc
            content_type = _image_content_type(body, "image/png")
        elif item.get("url"):
            body, content_type = await self._download_image(str(item["url"]), headers)
        else:
            raise ImageProviderError("图像生成服务响应中既无 b64_json 也无 url")
        if not body:
            raise ImageProviderError("图像生成服务返回了空图片")
        if len(body) > MAX_IMAGE_BYTES:
            raise ImageProviderError("生成图片不能超过 20 MB")
        return ProviderImage(
            body=body,
            content_type=content_type,
            revised_prompt=str(item.get("revised_prompt") or "")[:4000],
        )

    async def _generate_with_references(
        self, prompt: str, *, size: str, quality: str,
        reference_images: Sequence[ImageReference],
    ) -> ProviderImage:
        fields: list[tuple[str, str]] = [("model", self.model), ("prompt", prompt), ("n", "1"), ("size", size)]
        if quality:
            fields.append(("quality", quality))
        attempts = [
            [*fields, ("response_format", "b64_json")],
            list(fields),
            [item for item in fields if item[0] != "quality"],
        ]
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response_payload: dict | None = None
        last_error = ""
        for index, attempt in enumerate(attempts):
            try:
                response_payload = await self._post_multipart(attempt, reference_images, headers)
                break
            except ImageProviderError as exc:
                last_error = str(exc)
                if "HTTP 400" not in last_error or index == len(attempts) - 1:
                    raise
        if response_payload is None:
            raise ImageProviderError(last_error or "图像生成服务没有返回响应")
        return await self._decode_image_response(response_payload, headers)

    async def _decode_image_response(self, response_payload: dict, headers: dict[str, str]) -> ProviderImage:
        items = response_payload.get("data")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise ImageProviderError("图像生成服务响应缺少 data 数组")
        item = items[0]
        if item.get("b64_json"):
            try:
                body = base64.b64decode(str(item["b64_json"]), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ImageProviderError("图像生成服务返回了无效的 Base64 图片") from exc
            content_type = _image_content_type(body, "image/png")
        elif item.get("url"):
            body, content_type = await self._download_image(str(item["url"]), headers)
        else:
            raise ImageProviderError("图像生成服务响应中既无 b64_json 也无 url")
        if not body:
            raise ImageProviderError("图像生成服务返回了空图片")
        if len(body) > MAX_IMAGE_BYTES:
            raise ImageProviderError("生成图片不能超过 20 MB")
        return ProviderImage(body=body, content_type=content_type, revised_prompt=str(item.get("revised_prompt") or "")[:4000])

    async def _post_multipart(
        self, fields: Sequence[tuple[str, str]], reference_images: Sequence[ImageReference], headers: dict[str, str],
    ) -> dict:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds, connect=min(15.0, self.timeout_seconds))
        form = aiohttp.FormData()
        for key, value in fields:
            form.add_field(key, value)
        field_name = "image" if len(reference_images) == 1 else "image[]"
        for index, reference in enumerate(reference_images):
            form.add_field(
                field_name, reference.content,
                filename=str(reference.file_name or f"reference-{index + 1}.webp"),
                content_type=str(reference.content_type or "image/webp").split(";", 1)[0],
            )
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(_openai_image_edit_url(self.base_url), data=form, headers=headers, proxy=self.proxy_url) as response:
                    body = await _read_limited(response, MAX_JSON_RESPONSE_BYTES)
                    if response.status >= 400:
                        detail = body.decode("utf-8", "replace")[:1000]
                        raise ImageProviderError(f"图像生成服务返回 HTTP {response.status}: {detail or response.reason}")
        except ImageProviderError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ImageProviderError(f"无法连接图像生成服务：{exc}") from exc
        try:
            result = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError as exc:
            raise ImageProviderError("图像生成服务返回了无法解析的响应") from exc
        if not isinstance(result, dict):
            raise ImageProviderError("图像生成服务返回了非对象响应")
        return result

    async def _post_json(self, payload: dict, headers: dict[str, str]) -> dict:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds, connect=min(15.0, self.timeout_seconds))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    _openai_image_url(self.base_url),
                    json=payload,
                    headers=headers,
                    proxy=self.proxy_url,
                ) as response:
                    body = await _read_limited(response, MAX_JSON_RESPONSE_BYTES)
                    if response.status >= 400:
                        detail = body.decode("utf-8", "replace")[:1000]
                        raise ImageProviderError(
                            f"图像生成服务返回 HTTP {response.status}: {detail or response.reason}"
                        )
        except ImageProviderError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ImageProviderError(f"无法连接图像生成服务：{exc}") from exc
        try:
            result = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError as exc:
            raise ImageProviderError("图像生成服务返回了无法解析的响应") from exc
        if not isinstance(result, dict):
            raise ImageProviderError("图像生成服务返回了非对象响应")
        return result

    async def _download_image(
        self,
        image_url: str,
        api_headers: dict[str, str],
    ) -> tuple[bytes, str]:
        parsed = urlparse(image_url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ImageProviderError("生成图片 URL 仅支持 HTTP 或 HTTPS")
        api_origin = urlparse(self.base_url)
        headers = {}
        if (parsed.scheme, parsed.netloc) == (api_origin.scheme, api_origin.netloc):
            headers = {key: value for key, value in api_headers.items() if key != "Content-Type"}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds, connect=min(15.0, self.timeout_seconds))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(image_url, headers=headers, proxy=self.proxy_url) as response:
                    if response.status >= 400:
                        detail = (await response.text())[:1000]
                        raise ImageProviderError(
                            f"下载生成图片返回 HTTP {response.status}: {detail or response.reason}"
                        )
                    body = await _read_limited(response, MAX_IMAGE_BYTES)
                    content_type = _image_content_type(
                        body,
                        str(response.headers.get("Content-Type") or ""),
                    )
        except ImageProviderError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ImageProviderError(f"下载生成图片失败：{exc}") from exc
        return body, content_type


class MiniMaxImageProvider(ImageProvider):
    provider_id = "minimax"
    prompt_char_limit = 1_500

    async def generate(
        self, prompt: str, *, size: str, quality: str = "",
        reference_images: Sequence[ImageReference] = (),
    ) -> ProviderImage:
        if reference_images:
            raise ImageProviderError("当前 MiniMax 图像服务商不支持头像参考图，请关闭该选项后重试")
        if len(prompt) > self.prompt_char_limit:
            raise ImageProviderError("MiniMax 图像提示词不能超过 1500 个字符")
        try:
            width_text, height_text = str(size or "").strip().lower().split("x", 1)
            width, height = int(width_text), int(height_text)
        except (TypeError, ValueError) as exc:
            raise ImageProviderError(
                "MiniMax 图片尺寸必须是 512 到 2048 之间且为 8 的倍数"
            ) from exc
        if not all(
            512 <= value <= 2048 and value % 8 == 0 for value in (width, height)
        ):
            raise ImageProviderError(
                "MiniMax 图片尺寸必须是 512 到 2048 之间且为 8 的倍数"
            )
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "width": width,
            "height": height,
            "response_format": "base64",
        }
        response_payload = await self._post_json(payload, headers)
        base_response = response_payload.get("base_resp")
        if isinstance(base_response, dict):
            status_code = base_response.get("status_code")
            if status_code not in {None, 0, "0"}:
                status_message = str(base_response.get("status_msg") or "未知错误")
                raise ImageProviderError(
                    f"MiniMax 图像生成失败（{status_code}）：{status_message}"
                )
        data = response_payload.get("data")
        images = data.get("image_base64") if isinstance(data, dict) else None
        if not isinstance(images, list) or not images:
            raise ImageProviderError("MiniMax 图像生成响应缺少 data.image_base64")
        try:
            body = base64.b64decode(str(images[0]), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ImageProviderError(
                "MiniMax 图像生成服务返回了无效的 Base64 图片"
            ) from exc
        if not body:
            raise ImageProviderError("MiniMax 图像生成服务返回了空图片")
        if len(body) > MAX_IMAGE_BYTES:
            raise ImageProviderError("生成图片不能超过 20 MB")
        return ProviderImage(
            body=body,
            content_type=_image_content_type(body, "image/jpeg"),
        )

    async def _post_json(self, payload: dict, headers: dict[str, str]) -> dict:
        timeout = aiohttp.ClientTimeout(
            total=self.timeout_seconds,
            connect=min(15.0, self.timeout_seconds),
        )
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    _minimax_image_url(self.base_url),
                    json=payload,
                    headers=headers,
                    proxy=self.proxy_url,
                ) as response:
                    body = await _read_limited(response, MAX_JSON_RESPONSE_BYTES)
                    if response.status >= 400:
                        detail = body.decode("utf-8", "replace")[:1000]
                        raise ImageProviderError(
                            f"MiniMax 图像生成服务返回 HTTP {response.status}: {detail or response.reason}"
                        )
        except ImageProviderError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ImageProviderError(
                f"无法连接 MiniMax 图像生成服务：{exc}"
            ) from exc
        try:
            result = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError as exc:
            raise ImageProviderError("MiniMax 图像生成服务返回了无法解析的响应") from exc
        if not isinstance(result, dict):
            raise ImageProviderError("MiniMax 图像生成服务返回了非对象响应")
        return result


def create_image_provider(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
    proxy_url: str = "",
    provider_id: str = "openai-compatible",
) -> ImageProvider:
    kwargs = {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "timeout_seconds": timeout_seconds,
        "proxy_url": proxy_url,
    }
    provider_id = str(provider_id or "").strip().lower()
    hostname = (urlparse(str(base_url or "").strip()).hostname or "").lower()
    if provider_id == "minimax" or (
        hostname in {"api.minimax.cn", "api.minimaxi.com"}
        and model == "image-01"
    ):
        return MiniMaxImageProvider(**kwargs)
    if provider_id != "openai-compatible":
        raise ImageProviderError(f"不支持的图像生成 provider：{provider_id}")
    return OpenAICompatibleImageProvider(**kwargs)


def _openai_image_url(base_url: str) -> str:
    return openai_endpoint_url(base_url, "images/generations")


def _openai_image_edit_url(base_url: str) -> str:
    return openai_endpoint_url(base_url, "images/edits")


def _minimax_image_url(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    path = parsed.path.rstrip("/")
    if path.endswith("/image_generation"):
        target = path
    elif path.endswith("/v1"):
        target = path + "/image_generation"
    else:
        target = path + "/v1/image_generation"
    return urlunparse(parsed._replace(path=target))


async def _read_limited(response: aiohttp.ClientResponse, limit: int) -> bytes:
    if response.content_length is not None and response.content_length > limit:
        raise ImageProviderError("图像生成服务响应过大")
    chunks = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(chunks) + len(chunk) > limit:
            raise ImageProviderError("图像生成服务响应过大")
        chunks.extend(chunk)
    return bytes(chunks)


def _image_content_type(body: bytes, fallback: str) -> str:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    normalized = str(fallback or "").split(";", 1)[0].strip().lower()
    if normalized in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        return "image/jpeg" if normalized == "image/jpg" else normalized
    raise ImageProviderError("图像生成服务返回了不支持的图片格式")
