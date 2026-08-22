"""OpenAI 兼容生图插件会把 Base64 与 URL 结果统一保存为文件。"""

from __future__ import annotations

import base64
import importlib.util
import io
from pathlib import Path

from PIL import Image


def _load_plugin():
    path = Path(__file__).resolve().parents[1] / "plugins" / "imagegen-openai" / "main.py"
    spec = importlib.util.spec_from_file_location("test_imagegen_openai_main", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (400, 225), (90, 120, 160)).save(output, format="PNG")
    return output.getvalue()


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("DICEFRAME_PLUGIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DF_IMAGEGEN_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("DF_IMAGEGEN_MODEL", "image-model")


def test_base64_response_is_saved_and_returns_relative_path(tmp_path, monkeypatch):
    plugin = _load_plugin()
    payload = _png_bytes()
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin, "_post_json", lambda *args: {
        "data": [{"b64_json": base64.b64encode(payload).decode("ascii")}],
    })

    result = plugin.generate_image({"prompt": "harbor"}, {})

    assert result["ok"] is True
    assert "image_base64" not in result
    assert result["image_path"].startswith("generated-images/")
    assert (tmp_path / result["image_path"]).read_bytes() == payload


def test_url_response_is_downloaded_saved_and_returns_relative_path(tmp_path, monkeypatch):
    plugin = _load_plugin()
    payload = _png_bytes()
    download_calls = []
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin, "_post_json", lambda *args: {
        "data": [{"url": "https://files.example.test/generated.png"}],
    })

    def download(url, headers, timeout):
        download_calls.append((url, headers, timeout))
        return payload, "image/png"

    monkeypatch.setattr(plugin, "_download_bytes", download)

    result = plugin.generate_image({"prompt": "harbor"}, {})

    assert result["ok"] is True
    assert "image_base64" not in result
    assert (tmp_path / result["image_path"]).read_bytes() == payload
    assert download_calls[0][0] == "https://files.example.test/generated.png"
    assert download_calls[0][1] == {}
