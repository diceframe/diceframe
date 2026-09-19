"""WorldEvent receipts 测试（WR-05，母方案 §18/§19/§81）。

覆盖：receipt 的确定性稳定 ID、可见性派生、契约校验、随事务 summary 返回、
advance_world_time 聚合嵌套子事务 receipts、receipts 不写入持久化容器、
不是 EventBus（无订阅/无副作用，只是数据）。
"""

from __future__ import annotations

import pytest

from src.engine.game_instance import GameInstance
from src.engine.world.contracts import validate_world_event_record
from src.engine.world.receipts import receipts_from_applied
from src.engine.world_events import advance_world_time
from src.engine.world_state import (
    WorldStateError,
    apply_world_ops,
    world_entities,
)


def make_instance() -> GameInstance:
    return GameInstance(game_key=("web", "wr2-receipts", "bot"))


# ---------- 工厂层 ----------


def test_receipt_ids_are_deterministic_and_position_scoped() -> None:
    receipts = receipts_from_applied(
        [{"op": "set_fact", "key": "a.b", "visibility": "public"},
         {"op": "remove_fact", "key": "c.d", "visibility": "gm"}],
        revision=12, clock={"day": 1, "minute": 30}, source_round=4,
    )
    assert [item["event_id"] for item in receipts] == [
        "evt:000012:0:fact_set", "evt:000012:1:fact_removed",
    ]
    # 同输入同输出（重试同批产生相同凭据）。
    again = receipts_from_applied(
        [{"op": "set_fact", "key": "a.b", "visibility": "public"},
         {"op": "remove_fact", "key": "c.d", "visibility": "gm"}],
        revision=12, clock={"day": 1, "minute": 30}, source_round=4,
    )
    assert again == receipts


def test_receipt_visibility_follows_the_op() -> None:
    receipts = receipts_from_applied(
        [
            {"op": "set_fact", "key": "public.thing", "visibility": "public"},
            {"op": "set_fact", "key": "gm:secret", "visibility": "gm"},
            {"op": "advance_time", "minutes": 10},
            {"op": "schedule_event", "event_id": "night.ambush"},
            {"op": "register_entity", "entity_id": "npc:x", "visibility": "gm"},
            {"op": "start_process", "process_id": "process:x", "visibility": "public"},
        ],
        revision=1, clock={"day": 1, "minute": 0}, source_round=0,
    )
    visibilities = [item["visibility"] for item in receipts]
    assert visibilities == ["public", "gm", "public", "gm", "gm", "public"]
    assert receipts[1]["subject"] == "gm:secret"
    assert receipts[2]["subject"] is None
    assert receipts[3]["subject"] == "night.ambush"


def test_receipts_pass_the_world_event_contract() -> None:
    receipts = receipts_from_applied(
        [{"op": "register_entity", "entity_id": "npc:count", "visibility": "public"}],
        revision=3, clock={"day": 2, "minute": 10}, source_round=7,
    )
    for receipt in receipts:
        validate_world_event_record(receipt)
    assert receipts[0]["kind"] == "entity_registered"
    assert receipts[0]["revision"] == 3
    assert receipts[0]["clock"] == {"day": 2, "minute": 10}
    assert receipts[0]["source_round"] == 7


def test_unmapped_op_kind_fails_closed() -> None:
    with pytest.raises(ValueError, match="no WorldEvent receipt mapping"):
        receipts_from_applied(
            [{"op": "mystery_op"}], revision=1, clock={"day": 1, "minute": 0}, source_round=0,
        )


# ---------- 写入口集成 ----------


def test_apply_world_ops_returns_receipts_without_persisting_them() -> None:
    instance = make_instance()
    summary = apply_world_ops(instance, [
        {"op": "register_entity", "entity_id": "npc:count", "kind": "npc",
         "source_ref": "module:castle"},
        {"op": "set_fact", "key": "actor:count.location", "value": "castle",
         "visibility": "gm"},
    ])
    assert [item["kind"] for item in summary["events"]] == [
        "entity_registered", "fact_set",
    ]
    # receipts 是凭据不是容器：world_state 持久化形状不含 events。
    assert "events" not in instance.world_state
    assert set(world_entities(instance.world_state)) == {"npc:count"}


def test_failed_batch_produces_no_receipts() -> None:
    instance = make_instance()
    with pytest.raises(WorldStateError):
        apply_world_ops(instance, [
            {"op": "set_fact", "key": "a.b", "value": 1},
            {"op": "remove_fact", "key": "missing.thing"},
        ])
    # 整批失败：没有 revision 推进，也没有任何可观测 receipt。
    assert instance.world_state["revision"] == 0


# ---------- advance_world_time 聚合 ----------


def test_advance_aggregates_receipts_across_nested_settlements() -> None:
    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "advance_time", "minutes": 600},
        {"op": "schedule_event", "event_id": "night.ambush",
         "due_at": {"day": 1, "minute": 700},
         "ops": [{"op": "set_fact", "key": "world.alert", "value": "high"}]},
        {"op": "start_process", "process_id": "process:ritual", "kind": "ritual",
         "due_at": {"day": 1, "minute": 650}},
    ])
    summary = advance_world_time(instance, 200)
    kinds = [item["kind"] for item in summary["events"]]
    # 推进本身 + 事件结算（fact_set + event_settled）+ 进程结算。
    assert kinds == [
        "time_advanced", "fact_set", "event_settled", "process_settled",
    ]
    # 嵌套子事务 receipts 的 event_id 使用各自 revision，跨批不碰撞。
    event_ids = [item["event_id"] for item in summary["events"]]
    assert len(set(event_ids)) == len(event_ids)
    # 聚合结果仍未写进持久化容器。
    assert "events" not in instance.world_state
