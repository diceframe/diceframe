"""WorldState v1 characterization tests（WR-00，施工单 §93）。

目的：在 World Runtime v2（entities / relations / processes / WorldEvent /
memory projection）动工之前，**冻结当前 v1 的跨切面行为契约**，让后续每个
WR PR 的 schema / migration 变更都有一张"改动前后必须等价（或有意更新）"
的基线网：

- registry 级 save → recover：世界真相 + 定时事件 + memory outbox 一起活过
  进程重启（不只是 codec 级 roundtrip）；
- 未来 schema 版本围栏：读端静默降级、写端 fail closed（WR-02 改的就是这条
  seam，届时这组断言会被有意更新）；
- ``project_visible_state`` 投影形状（viewer 标签 / 字段集合 / 公开过滤）；
- 世界结算**不产生** memory delta（WR-06 引入 projection 前的空耦合契约）；
- rollback 恢复世界事实与事件状态，但不触碰 adventure_binding / rule_id；
- world op batch summary 的形状（后续 WorldEvent receipts 要在其上叠加）。

不改 schema、不改产品行为：本文件只读、只断言当前 main 的真实行为。
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance, GameRegistry
from src.engine.memory_outbox import pending_memory_deliveries, queue_memory_delivery
from src.engine.world_events import advance_world_time
from src.engine.world_state import (
    WORLD_STATE_SCHEMA_VERSION,
    WorldStateError,
    apply_world_ops,
    fact_value,
    fresh_world_state,
    project_visible_state,
    world_clock,
    world_revision,
    world_scheduled_events,
)


def make_instance(**kwargs) -> GameInstance:
    return GameInstance(game_key=("web", "wr2-char", "bot"), **kwargs)


def populate_world(instance: GameInstance) -> None:
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "actor:p1.location", "value": "village_east"},
        {"op": "set_fact", "key": "gm:ritual.site", "value": "clearing", "visibility": "gm"},
        {"op": "advance_time", "minutes": 60},
        {"op": "schedule_event", "event_id": "night.ambush",
         "due_at": {"day": 1, "minute": 1200},
         "label": "夜里伏击",
         "ops": [{"op": "set_fact", "key": "world.alert", "value": "high"}]},
    ])


# ---------- registry 级持久化：世界 + 事件 + outbox 一起活过重启 ----------


def test_registry_roundtrip_preserves_world_events_and_outbox(tmp_path) -> None:
    instance = make_instance()
    instance.players["p1"] = {"character_name": "Alice", "character_sheet": {"hp": 10}}
    populate_world(instance)
    # 同一轮里既有世界写入，也有一个待投递 memory delta：两者互不干扰。
    queue_memory_delivery(
        instance,
        effect_group_id="grp-1",
        memory_delta={"summary": "村民提到了仪式"},
        round_number=instance.round_number,
    )
    registry = GameRegistry(tmp_path / "saves")
    registry.register(instance)
    asyncio.run(registry.save(instance))

    recovered_registry = GameRegistry(registry.save_dir)
    recovered_list = asyncio.run(recovered_registry.recover_all())
    assert len(recovered_list) == 1
    recovered = recovered_list[0]

    assert recovered.world_state == instance.world_state
    assert fact_value(recovered.world_state, "actor:p1.location") == "village_east"
    assert world_scheduled_events(recovered.world_state)["night.ambush"]["status"] == "pending"
    assert world_clock(recovered.world_state) == {"day": 1, "minute": 60}
    deliveries = pending_memory_deliveries(recovered)
    assert [item["id"] for item in deliveries] == ["memory:grp-1"]

    # 恢复后的实例继续可结算：到点事件正常触发且只触发一次。
    summary = advance_world_time(recovered, 1200 - 60)
    assert [item["event_id"] for item in summary["applied"]] == ["night.ambush"]
    assert fact_value(recovered.world_state, "world.alert") == "high"
    again = advance_world_time(recovered, 10)
    assert again["applied"] == [] and again["failed"] == []


# ---------- 未来 schema 版本围栏（WR-02 已把当前版本推进到 2）----------


def test_future_world_schema_reads_degrade_and_writes_fail_closed() -> None:
    future = fresh_world_state()
    future["schema_version"] = WORLD_STATE_SCHEMA_VERSION + 1
    future["revision"] = 7
    instance = make_instance()
    instance.world_state = future

    # 读端：损坏/未知容器一律按空世界降级，绝不猜、绝不抛。
    assert world_revision(instance.world_state) == 0
    assert world_clock(instance.world_state) == {"day": 1, "minute": 0}
    assert fact_value(instance.world_state, "actor:p1.location") is None
    assert world_scheduled_events(instance.world_state) == {}
    projected = project_visible_state(instance, viewer_uid="p1", viewer_is_gm=False)
    assert projected["facts"] == {} and projected["revision"] == 0

    # 写端：未知 schema fail closed，且原容器不被改写。
    with pytest.raises(WorldStateError):
        apply_world_ops(instance, [{"op": "set_fact", "key": "a.b", "value": 1}])
    assert instance.world_state["schema_version"] == WORLD_STATE_SCHEMA_VERSION + 1
    with pytest.raises(WorldStateError):
        advance_world_time(instance, 30)


# ---------- 玩家/GM 投影形状 ----------


def test_project_visible_state_payload_shape_is_frozen() -> None:
    instance = make_instance()
    populate_world(instance)

    player_view = project_visible_state(instance, viewer_uid="p1", viewer_is_gm=False)
    assert set(player_view) == {"schema_version", "viewer", "revision", "clock", "facts"}
    assert player_view["schema_version"] == WORLD_STATE_SCHEMA_VERSION
    assert player_view["viewer"] == "player:p1"
    assert "actor:p1.location" in player_view["facts"]
    assert "gm:ritual.site" not in player_view["facts"]
    assert all(
        fact["visibility"] == "public" for fact in player_view["facts"].values()
    )

    gm_view = project_visible_state(instance, viewer_is_gm=True)
    assert gm_view["viewer"] == "gm"
    assert "gm:ritual.site" in gm_view["facts"]
    # 投影是深拷贝：改投影不得污染权威容器。
    player_view["facts"]["actor:p1.location"]["value"] = "tampered"
    assert fact_value(instance.world_state, "actor:p1.location") == "village_east"


# ---------- 世界结算与 memory 的空耦合（WR-06 前契约）----------


def test_world_writes_produce_no_memory_deltas() -> None:
    instance = make_instance()
    populate_world(instance)
    assert pending_memory_deliveries(instance) == []
    assert instance.economy.get("external_effects_outbox", []) == []

    summary = advance_world_time(instance, 1200)
    assert summary["applied"]
    assert pending_memory_deliveries(instance) == []
    # 已有 pending delta 不因世界写入被修改/清空。
    queued = queue_memory_delivery(
        instance, effect_group_id="grp-2",
        memory_delta={"summary": "x"}, round_number=1,
    )
    assert queued is not None
    apply_world_ops(instance, [{"op": "set_fact", "key": "a.b", "value": True}])
    advance_world_time(instance, 10)
    assert [item["id"] for item in pending_memory_deliveries(instance)] == ["memory:grp-2"]


# ---------- rollback / binding 隔离 ----------


def test_rollback_restores_world_but_not_bindings() -> None:
    instance = make_instance()
    instance.world_id = "world-1"
    instance.solo_mode = True
    instance.players["p1"] = {"character_name": "Alice", "character_sheet": {"hp": 10}}
    instance.bind_ruleset_runtime({
        "runtime_id": "core:dnd2024",
        "runtime_version": 1,
        "content_version": "2024-01",
        "state_schema_version": 1,
    })
    assert instance.bind_adventure({
        "adventure_id": "adv-1", "version": "1", "format": "v1",
        "content_digest": "deadbeef", "world_id": "world-1",
    })
    instance.rule_id = "coc7"
    asyncio.run(instance.start_round())
    asyncio.run(instance.add_action("p1", "点火把"))
    assert asyncio.run(instance.try_advance()) is True
    apply_world_ops(instance, [{"op": "set_fact", "key": "torch.lit", "value": True}])
    # 完成判定写入本轮 log（含判定入口的世界快照），随后才可整轮回滚。
    asyncio.run(instance.finish_judgment("火把点燃，影子在墙上晃动。"))
    assert instance.log, "判定完成后应有本轮 log entry"

    restored_round = asyncio.run(instance.rollback_last_round())
    assert restored_round is not None
    assert fact_value(instance.world_state, "torch.lit") is None
    # binding 与规则选择不是"这一轮结算出来的东西"：rollback 不触碰。
    assert instance.adventure_binding["adventure_id"] == "adv-1"
    assert instance.ruleset_runtime["id"] == "core:dnd2024"
    assert instance.rule_id == "coc7"


# ---------- batch summary 形状 ----------


def test_world_op_batch_summary_shape_is_frozen() -> None:
    instance = make_instance()
    populate_world(instance)
    before = deepcopy(instance.world_state)
    summary = apply_world_ops(instance, [
        {"op": "set_fact", "key": "door.open", "value": False},
        {"op": "remove_fact", "key": "door.open"},
        {"op": "set_fact", "key": "a.b", "value": 1},
    ])
    # WR-05 起 summary 增加 events（WorldEvent receipts，不持久化）。
    assert set(summary) == {"revision", "clock", "applied", "events"}
    assert summary["revision"] == world_revision(before) + 1
    assert summary["clock"] == world_clock(instance.world_state)
    assert [item["op"] for item in summary["applied"]] == [
        "set_fact", "remove_fact", "set_fact",
    ]
    assert instance.world_state["revision"] == summary["revision"]
