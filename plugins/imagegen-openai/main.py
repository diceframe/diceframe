"""imagegen-openai：OpenAI 兼容图像生成 provider 插件。

宿主通过 JSON-RPC stdio 调用 image-generation capability 的 generate 方法；
本进程同步处理请求（一次一张图），用标准库 urllib 调用
``POST {base_url}/images/generations``。所有配置经宿主以 DF_IMAGEGEN_*
环境变量注入。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from src.plugin_sdk import ProviderRuntime

runtime = ProviderRuntime()

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _compose_prompt(description: str) -> str:
    prefix = _env("DF_IMAGEGEN_STYLE_PREFIX")
    description = str(description or "").strip()
    if prefix and description:
        return f"{prefix}, {description}"
    return description or prefix


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        if value:
            request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("生图服务返回了非对象响应")
    return data


def _download_bytes(url: str, headers: dict[str, str], timeout: float) -> tuple[bytes, str]:
    if urllib.parse.urlsplit(url).scheme.lower() not in {"http", "https"}:
        raise ValueError("生成图片 URL 仅支持 HTTP 或 HTTPS")
    request = urllib.request.Request(url)
    for key, value in headers.items():
        if value:
            request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content = response.read(MAX_IMAGE_BYTES + 1)
        mime_type = str(response.headers.get("Content-Type") or "image/png")
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("生成图片不能超过 20 MB")
    return content, mime_type


def _download_headers(image_url: str, base_url: str, headers: dict[str, str]) -> dict[str, str]:
    image_origin = urllib.parse.urlsplit(image_url)
    api_origin = urllib.parse.urlsplit(base_url)
    if (image_origin.scheme, image_origin.netloc) == (api_origin.scheme, api_origin.netloc):
        return headers
    return {}


def _image_suffix(content: bytes, mime_type: str) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    normalized_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
    }.get(normalized_mime)
    if not suffix:
        raise ValueError("生图服务返回了不支持的图片格式")
    return suffix


def _store_image(content: bytes, mime_type: str) -> str:
    if not content:
        raise ValueError("生图服务返回了空图片")
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("生成图片不能超过 20 MB")
    suffix = _image_suffix(content, mime_type)
    digest = hashlib.sha256(content).hexdigest()
    data_dir = Path(os.environ["DICEFRAME_PLUGIN_DATA_DIR"]).resolve()
    image_dir = data_dir / "generated-images"
    image_dir.mkdir(parents=True, exist_ok=True)
    target = image_dir / f"{digest}{suffix}"
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
    return target.relative_to(data_dir).as_posix()


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        detail = exc.read().decode("utf-8", "replace")[:500]
    except Exception:
        detail = ""
    return f"HTTP {exc.code}: {detail or exc.reason}"


@runtime.capability(
    kind="image-generation",
    version=1,
    title="图像生成（OpenAI 兼容）",
    description="按文字描述生成场景图，供叙事流内嵌、场景卡与画廊展示。",
)
def generate_image(arguments: dict, context: dict) -> dict:
    prompt = _compose_prompt(str(arguments.get("prompt") or ""))
    if not prompt:
        return {"ok": False, "error": "画面描述为空"}

    base_url = _env("DF_IMAGEGEN_BASE_URL").rstrip("/")
    api_key = _env("DF_IMAGEGEN_API_KEY")
    model = _env("DF_IMAGEGEN_MODEL")
    size = _env("DF_IMAGEGEN_SIZE", "1792x1024")
    if not base_url:
        return {"ok": False, "error": "请先从模型设置选择 AI 服务商"}
    if not model:
        return {"ok": False, "error": "请先从所选服务商的模型目录选择生图模型"}
    try:
        timeout = float(_env("DF_IMAGEGEN_TIMEOUT", "120"))
    except ValueError:
        timeout = 120.0
    timeout = min(max(timeout, 5.0), 300.0)

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload: dict[str, Any] = {"model": model, "prompt": prompt[:4000], "n": 1, "size": size}
    # dall-e 系支持 response_format=b64_json；个别兼容实现不认该参数，
    # 带参失败时降级重试一次并兼容 url 响应。
    url = f"{base_url}/images/generations"
    try:
        data = _post_json(url, headers, {**payload, "response_format": "b64_json"}, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            return {"ok": False, "error": _http_error_message(exc)}
        try:
            data = _post_json(url, headers, payload, timeout)
        except urllib.error.HTTPError as retry_exc:
            return {"ok": False, "error": _http_error_message(retry_exc)}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "error": f"生图请求失败：{exc}"}

    items = data.get("data")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return {"ok": False, "error": "生图服务响应缺少 data 数组"}
    item = items[0]

    try:
        if item.get("b64_json"):
            content = base64.b64decode(str(item["b64_json"]), validate=True)
            mime_type = "image/png"
        elif item.get("url"):
            image_url = str(item["url"])
            content, mime_type = _download_bytes(
                image_url,
                _download_headers(image_url, base_url, headers),
                timeout,
            )
        else:
            return {"ok": False, "error": "生图服务响应中既无 b64_json 也无 url"}
        image_path = _store_image(content, mime_type)
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "error": f"下载生成图片失败：{exc}"}
    except (binascii.Error, ValueError, OSError) as exc:
        return {"ok": False, "error": f"处理生成图片失败：{exc}"}

    return {
        "ok": True,
        "image_path": image_path,
        "mime_type": mime_type,
        "revised_prompt": str(item.get("revised_prompt") or "")[:2000],
        "model": model,
    }


if __name__ == "__main__":
    runtime.run()
