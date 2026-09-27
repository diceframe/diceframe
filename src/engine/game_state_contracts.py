"""Typed contracts for generic game persistence and context boundaries.

These types describe stable top-level shapes. Ruleset-owned, plugin-owned and
legacy extension payloads remain intentionally opaque at the generic engine
boundary; the owning runtime validates their internal mechanics.
"""

from __future__ import annotations

from typing import Any, TypedDict

from src.engine.contracts import (
    ActionRecord,
    CheckResult,
    PlayerData,
    RoundLogEntry,
)


# Intentionally opaque at the generic engine boundary.
OpaqueState = dict[str, Any]


class PlayerRollbackState(TypedDict, total=False):
    """Compatibility snapshot of player fields that a round swipe may restore."""

    hp: Any
    max_hp: Any
    gold: Any
    deceased: Any
    death_round: Any
    status: Any
    sanity: Any
    max_sanity: Any
    luck: Any
    max_luck: Any
    mana: Any
    currency: Any
    resources: Any
    spells_known: Any
    inventory: Any
    equipment: Any
    key_items: Any


PlayerRollbackSnapshot = dict[str, PlayerRollbackState]


CharacterSheetView = TypedDict(
    "CharacterSheetView",
    {
        "hp": Any,
        "max_hp": Any,
        "class": Any,
        "race": Any,
        "level": Any,
        "xp": Any,
        "gold": Any,
        "attributes": Any,
        "equipment": Any,
        "skills": Any,
        "inventory": Any,
        "key_items": Any,
        "background": Any,
        "deceased": bool,
        "_modifiers": dict[str, int],
        "_armor": int,
        "_special_stats": dict[str, int],
    },
    total=False,
)


class PlayerContextView(TypedDict, total=False):
    character_name: str
    attendance: str
    character_sheet: CharacterSheetView
    # 席位控制器（human / ai / unclaimed）。投影是否对外暴露由各消费者决定，
    # 这里只固定字段契约，见 src.engine.player_control。
    control: OpaqueState


class GameContextView(TypedDict, total=False):
    world_name: str
    round_number: int
    scene: str
    game_time: str
    difficulty: str
    language: str
    players: dict[str, PlayerContextView]
    away_players: list[str]
    npcs: dict[str, OpaqueState]
    combat_state: str
    combat_enemies: list[OpaqueState]
    initiative_order: list[str]
    initiative_current: int
    quick_actions: list[str]
    attendance_note: str
    combat_active: bool
    solo_mode: bool
    puzzles: OpaqueState


class GamePersistedState(TypedDict, total=False):
    """Stable top-level save projection produced by ``GameStateCodec``."""

    instance_schema_version: int
    run_id: str
    memory_namespace: str
    game_key: list[str]
    world_id: str | None
    rule_id: str
    ruleset_runtime: OpaqueState
    ruleset_state: OpaqueState
    adventure_binding: OpaqueState
    adventure_progress: dict[str, Any]
    play_mode: str
    event_ledger: list[OpaqueState]
    world_name: str
    group_name: str
    state: str
    players: dict[str, PlayerData]
    npcs: dict[str, OpaqueState]
    action_queue: list[ActionRecord]
    pending_actions: list[ActionRecord]
    ready_players: list[str]
    away_players: list[str]
    combat_active: bool
    combat_enemies: list[OpaqueState]
    combat_state: str
    initiative_order: list[str]
    initiative_current: int
    scene: str
    log: list[RoundLogEntry]
    world_state: OpaqueState
    total_llm_calls: int
    total_tokens: int
    started_at: str
    last_activity: str
    language: str
    gm_uid: str
    modules: dict[str, dict[str, Any]]
    last_check: CheckResult | None
    last_checks: list[CheckResult]
    manual_roll_requests: list[dict[str, Any]]
    round_checks_prepared: bool
    round_start_snapshot: PlayerRollbackSnapshot
    round_entity_snapshot: OpaqueState
    death_save_outcomes: dict[str, dict[str, OpaqueState]]
    puzzles: OpaqueState
    plot_tracker: OpaqueState
