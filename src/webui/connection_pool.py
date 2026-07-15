"""SSE 多连接管理池 —— game_key → user_id → set[StreamResponse]。"""

from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import web

logger = logging.getLogger("trpg")


class ConnectionPool:
    """按 game_key 和 user_id 管理 SSE 连接。"""

    def __init__(self):
        # {game_key: {user_id: set[StreamResponse]}}
        self._conns: dict[str, dict[str, set[web.StreamResponse]]] = {}

    def add(self, game_key: str, user_id: str, resp: web.StreamResponse) -> None:
        self._conns.setdefault(game_key, {}).setdefault(user_id, set()).add(resp)

    def remove(self, game_key: str, user_id: str, resp: web.StreamResponse) -> None:
        game = self._conns.get(game_key, {})
        users = game.get(user_id, set())
        users.discard(resp)
        if not users:
            game.pop(user_id, None)
        if not game:
            self._conns.pop(game_key, None)

    async def broadcast(self, game_key: str, data: dict,
                        exclude_uid: str = "", private_uid: str = "") -> None:
        """广播消息。private_uid 非空则只推给该用户。"""
        game = self._conns.get(game_key, {})
        if not game:
            return
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        for uid, conns in list(game.items()):
            if private_uid and uid != private_uid:
                continue
            if exclude_uid and uid == exclude_uid:
                continue
            for resp in list(conns):
                try:
                    await resp.write(b"data: " + payload + b"\n\n")
                except ConnectionResetError:
                    conns.discard(resp)
