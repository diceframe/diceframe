"""Bot 侧平台会话映射与消息去重持久化。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any


class QQSessionStore:
    def __init__(self, path: Path, recent_limit: int = 500) -> None:
        self.path = path
        self.recent_limit = recent_limit
        self._lock = asyncio.Lock()
        self.groups: dict[str, dict[str, Any]] = {}
        self.players: dict[str, dict[str, str]] = {}
        self.recent_message_ids: list[str] = []
        self.recent_command_signatures: dict[str, float] = {}

    async def load(self) -> None:
        if not self.path.exists():
            return
        async with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                return
            self.groups = data.get("groups", {}) if isinstance(data.get("groups"), dict) else {}
            self.players = data.get("players", {}) if isinstance(data.get("players"), dict) else {}
            recent = data.get("recent_message_ids", [])
            self.recent_message_ids = [str(item) for item in recent[-self.recent_limit:]] if isinstance(recent, list) else []
            recent_commands = data.get("recent_command_signatures", {})
            if isinstance(recent_commands, dict):
                self.recent_command_signatures = {
                    str(key): float(value)
                    for key, value in recent_commands.items()
                    if isinstance(value, (int, float))
                }

    async def bind_group(self, group_id: str, game_key: str, gm_platform_id: str, gm_uid: str,
                         roster: list[dict[str, str]] | None = None) -> None:
        async with self._lock:
            self.groups[str(group_id)] = {
                "game_key": game_key,
                "gm_platform_id": str(gm_platform_id),
                "gm_uid": gm_uid,
                "roster": roster or [],
            }
            self.players[self.player_key(group_id, gm_platform_id)] = {
                "game_key": game_key,
                "user_id": gm_uid,
            }
            self._persist_locked()

    def group(self, group_id: str) -> dict[str, Any] | None:
        return self.groups.get(str(group_id))

    async def update_group_roster(self, group_id: str, roster: list[dict[str, Any]]) -> None:
        async with self._lock:
            group = self.groups.get(str(group_id))
            if not group:
                return
            group["roster"] = roster
            self._persist_locked()

    def player(self, group_id: str, platform_user_id: str) -> dict[str, str] | None:
        return self.players.get(self.player_key(group_id, platform_user_id))

    def bindings_for_platform(self, platform_user_id: str) -> list[tuple[str, dict[str, str]]]:
        suffix = f":{platform_user_id}"
        return [(key[:-len(suffix)], value) for key, value in self.players.items() if key.endswith(suffix)]

    async def bind_player(self, group_id: str, platform_user_id: str, user_id: str) -> bool:
        async with self._lock:
            group = self.groups.get(str(group_id))
            if not group:
                return False
            game_key = str(group["game_key"])
            for key, mapping in self.players.items():
                if key != self.player_key(group_id, platform_user_id) and mapping.get("game_key") == game_key and mapping.get("user_id") == user_id:
                    return False
            self.players[self.player_key(group_id, platform_user_id)] = {
                "game_key": game_key,
                "user_id": user_id,
            }
            self._persist_locked()
            return True

    async def remember_message(self, message_id: str) -> bool:
        message_id = str(message_id or "").strip()
        if not message_id:
            return True
        async with self._lock:
            if message_id in self.recent_message_ids:
                return False
            self.recent_message_ids.append(message_id)
            self.recent_message_ids = self.recent_message_ids[-self.recent_limit:]
            self._persist_locked()
            return True

    async def remember_command(self, signature: str, window_sec: float) -> bool:
        """Return False when the same semantic command was handled recently."""
        signature = str(signature or "").strip()
        if not signature or window_sec <= 0:
            return True
        now = time.time()
        cutoff = now - window_sec
        async with self._lock:
            self.recent_command_signatures = {
                key: ts for key, ts in self.recent_command_signatures.items() if ts >= cutoff
            }
            if signature in self.recent_command_signatures:
                return False
            self.recent_command_signatures[signature] = now
            if len(self.recent_command_signatures) > self.recent_limit:
                newest = sorted(self.recent_command_signatures.items(), key=lambda item: item[1])[-self.recent_limit:]
                self.recent_command_signatures = dict(newest)
            self._persist_locked()
            return True

    @staticmethod
    def player_key(group_id: str, platform_user_id: str) -> str:
        return f"{group_id}:{platform_user_id}"

    def _persist_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        data: dict[str, Any] = {
            "groups": self.groups,
            "players": self.players,
            "recent_message_ids": self.recent_message_ids,
            "recent_command_signatures": self.recent_command_signatures,
        }
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
