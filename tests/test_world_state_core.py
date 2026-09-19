"""World state core (WP4 / Issue 284 prerequisite).

Covers the whole contract of the authoritative world container:

* a new game starts from an explicit empty world (facts / logical clock /
  scheduled events), and every save declares the schema it was written with;
* world ops are the only write entry point: validated, bounded, atomic, and
  they record provenance;
* persistence: save/load round trip, v12 -> v13 migration, reset/restart give a
  fresh world, import/rebind keeps the play state but isolates the run;
* round semantics: rollback / abort / swipe revert the world ops of the round
  they belong to (ADR 0003 whole-round semantics), and never lose world truth.
"""

from __future__ import annotations

import asyncio

import pytest

from webapi_harness import web_api  # noqa: F401  (lifecycle fixture)

from src.engine.game_instance import GameInstance, GameState, _snapshot_players
from src.llm.client import LLMResponse
from src.engine.world_state import (
    WORLD_STATE_SCHEMA_VERSION,
    WorldStateError,
    apply_world_ops,
    ensure_world_state,
    fact_value,
    fresh_world_state,
    world_clock,
    world_facts,
    world_revision,
    world_scheduled_events,
)
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    migrate_game_state_payload,
    rebind_imported_game_state_payload,
)


def make_instance(*, rule_id: str = "freeform_fantasy") -> GameInstance:
    instance = GameInstance(game_key=("web", "world-state", "bot"), rule_id=rule_id)
    instance.state = GameState.ACTIVE_ACTION
    instance.players = {
        "p1": {"character_name": "阿岚", "character_sheet": {"hp": 10, "max_hp": 10}},
    }
    return instance


def set_location(instance: GameInstance, value: str, **kwargs) -> dict:
    return apply_world_ops(instance, [
        {"op": "set_fact", "key": "actor:p1.location", "value": value, **kwargs},
    ])


# ---- 新游戏默认值 -----------------------------------------------------------


def test_new_game_starts_with_an_explicit_empty_world() -> None:
    instance = make_instance()

    assert instance.world_state == fresh_world_state()
    assert instance.world_state == {
        "schema_version": WORLD_STATE_SCHEMA_VERSION,
        "revision": 0,
        "clock": {"day": 1, "minute": 0},
        "facts": {},
        "scheduled_events": {},
        "entities": {},
        "relations": {},
        "processes": {},
    }
    assert world_facts(instance.world_state) == {}
    assert world_scheduled_events(instance.world_state) == {}
    assert world_revision(instance.world_state) == 0
    # 新游戏声明自己写的是当前 schema，加载时不会被当成旧存档重复迁移。
    assert instance.to_dict()["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION


def test_ensure_world_state_never_guesses_user_data() -> None:
    assert ensure_world_state(None) == fresh_world_state()
    assert ensure_world_state({}) == fresh_world_state()
    assert ensure_world_state("not-a-state") == fresh_world_state()
    future = {"schema_version": 99, "facts": {"a": {"value": 1, "visibility": "gm"}}}
    # 未知 schema 原样保留：写路径负责拒绝，而不是静默改写成默认世界。
    assert ensure_world_state(future) == future


# ---- World Ops --------------------------------------------------------------


def test_set_and_remove_facts_record_provenance() -> None:
    instance = make_instance()
    instance.round_number = 4

    result = apply_world_ops(instance, [
        {"op": "set_fact", "key": "actor:p1.location", "value": "village_east"},
        {"op": "set_fact", "key": "bridge:old.passable", "value": False,
         "visibility": "gm"},
    ])

    assert result["revision"] == 1
    assert [item["created"] for item in result["applied"]] == [True, True]
    facts = world_facts(instance.world_state)
    assert facts["actor:p1.location"] == {
        "value": "village_east", "visibility": "public",
        "source_round": 4, "updated_revision": 1,
    }
    assert facts["bridge:old.passable"]["visibility"] == "gm"
    assert fact_value(instance.world_state, "actor:p1.location") == "village_east"

    # 更新时不写 visibility 就沿用原值：一次数值更新不能让 GM 私有事实变公开。
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "bridge:old.passable", "value": True},
    ])
    updated = world_facts(instance.world_state)["bridge:old.passable"]
    assert updated["value"] is True
    assert updated["visibility"] == "gm"
    assert updated["updated_revision"] == 2
    assert world_revision(instance.world_state) == 2

    apply_world_ops(instance, [{"op": "remove_fact", "key": "bridge:old.passable"}])
    assert "bridge:old.passable" not in world_facts(instance.world_state)
    assert world_revision(instance.world_state) == 3


@pytest.mark.parametrize("value", [
    {"nested": "object"},
    ["list"],
    1.5,
    None,
    "x" * 401,
    "line\nbreak",
])
def test_fact_values_are_bounded_scalars(value: object) -> None:
    instance = make_instance()

    with pytest.raises(WorldStateError, match="fact value"):
        apply_world_ops(instance, [
            {"op": "set_fact", "key": "actor:p1.location", "value": value},
        ])

    assert world_facts(instance.world_state) == {}


@pytest.mark.parametrize("key", [
    "", "has space", "村西", "with/slash", "with\\backslash", "x" * 121,
])
def test_fact_keys_are_canonical_not_display_text(key: str) -> None:
    instance = make_instance()

    with pytest.raises(WorldStateError):
        apply_world_ops(instance, [{"op": "set_fact", "key": key, "value": 1}])

    assert instance.world_state == fresh_world_state()


def test_visibility_vocabulary_is_closed() -> None:
    instance = make_instance()

    with pytest.raises(WorldStateError, match="visibility"):
        apply_world_ops(instance, [
            {"op": "set_fact", "key": "actor:p1.location", "value": "x",
             "visibility": "player:p2"},
        ])


def test_advance_time_moves_the_logical_clock() -> None:
    instance = make_instance()

    result = apply_world_ops(instance, [{"op": "advance_time", "minutes": 720}])

    assert result["clock"] == {"day": 1, "minute": 720}
    assert world_clock(instance.world_state) == {"day": 1, "minute": 720}

    apply_world_ops(instance, [{"op": "advance_time", "minutes": 720}])

    assert world_clock(instance.world_state) == {"day": 2, "minute": 0}


@pytest.mark.parametrize("minutes", [0, -5, 60 * 24 * 31, True, "60", None])
def test_advance_time_rejects_unusable_amounts(minutes: object) -> None:
    instance = make_instance()

    with pytest.raises(WorldStateError, match="minutes"):
        apply_world_ops(instance, [{"op": "advance_time", "minutes": minutes}])

    assert world_clock(instance.world_state) == {"day": 1, "minute": 0}


def test_schedule_and_cancel_events_are_data_operations() -> None:
    instance = make_instance()
    apply_world_ops(instance, [{"op": "advance_time", "minutes": 720}])

    result = apply_world_ops(instance, [{
        "op": "schedule_event",
        "event_id": "ritual:clearing",
        "due_at": {"day": 1, "minute": 840},
        "label": "清林仪式完成",
        "ops": [{"op": "set_fact", "key": "ritual:clearing.status",
                 "value": "completed"}],
    }])

    assert result["applied"][0]["op"] == "schedule_event"
    events = world_scheduled_events(instance.world_state)
    assert events == {"ritual:clearing": {
        "event_id": "ritual:clearing",
        "due_at": {"day": 1, "minute": 840},
        "status": "pending",
        "label": "清林仪式完成",
        "ops": [{"op": "set_fact", "key": "ritual:clearing.status",
                 "value": "completed"}],
    }}
    # 本 work package 只维护数据容器：推进时间不会替我们执行事件。
    apply_world_ops(instance, [{"op": "advance_time", "minutes": 180}])
    assert fact_value(instance.world_state, "ritual:clearing.status") is None
    assert world_scheduled_events(instance.world_state)["ritual:clearing"]["status"] == "pending"

    apply_world_ops(instance, [{"op": "cancel_event", "event_id": "ritual:clearing"}])

    assert world_scheduled_events(instance.world_state)["ritual:clearing"]["status"] == "cancelled"


def test_scheduled_events_fail_closed_on_bad_schedules() -> None:
    instance = make_instance()
    apply_world_ops(instance, [{"op": "advance_time", "minutes": 600}])

    with pytest.raises(WorldStateError, match="past"):
        apply_world_ops(instance, [{
            "op": "schedule_event", "event_id": "late",
            "due_at": {"day": 1, "minute": 600},
            "ops": [{"op": "set_fact", "key": "a.b", "value": 1}],
        }])
    with pytest.raises(WorldStateError, match="non-empty ops"):
        apply_world_ops(instance, [{
            "op": "schedule_event", "event_id": "empty",
            "due_at": {"day": 1, "minute": 700}, "ops": [],
        }])
    with pytest.raises(WorldStateError, match="cannot use"):
        apply_world_ops(instance, [{
            "op": "schedule_event", "event_id": "nested",
            "due_at": {"day": 1, "minute": 700},
            "ops": [{"op": "advance_time", "minutes": 10}],
        }])
    with pytest.raises(WorldStateError, match="fact value"):
        apply_world_ops(instance, [{
            "op": "schedule_event", "event_id": "malformed",
            "due_at": {"day": 1, "minute": 700},
            "ops": [{"op": "set_fact", "key": "a.b", "value": {"bad": "shape"}}],
        }])
    with pytest.raises(WorldStateError, match="event ops cannot use"):
        apply_world_ops(instance, [{
            "op": "schedule_event", "event_id": "complete-too-early",
            "due_at": {"day": 1, "minute": 700},
            "ops": [{"op": "complete_event", "event_id": "x", "status": "applied"}],
        }])
    # 事件描述未来：调度期只校验结构，引用合法性由结算时刻决定（见
    # tests/test_world_events.py 的 failed 事件用例）。
    apply_world_ops(instance, [{
        "op": "schedule_event", "event_id": "future-reference",
        "due_at": {"day": 1, "minute": 700},
        "ops": [{"op": "remove_fact", "key": "never.set"}],
    }])

    assert set(world_scheduled_events(instance.world_state)) == {"future-reference"}


def test_duplicate_event_ids_and_non_pending_cancels_are_rejected() -> None:
    instance = make_instance()
    apply_world_ops(instance, [{
        "op": "schedule_event", "event_id": "ritual:clearing",
        "due_at": {"day": 1, "minute": 100},
        "ops": [{"op": "set_fact", "key": "ritual:clearing.status", "value": "done"}],
    }])

    with pytest.raises(WorldStateError, match="reuses event id"):
        apply_world_ops(instance, [{
            "op": "schedule_event", "event_id": "ritual:clearing",
            "due_at": {"day": 1, "minute": 200},
            "ops": [{"op": "set_fact", "key": "ritual:clearing.status", "value": "x"}],
        }])

    apply_world_ops(instance, [{"op": "cancel_event", "event_id": "ritual:clearing"}])
    with pytest.raises(WorldStateError, match="non-pending"):
        apply_world_ops(instance, [{"op": "cancel_event", "event_id": "ritual:clearing"}])
    with pytest.raises(WorldStateError, match="unknown event"):
        apply_world_ops(instance, [{"op": "cancel_event", "event_id": "ghost"}])


# ---- 非法输入与原子性 --------------------------------------------------------


@pytest.mark.parametrize("ops", [
    [],
    [{"op": "teleport"}],
    [{"op": "set_fact", "key": "a.b", "value": 1, "extra": True}],
    [{"op": "remove_fact"}],
    [{"op": "remove_fact", "key": "never.set"}],
    [{"op": "set_fact", "key": "a.b"}],
    "not-a-list",
])
def test_illegal_world_ops_are_rejected(ops: object) -> None:
    instance = make_instance()
    set_location(instance, "village_east")
    before = instance.world_state

    with pytest.raises(WorldStateError):
        apply_world_ops(instance, ops)

    assert instance.world_state is before


def test_a_failing_op_rolls_back_the_whole_batch() -> None:
    instance = make_instance()
    set_location(instance, "village_east")
    before = instance.world_state

    with pytest.raises(WorldStateError):
        apply_world_ops(instance, [
            {"op": "set_fact", "key": "door:cellar.locked", "value": False},
            {"op": "remove_fact", "key": "never.set"},
        ])

    assert instance.world_state is before
    assert "door:cellar.locked" not in world_facts(instance.world_state)


def test_unknown_or_corrupt_world_state_fails_closed_on_write() -> None:
    instance = make_instance()
    instance.world_state = {"schema_version": 99, "facts": {}}

    with pytest.raises(WorldStateError, match="unsupported world state schema"):
        set_location(instance, "village_east")

    # WR-02 起 v2 是当前 schema：裸 v1 容器在权威写路径同样 fail closed
    # （装载路径的 ensure/migration 才负责 v1 → v2 升级）。
    instance.world_state = {"schema_version": 1, "revision": "x"}
    with pytest.raises(WorldStateError, match="unsupported world state schema"):
        set_location(instance, "village_east")

    instance.world_state = {"schema_version": 2, "revision": "x"}
    with pytest.raises(WorldStateError, match="revision"):
        set_location(instance, "village_east")

    instance.world_state = {
        "schema_version": 2, "revision": 0, "clock": {"day": 1, "minute": 0},
        "facts": {"ok": {"value": 1, "visibility": "public"}}, "scheduled_events": {},
        "entities": {}, "relations": {}, "processes": {},
    }
    apply_world_ops(instance, [{"op": "set_fact", "key": "second", "value": 2}])
    assert set(world_facts(instance.world_state)) == {"ok", "second"}


def test_readers_never_crash_on_a_corrupt_container() -> None:
    instance = make_instance()
    instance.world_state = {"schema_version": 1, "facts": "not-an-object"}

    assert world_facts(instance.world_state) == {}
    assert world_scheduled_events(instance.world_state) == {}
    assert fact_value(instance.world_state, "actor:p1.location") is None
    assert world_revision(instance.world_state) == 0
    assert world_clock(instance.world_state) == {"day": 1, "minute": 0}


# ---- 持久化 -----------------------------------------------------------------


def test_save_load_roundtrip_preserves_world_truth() -> None:
    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "actor:p1.location", "value": "village_east"},
        {"op": "advance_time", "minutes": 90},
        {"op": "schedule_event", "event_id": "night.ambush",
         "due_at": {"day": 1, "minute": 1200},
         "ops": [{"op": "set_fact", "key": "world.alert", "value": "high"}]},
    ])

    recovered = GameInstance.from_dict(instance.to_dict())

    assert recovered.world_state == instance.world_state
    assert fact_value(recovered.world_state, "actor:p1.location") == "village_east"
    assert world_clock(recovered.world_state) == {"day": 1, "minute": 90}
    # 重载后仍可继续写入，revision 连续。
    set_location(recovered, "village_west")
    assert world_revision(recovered.world_state) == world_revision(instance.world_state) + 1


def test_v12_saves_gain_an_empty_world_container() -> None:
    legacy = {
        "game_key": ["web", "legacy", "bot"],
        "state": "paused",
        "instance_schema_version": 12,
        "players": {"p1": {"character_sheet": {"hp": 8}}},
    }

    migrated = migrate_game_state_payload(legacy)

    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert migrated["world_state"] == fresh_world_state()
    # 幂等：重复迁移不会重置或改写已有世界真相。
    assert migrate_game_state_payload(migrated) == migrated

    payload = migrate_game_state_payload({
        "game_key": ["web", "older", "bot"], "instance_schema_version": 1,
    })
    assert payload["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert payload["world_state"] == fresh_world_state()


def test_migration_keeps_an_existing_world_payload() -> None:
    world = fresh_world_state()
    world["facts"] = {"actor:p1.location": {
        "value": "village_east", "visibility": "gm",
        "source_round": 2, "updated_revision": 1,
    }}
    world["revision"] = 1

    migrated = migrate_game_state_payload({
        "game_key": ["web", "mid", "bot"],
        "instance_schema_version": 12,
        "world_state": world,
    })

    assert migrated["world_state"] == world


def test_reloading_a_new_save_does_not_replay_currency_migration() -> None:
    """新存档写明当前 schema，加载时不会被当成旧 CoC 存档再 ×100。"""

    instance = make_instance(rule_id="freeform_coc")
    instance.get_character_sheet("p1")["currency"] = {"amount": 70}
    instance.get_character_sheet("p1")["gold"] = 70

    reloaded = GameInstance.from_dict(instance.to_dict())

    assert reloaded.instance_schema_version == CURRENT_INSTANCE_SCHEMA_VERSION
    assert reloaded.get_character_sheet("p1")["currency"] == {"amount": 70}
    assert reloaded.get_character_sheet("p1")["gold"] == 70


def test_import_rebind_keeps_world_truth_and_isolates_the_run() -> None:
    instance = make_instance()
    set_location(instance, "village_east")
    payload = instance.to_dict()

    rebound = rebind_imported_game_state_payload(
        payload, game_key=("web", "imported", "bot"), run_id="run_new",
    )

    assert rebound["game_key"] == ["web", "imported", "bot"]
    assert rebound["run_id"] == "run_new"
    assert rebound["memory_namespace"] == "('web', 'imported', 'bot')::run:run_new"
    assert rebound["world_state"] == instance.world_state
    assert fact_value(rebound["world_state"], "actor:p1.location") == "village_east"


# ---- 回合语义 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_rollback_reverts_world_ops_of_that_round() -> None:
    instance = make_instance()
    instance.round_number = 1
    set_location(instance, "village_east")
    world_before_round = instance.world_state

    assert instance._do_advance_locked() is True
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "door:cellar.locked", "value": False},
        {"op": "advance_time", "minutes": 30},
    ])
    await instance.finish_judgment("本轮叙事", state_changes=[])

    assert instance.log[-1]["pre_world_state"] == world_before_round
    assert fact_value(instance.world_state, "door:cellar.locked") is False

    rolled_back = await instance.rollback_last_round()

    assert rolled_back == 1
    assert instance.world_state == world_before_round
    assert fact_value(instance.world_state, "actor:p1.location") == "village_east"
    assert fact_value(instance.world_state, "door:cellar.locked") is None
    assert world_clock(instance.world_state) == {"day": 1, "minute": 0}


@pytest.mark.asyncio
async def test_abort_round_processing_restores_the_judgment_entry_world() -> None:
    instance = make_instance()
    set_location(instance, "village_east")
    world_before_round = instance.world_state
    assert instance._do_advance_locked() is True
    apply_world_ops(instance, [{"op": "set_fact", "key": "door:cellar.locked", "value": False}])

    assert await instance.abort_round_processing() is True

    assert instance.world_state == world_before_round


def test_reset_starts_from_an_empty_world() -> None:
    instance = make_instance()
    set_location(instance, "village_east")
    apply_world_ops(instance, [{"op": "advance_time", "minutes": 600}])

    asyncio.run(instance.reset())

    assert instance.world_state == fresh_world_state()


@pytest.mark.asyncio
async def test_swipe_branch_cut_reverts_world_ops_after_the_target_round(
    web_api, monkeypatch,
) -> None:
    """swipe 切回第 1 轮时，第 1 轮之后写入的世界事实一起撤销（ADR 0003）。"""

    api, _lorebook, registry, llm, _worlds = web_api
    created = await api.create_game(
        "template_world", "Swipe world", gm_uid="gm",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 1}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    instance.gm_uid = uid
    instance.round_number = 1
    set_location(instance, "village_east")
    round1_world = instance.world_state
    instance.log.append({
        "round": 1, "actions": [], "gm_response": "旧分支",
        "pre_state_snapshot": _snapshot_players(instance),
        "pre_world_state": round1_world,
        "swipes": ["旧分支"], "current_swipe": 0,
    })
    instance.log.append({
        "round": 2, "actions": [], "gm_response": "第二轮", "pre_state_snapshot": {},
    })
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "door:cellar.locked", "value": False},
        {"op": "advance_time", "minutes": 30},
    ])

    async def replacement_swipe(*, system_prompt, user_message, **kwargs):
        del system_prompt, user_message, kwargs
        return LLMResponse(
            content="重写的第一轮。\n---\nNONE",
            narration="重写的第一轮。",
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=6,
            is_narration_only=True,
            provider_used="fake",
        )

    monkeypatch.setattr(llm, "call", replacement_swipe)

    assert await api._handler.generate_swipe(instance, 1) == "重写的第一轮。"

    assert instance.world_state == round1_world
    assert fact_value(instance.world_state, "actor:p1.location") == "village_east"
    assert fact_value(instance.world_state, "door:cellar.locked") is None
    assert world_clock(instance.world_state) == {"day": 1, "minute": 0}


@pytest.mark.asyncio
async def test_restart_and_reset_start_a_new_world(web_api) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world", "WorldState",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 3}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    set_location(instance, "village_east")
    apply_world_ops(instance, [{"op": "advance_time", "minutes": 600}])
    await registry.save(instance)

    restarted = await api.restart_game(created["game_key"])
    after_restart = registry.get(api._parse_key(created["game_key"]))

    assert restarted["ok"] is True
    assert after_restart.world_state == fresh_world_state()
    assert fact_value(after_restart.world_state, "actor:p1.location") is None
    assert after_restart.to_dict()["world_state"] == fresh_world_state()

    after_restart.round_number = 2
    set_location(after_restart, "village_west")
    reset = await api.reset_game(created["game_key"])
    after_reset = registry.get(api._parse_key(created["game_key"]))

    assert reset["ok"] is True
    assert after_reset.world_state == fresh_world_state()
