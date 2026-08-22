"""WebUI 图像生成域：插件测试、本局生成图画廊、场景图转地图背景。"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from src.imagegen import ImageGenError

if TYPE_CHECKING:
    from src.webui.api import WebAPI


async def test_generation(api: "WebAPI", prompt: str) -> dict[str, Any]:
    """插件页测试生图：走同一门面与资产管线，返回可预览的 asset_id。"""
    generator = getattr(api, "_imagegen", None)
    if generator is None:
        return {"ok": False, "error": "图像生成插件运行时未初始化"}
    if not generator.available():
        return {"ok": False, "error": "没有正在运行的图像生成插件，请先在插件页启用"}
    try:
        result = await generator.generate(prompt)
    except ImageGenError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "asset_id": result["asset_id"],
        "revised_prompt": result.get("revised_prompt", ""),
    }


def collect_game_images(api: "WebAPI", game_key: str, user_id: str) -> list[dict[str, Any]]:
    """汇总本局叙事流中全部场景图（倒序：最新在前）。"""
    inst = api._reg.get(api._parse_key(game_key))
    if inst is None:
        raise KeyError("游戏不存在")
    if not user_id or (user_id != inst.gm_uid and user_id not in inst.players):
        raise PermissionError("当前身份不属于本局游戏")
    images: list[dict[str, Any]] = []
    for entry in inst.log:
        record = entry.get("scene_image")
        if not isinstance(record, dict):
            continue
        reference = record.get("reference") if isinstance(record.get("reference"), dict) else {}
        asset_id = str(reference.get("asset_id") or "")
        if not asset_id:
            continue
        images.append({
            "round": int(entry.get("round") or 0),
            "asset_id": asset_id,
            "prompt": str(record.get("prompt") or ""),
            "revised_prompt": str(record.get("revised_prompt") or ""),
            "status": str(record.get("status") or "ready"),
        })
    images.sort(key=lambda item: -int(item["round"]))
    return images


async def set_map_background_from_scene(
    api: "WebAPI",
    game_key: str,
    user_id: str,
    asset_id: str,
) -> dict[str, Any]:
    """把已生成的场景图复制为地图背景（两个资产库同构、内容寻址不重复）。"""
    inst = api._reg.get(api._parse_key(game_key))
    if inst is None:
        return {"ok": False, "error": "游戏不存在"}
    if not user_id or user_id != inst.gm_uid:
        return {"ok": False, "error": "仅 GM 可修改地图背景"}
    source = api.scene_image_file(str(asset_id or ""))
    if source is None:
        return {"ok": False, "error": "场景图不存在"}
    upload = api.save_map_background_upload(
        base64.b64encode(source.read_bytes()).decode("ascii"),
        f"scene-{asset_id}.webp",
    )
    if not upload.get("ok"):
        return {"ok": False, "error": str(upload.get("error") or "地图背景保存失败")}
    return await api.update_map_background(game_key, upload.get("map_background"))
