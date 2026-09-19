"""Process Runtime 测试（WR-04，母方案 §16/§80/§97）。

覆盖：start/complete/cancel/fail 的持久化语义、due_at 必须在未来、
到期进程只随权威时间推进确定性结算（无后台 tick）、结算幂等、
批原子性、容器上限与整轮回滚恢复。
"""

from __future__ import annotations

import asyncio

import pytest

from src.engine.game_instance import GameInstance
from src.engine.world_events import advance_world_time, due_processes
from src.engine.world_state import (
    MAX_PROCESSES,
    WorldStateError,
    apply_world_ops,
    world_processes,
)


def make_instance() -> GameInstance:
    return GameInstance(game_key=("web", "wr2-process", "bot"))


def _start_ritual(instance: GameInstance, *, due_minutes: int = 840) -> None:
    apply_world_ops(instance, [
        {"op": "start_process", "process_id": "process:clearing-ritual", "kind": "ritual",
         "participants": ["npc:warlock"], "location": "location:clearing",
         "due_at": {"day": 1, "minute": due_minutes}, "visibility": "gm"},
    ])


def test_start_process_stamps_logical_clock_and_running_status() -> None:
    instance = make_instance()
    apply_world_ops(instance, [{"op": "advance_time", "minutes": 120}])
    _start_ritual(instance)
    process = world_processes(instance.world_state)["process:clearing-ritual"]
    assert process["status"] == "running"
    assert process["started_at"] == {"day": 1, "minute": 120}
    assert process["due_at"] == {"day": 1, "minute": 840}
    assert process["visibility"] == "gm"


def test_start_process_rejects_past_or_present_due_at() -> None:
    instance = make_instance()
    apply_world_ops(instance, [{"op": "advance_time", "minutes": 120}])
    with pytest.raises(WorldStateError, match="already past its due_at"):
        _start_ritual(instance, due_minutes=120)
    with pytest.raises(WorldStateError, match="already past its due_at"):
        _start_ritual(instance, due_minutes=60)
    # 批内先推进再启动：due_at 相对批内的"新时刻"校验（180 现在已是过去）。
    apply_world_ops(instance, [
        {"op": "advance_time", "minutes": 60},
        {"op": "start_process", "process_id": "process:later", "kind": "siege",
         "due_at": {"day": 1, "minute": 240}},
    ])
    with pytest.raises(WorldStateError, match="already past its due_at"):
        apply_world_ops(instance, [
            {"op": "advance_time", "minutes": 60},
            {"op": "start_process", "process_id": "process:stale", "kind": "siege",
             "due_at": {"day": 1, "minute": 180}},
        ])


def test_start_process_rejects_duplicate_id_and_bad_fields() -> None:
    instance = make_instance()
    _start_ritual(instance)
    with pytest.raises(WorldStateError, match="reuses process id"):
        _start_ritual(instance)
    with pytest.raises(WorldStateError, match="unknown field"):
        apply_world_ops(instance, [
            {"op": "start_process", "process_id": "process:x", "kind": "ritual", "progress": 63},
        ])
    with pytest.raises(WorldStateError, match="participants"):
        apply_world_ops(instance, [
            {"op": "start_process", "process_id": "process:x", "kind": "ritual",
             "participants": "npc:warlock"},
        ])


def test_complete_cancel_fail_transitions_are_explicit_and_once() -> None:
    instance = make_instance()
    _start_ritual(instance)
    apply_world_ops(instance, [{"op": "complete_process", "process_id": "process:clearing-ritual"}])
    assert world_processes(instance.world_state)["process:clearing-ritual"]["status"] == "completed"
    with pytest.raises(WorldStateError, match="non-running process"):
        apply_world_ops(instance, [{"op": "complete_process", "process_id": "process:clearing-ritual"}])

    apply_world_ops(instance, [
        {"op": "start_process", "process_id": "process:search", "kind": "police_search"},
    ])
    apply_world_ops(instance, [{"op": "cancel_process", "process_id": "process:search"}])
    assert world_processes(instance.world_state)["process:search"]["status"] == "cancelled"

    apply_world_ops(instance, [
        {"op": "start_process", "process_id": "process:flood", "kind": "flood"},
    ])
    apply_world_ops(instance, [{"op": "fail_process", "process_id": "process:flood"}])
    assert world_processes(instance.world_state)["process:flood"]["status"] == "failed"

    with pytest.raises(WorldStateError, match="unknown process"):
        apply_world_ops(instance, [{"op": "cancel_process", "process_id": "process:none"}])


def test_due_process_settles_deterministically_on_time_advance() -> None:
    instance = make_instance()
    _start_ritual(instance, due_minutes=840)
    apply_world_ops(instance, [
        {"op": "start_process", "process_id": "process:siege", "kind": "siege",
         "due_at": {"day": 1, "minute": 600}},
    ])

    summary = advance_world_time(instance, 840)
    assert [item["process_id"] for item in summary["processes"]] == [
        "process:siege", "process:clearing-ritual",
    ]
    assert world_processes(instance.world_state)["process:clearing-ritual"]["status"] == "completed"
    assert world_processes(instance.world_state)["process:siege"]["status"] == "completed"

    # 结算幂等：再次推进不再重复结算。
    again = advance_world_time(instance, 10)
    assert again["processes"] == []


def test_process_without_due_at_runs_until_explicitly_settled() -> None:
    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "start_process", "process_id": "process:manhunt", "kind": "police_search"},
    ])
    for _ in range(3):
        summary = advance_world_time(instance, 600)
        assert summary["processes"] == []
    assert world_processes(instance.world_state)["process:manhunt"]["status"] == "running"
    apply_world_ops(instance, [{"op": "complete_process", "process_id": "process:manhunt"}])
    assert world_processes(instance.world_state)["process:manhunt"]["status"] == "completed"


def test_cancelled_process_is_never_auto_settled() -> None:
    instance = make_instance()
    _start_ritual(instance, due_minutes=840)
    apply_world_ops(instance, [{"op": "cancel_process", "process_id": "process:clearing-ritual"}])
    summary = advance_world_time(instance, 840)
    assert summary["processes"] == []
    assert world_processes(instance.world_state)["process:clearing-ritual"]["status"] == "cancelled"


def test_due_processes_reader_orders_stably_and_ignores_garbage() -> None:
    instance = make_instance()
    _start_ritual(instance, due_minutes=840)
    assert [item["process_id"] for item in due_processes(
        instance.world_state, target_minutes=900,
    )] == ["process:clearing-ritual"]
    instance.world_state["processes"]["garbage"] = "not-a-dict"
    assert due_processes(instance.world_state, target_minutes=900) != []


def test_process_batch_is_atomic() -> None:
    instance = make_instance()
    with pytest.raises(WorldStateError):
        apply_world_ops(instance, [
            {"op": "start_process", "process_id": "process:ritual", "kind": "ritual"},
            {"op": "cancel_process", "process_id": "process:ghost"},
        ])
    assert world_processes(instance.world_state) == {}


def test_process_container_bound_is_enforced() -> None:
    instance = make_instance()
    for index in range(MAX_PROCESSES):
        instance.world_state["processes"][f"process:p{index}"] = {
            "process_id": f"process:p{index}", "kind": "ritual", "status": "running",
            "participants": [], "location": None,
            "started_at": {"day": 1, "minute": 0}, "due_at": None,
            "visibility": "public", "source_ref": None,
        }
    with pytest.raises(WorldStateError, match=f"exceeds {MAX_PROCESSES} processes"):
        apply_world_ops(instance, [
            {"op": "start_process", "process_id": "process:overflow", "kind": "ritual"},
        ])


def test_started_process_is_reverted_by_whole_round_rollback() -> None:
    instance = make_instance()
    instance.solo_mode = True
    instance.players["p1"] = {"character_name": "Alice", "character_sheet": {"hp": 10}}
    asyncio.run(instance.start_round())
    asyncio.run(instance.add_action("p1", "仪式开始"))
    assert asyncio.run(instance.try_advance()) is True
    _start_ritual(instance)
    asyncio.run(instance.finish_judgment("祭坛亮起了微光。"))

    assert asyncio.run(instance.rollback_last_round()) is not None
    assert world_processes(instance.world_state) == {}
