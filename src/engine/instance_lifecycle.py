"""Instance lifecycle mutation details for :class:`GameInstance`.

本模块只放 GameInstance 生命周期状态的**持锁 mutation detail**：

``activate_locked`` / ``pause_locked`` / ``resume_locked`` / ``end_locked`` /
``reset_locked``。

边界：

- 本模块**不获取任何锁**；调用方（GameInstance 的 public 方法）负责持有
  ``_lock``。
- ``reset_locked`` 是从开工基线 ``main`` 的 ``reset()`` 函数体**机械迁移**的：
  保留/清空/轮换行为以基线代码为唯一真值，禁止按字段清单重新实现。
  未被基线 reset() 触碰的字段（play_mode / scene_image / death_save_outcomes
  等"隐式保留"字段）在本 PR 中保持原样；疑似 bug 记录 follow-up，不顺手修。
  例外：``adventure_progress`` 已有意改为随 ``world_state`` 一起清空（两者同属
  一轮 run，见 reset_locked 内注释）。
- 不 runtime import ``src.engine.game_instance``（仅 ``TYPE_CHECKING`` 类型引用），
  依赖方向保持 ``game_instance → instance_lifecycle``。
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING

from src.engine import progression
from src.engine.game_state import GameState
from src.engine.language import normalize_language
from src.engine.world_state import fresh_world_state

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance

logger = logging.getLogger("trpg")


def activate_locked(instance: GameInstance) -> None:
    """``activate`` 的持锁实现（调用方必须已持有 ``_lock``）。"""
    from src.engine.modules import session_stats

    session_stats.require_writable(instance)
    instance.state = GameState.ACTIVE_ACTION
    session_stats.mark_started(instance)
    session_stats.touch(instance)
    logger.info("游戏激活 - game_key=%s", instance.game_key)


def pause_locked(instance: GameInstance) -> None:
    """``pause`` 的持锁实现（调用方必须已持有 ``_lock``）。"""
    instance.state = GameState.PAUSED


def resume_locked(instance: GameInstance) -> None:
    """``resume`` 的持锁实现（调用方必须已持有 ``_lock``）。"""
    instance.state = GameState.ACTIVE_ACTION


def end_locked(instance: GameInstance) -> None:
    """``end`` 的持锁实现（调用方必须已持有 ``_lock``）。"""
    instance.state = GameState.ENDED


def reset_locked(instance: GameInstance, *, keep_seed: bool = True) -> None:
    """``reset`` 的持锁实现（调用方必须已持有 ``_lock``）。

    **机械迁移自基线 reset() 函数体**：保留 / 清空 / 轮换的语句顺序与字段
    集合与基线逐句一致。reset 的真实契约由
    ``tests/test_game_instance_reset_characterization.py`` 冻结。
    """
    from src.engine.modules import (
        adventure_runtime_state, checks, legacy_combat, narrative_notes, private_channels, round_safety,
        ruleset_runtime, seat_activity, session_stats, table_settings,
    )
    from src.engine.modules import combat_extension_state

    session_stats.require_writable(instance)
    checks.require_writable(instance)
    round_safety.require_writable(instance)
    legacy_combat.require_writable(instance)
    ruleset_runtime.require_writable(instance)
    adventure_runtime_state.require_writable(instance)
    narrative_notes.require_writable(instance)
    seat_activity.require_writable(instance)
    saved_seed = table_settings.seed_code(instance) if keep_seed else ""
    saved_world_id = instance.world_id
    saved_world_name = instance.world_name
    saved_group_name = instance.group_name
    saved_solo = table_settings.solo_mode(instance)
    saved_narrative_perspective = table_settings.narrative_perspective(instance)
    saved_gm_style_override = copy.deepcopy(table_settings.gm_style_override(instance))
    saved_language = normalize_language(instance.language)
    saved_ruleset_runtime = copy.deepcopy(ruleset_runtime.binding(instance))
    saved_adventure_binding = copy.deepcopy(instance.adventure_binding)
    instance.rotate_run_identity()
    instance.players.clear()
    # 例外（同 adventure_progress）：席位"已行动"标记属于这一轮 run，随名册与日志一起清空。
    seat_activity.reset(instance)
    instance.npcs.clear()
    progression.reset(instance)
    instance.action_queue.clear()
    instance.pending_actions.clear()
    instance.ready_players.clear()
    legacy_combat.reset(instance)
    narrative_notes.replace_scene(instance, "")
    narrative_notes.replace_game_time(instance, "")
    instance.log.clear()
    narrative_notes.summary(instance).clear()
    narrative_notes.key_facts(instance).clear()
    # 世界真相属于这一轮 run：重置与重开都从空世界重新开始。
    instance.world_state = fresh_world_state()
    # Adventure v2 进度与世界状态同属这一轮 run（完成节点的后果写在 world_state
    # 里）：只清世界却保留进度会出现"节点已完成、世界没变"的矛盾。原地 reset
    # 不能解析冒险包，因此清空为"未初始化"（节点推进 fail closed）；生产的
    # reset/restart 走新 run 候选并由 initialize_adventure_run 重新初始化。
    adventure_runtime_state.replace_progress(instance, {})
    session_stats.reset(instance)
    instance.puzzle_manager = None
    instance.plot_tracker = None
    instance.pending_combat_results.clear()
    combat_extension_state.replace_current(instance, {})
    combat_extension_state.round_snapshots(instance).clear()
    instance.lorebook_timed_state.clear()
    instance.health_events.clear()
    instance.health_status.clear()
    instance.quick_actions.clear()
    narrative_notes.confirmed_items(instance).clear()
    private_channels.private_log(instance).clear()
    private_channels.table_talk(instance).clear()
    checks.clear_round(instance)
    round_safety.clear_snapshots(instance)
    instance.last_state_update = None
    instance.last_token_budget_bump = None
    instance.gm_directives.clear()
    ruleset_runtime.reset(instance, saved_ruleset_runtime)
    instance.adventure_binding = saved_adventure_binding
    instance.state = GameState.CREATED
    instance.world_id = saved_world_id
    instance.world_name = saved_world_name
    instance.group_name = saved_group_name
    table_settings.replace_solo_mode(instance, saved_solo)
    table_settings.replace_narrative_perspective(instance, saved_narrative_perspective)
    table_settings.replace_gm_style_override(instance, saved_gm_style_override)
    instance.language = saved_language
    table_settings.replace_seed_code(instance, saved_seed)
    logger.info("游戏已重置 (seed=%s) - game_key=%s", table_settings.seed_code(instance), instance.game_key)
