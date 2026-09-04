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
        data: GamePersistedState = {
            "instance_schema_version": instance.instance_schema_version,
            "run_id": instance.run_id,
            "memory_namespace": instance.memory_namespace,
            "economy": instance.economy,
            "game_key": list(instance.game_key),
            "world_id": instance.world_id,
            "rule_id": instance.rule_id,
            "adventure_binding": instance.adventure_binding,
            "scene_image": instance.scene_image,
            "map_background": instance.map_background,
            "world_name": instance.world_name,
            "group_name": instance.group_name,
            "state": instance.state.value,
            "players": instance.players,
            "npcs": instance.npcs,
            "round_number": instance.round_number,
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
            "game_time": instance.game_time,
            "log": instance.log[-100:],
            "summary": instance.summary,
            "key_facts": instance.key_facts,
            "total_llm_calls": instance.total_llm_calls,
            "total_tokens": instance.total_tokens,
            "started_at": instance.started_at,
            "last_activity": instance.last_activity,
            "solo_mode": instance.solo_mode,
            "seed_code": instance.seed_code,
            "difficulty": instance.difficulty,
            "narrative_perspective": instance.narrative_perspective,
            "language": normalize_language(instance.language),
            "luck_timeout_seconds": instance.luck_timeout_seconds,
            "entry_point": instance.entry_point,
            "max_players": instance.max_players,
            "gm_uid": instance.gm_uid,
            "player_access_open": instance.player_access_open,
            "bot_bind_token": instance.bot_bind_token,
            "room_password": instance.room_password,
            "room_token": instance.room_token,
            "pending_combat_results": instance.pending_combat_results,
            "lorebook_timed_state": instance.lorebook_timed_state,
            "quick_actions": instance.quick_actions,
            "health_events": instance.health_events[-100:],
            "health_status": instance.health_status,
            "last_check": instance.last_check,
            "last_checks": instance.last_checks,
            "last_overreach": instance.last_overreach,
            "round_checks_prepared": instance.round_checks_prepared,
            "round_start_snapshot": instance.round_start_snapshot,
            "death_save_outcomes": instance.death_save_outcomes,
            "last_state_update": instance.last_state_update,
            "last_token_budget_bump": instance.last_token_budget_bump,
            "gm_directives": instance.gm_directives,
            "confirmed_items": instance.confirmed_items,
            "private_log": instance.private_log,
            "table_talk": instance.table_talk,
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
            instance_schema_version=int(data.get("instance_schema_version", 6) or 6),
            run_id=str(data.get("run_id") or ""),
            memory_namespace=str(data.get("memory_namespace") or ""),
            economy=data.get("economy") or {},
            world_id=data.get("world_id"),
            # Empty marks a pre-rule_id save. The WebUI service resolves it from
            # the world template on first read and persists the migrated value.
            rule_id=data.get("rule_id", ""),
            ruleset_runtime=data.get("ruleset_runtime") or {},
            ruleset_state=data.get("ruleset_state") or {},
            adventure_binding=data.get("adventure_binding") or {},
            event_ledger=data.get("event_ledger") or [],
            scene_image=data.get("scene_image", {}),
            map_background=data.get("map_background", {}),
            world_name=data.get("world_name", ""),
            group_name=data.get("group_name", ""),
            state=state_type(data["state"]),
            players=data.get("players", {}),
            npcs=data.get("npcs", {}),
            round_number=data.get("round_number", 0),
            action_queue=data.get("action_queue", []),
            pending_actions=data.get("pending_actions", []),
            combat_active=data.get("combat_active", False),
            combat_enemies=data.get("combat_enemies", []),
            combat_state=data.get("combat_state", "none"),
            initiative_order=data.get("initiative_order", []),
            initiative_current=data.get("initiative_current", 0),
            scene=data.get("scene", ""),
            game_time=data.get("game_time", ""),
            log=data.get("log", []),
            summary=data.get("summary", {}),
            key_facts=data.get("key_facts", []),
            total_llm_calls=data.get("total_llm_calls", 0),
            total_tokens=data.get("total_tokens", 0),
            started_at=data.get("started_at", ""),
            last_activity=data.get("last_activity", ""),
            solo_mode=data.get("solo_mode", False),
            seed_code=data.get("seed_code", ""),
            difficulty=data.get("difficulty", "标准"),
            narrative_perspective=data.get("narrative_perspective", "auto"),
            language=normalize_language(data.get("language", DEFAULT_LANGUAGE)),
            luck_timeout_seconds=int(data.get("luck_timeout_seconds", 60) or 0),
            entry_point=data.get("entry_point", "web"),
            max_players=data.get("max_players", 6),
            gm_uid=data.get("gm_uid", ""),
            player_access_open=data.get("player_access_open", True),
            bot_bind_token=data.get("bot_bind_token", ""),
            room_password=data.get("room_password", ""),
            room_token=data.get("room_token", ""),
            pending_combat_results=data.get("pending_combat_results", []),
            lorebook_timed_state=data.get("lorebook_timed_state", {}),
            quick_actions=data.get("quick_actions", []),
            health_events=data.get("health_events", []),
            health_status=data.get("health_status", {}),
            last_check=data.get("last_check"),
            last_checks=data.get("last_checks") or [],
            last_overreach=data.get("last_overreach") or [],
            round_checks_prepared=bool(data.get("round_checks_prepared", False)),
            round_start_snapshot=data.get("round_start_snapshot") or {},
            death_save_outcomes=death_save_outcomes,
            last_state_update=data.get("last_state_update"),
            last_token_budget_bump=data.get("last_token_budget_bump"),
            gm_directives=data.get("gm_directives", []),
            ready_players=set(data.get("ready_players", [])),
            away_players=set(data.get("away_players", [])),
            confirmed_items=data.get("confirmed_items", []),
            private_log=data.get("private_log", {}),
            table_talk=[
                item
                for item in (data.get("table_talk") or [])
                if isinstance(item, dict) and item.get("visibility") == "party"
            ][-50:],
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
