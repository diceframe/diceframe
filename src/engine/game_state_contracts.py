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
    TableTalkExchange,
    TokenBudgetBump,
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
    economy: OpaqueState
    game_key: list[str]
    world_id: str | None
    rule_id: str
    ruleset_runtime: OpaqueState
    ruleset_state: OpaqueState
    adventure_binding: OpaqueState
    event_ledger: list[OpaqueState]
    scene_image: dict[str, str]
    map_background: dict[str, str]
    world_name: str
    group_name: str
    state: str
    players: dict[str, PlayerData]
    npcs: dict[str, OpaqueState]
    round_number: int
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
    game_time: str
    log: list[RoundLogEntry]
    summary: OpaqueState
    key_facts: list[Any]
    total_llm_calls: int
    total_tokens: int
    started_at: str
    last_activity: str
    solo_mode: bool
    seed_code: str
    difficulty: str
    narrative_perspective: str
    language: str
    luck_timeout_seconds: int
    entry_point: str
    max_players: int
    gm_uid: str
    player_access_open: bool
    bot_bind_token: str
    room_password: str
    room_token: str
    pending_combat_results: list[OpaqueState]
    lorebook_timed_state: dict[str, OpaqueState]
    quick_actions: list[str]
    health_events: list[OpaqueState]
    health_status: OpaqueState
    last_check: CheckResult | None
    last_checks: list[CheckResult]
    last_overreach: list[Any]
    round_checks_prepared: bool
    round_start_snapshot: PlayerRollbackSnapshot
    death_save_outcomes: dict[str, dict[str, OpaqueState]]
    last_state_update: OpaqueState | None
    last_token_budget_bump: TokenBudgetBump | None
    gm_directives: list[OpaqueState]
    confirmed_items: list[Any]
    private_log: dict[str, list[OpaqueState]]
    table_talk: list[TableTalkExchange]
    puzzles: OpaqueState
    plot_tracker: OpaqueState
