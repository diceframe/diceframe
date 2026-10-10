"""reset() 契约 characterization tests（施工单 §11.1.2）。

目的不是定义"理想 reset"，而是**冻结开工基线的真实 reset contract**：

- 明确保留的字段继续保留；
- 明确清空的字段继续清空；
- ``rotate_run_identity`` 的 run_id / memory_namespace / economy 行为不变；
- 未被旧 reset() 触碰的字段（"隐式保留"）保持不变 —— 包括
  ``death_save_outcomes`` / ``last_overreach`` / ``rule_id`` 这类看起来
  "似乎应该清"但基线 reset() 确实不碰的字段。

extraction 重构必须让这组测试在迁移前后都保持绿色；任何字段行为变化都
说明迁移不是机械复制。修复"疑似 bug 的 reset 字段"属于另开的 bugfix PR。
"""

from __future__ import annotations

import pytest

from src.engine.modules import narrative_notes
from src.engine.game_instance import GameInstance, GameState
from src.engine.modules import economy_state
from src.engine.modules import media, private_channels, room_access, session_stats
from src.engine.world_state import fresh_world_state
from src.engine.modules import checks, combat_extension_state, ruleset_runtime, table_settings


def _make_populated_instance() -> GameInstance:
    """构造一个几乎所有字段都有值的实例，用于观察 reset 前后差异。"""
    instance = GameInstance(game_key=("web", "reset-characterization", "bot"))
    instance.run_id = "run_before"
    instance.memory_namespace = "('web', 'reset-characterization', 'bot')::run:run_before"
    economy_state.replace_state(instance, {
        "schema_version": 2,
        "run_id": "run_before",
        "next_sequence": 7,
        "proposals": [{"id": "p1"}],
        "transactions": [{"id": "t1"}],
        "idempotency_records": {"k": "v"},
        "effect_groups": [{"id": "g1"}],
        "external_effects_outbox": [{"id": "o1"}],
        "outcomes": [{"id": "x1"}],
    })
    instance.world_id = "world-1"
    instance.world_name = "Test World"
    instance.rule_id = "coc7"
    ruleset_runtime.replace_binding(instance, {
        "id": "core:dnd2024",
        "version": 1,
        "content_version": "2024-01",
        "state_schema_version": 3,
    })
    ruleset_runtime.replace_state(instance, {"state_schema_version": 3, "campaign": {"party": []}})
    instance.adventure_binding = {
        "adventure_id": "adv-1",
        "version": "1",
        "format": "v1",
        "content_digest": "deadbeef",
        "world_id": "world-1",
    }
    instance.play_mode = "adventure"
    ruleset_runtime.replace_event_ledger(instance, [{"event": "e1"}])
    media.replace_scene_image(instance, {"asset_id": "img-1"})
    media.replace_map_background(instance, {"asset_id": "map-1"})
    instance.group_name = "Group"
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.players = {
        "u1": {
            "character_name": "Alice",
            "character_sheet": {"hp": 10, "gold": 5, "deceased": False},
            "control": {"mode": "human", "revision": 3},
        },
    }
    instance.npcs = {"goblin": {"hp": 7}}
    instance.round_number = 4
    instance.action_queue = [{"user_id": "u1", "text": "act"}]
    instance.pending_actions = [{"user_id": "u2", "text": "pending"}]
    instance.ready_players = {"u1"}
    instance.away_players = {"u2"}
    instance.combat_active = True
    instance.combat_enemies = [{"hp": 3}]
    instance.combat_state = "active"
    instance.initiative_order = ["u1"]
    instance.initiative_current = 1
    room_access.replace_max_players(instance, 9)
    instance.gm_uid = "gm1"
    room_access.replace_player_access_open(instance, False)
    instance.away_control_policy = "ai_takeover"
    room_access.replace_bot_bind_token(instance, "bind-token")
    instance.set_room_password("secret-pass")
    room_access.issue_room_token(instance, token="room-token")
    private_channels.replace_private_log(instance, {"u1": [{"role": "gm", "text": "hi"}]})
    private_channels.replace_table_talk(instance, [{"speaker": "u1", "text": "tt"}])
    narrative_notes.replace_scene(instance, "老桥")
    narrative_notes.replace_game_time(instance, "14:00")
    instance.log = [{"round": 4, "gm_response": "叙事"}]
    narrative_notes.replace_summary(instance, {"narrative": "sum"})
    narrative_notes.replace_key_facts(instance, ["fact"])
    instance.world_state = {
        "schema_version": 1,
        "revision": 5,
        "clock": {"day": 1, "minute": 60},
        "facts": {"actor:u1.location": {"value": "bridge"}},
        "scheduled_events": [],
    }
    instance.adventure_progress = {
        "active_nodes": ["vault"], "completed_nodes": ["gate"], "history": [],
    }
    instance.last_saved_log_count = 3
    session_stats.replace_total_llm_calls(instance, 11)
    session_stats.replace_total_tokens(instance, 2222)
    session_stats.replace_started_at(instance, "2026-01-01T00:00:00+00:00")
    session_stats.replace_last_activity(instance, "2026-01-01T01:00:00+00:00")
    checks.replace_last_check(instance, {"check_id": "c1"})
    checks.replace_last_checks(instance, [{"check_id": "c1"}])
    checks.replace_manual_roll_requests(instance, [{"request_id": "r1"}])
    instance.round_unpriced_purchase_intents = [{"item": "potion"}]
    checks.replace_round_checks_prepared(instance, True)
    instance.round_start_snapshot = {"u1": {"hp": 10}}
    instance.round_entity_snapshot = {"npcs": {"goblin": {"hp": 7}}}
    instance.death_save_outcomes = {"3": {"u1": {"roll": 18}}}
    instance.gm_directives = [{"id": "d1"}]
    instance.last_state_update = {"hp": "10"}
    instance.last_overreach = [{"player": "u1"}]
    instance.last_world_legality = [{"player": "u1"}]
    instance.last_world_events = [{"event_id": "e1"}]
    instance.last_token_budget_bump = {"kind": "narrative", "from": 1, "to": 2}
    table_settings.replace_solo_mode(instance, True)
    table_settings.replace_seed_code(instance, "SEED42")
    table_settings.replace_difficulty(instance, "硬核")
    table_settings.replace_narrative_perspective(instance, "immersive")
    table_settings.replace_gm_style_override(instance, dict(PRESERVED_GM_STYLE_OVERRIDE))
    instance.language = "en"
    table_settings.replace_entry_point(instance, "plugin")
    instance.pending_combat_results = [{"damage": 5}]
    instance.lorebook_timed_state = {"e1": {"remaining": 3}}
    instance.quick_actions = ["attack"]
    instance.health_events = [{"kind": "degraded"}]
    instance.health_status = {"degraded": True}
    table_settings.replace_luck_timeout_seconds(instance, 90)
    table_settings.replace_economy_reward_policy(instance, {"mode": "auto_small_cash", "auto_reward_cap": 10})
    combat_extension_state.replace_current(instance, {"schema_version": 1, "pools": {"p1": {}}})
    combat_extension_state.replace_round_snapshots(instance, {"4": {"schema_version": 1}})
    instance.pending_luck_after_recovery = True
    narrative_notes.replace_confirmed_items(instance, ["sword"])
    return instance


EXPECTED_PRESERVED = {
    "world_id": "world-1",
    "world_name": "Test World",
    "group_name": "Group",
    "language": "en",
    "adventure_binding": {
        "adventure_id": "adv-1",
        "version": "1",
        "format": "v1",
        "content_digest": "deadbeef",
        "world_id": "world-1",
    },
}

# Table settings are module state (no GameInstance attributes). reset() saves
# and restores these explicitly; the others it never touches.
PRESERVED_GM_STYLE_OVERRIDE = {"tone": "grim"}


def _assert_table_settings_preserved(instance: GameInstance) -> None:
    assert table_settings.solo_mode(instance) is True
    assert table_settings.narrative_perspective(instance) == "immersive"
    assert table_settings.gm_style_override(instance) == PRESERVED_GM_STYLE_OVERRIDE


def _assert_table_settings_implicitly_preserved(instance: GameInstance) -> None:
    assert table_settings.difficulty(instance) == "硬核"
    assert table_settings.entry_point(instance) == "plugin"
    assert table_settings.luck_timeout_seconds(instance) == 90
    assert table_settings.economy_reward_policy(instance) == {"mode": "auto_small_cash", "auto_reward_cap": 10}


# The ruleset runtime binding is module state (no GameInstance attribute).
EXPECTED_PRESERVED_RULESET_BINDING = {
    "id": "core:dnd2024",
    "version": 1,
    "content_version": "2024-01",
    "state_schema_version": 3,
}

# 基线 reset() 明确清空/归零的字段（值 = 字段自己的空形态）。
EXPECTED_CLEARED = {
    "npcs": {},
    "log": [],
    "pending_combat_results": [],
    "lorebook_timed_state": {},
    "health_events": [],
    "health_status": {},
    "quick_actions": [],
    "gm_directives": [],
}

# 基线 reset() 根本不触碰的字段 —— "隐式保留"。这里冻结的是基线行为本身。
EXPECTED_IMPLICIT_PRESERVED = {
    "rule_id": "coc7",
    "play_mode": "adventure",
    "away_players": {"u2"},
    "gm_uid": "gm1",
    "away_control_policy": "ai_takeover",
    "last_saved_log_count": 3,
    "pending_luck_after_recovery": True,
    "round_unpriced_purchase_intents": [{"item": "potion"}],
    "death_save_outcomes": {"3": {"u1": {"roll": 18}}},
    "last_overreach": [{"player": "u1"}],
    "last_world_legality": [{"player": "u1"}],
    "last_world_events": [{"event_id": "e1"}],
}


@pytest.mark.asyncio
async def test_reset_keeps_seed_and_preserves_configuration_fields() -> None:
    instance = _make_populated_instance()
    await instance.reset(keep_seed=True)

    assert instance.state == GameState.CREATED
    assert table_settings.seed_code(instance) == "SEED42"
    _assert_table_settings_preserved(instance)
    for field, expected in EXPECTED_PRESERVED.items():
        actual = getattr(instance, field)
        assert actual == expected, f"reset 必须保留 {field}"
    assert ruleset_runtime.binding(instance) == EXPECTED_PRESERVED_RULESET_BINDING, "reset 必须保留 ruleset_runtime"
    # 保留字段应是深拷贝，reset 后修改不影响旧对象引用。
    assert table_settings.gm_style_override(instance) is not PRESERVED_GM_STYLE_OVERRIDE


@pytest.mark.asyncio
async def test_reset_without_seed_clears_seed_code() -> None:
    instance = _make_populated_instance()
    await instance.reset(keep_seed=False)
    assert table_settings.seed_code(instance) == ""


@pytest.mark.asyncio
async def test_reset_clears_runtime_and_narrative_state() -> None:
    instance = _make_populated_instance()
    await instance.reset(keep_seed=True)

    assert instance.players == {}
    assert instance.round_number == 0
    assert instance.action_queue == []
    assert instance.pending_actions == []
    assert instance.ready_players == set()
    assert instance.combat_active is False
    assert instance.combat_enemies == []
    assert instance.combat_state == "none"
    assert instance.initiative_order == []
    assert instance.initiative_current == 0
    assert narrative_notes.scene(instance) == ""
    assert narrative_notes.game_time(instance) == ""
    assert instance.world_state == fresh_world_state()
    # Progress and world truth belong to the same run: never keep "node
    # completed" after the world that recorded its consequences is wiped.
    assert instance.adventure_progress == {}
    assert session_stats.total_llm_calls(instance) == 0
    assert session_stats.total_tokens(instance) == 0
    assert session_stats.started_at(instance) == ""
    assert session_stats.last_activity(instance) == ""
    assert instance.puzzle_manager is None
    assert instance.plot_tracker is None
    assert combat_extension_state.current(instance) == {}
    assert combat_extension_state.round_snapshots(instance) == {}
    assert checks.last_check(instance) is None
    assert checks.last_checks(instance) == []
    assert checks.round_checks_prepared(instance) is False
    assert instance.round_start_snapshot == {}
    assert instance.round_entity_snapshot == {}
    assert instance.last_state_update is None
    assert instance.last_token_budget_bump is None
    for key, expected_empty in EXPECTED_CLEARED.items():
        assert getattr(instance, key) == expected_empty, f"reset 必须清空 {key}"
    assert private_channels.private_log(instance) == {}, "reset 必须清空 private_log"
    assert private_channels.table_talk(instance) == [], "reset 必须清空 table_talk"
    assert narrative_notes.summary(instance) == {}, "reset 必须清空 summary"
    assert narrative_notes.key_facts(instance) == [], "reset 必须清空 key_facts"
    assert narrative_notes.confirmed_items(instance) == [], "reset 必须清空 confirmed_items"
    assert ruleset_runtime.event_ledger(instance) == [], "reset 必须清空 event_ledger"


@pytest.mark.asyncio
async def test_reset_rotates_run_identity_and_economy() -> None:
    instance = _make_populated_instance()
    old_run_id = instance.run_id
    old_namespace = instance.memory_namespace
    await instance.reset(keep_seed=True)

    assert instance.run_id != old_run_id
    assert instance.run_id.startswith("run_")
    assert instance.memory_namespace != old_namespace
    assert instance.memory_namespace.endswith(f"::run:{instance.run_id}")
    assert instance.memory_namespace.startswith(str(instance.game_key))
    # economy 整体重建为全新 run 的初始形态，不残留旧 proposals/transactions。
    assert economy_state.state(instance)["schema_version"] == 2
    assert economy_state.state(instance)["run_id"] == instance.run_id
    assert economy_state.state(instance)["next_sequence"] == 1
    assert economy_state.state(instance)["proposals"] == []
    assert economy_state.state(instance)["transactions"] == []
    assert economy_state.state(instance)["external_effects_outbox"] == []


@pytest.mark.asyncio
async def test_reset_does_not_touch_implicit_preserve_fields() -> None:
    instance = _make_populated_instance()
    await instance.reset(keep_seed=True)

    _assert_table_settings_implicitly_preserved(instance)
    for field, expected in EXPECTED_IMPLICIT_PRESERVED.items():
        actual = getattr(instance, field)
        assert actual == expected, (
            f"基线 reset() 不触碰 {field}；extraction 不得改变这一行为"
        )
    assert media.scene_image(instance) == {"asset_id": "img-1"}
    assert media.map_background(instance) == {"asset_id": "map-1"}
    assert room_access.max_players(instance) == 9
    assert room_access.player_access_open(instance) is False
    assert room_access.bot_bind_token(instance) == "bind-token"
    assert checks.manual_roll_requests(instance) == [{"request_id": "r1"}]


@pytest.mark.asyncio
async def test_reset_keeps_the_room_password_and_room_tokens() -> None:
    instance = _make_populated_instance()
    await instance.reset(keep_seed=True)

    assert room_access.has_room_password(instance) is True
    assert room_access.verify_room_password(instance, "secret-pass")
    assert room_access.verify_room_token(instance, "room-token")
    # Only hashes are kept; no plaintext attribute rides along.
    assert "secret-pass" not in str(instance.to_dict())
    assert not hasattr(instance, "room_password") and not hasattr(instance, "room_token")


@pytest.mark.asyncio
async def test_reset_rebuilds_ruleset_state_from_preserved_runtime() -> None:
    instance = _make_populated_instance()
    await instance.reset(keep_seed=True)

    assert ruleset_runtime.state(instance) == {"state_schema_version": 3}
