"""Persisted-state codec for :class:`GameInstance`.

The aggregate owns state transitions and invariants.  This module owns the
stable persistence projection and reconstruction mechanics so storage shape
changes do not keep expanding the aggregate implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from src.engine.game_state_contracts import GamePersistedState
from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.migrations.instance import normalize_game_state_payload

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance, GameState


class GameStateCodec:
    """Encode and reconstruct the persisted ``GameInstance`` projection."""

    @staticmethod
    def encode(instance: GameInstance) -> GamePersistedState:
        from src.engine.modules import health

        data: GamePersistedState = {
            "instance_schema_version": instance.instance_schema_version,
            "run_id": instance.run_id,
            "memory_namespace": instance.memory_namespace,
            "game_key": list(instance.game_key),
            "world_id": instance.world_id,
            "rule_id": instance.rule_id,
            "adventure_binding": instance.adventure_binding,
            "adventure_progress": instance.adventure_progress,
            "play_mode": instance.play_mode,
            "world_name": instance.world_name,
            "group_name": instance.group_name,
            "state": instance.state.value,
            "players": instance.players,
            "npcs": instance.npcs,
            "action_queue": instance.action_queue,
            "pending_actions": instance.pending_actions,
            "ready_players": sorted(instance.ready_players),
            "away_players": sorted(instance.away_players),
            "combat_active": instance.combat_active,
            "combat_enemies": instance.combat_enemies,
            "combat_state": instance.combat_state,
            "initiative_order": instance.initiative_order,
            "initiative_current": instance.initiative_current,
            "scene": instance.scene,
            "log": instance.log[-100:],
            "world_state": instance.world_state,
            "total_llm_calls": instance.total_llm_calls,
            "total_tokens": instance.total_tokens,
            "started_at": instance.started_at,
            "last_activity": instance.last_activity,
            "language": normalize_language(instance.language),
            "max_players": instance.max_players,
            "gm_uid": instance.gm_uid,
            "player_access_open": instance.player_access_open,
            "bot_bind_token": instance.bot_bind_token,
            "room_password": instance.room_password,
            "room_token": instance.room_token,
            "modules": {**instance.modules, "health": health.persisted_state(instance)},
            "last_check": instance.last_check,
            "last_checks": instance.last_checks,
            "manual_roll_requests": instance.manual_roll_requests,
            "round_checks_prepared": instance.round_checks_prepared,
            "round_start_snapshot": instance.round_start_snapshot,
            "round_entity_snapshot": instance.round_entity_snapshot,
            "death_save_outcomes": instance.death_save_outcomes,
        }
        if instance.ruleset_runtime:
            data["ruleset_runtime"] = instance.ruleset_runtime
            data["ruleset_state"] = instance.ruleset_state
            data["event_ledger"] = instance.event_ledger
        if instance.puzzle_manager and hasattr(instance.puzzle_manager, "to_active_dict"):
            data["puzzles"] = instance.puzzle_manager.to_active_dict()
        if instance.plot_tracker and hasattr(instance.plot_tracker, "to_dict"):
            data["plot_tracker"] = instance.plot_tracker.to_dict()
        return data

    @staticmethod
    def decode(
        data: Mapping[str, Any],
        *,
        instance_type: type[GameInstance],
        state_type: type[GameState],
    ) -> GameInstance:
        data = normalize_game_state_payload(data)
        raw_death_save_outcomes = data.get("death_save_outcomes")
        death_save_outcomes = (
            raw_death_save_outcomes
            if isinstance(raw_death_save_outcomes, dict)
            else {}
        )
        instance = instance_type(
            game_key=tuple(data["game_key"]),
            instance_schema_version=int(data.get("instance_schema_version", 11) or 11),
            run_id=str(data.get("run_id") or ""),
            memory_namespace=str(data.get("memory_namespace") or ""),
            world_id=data.get("world_id"),
            # Empty marks a pre-rule_id save. The WebUI service resolves it from
            # the world template on first read and persists the migrated value.
            rule_id=data.get("rule_id", ""),
            ruleset_runtime=data.get("ruleset_runtime") or {},
            ruleset_state=data.get("ruleset_state") or {},
            adventure_binding=data.get("adventure_binding") or {},
            # FIX-04 §6.2/§6.3：旧存档没有这个键 → 空进度（不猜进度，不迁移 v1
            # campaign 状态）；非 dict 的脏值同样降级为空进度。
            adventure_progress=(
                data.get("adventure_progress")
                if isinstance(data.get("adventure_progress"), dict)
                else {}
            ),
            play_mode=(
                str(data.get("play_mode") or "")
                if str(data.get("play_mode") or "").casefold() in {"free", "adventure"}
                else (
                    "adventure"
                    if isinstance(data.get("adventure_binding"), dict)
                    and data.get("adventure_binding", {}).get("adventure_id")
                    else "free"
                )
            ),
            event_ledger=data.get("event_ledger") or [],
            world_name=data.get("world_name", ""),
            group_name=data.get("group_name", ""),
            state=state_type(data["state"]),
            players=data.get("players", {}),
            npcs=data.get("npcs", {}),
            action_queue=data.get("action_queue", []),
            pending_actions=data.get("pending_actions", []),
            combat_active=data.get("combat_active", False),
            combat_enemies=data.get("combat_enemies", []),
            combat_state=data.get("combat_state", "none"),
            initiative_order=data.get("initiative_order", []),
            initiative_current=data.get("initiative_current", 0),
            scene=data.get("scene", ""),
            log=data.get("log", []),
            # 旧存档没有这个键：空世界（不是"猜测世界事实"）。
            world_state=(
                data.get("world_state")
                if isinstance(data.get("world_state"), dict)
                else {}
            ),
            total_llm_calls=data.get("total_llm_calls", 0),
            total_tokens=data.get("total_tokens", 0),
            started_at=data.get("started_at", ""),
            last_activity=data.get("last_activity", ""),
            language=normalize_language(data.get("language", DEFAULT_LANGUAGE)),
            max_players=data.get("max_players", 6),
            gm_uid=data.get("gm_uid", ""),
            player_access_open=data.get("player_access_open", True),
            bot_bind_token=data.get("bot_bind_token", ""),
            room_password=data.get("room_password", ""),
            room_token=data.get("room_token", ""),
            modules=data.get("modules") if isinstance(data.get("modules"), dict) else {},
            last_check=data.get("last_check"),
            last_checks=data.get("last_checks") or [],
            manual_roll_requests=data.get("manual_roll_requests") or [],
            round_checks_prepared=bool(data.get("round_checks_prepared", False)),
            round_start_snapshot=data.get("round_start_snapshot") or {},
            # 旧存档没有这个键：默认空快照，回滚时退化为按目标核对战斗缓存。
            round_entity_snapshot=(
                data.get("round_entity_snapshot")
                if isinstance(data.get("round_entity_snapshot"), dict)
                else {}
            ),
            death_save_outcomes=death_save_outcomes,
            ready_players=set(data.get("ready_players", [])),
            away_players=set(data.get("away_players", [])),
        )

        puzzles_data = data.get("puzzles")
        if puzzles_data:
            from src.engine.puzzle import PuzzleManager

            instance.puzzle_manager = PuzzleManager.from_dict(puzzles_data)

        from src.engine.plot_tracker import PlotTracker

        plot_data = data.get("plot_tracker")
        instance.plot_tracker = (
            PlotTracker.from_dict(plot_data) if plot_data else PlotTracker()
        )
        return instance
