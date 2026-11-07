"""Shared contracts and compensation state for game lifecycle transactions."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from src.rules.rule_system import RuleSystem
from src.engine.player_control import is_ai_controlled

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
    def bind_lorebook(self, binding: dict[str, Any]) -> Any: ...
    def delete_binding(self, binding_id: str) -> bool: ...


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
    # 创建提交后唤醒纯 AI 对局；生命周期层只负责触发，回合推进仍由 turns
    # 的唯一 progression 边界实现。None 表示该运行时没有配置这条能力。
    resume_after_creation: Callable[[Any], Awaitable[dict[str, Any]]] | None = None
    # Optional pool used when a newly-created custom world should keep the
    # ruleset's varied default covers for future saves.
    list_default_scene_images: Callable[[str, str], list[dict[str, str]]] | None = None
    # issue #700：开局勾选 catalog 模块声明的资料册时按需物化。
    # None 表示组合根未接线（未启用插件宿主等），此时 module ref 按原样拒绝。
    materialize_module_book: Callable[[str, str, str], bool] | None = None
    get_game_defaults: Callable[[], dict[str, Any]] = lambda: {}


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
    created_binding_ids: list[str] = field(default_factory=list)

    def track_binding(self, binding_id: str) -> None:
        if binding_id and binding_id not in self.created_binding_ids:
            self.created_binding_ids.append(binding_id)

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
        lorebook = getattr(self.dependencies, "lorebook", None)
        delete_binding = getattr(lorebook, "delete_binding", None)
        if callable(delete_binding):
            for binding_id in reversed(self.created_binding_ids):
                try:
                    delete_binding(binding_id)
                except Exception:
                    logger.warning("清理创建失败的 Lorebook binding 失败: %s", binding_id, exc_info=True)
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


async def resume_created_ai_table(
    dependencies: GameLifecycleDependencies, instance: Any,
) -> dict[str, Any] | None:
    """Start progression for a committed table with only AI-controlled seats.

    Creation normally leaves the first round waiting for player input.  That is
    correct for a human table, but an all-AI table has no caller that can open
    the gate later.  Keep this trigger at the lifecycle boundary and delegate
    the actual fill/advance work to the same turn service used by control
    changes, so there is still only one authoritative progression path.
    """

    resume = dependencies.resume_after_creation
    if resume is None or instance.active_human_players:
        return None
    if not any(
        is_ai_controlled(instance, uid)
        for uid in instance.active_alive_players
    ):
        return None
    try:
        return await resume(instance)
    except Exception:
        # The save is already committed.  A model/provider failure must not
        # turn a successful creation into a false failure or roll it back.
        logger.exception("纯 AI 对局创建后的自动推进失败: %s", instance.game_key)
        return None
