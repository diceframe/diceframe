"""Bot 渠道接入：游戏绑定凭证与代表玩家校验。"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.webui.api import WebAPI


async def get_bind_token(api: "WebAPI", game_key: str, rotate: bool = False) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    if rotate or not getattr(inst, "bot_bind_token", ""):
        inst.bot_bind_token = secrets.token_urlsafe(18)
        await api._reg.save(inst)
    return {"ok": True, "bind_token": inst.bot_bind_token}


async def verify_bind_game(api: "WebAPI", game_key: str, bind_token: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    expected = str(getattr(inst, "bot_bind_token", "") or "")
    if not expected or not secrets.compare_digest(expected, str(bind_token or "")):
        return {"ok": False, "error": "绑定凭证无效或已使用，请由 GM 在网页重新生成一次性绑定命令"}
    result = {
        "ok": True,
        "game_key": game_key,
        "gm_uid": inst.gm_uid,
        "world_name": inst.world_name,
        "player_access_open": bool(getattr(inst, "player_access_open", True)),
        "players": [
            {
                "user_id": user_id,
                "character_name": str(player.get("character_name") or user_id),
            }
            for user_id, player in inst.players.items()
        ],
    }
    inst.bot_bind_token = ""
    await api._reg.save(inst)
    return result


def actor_allowed(api: "WebAPI", game_key: str, user_id: str) -> bool:
    inst = api._reg.get(api._parse_key(game_key))
    return bool(inst and user_id and user_id in inst.players)
