"""Bot Bridge extension protocol backed by managed process plugins."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.bots.bridge_core.card_renderer import BRAND_FOOTER, render_card_png
from src.localization.messages import t

logger = logging.getLogger("trpg")

BRIDGE_EXTENSION_PROTOCOL_VERSION = 1
BRIDGE_EXTENSION_STAGES = ("before_message", "after_result", "render")
MAX_BRIDGE_PAYLOAD_BYTES = 192 * 1024
_BRIDGE_CARD_NAME_RE = re.compile(r"^card_[0-9a-f]{8,64}\.png$")


@dataclass(frozen=True)
class BotExtensionDependencies:
    plugin_host: Any | None


def capabilities(dependencies: BotExtensionDependencies) -> dict[str, Any]:
    host = dependencies.plugin_host
    extensions = host.list_bridge_extensions() if host else []
    return {
        "protocol_version": BRIDGE_EXTENSION_PROTOCOL_VERSION,
        "stages": list(BRIDGE_EXTENSION_STAGES),
        "extensions": len(extensions),
    }


async def apply(
    dependencies: BotExtensionDependencies,
    stage: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    stage = str(stage or "").strip()
    if stage not in BRIDGE_EXTENSION_STAGES:
        return {"ok": False, "error": t("webui.services.bot_extensions.unsupported_stage", stage=stage)}
    if not isinstance(payload, dict):
        return {"ok": False, "error": t("webui.services.bot_extensions.payload_not_object")}
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return {"ok": False, "error": t("webui.services.bot_extensions.payload_not_json")}
    if len(encoded) > MAX_BRIDGE_PAYLOAD_BYTES:
        return {"ok": False, "error": t("webui.services.bot_extensions.payload_too_large")}
    host = dependencies.plugin_host
    if not host:
        return {
            "ok": True,
            "handled": False,
            "payload": payload,
            "outputs": [],
            "applied": [],
        }
    result = await host.apply_bridge_extensions(stage, payload)
    outputs = result.get("outputs") if isinstance(result.get("outputs"), list) else []
    if outputs:
        result["outputs"] = _materialize_cards(dependencies, outputs)
    return {"ok": True, **result}


def bridge_card_dir(dependencies: BotExtensionDependencies) -> Path:
    """卡片渲染输出目录（data/bot/cards），与清缓存按钮一致。"""
    if dependencies.plugin_host is None:
        raise KeyError("插件宿主未启用")
    data_dir = Path(dependencies.plugin_host.data_dir).resolve()
    return (data_dir / "bot" / "cards").resolve()


def _materialize_cards(
    dependencies: BotExtensionDependencies,
    outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把服务端收到的 card 输出渲染成 PNG，转成可下载的 image 输出。

    渲染失败（如 PIL 缺失）时保留原 card 输出并记 warning，让渠道走自己的降级路径。
    """
    materialized: list[dict[str, Any]] = []
    for output in outputs:
        if not isinstance(output, dict) or output.get("type") != "card":
            materialized.append(output)
            continue
        title = str(output.get("title") or "").strip()
        subtitle = str(output.get("subtitle") or "").strip()
        lines = output.get("lines") if isinstance(output.get("lines"), list) else []
        try:
            png = render_card_png(
                bridge_card_dir(dependencies),
                title=title,
                subtitle=subtitle,
                lines=lines,
                footer=BRAND_FOOTER,
            )
        except Exception as exc:
            logger.warning("Bot Bridge 卡片渲染失败，保留原 card 输出: %s", exc)
            materialized.append(output)
            continue
        fallback = str(output.get("fallback_text") or "").strip()
        if not fallback:
            fallback = "\n".join([title, subtitle, *[str(x) for x in lines]]).strip()
        materialized.append({
            "type": "image",
            "asset_url": f"/api/bot/bridge-cards/{png.name}",
            "alt": title,
            "fallback_text": fallback,
        })
    return materialized


def bridge_card_path(dependencies: BotExtensionDependencies, name: str) -> Path:
    """解析卡片渲染输出文件路径；非法或不存在时抛异常。"""
    if not _BRIDGE_CARD_NAME_RE.match(str(name or "")):
        raise ValueError(t("webui.services.bot_extensions.card_file_name_invalid"))
    root = bridge_card_dir(dependencies)
    target = (root / str(name)).resolve()
    if root not in target.parents:
        raise ValueError(t("webui.services.bot_extensions.card_file_path_invalid"))
    if not target.is_file():
        raise KeyError(t("webui.services.bot_extensions.card_file_missing"))
    return target


def asset_path(
    dependencies: BotExtensionDependencies,
    plugin_id: str,
    relative_path: str,
) -> Path:
    if not dependencies.plugin_host:
        raise KeyError("插件宿主未启用")
    return dependencies.plugin_host.bridge_asset_path(plugin_id, relative_path)


class BotExtensionService:
    """Managed Bot Bridge extensions and their generated asset boundary."""

    def __init__(self, dependencies: BotExtensionDependencies) -> None:
        self._dependencies = dependencies

    def capabilities(self) -> dict[str, Any]:
        return capabilities(self._dependencies)

    async def apply(
        self, stage: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await apply(self._dependencies, stage, payload)

    def asset_path(self, plugin_id: str, relative_path: str) -> Path:
        return asset_path(self._dependencies, plugin_id, relative_path)

    def bridge_card_path(self, name: str) -> Path:
        return bridge_card_path(self._dependencies, name)

    def materialize_cards(
        self, outputs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return _materialize_cards(self._dependencies, outputs)
