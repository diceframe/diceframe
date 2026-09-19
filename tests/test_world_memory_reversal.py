"""World Memory reversal 测试（WR-07，母方案 §100）。

目标：**无幽灵记忆**——世界变化被 rollback / swipe / abort+重放 撤销或重做
时，authoritative_world 记忆的出入与之一一对应：

- 整轮回滚：未投递的 world 记忆 superseded（不投递）；已投递的进入
  reversal_pending，经 journal 逆向恢复（"NPC 已死但记忆说他死了"不出现）；
- swipe 分支切断：同一撤销机制按目标轮生效；
- abort 判定后重放：相同 event_id 确定性重现 → superseded 投递复活 →
  重放轮的记忆不缺失；
- 世界真相本身随 pre_world_state 快照恢复（与记忆撤销同批完成）。
"""

from __future__ import annotations

import asyncio

import pytest

from src.engine.economy import reverse_round_economy
from src.engine.game_instance import GameInstance
from src.engine.memory_outbox import (
    complete_memory_delivery,
    complete_memory_reversal,
    pending_memory_deliveries,
    pending_memory_reversals,
)
from src.engine.world.memory_projection import queue_world_memory
from src.engine.world_state import apply_world_ops, fact_value
from src.memory.delta import MemoryStore


def make_instance() -> GameInstance:
    return GameInstance(game_key=("web", "wr2-reversal", "bot"))


def _receipt(kind: str, subject: str, *, event_id: str) -> dict:
    return {
        "event_id": event_id,
        "kind": kind,
        "revision": 5,
        "clock": {"day": 1, "minute": 60},
        "source_round": 2,
        "visibility": "public",
        "subject": subject,
        "summary": "",
    }


def _drain_into_store(instance: GameInstance, store: MemoryStore) -> None:
    """按生产路径把 pending 投递写入 MemoryStore 并回执。"""

    for delivery in list(pending_memory_deliveries(instance)):
        asyncio.run(store.apply_economy_delta(
            str(instance.game_key),
            str(delivery["id"]),
            delivery["payload"],
            int(delivery.get("round") or 0),
        ))
        complete_memory_delivery(instance, str(delivery["id"]))


def test_rollback_supersedes_pending_and_reverses_delivered_world_memory(tmp_path) -> None:
    instance = make_instance()
    instance.solo_mode = True
    instance.players["p1"] = {"character_name": "Alice", "character_sheet": {"hp": 10}}
    store = MemoryStore(tmp_path / "memory.db")
    store.open()
    try:
        # 第 1 轮：正常完成判定（log 里有真实历史轮）。
        asyncio.run(instance.start_round())
        asyncio.run(instance.add_action("p1", "赶路去码头"))
        assert asyncio.run(instance.try_advance()) is True
        asyncio.run(instance.finish_judgment("你们沿着大路到了桥头。"))

        # 第 2 轮判定阶段：桥被摧毁（世界事实 + world 记忆投递）。
        asyncio.run(instance.add_action("p1", "炸桥"))
        assert asyncio.run(instance.try_advance()) is True
        apply_world_ops(instance, [
            {"op": "set_fact", "key": "location:bridge.passable", "value": False},
            {"op": "add_relation", "relation_id": "rel:bridge-road", "kind": "connects",
             "from_ref": "location:east", "to_ref": "location:harbor"},
        ])
        # 注意：fact_set 按确定性策略不晋升（当前事实的 authority 是 WorldState）；
        # 记忆侧的代表是 relation / process 生命周期 receipt。
        receipts = [
            _receipt("process_settled", "process:bridge-collapse", event_id="evt:000002:0:process_settled"),
            _receipt("relation_added", "rel:bridge-road", event_id="evt:000002:1:relation_added"),
        ]
        queue_world_memory(instance, receipts, round_number=instance.round_number)
        _drain_into_store(instance, store)
        assert pending_memory_deliveries(instance) == []
        # 记账完成后正常提交本轮（pre_world_state 已在判定入口拍下）。
        asyncio.run(instance.finish_judgment("一声巨响，桥塌了。"))

        def recalled(entity_kw: str) -> list[tuple[str, str]]:
            rows = store.recall(
            str(instance.game_key), [entity_kw], limit=10, viewer_is_gm=True,
        )
            return [(row["entity"], row["value"]) for row in rows]

        assert recalled("rel:bridge-road"), "投递后记忆应可召回"

        # 整轮回滚到第 2 轮：世界真相恢复 + 已投递世界记忆进入撤销。
        restored = asyncio.run(instance.rollback_last_round())
        assert restored is not None
        assert fact_value(instance.world_state, "location:bridge.passable") is None
        reversals = pending_memory_reversals(instance)
        assert [item["id"] for item in reversals] == [
            "memory:worldevent:evt:000002:0:process_settled",
            "memory:worldevent:evt:000002:1:relation_added",
        ]

        # 撤销投递：journal 逆向，记忆回到投递前状态。
        for delivery in reversals:
            assert asyncio.run(store.reverse_economy_delta(
                str(instance.game_key), str(delivery["id"]),
            ))
            complete_memory_reversal(instance, str(delivery["id"]))
        assert pending_memory_reversals(instance) == []
        assert recalled("rel:bridge-road") == [], "撤销后不得残留幽灵记忆"
    finally:
        store.close()


def test_swipe_branch_cut_reverses_world_memory_of_the_cut_round(tmp_path) -> None:
    instance = make_instance()
    store = MemoryStore(tmp_path / "memory.db")
    store.open()
    try:
        apply_world_ops(instance, [{"op": "register_entity", "entity_id": "npc:count", "kind": "npc"}])
        queue_world_memory(
            instance,
            [_receipt("entity_registered", "npc:count", event_id="evt:000001:0:entity_registered")],
            round_number=1,
        )
        _drain_into_store(instance, store)

        # swipe 分支切断第 1 轮（与 SwipeGenerator 相同的撤销路径）。
        reverse_round_economy(instance, 1)
        reversals = pending_memory_reversals(instance)
        assert reversals
        for delivery in reversals:
            assert asyncio.run(store.reverse_economy_delta(
                str(instance.game_key), str(delivery["id"]),
            ))
            complete_memory_reversal(instance, str(delivery["id"]))
        rows = store.recall(
            str(instance.game_key), ["npc:count"], limit=10, viewer_is_gm=True,
        )
        assert rows == []
    finally:
        store.close()


def test_requeued_superseded_delivery_is_resurrected_after_replay() -> None:
    instance = make_instance()
    receipts = [
        _receipt("entity_registered", "npc:count", event_id="evt:000002:0:entity_registered"),
    ]
    queue_world_memory(instance, receipts, round_number=2)
    # abort+重放前世界被回滚：rollback era 将 pending 投递 superseded。
    reverse_round_economy(instance, 2)
    deliveries = instance.economy["external_effects_outbox"]
    assert deliveries[0]["status"] == "superseded"
    assert "payload" not in deliveries[0]

    # 重放轮确定性重现相同 event_id → 重新入队必须复活投递（幽灵缺失防线）。
    queue_world_memory(instance, receipts, round_number=2)
    assert deliveries[0]["status"] == "pending"
    assert deliveries[0]["payload"]["memory_kind"] == "authoritative_world"
    assert len(pending_memory_deliveries(instance)) == 1


def test_store_can_round_trip_the_world_memory_delivery(tmp_path) -> None:
    instance = make_instance()
    store = MemoryStore(tmp_path / "memory.db")
    store.open()
    try:
        queue_world_memory(
            instance,
            [_receipt("process_settled", "process:ritual", event_id="evt:000003:0:process_settled")],
            round_number=3,
        )
        _drain_into_store(instance, store)
        row = store._conn.execute(
            "SELECT memory_kind, visibility, world_revision FROM memory_entries "
            "WHERE game_key=? AND entity='process:ritual'",
            (str(instance.game_key),),
        ).fetchone()
        assert tuple(row) == ("authoritative_world", "public", 5)
    finally:
        store.close()
