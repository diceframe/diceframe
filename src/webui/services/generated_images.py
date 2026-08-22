"""Generated-image application services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.imagegen import ImageGenerationRequest, game_image_owner_id

if TYPE_CHECKING:
    from src.webui.api import WebAPI


async def generate_image(
    api: "WebAPI",
    *,
    prompt: str,
    purpose: str,
    owner_type: str,
    owner_id: str,
    aspect_ratio: str = "",
    style: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = await api._imagegen.generate(ImageGenerationRequest(
        prompt=prompt,
        purpose=purpose,
        owner_type=owner_type,
        owner_id=owner_id,
        aspect_ratio=aspect_ratio,
        style=style,
        context=dict(context or {}),
    ))
    return {
        "ok": True,
        **result.public_dict(),
        "reference": {"kind": "generated", "asset_id": result.asset_id},
    }


def list_game_images(
    api: "WebAPI",
    game_key: str,
    user_id: str,
    *,
    purpose: str = "",
) -> list[dict[str, Any]]:
    inst = api._reg.get(api._parse_key(game_key))
    if inst is None:
        raise KeyError("游戏不存在")
    if not user_id or (user_id != inst.gm_uid and user_id not in inst.players):
        raise PermissionError("当前身份不属于本局游戏")
    records = api._imagegen.assets.list_records(
        owner_type="game",
        owner_id=game_image_owner_id(inst.game_key),
        purpose=purpose,
    )
    for record in records:
        context = record.get("context") if isinstance(record.get("context"), dict) else {}
        if context.get("round") is not None:
            record["round"] = int(context.get("round") or 0)
    return records


async def use_as_map_background(
    api: "WebAPI",
    game_key: str,
    user_id: str,
    asset_id: str,
) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if inst is None:
        return {"ok": False, "error": "游戏不存在"}
    if not user_id or user_id != inst.gm_uid:
        return {"ok": False, "error": "仅 GM 可修改地图背景"}
    if api.generated_image_file(asset_id) is None:
        return {"ok": False, "error": "生成图片不存在"}
    return await api.update_map_background(
        game_key,
        {"kind": "generated", "asset_id": asset_id},
    )
