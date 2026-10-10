"""Scheduled events + logical world time (WP6 / Issue 284).

`advance_world_time` is the only way logical time moves, and moving it is what
settles due scheduled events.  The contract under test:

* no background tick: time advances only when the authoritative round flow asks;
* deterministic order and one atomic commit per advance;
* idempotency: an event settles exactly once, across retries, reloads and saves;
* cancelled events never fire, and an event whose ops no longer apply is marked
  ``failed`` instead of blocking the clock or being retried forever;
* rollback / reset / restart keep event state consistent (old runs never leak
  their pending events into a new run).
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from webapi_harness import web_api  # noqa: F401  (lifecycle fixture)

from src.commands.check_planner import plan_round_checks
from src.engine.game_instance import GameInstance, GameState
from src.engine.world_events import (
    MAX_ADVANCE_MINUTES,
    advance_world_time,
    due_events,
)
from src.engine.world_state import (
    WorldStateError,
    apply_world_ops,
    fact_value,
    world_clock,
    world_scheduled_events,
)
from src.llm.world_prompt import format_world_events_block
from src.rules.rule_system import RuleSystem
from src.engine.modules import world_reports


def make_instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "world-events", "bot"), rule_id="test")
    instance.state = GameState.ACTIVE_ACTION
    instance.players = {
        "p1": {"user_id": "p1", "character_name": "阿岚",
               "character_sheet": {"attributes": {"str": 12}}},
    }
    return instance


def at_noon(instance: GameInstance) -> None:
    apply_world_ops(instance, [{"op": "advance_time", "minutes": 720}])


def schedule_ritual(instance: GameInstance, *, due_minute: int = 840) -> None:
    apply_world_ops(instance, [{
        "op": "schedule_event",
        "event_id": "ritual:clearing",
        "due_at": {"day": 1, "minute": due_minute},
        "label": "清林仪式完成",
        "ops": [{"op": "set_fact", "key": "ritual:clearing.status",
                 "value": "completed"}],
    }])


def _with_event_ops(state: dict, ops: list) -> dict:
    """A detached copy of ``state`` whose scheduled event carries ``ops``."""

    corrupted = deepcopy(state)
    corrupted["scheduled_events"]["ritual:clearing"]["ops"] = ops
    return corrupted


# ---- §8.6 示例：12:00 安排 14:00 的事件 --------------------------------------


def test_due_event_settles_once_at_its_moment() -> None:
    instance = make_instance()
    at_noon(instance)
    schedule_ritual(instance)

    early = advance_world_time(instance, 60)

    assert early["clock"] == {"day": 1, "minute": 780}
    assert early["applied"] == [] and early["failed"] == []
    assert fact_value(instance.world_state, "ritual:clearing.status") is None

    on_time = advance_world_time(instance, 60)

    assert on_time["clock"] == {"day": 1, "minute": 840}
    assert on_time["applied"] == [{
        "event_id": "ritual:clearing", "label": "清林仪式完成",
        "due_at": {"day": 1, "minute": 840},
    }]
    assert fact_value(instance.world_state, "ritual:clearing.status") == "completed"
    event = world_scheduled_events(instance.world_state)["ritual:clearing"]
    assert event["status"] == "applied"
    assert event["settled_at"] == {"day": 1, "minute": 840}

    later = advance_world_time(instance, 60)

    assert later["applied"] == []
    assert fact_value(instance.world_state, "ritual:clearing.status") == "completed"


def test_settlement_is_idempotent_across_reload_and_repeat_advances() -> None:
    instance = make_instance()
    at_noon(instance)
    schedule_ritual(instance, due_minute=780)
    advance_world_time(instance, 60)

    reloaded = GameInstance.from_dict(instance.to_dict())
    again = advance_world_time(reloaded, 60)

    assert again["applied"] == []
    assert world_scheduled_events(reloaded.world_state)["ritual:clearing"]["status"] == "applied"
    # 再次加载也不会重放：事件状态随存档持久化。
    twice = GameInstance.from_dict(reloaded.to_dict())
    assert advance_world_time(twice, 60)["applied"] == []


def test_multiple_due_events_settle_in_stable_order() -> None:
    instance = make_instance()
    at_noon(instance)
    # 同一时刻、不同 id：顺序由 canonical event id 决定，与插入顺序无关。
    for event_id in ("b:second", "a:first"):
        apply_world_ops(instance, [{
            "op": "schedule_event", "event_id": event_id,
            "due_at": {"day": 1, "minute": 800},
            "ops": [{"op": "set_fact", "key": f"event.{event_id}.done", "value": True}],
        }])
    apply_world_ops(instance, [{
        "op": "schedule_event", "event_id": "later:third",
        "due_at": {"day": 1, "minute": 900},
        "ops": [{"op": "set_fact", "key": "event.later.done", "value": True}],
    }])

    first = advance_world_time(instance, 100)

    assert [item["event_id"] for item in first["applied"]] == ["a:first", "b:second"]
    second = advance_world_time(instance, 100)
    assert [item["event_id"] for item in second["applied"]] == ["later:third"]

    pending = due_events(instance.world_state, target_minutes=10_000)
    assert pending == []


def test_cancelled_events_never_fire() -> None:
    instance = make_instance()
    at_noon(instance)
    schedule_ritual(instance)
    apply_world_ops(instance, [{"op": "cancel_event", "event_id": "ritual:clearing"}])

    result = advance_world_time(instance, 120)

    assert result["applied"] == []
    assert fact_value(instance.world_state, "ritual:clearing.status") is None
    assert world_scheduled_events(instance.world_state)["ritual:clearing"]["status"] == "cancelled"


def test_event_whose_ops_no_longer_apply_is_marked_failed_and_does_not_block(
) -> None:
    instance = make_instance()
    at_noon(instance)
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "ward:gate.raised", "value": True},
    ])
    apply_world_ops(instance, [{
        "op": "schedule_event", "event_id": "ward:fade",
        "due_at": {"day": 1, "minute": 780},
        "label": "结界消散",
        "ops": [{"op": "remove_fact", "key": "ward:gate.raised"}],
    }])
    apply_world_ops(instance, [{
        "op": "schedule_event", "event_id": "bell:ring",
        "due_at": {"day": 1, "minute": 790},
        "label": "钟声",
        "ops": [{"op": "set_fact", "key": "bell.rang", "value": True}],
    }])
    # 事实在事件到期前被移除：remove_fact 到期时已无法应用。
    apply_world_ops(instance, [{"op": "remove_fact", "key": "ward:gate.raised"}])

    result = advance_world_time(instance, 120)

    assert [item["event_id"] for item in result["failed"]] == ["ward:fade"]
    assert result["failed"][0]["status"] if "status" in result["failed"][0] else True
    assert [item["event_id"] for item in result["applied"]] == ["bell:ring"]
    # 时钟照常推进，其它事件照常结算，失败事件不重试。
    assert result["clock"] == {"day": 1, "minute": 840}
    assert fact_value(instance.world_state, "bell.rang") is True
    events = world_scheduled_events(instance.world_state)
    assert events["ward:fade"]["status"] == "failed"
    assert events["ward:fade"]["error"]
    assert advance_world_time(instance, 60)["failed"] == []


def test_events_may_chain_and_see_earlier_settlements() -> None:
    instance = make_instance()
    at_noon(instance)
    apply_world_ops(instance, [{
        "op": "schedule_event", "event_id": "a:open",
        "due_at": {"day": 1, "minute": 760},
        "ops": [{"op": "set_fact", "key": "gate.open", "value": True}],
    }])
    apply_world_ops(instance, [{
        "op": "schedule_event", "event_id": "b:enter",
        "due_at": {"day": 1, "minute": 770},
        "ops": [{"op": "remove_fact", "key": "gate.open"}],
    }])

    result = advance_world_time(instance, 120)

    assert [item["event_id"] for item in result["applied"]] == ["a:open", "b:enter"]
    assert fact_value(instance.world_state, "gate.open") is None


# ---- 参数与失败边界 ----------------------------------------------------------


@pytest.mark.parametrize("minutes", [0, -5, MAX_ADVANCE_MINUTES + 1, True, 1.5, "60", None])
def test_unusable_advance_amounts_are_rejected_without_writing(minutes: object) -> None:
    instance = make_instance()
    at_noon(instance)
    before = instance.world_state

    with pytest.raises(WorldStateError):
        advance_world_time(instance, minutes)

    assert instance.world_state is before


def test_corrupt_or_unsupported_world_state_makes_advance_inert() -> None:
    instance = make_instance()
    before = world_clock(instance.world_state)
    instance.world_state = {"schema_version": 99}

    with pytest.raises(WorldStateError):
        advance_world_time(instance, 60)

    assert instance.world_state == {"schema_version": 99}
    assert world_clock(before) == before


def test_corrupt_persisted_event_ops_fail_closed_instead_of_applying_nothing() -> None:
    """#309 原始复现：``ops=[123]`` 曾被静默过滤成空，事件却仍被标记 applied。"""

    instance = make_instance()
    at_noon(instance)
    schedule_ritual(instance, due_minute=780)
    # 模拟损坏的存档：写路径永远不会产生这种事件，只有坏数据会走到这里。
    instance.world_state = _with_event_ops(instance.world_state, [123])
    corrupted = instance.world_state

    with pytest.raises(WorldStateError):
        advance_world_time(instance, 60)

    # fail closed：时钟不推进、事件不变 applied、坏 op 不被静默丢弃。
    assert instance.world_state is corrupted
    assert world_clock(instance.world_state) == {"day": 1, "minute": 720}
    stored = world_scheduled_events(instance.world_state)["ritual:clearing"]
    assert stored["status"] == "pending"
    assert stored["ops"] == [123]


@pytest.mark.parametrize("payload", [
    123,
    "not-an-object",
    {},
    {"op": "teleport"},
    {"op": "advance_time", "minutes": 30},
    {"op": "schedule_event", "event_id": "nested"},
    {"op": "set_fact", "key": "not a canonical key", "value": 1},
])
def test_persisted_event_ops_obey_the_write_path_shape_contract(payload: object) -> None:
    """读取路径不得比写入路径宽松：同一套 op shape contract。"""

    instance = make_instance()
    at_noon(instance)
    schedule_ritual(instance, due_minute=780)
    instance.world_state = _with_event_ops(instance.world_state, [payload])
    corrupted = instance.world_state

    with pytest.raises(WorldStateError):
        advance_world_time(instance, 60)

    assert instance.world_state is corrupted
    assert world_clock(instance.world_state) == {"day": 1, "minute": 720}
    stored = world_scheduled_events(instance.world_state)["ritual:clearing"]
    assert stored["status"] == "pending"
    assert stored["ops"] == [payload]
    assert fact_value(instance.world_state, "ritual:clearing.status") is None


# ---- rollback / run 隔离（§8.7） --------------------------------------------


@pytest.mark.asyncio
async def test_round_rollback_restores_event_state() -> None:
    instance = make_instance()
    at_noon(instance)
    schedule_ritual(instance, due_minute=780)
    world_before_round = instance.world_state

    assert instance._do_advance_locked() is True
    advance_world_time(instance, 60)
    assert world_scheduled_events(instance.world_state)["ritual:clearing"]["status"] == "applied"
    await instance.finish_judgment("本轮叙事", state_changes=[])

    assert await instance.rollback_last_round() == 1

    assert instance.world_state == world_before_round
    assert world_scheduled_events(instance.world_state)["ritual:clearing"]["status"] == "pending"
    assert fact_value(instance.world_state, "ritual:clearing.status") is None


def test_reset_drops_pending_events_of_the_old_run() -> None:
    import asyncio

    instance = make_instance()
    at_noon(instance)
    schedule_ritual(instance)

    asyncio.run(instance.reset())

    assert world_scheduled_events(instance.world_state) == {}
    assert world_clock(instance.world_state) == {"day": 1, "minute": 0}


@pytest.mark.asyncio
async def test_restart_does_not_carry_pending_events_into_the_new_run(web_api) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world", "WorldEvents",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 1}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    at_noon(instance)
    schedule_ritual(instance)
    await registry.save(instance)

    assert (await api.restart_game(created["game_key"]))["ok"] is True
    restarted = registry.get(api._parse_key(created["game_key"]))

    assert world_scheduled_events(restarted.world_state) == {}
    assert advance_world_time(restarted, 1440)["applied"] == []


# ---- GM 可见的已结算事件 ------------------------------------------------------


def test_settled_events_render_for_the_gm_and_clear_each_round() -> None:
    instance = make_instance()
    assert format_world_events_block(instance) == ""

    world_reports.replace_last_world_events(instance, [{
        "event_id": "ritual:clearing", "label": "清林仪式完成",
        "due_at": {"day": 1, "minute": 840}, "status": "applied",
    }])
    rendered = format_world_events_block(instance)
    assert "【世界时间推进·已结算事件】" in rendered
    assert "清林仪式完成" in rendered

    instance.language = "en"
    assert "took effect" in format_world_events_block(instance)

    world_reports.replace_last_world_events(instance, [{
        "event_id": "ward:fade", "label": "结界消散",
        "due_at": {"day": 1, "minute": 780}, "status": "failed", "error": "unknown fact",
    }])
    instance.language = "zh-CN"
    assert "未能结算" in format_world_events_block(instance)

    reloaded = GameInstance.from_dict(instance.to_dict())
    assert world_reports.last_world_events(reloaded) == world_reports.last_world_events(instance)
    reloaded.reset_round_checks()
    assert world_reports.last_world_events(reloaded) == []


# ---- planner seam -----------------------------------------------------------


class _FakeToolResponse:
    native_tools = True
    provider_used = "fake"
    total_tokens = 5

    def __init__(self, arguments: dict):
        self.tool_calls = [{"name": "dice_checks", "arguments": arguments}]


class _FakeToolClient:
    default = "fake"

    def __init__(self, arguments: dict):
        self._arguments = arguments

    async def call_tools(self, *_args, **_kwargs):
        return _FakeToolResponse(self._arguments)


def planner_rule() -> RuleSystem:
    return RuleSystem({
        "rule_id": "test", "name": "Test", "dice_system": "d20",
        "mechanics": "dnd5e_core",
        "attributes": [{"key": "str", "name": "力量"}],
        "dc_table": {"easy": 8, "normal": 12, "hard": 16},
    })


@pytest.mark.asyncio
@pytest.mark.parametrize(("raw", "expected"), [
    ({"minutes": 60}, {"minutes": 60}),
    ({"minutes": 720, "reason": "长途赶路"}, {"minutes": 720, "reason": "长途赶路"}),
    ({"minutes": 0}, None),
    ({"minutes": -30}, None),
    ({"minutes": MAX_ADVANCE_MINUTES + 1}, None),
    ({"minutes": "60"}, None),
    ({"minutes": True}, None),
    ("not-an-object", None),
])
async def test_planner_parses_only_usable_time_advances(raw: object, expected: object) -> None:
    instance = make_instance()
    instance.action_queue = [{"user_id": "p1", "text": "我们休息一小时"}]

    _planned, metadata = await plan_round_checks(
        instance, planner_rule(), _FakeToolClient({
            "checks": [], "world_time_advance": raw,
        }),
    )

    assert metadata["world_time_advance"] == expected


@pytest.mark.asyncio
async def test_missing_time_advance_output_keeps_checks_working() -> None:
    instance = make_instance()
    instance.action_queue = [{"user_id": "p1", "text": "我观察四周"}]

    _planned, metadata = await plan_round_checks(
        instance, planner_rule(), _FakeToolClient({"checks": []}),
    )

    assert metadata["world_time_advance"] is None
    assert metadata["errors"] == []
