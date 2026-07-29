"""Bot Bridge extension protocol backed by managed process plugins."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.webui.api import WebAPI

BRIDGE_EXTENSION_PROTOCOL_VERSION = 1
BRIDGE_EXTENSION_STAGES = ("before_message", "after_result", "render")
MAX_BRIDGE_PAYLOAD_BYTES = 192 * 1024


def capabilities(api: "WebAPI") -> dict[str, Any]:
    extensions = api._plugins.list_bridge_extensions() if api._plugins else []
    return {
        "protocol_version": BRIDGE_EXTENSION_PROTOCOL_VERSION,
        "stages": list(BRIDGE_EXTENSION_STAGES),
        "extensions": len(extensions),
    }


async def apply(
    api: "WebAPI",
    stage: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    stage = str(stage or "").strip()
    if stage not in BRIDGE_EXTENSION_STAGES:
        return {"ok": False, "error": f"不支持的 Bot Bridge 扩展阶段：{stage}"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "payload 必须是对象"}
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return {"ok": False, "error": "payload 不是有效 JSON"}
    if len(encoded) > MAX_BRIDGE_PAYLOAD_BYTES:
        return {"ok": False, "error": "Bot Bridge 扩展 payload 不能超过 192 KB"}
    if not api._plugins:
        return {
            "ok": True,
            "handled": False,
            "payload": payload,
            "outputs": [],
            "applied": [],
        }
    result = await api._plugins.apply_bridge_extensions(stage, payload)
    return {"ok": True, **result}


def asset_path(api: "WebAPI", plugin_id: str, relative_path: str) -> Path:
    if not api._plugins:
        raise KeyError("插件宿主未启用")
    return api._plugins.bridge_asset_path(plugin_id, relative_path)
