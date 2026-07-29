"""Reference ``bot-extension`` plugin for DiceFrame."""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

from src.plugin_sdk import BridgeExtensionRuntime

runtime = BridgeExtensionRuntime()
data_dir = Path(os.environ["DICEFRAME_PLUGIN_DATA_DIR"])


def _write_demo_png(path: Path, width: int = 480, height: int = 160) -> None:
    """Write a dependency-free solid PNG used only by this protocol example."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes((35, 42, 58, 255)) * width
    raw = row * height
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


@runtime.extension(
    name="example-command",
    title="示例命令",
    description="处理“插件测试”或“plugin test”。",
    stages=["before_message"],
    priority=100,
)
def example_command(_stage: str, payload: dict) -> dict:
    text = str(payload.get("text") or "").strip().lower()
    if text not in {"插件测试", "plugin test"}:
        return {"handled": False}
    return {
        "handled": True,
        "outputs": [{
            "type": "card",
            "title": "Bot Bridge 扩展已运行",
            "subtitle": "命令由示例插件处理",
            "lines": ["可以继续添加命令、Hook 和渲染器。"],
            "fallback_text": "Bot Bridge 扩展已运行。",
        }],
    }


@runtime.extension(
    name="reply-footer",
    title="回复后缀",
    description="在普通文字回复后附加配置的短文本。",
    stages=["after_result"],
    priority=10,
    kinds=["text"],
)
def reply_footer(_stage: str, payload: dict) -> dict:
    footer = str(os.getenv("BRIDGE_REPLY_FOOTER") or "").strip()
    text = str(payload.get("text") or "")
    if not footer or not text:
        return {"handled": False}
    changed = dict(payload)
    changed["text"] = f"{text}\n{footer}"
    return {"handled": False, "payload": changed}


@runtime.extension(
    name="qq-image-card",
    title="QQ 图片卡片示例",
    description="把 QQ 结构化卡片替换为插件生成的 PNG。",
    stages=["render"],
    priority=20,
    platforms=["qq"],
    kinds=["card"],
)
def qq_image_card(_stage: str, payload: dict) -> dict:
    if str(os.getenv("BRIDGE_IMAGE_CARDS") or "").lower() not in {"1", "true", "yes"}:
        return {"handled": False}
    image = data_dir / "example-card.png"
    _write_demo_png(image)
    fallback = str(payload.get("fallback_text") or payload.get("title") or "DiceFrame 卡片")
    return {
        "handled": True,
        "outputs": [{
            "type": "image",
            "path": image.name,
            "alt": str(payload.get("title") or "DiceFrame 卡片"),
            "fallback_text": fallback,
        }],
    }


runtime.run()
