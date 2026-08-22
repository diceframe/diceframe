"""Built-in image-generation service, provider, and asset persistence."""

from __future__ import annotations

import asyncio
import base64
import io
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image

from src.imagegen import (
    ImageGenerationError,
    ImageGenerationRequest,
    ImageGenerationService,
)
from src.imagegen.providers import (
    ImageProviderError,
    OpenAICompatibleImageProvider,
    ProviderImage,
)


def _png_bytes(size: tuple[int, int] = (400, 225), color=(90, 120, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _config(**overrides):
    config = {
        "imagegen_enabled": True,
        "imagegen_provider": "openai-compatible",
        "imagegen_base_url": "https://images.example/v1",
        "imagegen_api_key": "secret",
        "imagegen_model": "image-model",
        "imagegen_square_size": "1024x1024",
        "imagegen_landscape_size": "1792x1024",
        "imagegen_quality": "high",
        "imagegen_style_prefix": "Painterly tabletop RPG art.",
        "imagegen_timeout_seconds": 30,
        "imagegen_auto_scene": True,
    }
    config.update(overrides)
    return config


class _FakeProvider:
    def __init__(self, *, body: bytes | None = None, error: str = "") -> None:
        self.body = body or _png_bytes()
        self.error = error
        self.calls = []

    async def generate(self, prompt: str, *, size: str, quality: str = "") -> ProviderImage:
        self.calls.append((prompt, size, quality))
        if self.error:
            raise ImageProviderError(self.error)
        return ProviderImage(self.body, "image/png", "provider revised prompt")


def _service(tmp_path, provider: _FakeProvider, **config_overrides) -> ImageGenerationService:
    service = ImageGenerationService(_config(**config_overrides), tmp_path)
    service._provider = lambda: provider
    return service


def test_service_availability_and_config_validation(tmp_path):
    disabled = ImageGenerationService(
        _config(imagegen_enabled=False, imagegen_base_url="", imagegen_model=""),
        tmp_path,
    )
    assert disabled.available is False
    assert disabled.public_config() == {
        "enabled": False,
        "available": False,
        "provider": "openai-compatible",
        "model": "",
        "auto_scene": True,
    }
    with pytest.raises(ImageGenerationError, match="尚未配置或启用"):
        asyncio.run(disabled.generate(ImageGenerationRequest(prompt="harbor")))
    with pytest.raises(ValueError, match="Base URL"):
        ImageGenerationService(_config(imagegen_base_url="file:///tmp/images"), tmp_path)
    with pytest.raises(ValueError, match="必须选择模型"):
        ImageGenerationService(_config(imagegen_model=""), tmp_path)


def test_service_bypasses_proxy_for_local_endpoints(tmp_path):
    local = ImageGenerationService(
        _config(imagegen_base_url="http://127.0.0.1:8080/v1"),
        tmp_path,
        proxy_url="http://proxy.example:7890",
    )
    remote = ImageGenerationService(
        _config(imagegen_base_url="https://images.example/v1"),
        tmp_path,
        proxy_url="http://proxy.example:7890",
    )

    assert local.proxy_url == ""
    assert remote.proxy_url == "http://proxy.example:7890"


@pytest.mark.parametrize(
    ("purpose", "aspect_ratio", "requested_size", "stored_size"),
    [
        ("scene", "16:9", "1792x1024", (1600, 900)),
        ("avatar", "", "1024x1024", (512, 512)),
        ("item", "", "1024x1024", (768, 768)),
        ("map", "16:9", "1792x1024", (400, 225)),
        ("freeform", "1:1", "1024x1024", (400, 225)),
    ],
)
def test_generate_supports_all_purposes_and_normalizes_assets(
    tmp_path,
    purpose,
    aspect_ratio,
    requested_size,
    stored_size,
):
    provider = _FakeProvider()
    service = _service(tmp_path, provider)
    result = asyncio.run(service.generate(ImageGenerationRequest(
        prompt="misty harbor at dusk",
        purpose=purpose,
        owner_type="game",
        owner_id="web:room:bot",
        aspect_ratio=aspect_ratio,
        style="muted blue palette",
        context={"round": 3},
    )))

    assert result.purpose == purpose
    assert result.revised_prompt == "provider revised prompt"
    assert provider.calls[0][1:] == (requested_size, "high")
    assert provider.calls[0][0].startswith("Painterly tabletop RPG art.\n\nmuted blue palette")
    assert "misty harbor at dusk" in provider.calls[0][0]
    with Image.open(service.assets.file(result.asset_id)) as stored:
        assert stored.format == "WEBP"
        assert stored.size == stored_size

    records = service.assets.list_records(
        owner_type="game",
        owner_id="web:room:bot",
        purpose=purpose,
    )
    assert len(records) == 1
    assert records[0]["generation_id"] == result.generation_id
    assert records[0]["context"] == {"round": 3}


def test_content_addressing_reuses_asset_but_keeps_generation_history(tmp_path):
    provider = _FakeProvider()
    service = _service(tmp_path, provider)
    request = ImageGenerationRequest(prompt="harbor", purpose="scene")

    first = asyncio.run(service.generate(request))
    second = asyncio.run(service.generate(request))

    assert first.asset_id == second.asset_id
    assert first.generation_id != second.generation_id
    assert len(service.assets.list_records()) == 2


def test_service_translates_provider_asset_and_request_errors(tmp_path):
    failing = _service(tmp_path / "failing", _FakeProvider(error="upstream down"))
    with pytest.raises(ImageGenerationError, match="upstream down"):
        asyncio.run(failing.generate(ImageGenerationRequest(prompt="harbor")))

    garbage = _service(tmp_path / "garbage", _FakeProvider(body=b"not-an-image"))
    with pytest.raises(ImageGenerationError, match="无法读取"):
        asyncio.run(garbage.generate(ImageGenerationRequest(prompt="harbor")))

    service = _service(tmp_path / "request", _FakeProvider())
    with pytest.raises(ImageGenerationError, match="画面描述为空"):
        asyncio.run(service.generate(ImageGenerationRequest(prompt="   ")))
    with pytest.raises(ImageGenerationError, match="不支持"):
        asyncio.run(service.generate(ImageGenerationRequest(prompt="x", purpose="cover")))


@pytest.mark.asyncio
async def test_openai_provider_reads_base64_and_retries_400_compatibility(monkeypatch):
    provider = OpenAICompatibleImageProvider(
        base_url="https://images.example/v1",
        api_key="secret",
        model="image-model",
        timeout_seconds=30,
    )
    calls = []

    async def fake_post(payload, headers):
        calls.append((payload, headers))
        if len(calls) == 1:
            raise ImageProviderError("图像生成服务返回 HTTP 400: unsupported response_format")
        return {
            "data": [{
                "b64_json": base64.b64encode(_png_bytes()).decode("ascii"),
                "revised_prompt": "revised",
            }],
        }

    monkeypatch.setattr(provider, "_post_json", fake_post)
    result = await provider.generate("harbor", size="1024x1024", quality="high")

    assert result.body.startswith(b"\x89PNG")
    assert result.content_type == "image/png"
    assert result.revised_prompt == "revised"
    assert calls[0][0]["response_format"] == "b64_json"
    assert "response_format" not in calls[1][0]
    assert calls[0][1]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_openai_provider_accepts_url_results(monkeypatch):
    provider = OpenAICompatibleImageProvider(
        base_url="https://images.example/v1",
        api_key="secret",
        model="image-model",
        timeout_seconds=30,
    )
    downloaded = []

    async def fake_post(payload, headers):
        return {"data": [{"url": "https://cdn.example/result.png"}]}

    async def fake_download(url, headers):
        downloaded.append((url, headers))
        return _png_bytes(), "image/png"

    monkeypatch.setattr(provider, "_post_json", fake_post)
    monkeypatch.setattr(provider, "_download_image", fake_download)

    result = await provider.generate("harbor", size="1024x1024")
    assert result.content_type == "image/png"
    assert downloaded[0][0] == "https://cdn.example/result.png"


@pytest.mark.asyncio
async def test_url_download_forwards_credentials_only_to_same_origin():
    seen_headers = []

    async def image_handler(request):
        seen_headers.append(request.headers.get("Authorization", ""))
        return web.Response(body=_png_bytes(), content_type="image/png")

    app = web.Application()
    app.router.add_get("/asset.png", image_handler)
    async with TestServer(app) as server:
        provider = OpenAICompatibleImageProvider(
            base_url=str(server.make_url("/v1")),
            api_key="secret",
            model="image-model",
            timeout_seconds=30,
        )
        body, content_type = await provider._download_image(
            str(server.make_url("/asset.png")),
            {"Authorization": "Bearer secret", "Content-Type": "application/json"},
        )
        assert body.startswith(b"\x89PNG")
        assert content_type == "image/png"
        assert seen_headers == ["Bearer secret"]

        provider.base_url = "https://images.example/v1"
        await provider._download_image(
            str(server.make_url("/asset.png")),
            {"Authorization": "Bearer secret", "Content-Type": "application/json"},
        )
        assert seen_headers[-1] == ""


def test_generation_records_are_valid_json(tmp_path):
    service = _service(tmp_path, _FakeProvider())
    result = asyncio.run(service.generate(ImageGenerationRequest(prompt="harbor")))
    record_path = service.assets.records_dir / f"{result.generation_id}.json"
    assert json.loads(record_path.read_text(encoding="utf-8"))["asset_id"] == result.asset_id
