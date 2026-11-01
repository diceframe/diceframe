"""全局游戏实例注册表与持久化门面。

``GameInstance`` 是单局状态的 aggregate root；本模块只负责按
``game_key`` 管理实例，并把存档操作委托给 ``persistence``。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance, GameState


class GameRegistry:
    """按 ``game_key`` 管理 ``GameInstance`` 并提供持久化门面。"""

    _KEY_SEPARATOR = "#"

    def __init__(self, save_dir: Path):
        self._instances: dict[tuple, GameInstance] = {}
        self._save_locks: dict[tuple, asyncio.Lock] = {}
        self.save_dir = Path(save_dir)

    def get(self, game_key: tuple) -> GameInstance | None:
        return self._instances.get(game_key)

    def get_or_create(self, game_key: tuple) -> GameInstance:
        if game_key not in self._instances:
            from src.engine.game_instance import GameInstance

            self._instances[game_key] = GameInstance(game_key=game_key)
        return self._instances[game_key]

    def register(self, instance: GameInstance) -> None:
        self._instances[instance.game_key] = instance

    def remove(self, game_key: tuple) -> None:
        self._instances.pop(game_key, None)

    def list_active(self) -> list[GameInstance]:
        from src.engine.game_instance import GameState

        return [i for i in self._instances.values() if i.state not in (GameState.ENDED,)]

    def list_all(self) -> list[GameInstance]:
        return list(self._instances.values())

    @staticmethod
    def make_game_key(platform: str, target_id: str, account_id: str) -> tuple:
        return (platform, target_id, account_id)

    @staticmethod
    def _persistence() -> Any:
        # Lazy import avoids persistence -> GameRegistry initialization cycles.
        from src.engine import persistence

        return persistence

    def _save_path(self, game_key: tuple) -> Path:
        return self._persistence()._save_path(self, game_key)

    def save_package_state_path(self, game_key: tuple) -> Path:
        return self._persistence()._save_path(self, game_key)

    async def save(self, instance: GameInstance) -> None:
        lock = self._save_locks.setdefault(instance.game_key, asyncio.Lock())
        async with lock:
            current = self.get(instance.game_key)
            if current is not None and current is not instance:
                raise RuntimeError("stale game instance cannot overwrite the current run")
            await self._persistence().save(self, instance)

    async def replace_current(self, expected: GameInstance, candidate: GameInstance) -> None:
        if candidate.game_key != expected.game_key:
            raise ValueError("replacement game key mismatch")
        lock = self._save_locks.setdefault(expected.game_key, asyncio.Lock())
        async with lock:
            if self.get(expected.game_key) is not expected:
                raise RuntimeError("game run changed while replacement was being prepared")
            await self._persistence().save(self, candidate)
            self.register(candidate)

    async def load(self, game_key: tuple) -> GameInstance | None:
        return await self._persistence().load(self, game_key)

    async def recover_all(self) -> list[GameInstance]:
        return await self._persistence().recover_all(self)

    async def import_save_zip(
        self,
        payload: bytes,
        *,
        platform: str = "web",
        account_id: str = "web_bot",
        scene_image_importer: Callable[[bytes], dict[str, Any]] | None = None,
        map_background_importer: Callable[[bytes], dict[str, Any]] | None = None,
    ) -> dict:
        return await self._persistence().import_save_zip(
            self,
            payload,
            platform=platform,
            account_id=account_id,
            scene_image_importer=scene_image_importer,
            map_background_importer=map_background_importer,
        )

    async def save_all_active(self) -> None:
        await self._persistence().save_all_active(self)
