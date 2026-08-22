"""场景图生成门面：provider 调用、资产归一化落盘与错误转译。"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from src.imagegen import ImageGenError, SceneImageGenerator


def _png_bytes(size: tuple[int, int] = (400, 225), color=(90, 120, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeHost:
    def __init__(
        self,
        provider_dir: Path,
        *,
        payload: bytes | None = None,
        error: str = "",
        plugin: str = "stub",
        legacy_base64: bool = False,
    ):
        self.plugin = plugin
        self.calls = []
        self._provider_dir = provider_dir
        self._payload = payload
        self._error = error
        self._legacy_base64 = legacy_base64

    def find_provider(self, capability):
        return self.plugin if self._payload is not None or self._error else None

    async def call_provider(self, plugin_id, capability, alias, arguments, *, timeout=0):
        self.calls.append((plugin_id, capability, alias, arguments, timeout))
        if self._error:
            return {"ok": False, "error": self._error}
        if not self._legacy_base64:
            self._provider_dir.mkdir(parents=True, exist_ok=True)
            (self._provider_dir / "generated.png").write_bytes(self._payload)
            return {"ok": True, "image_path": "generated.png", "mime_type": "image/png"}
        return {
            "ok": True,
            "image_base64": base64.b64encode(self._payload).decode("ascii"),
            "mime_type": "image/png",
        }

    def provider_asset_path(self, plugin_id, relative_path):
        return self._provider_dir / relative_path


def test_generator_without_plugin_degrades(tmp_path):
    generator = SceneImageGenerator(None, tmp_path)
    assert generator.available() is False
    with pytest.raises(ImageGenError, match="没有正在运行"):
        import asyncio
        asyncio.run(generator.generate("harbor"))


def test_generate_stores_normalized_webp_asset(tmp_path):
    host = _FakeHost(tmp_path / "provider", payload=_png_bytes())
    generator = SceneImageGenerator(host, tmp_path)

    import asyncio
    result = asyncio.run(generator.generate("misty harbor at dusk"))

    reference = result["reference"]
    assert reference["kind"] == "upload"
    asset_path = tmp_path / f"{reference['asset_id']}.webp"
    assert asset_path.is_file()
    with Image.open(asset_path) as stored:
        assert stored.format == "WEBP"
        assert stored.size == (1600, 900)
    assert host.calls[0][1] == "image-generation"
    assert host.calls[0][2] == "generate"
    assert host.calls[0][3] == {"prompt": "misty harbor at dusk"}
    # 内容寻址：同图再生成复用同一资产
    again = asyncio.run(generator.generate("misty harbor at dusk"))
    assert again["asset_id"] == reference["asset_id"]


def test_generate_translates_plugin_and_image_errors(tmp_path):
    import asyncio

    failing = SceneImageGenerator(_FakeHost(tmp_path / "failing", error="upstream down"), tmp_path)
    with pytest.raises(ImageGenError, match="upstream down"):
        asyncio.run(failing.generate("harbor"))

    tiny = SceneImageGenerator(
        _FakeHost(tmp_path / "tiny", payload=_png_bytes(size=(120, 90))), tmp_path,
    )
    with pytest.raises(ImageGenError, match="尺寸"):
        asyncio.run(tiny.generate("harbor"))

    garbage = SceneImageGenerator(
        _FakeHost(tmp_path / "garbage", payload=b"not-an-image"), tmp_path,
    )
    with pytest.raises(ImageGenError):
        asyncio.run(garbage.generate("harbor"))

    empty = SceneImageGenerator(_FakeHost(tmp_path / "empty", payload=_png_bytes()), tmp_path)
    with pytest.raises(ImageGenError, match="画面描述为空"):
        asyncio.run(empty.generate("   "))


def test_generate_accepts_legacy_base64_result(tmp_path):
    import asyncio

    host = _FakeHost(
        tmp_path / "legacy",
        payload=_png_bytes(),
        legacy_base64=True,
    )
    result = asyncio.run(SceneImageGenerator(host, tmp_path).generate("harbor"))

    assert (tmp_path / f"{result['asset_id']}.webp").is_file()
