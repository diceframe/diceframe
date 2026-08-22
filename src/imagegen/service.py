"""场景图生成门面：把 provider 插件的生图结果接入既有场景图资产管线。

只做三件事：找到声明 image-generation capability 的运行中插件、调用其
generate 方法、读取插件数据目录中的图像并归一化为场景头图资产（内容寻址
WebP）。不存在 provider 插件时零开销降级。
"""

from __future__ import annotations

import base64
import binascii
import logging
import types
from pathlib import Path
from typing import Any

logger = logging.getLogger("trpg")

IMAGEGEN_CAPABILITY = "image-generation"
# 宿主侧等待上限：略高于插件默认 120s，给归一化与传输留余量。
DEFAULT_IMAGEGEN_TIMEOUT = 150.0


def _scene_image_assets():
    # 延迟导入：scene_images 属于 webui 服务层，其包初始化会连带拉起
    # commands（round_processor 又引用本模块），顶层导入会成环。
    from src.webui.services import scene_images

    return scene_images


class ImageGenError(RuntimeError):
    """生图失败（插件缺失、调用出错或图像无效）。"""


class SceneImageGenerator:
    """包装 PluginHost 的生图调用；assets_dir 与场景头图存储同源。"""

    def __init__(self, plugin_host: Any, assets_dir: Path) -> None:
        self._host = plugin_host
        self._assets_dir = Path(assets_dir)

    def available(self) -> bool:
        return self._find_plugin() is not None

    def plugin_id(self) -> str | None:
        return self._find_plugin()

    async def generate(self, prompt: str, *, timeout: float = DEFAULT_IMAGEGEN_TIMEOUT) -> dict[str, Any]:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ImageGenError("画面描述为空")
        plugin_id = self._find_plugin()
        if not plugin_id:
            raise ImageGenError("没有正在运行的图像生成插件")
        try:
            result = await self._host.call_provider(
                plugin_id, IMAGEGEN_CAPABILITY, "generate",
                {"prompt": prompt}, timeout=timeout,
            )
        except Exception as exc:
            raise ImageGenError(f"图像生成插件调用失败：{exc}") from exc
        if not result.get("ok"):
            raise ImageGenError(str(result.get("error") or "图像生成失败"))
        raw = self._read_result_image(plugin_id, result)
        if not raw:
            raise ImageGenError("图像生成插件返回了空图像")
        scene_images = _scene_image_assets()
        try:
            payload = scene_images._normalized_scene_image(raw)
        except ValueError as exc:
            raise ImageGenError(str(exc)) from exc
        reference = scene_images._store_payload(
            types.SimpleNamespace(_scene_images_dir=self._assets_dir), payload,
        )
        return {
            "reference": reference,
            "asset_id": reference["asset_id"],
            "prompt": prompt,
            "revised_prompt": str(result.get("revised_prompt") or ""),
        }

    def _read_result_image(self, plugin_id: str, result: dict[str, Any]) -> bytes:
        relative_path = str(result.get("image_path") or "").strip()
        if relative_path:
            try:
                source = self._host.provider_asset_path(plugin_id, relative_path)
                return source.read_bytes()
            except (KeyError, ValueError, OSError) as exc:
                raise ImageGenError(f"图像生成插件返回了无效的图片文件：{exc}") from exc
        try:
            return base64.b64decode(str(result.get("image_base64") or ""), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ImageGenError("图像生成插件返回了无效的图像数据") from exc

    def _find_plugin(self) -> str | None:
        if self._host is None:
            return None
        try:
            return self._host.find_provider(IMAGEGEN_CAPABILITY)
        except Exception:
            logger.debug("查找图像生成插件失败", exc_info=True)
            return None
