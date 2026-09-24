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
- 不 runtime import ``src.engine.game_instance``（仅 ``TYPE_CHECKING`` 类型引用），
  依赖方向保持 ``game_instance → instance_lifecycle``。
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
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
    instance.state = GameState.ACTIVE_ACTION
    if not instance.started_at:
        instance.started_at = datetime.now(timezone.utc).isoformat()
    instance.last_activity = datetime.now(timezone.utc).isoformat()
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
    saved_seed = instance.seed_code if keep_seed else ""
    saved_world_id = instance.world_id
    saved_world_name = instance.world_name
    saved_group_name = instance.group_name
    saved_solo = instance.solo_mode
    saved_narrative_perspective = instance.narrative_perspective
    saved_gm_style_override = copy.deepcopy(instance.gm_style_override)
    saved_language = normalize_language(instance.language)
    saved_ruleset_runtime = copy.deepcopy(instance.ruleset_runtime)
    saved_adventure_binding = copy.deepcopy(instance.adventure_binding)
    instance.rotate_run_identity()
    instance.players.clear()
    instance.npcs.clear()
    progression.reset(instance)
    instance.action_queue.clear()
    instance.pending_actions.clear()
    instance.ready_players.clear()
    instance.combat_active = False
    instance.combat_enemies.clear()
    instance.combat_state = "none"
    instance.initiative_order.clear()
    instance.initiative_current = 0
    instance.scene = ""
    instance.game_time = ""
    instance.log.clear()
    instance.summary.clear()
    instance.key_facts.clear()
    # 世界真相属于这一轮 run：重置与重开都从空世界重新开始。
    instance.world_state = fresh_world_state()
    instance.total_llm_calls = 0
    instance.total_tokens = 0
    instance.started_at = ""
    instance.last_activity = ""
    instance.puzzle_manager = None
    instance.plot_tracker = None
    instance.pending_combat_results.clear()
    instance.combat_extension = {}
    instance.combat_extension_round_snapshots.clear()
    instance.lorebook_timed_state.clear()
    instance.health_events.clear()
    instance.health_status.clear()
    instance.quick_actions.clear()
    instance.confirmed_items.clear()
    instance.private_log.clear()
    instance.table_talk.clear()
    instance.last_check = None
    instance.last_checks.clear()
    instance.round_checks_prepared = False
    instance.round_start_snapshot.clear()
    instance.round_entity_snapshot.clear()
    instance.last_state_update = None
    instance.last_token_budget_bump = None
    instance.gm_directives.clear()
    instance.ruleset_runtime = saved_ruleset_runtime
    instance.ruleset_state = (
        {"state_schema_version": int(saved_ruleset_runtime.get("state_schema_version", 1) or 1)}
        if saved_ruleset_runtime else {}
    )
    instance.adventure_binding = saved_adventure_binding
    instance.event_ledger.clear()
    instance.state = GameState.CREATED
    instance.world_id = saved_world_id
    instance.world_name = saved_world_name
    instance.group_name = saved_group_name
    instance.solo_mode = saved_solo
    instance.narrative_perspective = saved_narrative_perspective
    instance.gm_style_override = saved_gm_style_override
    instance.language = saved_language
    instance.seed_code = saved_seed
    logger.info("游戏已重置 (seed=%s) - game_key=%s", instance.seed_code, instance.game_key)
