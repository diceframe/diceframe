"""Shared contracts and compensation state for game lifecycle transactions."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from src.rules.rule_system import RuleSystem

logger = logging.getLogger("trpg")
GameKey = tuple[str, ...]


class LifecycleRegistry(Protocol):
    def get(self, game_key: GameKey) -> Any | None: ...
    def list_all(self) -> list[Any]: ...
    def remove(self, game_key: GameKey) -> None: ...
    def save_package_state_path(self, game_key: GameKey) -> Path: ...
    async def save(self, instance: Any) -> None: ...


class LifecycleHandler(Protocol):
    async def create_game(self, game_key: GameKey, **kwargs: Any) -> Any: ...
    async def start_game(self, instance: Any) -> str: ...
    async def reset_game(self, instance: Any) -> Any: ...
    async def restart_game(self, instance: Any) -> Any: ...


class LifecycleLorebook(Protocol):
    def get_world(self, world_id: str) -> dict[str, Any] | None: ...
    def create_world(self, world_id: str, name: str, **kwargs: Any) -> Any: ...
    def list_entries(self, world_id: str) -> list[dict[str, Any]]: ...
    def get_entry(self, entry_id: str) -> dict[str, Any] | None: ...
    def add_entry(self, entry: dict[str, Any]) -> Any: ...


class LifecycleRulesets(Protocol):
    def resolve(self, template: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class GameLifecycleDependencies:
    registry: LifecycleRegistry
    handler: LifecycleHandler | None
    rulesets: LifecycleRulesets
    lorebook: LifecycleLorebook | None
    worlds_dir: Path | None
    rules_dir: Path
    parse_game_key: Callable[[str], GameKey]
    llm_configuration_error: Callable[[str], dict[str, Any] | None]
    load_rule_by_id: Callable[[str, str], RuleSystem | None]
    resolve_adventure_binding: Callable[..., dict[str, Any]]
    resolve_default_scene_image: Callable[[str, str], dict[str, str]]
    materialize_scene_image: Callable[[Any], dict[str, str]]
    validate_map_background: Callable[[Any], dict[str, str]]
    create_player: Callable[..., Awaitable[dict[str, Any]]]
    cleanup_orphan_game_templates: Callable[[str], int]
    refresh_lorebook_index: Callable[[str], None]
    project_rule_id: Callable[[Any], str]
    clean_public_narration: Callable[[str], str]
    # FIX-04 §6.5：v2 冒险的创建事务步骤（进度初始化 + 世界种子原子物化）。
    # 在创建事务内调用；v1 / 未绑定冒险时是 no-op。
    initialize_adventure_run: Callable[[Any], dict[str, Any]] | None = None


class CreationPhase(str, Enum):
    PREFLIGHTED = "preflighted"
    WORLD_MATERIALIZED = "world_materialized"
    INSTANCE_REGISTERED = "instance_registered"
    INSTANCE_CONFIGURED = "instance_configured"
    PLAYERS_CREATED = "players_created"
    OPENING_STARTED = "opening_started"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


_NEXT_CREATION_PHASES = {
    CreationPhase.PREFLIGHTED: {
        CreationPhase.WORLD_MATERIALIZED,
        CreationPhase.INSTANCE_REGISTERED,
    },
    CreationPhase.WORLD_MATERIALIZED: {CreationPhase.INSTANCE_REGISTERED},
    CreationPhase.INSTANCE_REGISTERED: {CreationPhase.INSTANCE_CONFIGURED},
    CreationPhase.INSTANCE_CONFIGURED: {CreationPhase.PLAYERS_CREATED},
    CreationPhase.PLAYERS_CREATED: {CreationPhase.OPENING_STARTED},
}


@dataclass
class CreationTransaction:
    """Track creation phases and own the single idempotent compensation path."""

    dependencies: GameLifecycleDependencies
    game_key: GameKey
    world_id: str
    phase: CreationPhase = CreationPhase.PREFLIGHTED

    def advance(self, phase: CreationPhase) -> None:
        if phase not in _NEXT_CREATION_PHASES.get(self.phase, set()):
            raise RuntimeError(
                f"invalid creation phase transition: {self.phase.value} -> {phase.value}"
            )
        self.phase = phase

    def rollback(self) -> None:
        if self.phase in {CreationPhase.COMMITTED, CreationPhase.ROLLED_BACK}:
            return
        save_dir = self.dependencies.registry.save_package_state_path(
            self.game_key
        ).parent
        try:
            if save_dir.exists():
                shutil.rmtree(save_dir)
        except Exception:
            logger.warning("清理创建失败的存档目录失败: %s", save_dir, exc_info=True)
        finally:
            self.dependencies.registry.remove(self.game_key)
        if self.world_id:
            try:
                self.dependencies.cleanup_orphan_game_templates(self.world_id)
            except Exception:
                logger.warning(
                    "清理创建失败的临时世界模板失败: %s",
                    self.world_id,
                    exc_info=True,
                )
        self.phase = CreationPhase.ROLLED_BACK

    async def commit(self, instance: Any) -> None:
        if self.phase is not CreationPhase.OPENING_STARTED:
            raise RuntimeError(f"cannot commit creation from {self.phase.value}")
        await self.dependencies.registry.save(instance)
        self.phase = CreationPhase.COMMITTED


async def _start_created_game(
    dependencies: GameLifecycleDependencies, instance: Any, runtime: Any | None
) -> str:
    """Start every save through DiceFrame's single narrative game loop."""

    del runtime
    return await dependencies.handler.start_game(instance)
