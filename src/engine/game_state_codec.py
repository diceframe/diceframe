"""Persisted-state codec for :class:`GameInstance`.

The aggregate owns state transitions and invariants.  This module owns the
stable persistence projection and reconstruction mechanics so storage shape
changes do not keep expanding the aggregate implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from src.compat.characters import repair_ruleset_health_display
from src.engine.game_state_contracts import GamePersistedState
from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.migrations.instance import normalize_game_state_payload

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance, GameState


class GameStateCodec:
    """Encode and reconstruct the persisted ``GameInstance`` projection."""

    @staticmethod
    def encode(instance: GameInstance) -> GamePersistedState:
        from src.engine.modules import health, ruleset_runtime

        data: GamePersistedState = {
            "instance_schema_version": instance.instance_schema_version,
            "run_id": instance.run_id,
            "memory_namespace": instance.memory_namespace,
            "game_key": list(instance.game_key),
            "world_id": instance.world_id,
            "rule_id": instance.rule_id,
            "adventure_binding": instance.adventure_binding,
            "world_name": instance.world_name,
            "group_name": instance.group_name,
            "state": instance.state.value,
            "players": instance.players,
            "npcs": instance.npcs,
            "action_queue": instance.action_queue,
            "pending_actions": instance.pending_actions,
            "ready_players": sorted(instance.ready_players),
            "away_players": sorted(instance.away_players),
            "log": instance.log[-100:],
            "world_state": instance.world_state,
            "language": normalize_language(instance.language),
            "gm_uid": instance.gm_uid,
            "modules": {
                **instance.modules,
                "health": health.persisted_state(instance),
                "ruleset_runtime": ruleset_runtime.persisted_state(instance),
            },
        }
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
        instance = instance_type(
            game_key=tuple(data["game_key"]),
            instance_schema_version=int(data.get("instance_schema_version", 11) or 11),
            run_id=str(data.get("run_id") or ""),
            memory_namespace=str(data.get("memory_namespace") or ""),
            world_id=data.get("world_id"),
            # Empty marks a pre-rule_id save. The WebUI service resolves it from
            # the world template on first read and persists the migrated value.
            rule_id=data.get("rule_id", ""),
            adventure_binding=data.get("adventure_binding") or {},
            world_name=data.get("world_name", ""),
            group_name=data.get("group_name", ""),
            state=state_type(data["state"]),
            players=data.get("players", {}),
            npcs=data.get("npcs", {}),
            action_queue=data.get("action_queue", []),
            pending_actions=data.get("pending_actions", []),
            log=data.get("log", []),
            # 旧存档没有这个键：空世界（不是"猜测世界事实"）。
            world_state=(
                data.get("world_state")
                if isinstance(data.get("world_state"), dict)
                else {}
            ),
            language=normalize_language(data.get("language", DEFAULT_LANGUAGE)),
            gm_uid=data.get("gm_uid", ""),
            modules=data.get("modules") if isinstance(data.get("modules"), dict) else {},
            ready_players=set(data.get("ready_players", [])),
            away_players=set(data.get("away_players", [])),
        )
        # 旧存档可能留下与 canonical 矛盾的显示层 HP；加载时按 canonical 修复显示副本。
        for player in instance.players.values():
            sheet = player.get("character_sheet") if isinstance(player, dict) else None
            if isinstance(sheet, dict):
                repair_ruleset_health_display(sheet)
        from src.engine.modules import adventure_runtime_state

        # An empty or unknown play mode (for example from an in-memory new run)
        # is derived from the binding on every load, as before the slot existed.
        adventure_runtime_state.normalize_decoded_play_mode(instance)

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
