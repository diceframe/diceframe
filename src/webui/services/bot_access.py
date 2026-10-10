"""Bot 渠道接入：游戏绑定凭证与代表玩家校验。"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from src.engine.modules import room_access


GameKey = tuple[str, ...]


class BotAccessRegistry(Protocol):
    def get(self, game_key: GameKey) -> Any | None: ...
    async def save(self, instance: Any) -> None: ...


@dataclass(frozen=True)
class BotAccessDependencies:
    registry: BotAccessRegistry
    parse_game_key: Callable[[str], GameKey]


async def get_bind_token(dependencies: BotAccessDependencies, game_key: str, rotate: bool = False) -> dict[str, Any]:
    inst = dependencies.registry.get(dependencies.parse_game_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    if rotate or not room_access.bot_bind_token(inst):
        inst.set_bot_bind_token(secrets.token_urlsafe(18))
        await dependencies.registry.save(inst)
    return {"ok": True, "bind_token": room_access.bot_bind_token(inst)}


async def verify_bind_game(dependencies: BotAccessDependencies, game_key: str, bind_token: str) -> dict[str, Any]:
    inst = dependencies.registry.get(dependencies.parse_game_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    expected = str(room_access.bot_bind_token(inst) or "")
    if not expected or not secrets.compare_digest(expected, str(bind_token or "")):
        return {"ok": False, "error": "绑定凭证无效或已使用，请由 GM 在网页重新生成一次性绑定命令"}
    result = {
        "ok": True,
        "game_key": game_key,
        "gm_uid": inst.gm_uid,
        "world_name": inst.world_name,
        "language": str(getattr(inst, "language", "") or "zh-CN"),
        "player_access_open": bool(room_access.player_access_open(inst)),
        "players": [
            {
                "user_id": user_id,
                "character_name": str(player.get("character_name") or user_id),
            }
            for user_id, player in inst.players.items()
        ],
    }
    inst.set_bot_bind_token("")
    await dependencies.registry.save(inst)
    return result


def actor_allowed(dependencies: BotAccessDependencies, game_key: str, user_id: str) -> bool:
    inst = dependencies.registry.get(dependencies.parse_game_key(game_key))
    return bool(inst and user_id and user_id in inst.players)


class BotAccessService:
    """Bot binding and actor checks against one explicit registry boundary."""

    def __init__(self, dependencies: BotAccessDependencies) -> None:
        self._dependencies = dependencies

    async def get_bind_token(
        self, game_key: str, rotate: bool = False,
    ) -> dict[str, Any]:
        return await get_bind_token(self._dependencies, game_key, rotate)

    async def verify_bind_game(
        self, game_key: str, bind_token: str,
    ) -> dict[str, Any]:
        return await verify_bind_game(self._dependencies, game_key, bind_token)

    def actor_allowed(self, game_key: str, user_id: str) -> bool:
        return actor_allowed(self._dependencies, game_key, user_id)
