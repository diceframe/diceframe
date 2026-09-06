"""通用战斗调度器契约测试（Issue 212 PR 4）。"""

from __future__ import annotations

import pytest

from src.engine.combat_scheduler import (
    RoundRobinScheduler,
    SchedulerConfig,
    SchedulerError,
    SchedulerState,
    ThresholdScheduler,
    scheduler_from_config,
)


def _state(actors: dict, order: tuple[str, ...] = (), **overrides) -> SchedulerState:
    values = {
        "kind": "round_robin",
        "order": order,
        "turn_index": 0,
        "round": 1,
        "gauges": {},
        "ready": (),
    }
    values.update(overrides)
    return SchedulerState(**values)


# ---------- 配置 fail closed ----------


def test_unknown_scheduler_kind_fails_closed() -> None:
    with pytest.raises(SchedulerError, match="unknown scheduler kind"):
        SchedulerConfig.from_payload({"kind": "xianxia_atb"})
    with pytest.raises(SchedulerError):
        scheduler_from_config(SchedulerConfig(kind="atb"))
    with pytest.raises(SchedulerError, match="must be an object"):
        SchedulerConfig.from_payload("threshold")


def test_invalid_threshold_configuration_fails_closed() -> None:
    with pytest.raises(SchedulerError, match="threshold must be a positive integer"):
        SchedulerConfig(kind="threshold", threshold=0)
    with pytest.raises(SchedulerError, match="overflow policy"):
        SchedulerConfig(kind="threshold", overflow="drop")
    with pytest.raises(SchedulerError, match="consume policy"):
        SchedulerConfig(kind="threshold", consume="keep")


def test_from_dict_round_trip_and_rejection() -> None:
    state = SchedulerState(
        kind="threshold", order=("a", "b"), turn_index=0, round=2,
        gauges={"a": 40, "b": 90}, ready=("b",),
    )
    restored = SchedulerState.from_dict(state.to_dict())
    assert restored == state
    with pytest.raises(SchedulerError, match="unknown scheduler kind"):
        SchedulerState.from_dict({"kind": "atb"})


# ---------- round robin / initiative ----------


def test_round_robin_keeps_legacy_ordering_and_wraps() -> None:
    scheduler = RoundRobinScheduler(SchedulerConfig(kind="round_robin"))
    combat = {"actors": {"a": {"alive": True}, "b": {"alive": True}, "c": {"alive": True}}}
    result = scheduler.initialize(combat)
    assert result.current_actor_id == "a"
    result = scheduler.advance(result.state, combat)
    assert result.current_actor_id == "b"
    result = scheduler.advance(result.state, combat)
    assert result.current_actor_id == "c"
    result = scheduler.advance(result.state, combat)
    # 回到起点并进入下一轮。
    assert result.current_actor_id == "a"
    assert result.state.round == 2


def test_round_robin_skips_dead_actors() -> None:
    scheduler = RoundRobinScheduler(SchedulerConfig(kind="round_robin"))
    combat = {"actors": {"a": {}, "b": {}, "c": {}}}
    result = scheduler.initialize(combat)
    combat = {"actors": {"a": {}, "b": {"alive": False}, "c": {}}}
    result = scheduler.advance(result.state, combat)
    assert result.current_actor_id == "c"  # 跳过死亡的 b


def test_initiative_orders_by_value_then_actor_id() -> None:
    scheduler = scheduler_from_config(SchedulerConfig(kind="initiative"))
    combat = {"actors": {
        "player:zed": {"initiative": 12},
        "player:amy": {"initiative": 15},
        "enemy:goblin": {"initiative": 12},
    }}
    result = scheduler.initialize(combat)
    assert result.state.order == ("player:amy", "enemy:goblin", "player:zed")


def test_initiative_advance_wraps_round() -> None:
    scheduler = scheduler_from_config(SchedulerConfig(kind="initiative"))
    combat = {"actors": {"a": {"initiative": 5}, "b": {"initiative": 9}}}
    result = scheduler.initialize(combat)
    assert result.current_actor_id == "b"
    result = scheduler.advance(result.state, combat)
    assert result.current_actor_id == "a"
    result = scheduler.advance(result.state, combat)
    assert result.current_actor_id == "b"
    assert result.state.round == 2


def test_consume_turn_by_non_holder_is_rejected() -> None:
    scheduler = RoundRobinScheduler(SchedulerConfig(kind="round_robin"))
    combat = {"actors": {"a": {}, "b": {}}}
    result = scheduler.initialize(combat)
    with pytest.raises(SchedulerError, match="not the current turn holder"):
        scheduler.consume_turn(result.state, combat, "b")


# ---------- threshold (ATB) ----------


def _threshold_scheduler(**overrides) -> ThresholdScheduler:
    """测试夹具的 actor 速度字段叫 speed；显式声明映射。"""
    config = SchedulerConfig(kind="threshold", speed_stat="speed", **overrides)
    return ThresholdScheduler(config)


def _threshold_combat(speeds: dict[str, int]) -> dict:
    return {
        "actors": {
            actor_id: {"alive": True, "speed": speed, "initiative_modifier": 0}
            for actor_id, speed in speeds.items()
        },
    }


def test_threshold_gauge_accumulates_until_ready() -> None:
    scheduler = _threshold_scheduler(threshold=100)
    combat = _threshold_combat({"player:x": 25, "enemy:goblin": 10})
    result = scheduler.initialize(combat)
    assert result.ready_actor_ids == ()

    result = scheduler.advance(result.state, combat)
    # 4 tick 后 x 达到 100 就绪；goblin 同步累积 40。
    assert result.ready_actor_ids == ("player:x",)
    assert result.state.gauges["enemy:goblin"] == 40

    # 消费后 gauge 归零（默认 reset），goblin 继续累积。
    result = scheduler.consume_turn(result.state, combat, "player:x")
    assert result.ready_actor_ids == ()
    assert result.state.gauges["player:x"] == 0


def test_threshold_simultaneous_ready_uses_deterministic_tie_break() -> None:
    scheduler = _threshold_scheduler(threshold=100)
    combat = {
        "actors": {
            "player:zed": {"speed": 50, "initiative_modifier": 9},
            "player:amy": {"speed": 60, "initiative_modifier": 0},
            "enemy:goblin": {"speed": 50, "initiative_modifier": 0},
        },
    }
    result = scheduler.initialize(combat)
    result = scheduler.advance(result.state, combat)
    # 同为 2 tick 达标：先比 speed（amy 60 > goblin 50 > zed 50），
    # goblin 与 zed speed 相同 → 比 initiative_modifier（zed 9 > 0）。
    assert result.ready_actor_ids == ("player:amy", "player:zed", "enemy:goblin")

    # 完全同速同修正时按 canonical actor_id 字典序。
    same = _threshold_combat({"b": 50, "a": 50})
    result = scheduler.initialize(same)
    result = scheduler.advance(result.state, same)
    assert result.ready_actor_ids == ("a", "b")


def test_threshold_speed_buff_changes_next_advance() -> None:
    scheduler = _threshold_scheduler(threshold=100)
    combat = _threshold_combat({"player:x": 25, "enemy:goblin": 50})
    result = scheduler.initialize(combat)
    result = scheduler.advance(result.state, combat)
    assert result.ready_actor_ids == ("enemy:goblin",)  # goblin 先满
    # 遁术加速：x 的 speed 从 25 提升到 100（服务端状态变化）。
    buffed = _threshold_combat({"player:x": 100, "enemy:goblin": 50})
    result = scheduler.consume_turn(result.state, buffed, "enemy:goblin")
    result = scheduler.advance(result.state, buffed)
    assert result.ready_actor_ids == ("player:x",)  # 下一推进立即反映新速度


def test_threshold_consume_carry_policy_keeps_overflow() -> None:
    scheduler = _threshold_scheduler(threshold=100, consume="carry", overflow="carry")
    combat = _threshold_combat({"player:x": 60})
    result = scheduler.initialize(combat)
    result = scheduler.advance(result.state, combat)
    assert result.state.gauges["player:x"] == 120  # 超过阈值保留溢出
    result = scheduler.consume_turn(result.state, combat, "player:x")
    assert result.state.gauges["player:x"] == 20  # carry：120 - 100


def test_threshold_overflow_clamp_caps_gauge() -> None:
    scheduler = _threshold_scheduler(threshold=100, overflow="clamp")
    combat = _threshold_combat({"player:x": 60, "enemy:y": 10})
    result = scheduler.initialize(combat)
    result = scheduler.advance(result.state, combat)
    # clamp：x 停在 100（不保留溢出），y 推进到 20。
    assert result.state.gauges["player:x"] == 100
    assert result.state.gauges["enemy:y"] == 20


def test_threshold_dead_actors_are_never_ready() -> None:
    scheduler = _threshold_scheduler(threshold=100)
    combat = _threshold_combat({"player:x": 50, "enemy:goblin": 50})
    result = scheduler.initialize(combat)
    dead = _threshold_combat({"player:x": 50})
    dead["actors"]["enemy:goblin"] = {"alive": False, "speed": 50}
    result = scheduler.advance(result.state, dead)
    assert result.ready_actor_ids == ("player:x",)


def test_threshold_repeated_consume_is_rejected() -> None:
    scheduler = _threshold_scheduler(threshold=100)
    combat = _threshold_combat({"player:x": 50})
    result = scheduler.initialize(combat)
    result = scheduler.advance(result.state, combat)
    result = scheduler.consume_turn(result.state, combat, "player:x")
    with pytest.raises(SchedulerError, match="not ready"):
        scheduler.consume_turn(result.state, combat, "player:x")


def test_threshold_requires_positive_speed_fail_closed() -> None:
    scheduler = _threshold_scheduler(threshold=100)
    with pytest.raises(SchedulerError, match="speed > 0"):
        scheduler.initialize(_threshold_combat({"player:x": 0}))
    with pytest.raises(SchedulerError):
        scheduler.initialize({"actors": {}})
