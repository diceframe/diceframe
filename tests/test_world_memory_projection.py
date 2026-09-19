"""World Memory projection 测试（WR-06，母方案 §21/§22/§69/§99）。

覆盖：
- 确定性晋升策略：白名单外的 receipt 不产生记忆（fact/time/事件簿记）；
- 候选 delta 的 provenance（authoritative_world / worldevent / event_id /
  world_revision / visibility）；
- outbox 幂等：同一 receipt 重复投影只产生一条投递；
- MemoryStore：迁移加列、LLM delta 默认 soft、authoritative delta 盖章、
  旧行（NULL）读取按 legacy_soft 分类；
- Memory 永不反写 WorldState。
"""

from __future__ import annotations

import asyncio

import pytest

from src.engine.game_instance import GameInstance
from src.engine.world.memory_projection import (
    PROMOTED_EVENT_KINDS,
    queue_world_memory,
    world_memory_delta,
)
from src.engine.world_state import apply_world_ops, fact_value
from src.memory.delta import MEMORY_KINDS, MemoryStore, _delta_provenance


def make_instance() -> GameInstance:
    return GameInstance(game_key=("web", "wr2-memory", "bot"))


def _receipt(kind: str, subject: str | None, *, event_id: str = "evt:000001:0:x",
             visibility: str = "public", revision: int = 7,
             summary: str = "") -> dict:
    return {
        "event_id": event_id,
        "kind": kind,
        "revision": revision,
        "clock": {"day": 1, "minute": 60},
        "source_round": 3,
        "visibility": visibility,
        "subject": subject,
        "summary": summary,
    }


# ---------- 确定性晋升策略 ----------


def test_promotion_whitelist_covers_world_structure_changes() -> None:
    assert set(PROMOTED_EVENT_KINDS) == {
        "entity_registered", "entity_retired",
        "relation_added", "relation_status_changed", "relation_removed",
        "process_settled",
    }


@pytest.mark.parametrize("kind", [
    "fact_set", "fact_removed", "time_advanced",
    "event_scheduled", "event_cancelled", "event_settled",
])
def test_fact_and_bookkeeping_receipts_are_not_promoted(kind: str) -> None:
    assert world_memory_delta(_receipt(kind, "a.b")) is None


def test_promoted_receipt_becomes_provenance_stamped_delta() -> None:
    delta = world_memory_delta(
        _receipt(
            "relation_status_changed", "rel:east-harbor", visibility="gm",
            summary="kind=alliance status=broken",
        ),
    )
    assert delta is not None
    assert delta["add"] == [{
        "entity": "rel:east-harbor",
        "relation": "world_event",
        # FIX-05 §7.5：确定性语义（事件种类 / 主体 / relation·process 状态 /
        # 来源 revision / event id）全部保留，不再只是 "kind @ evt"。
        "value": (
            "relation_status_changed · rel:east-harbor · kind=alliance status=broken"
            " · rev 7 · @ evt:000001:0:x"
        ),
    }]
    assert delta["memory_kind"] == "authoritative_world"
    assert delta["source_kind"] == "worldevent"
    assert delta["source_id"] == "evt:000001:0:x"
    assert delta["world_revision"] == 7
    # GM 私密世界事件 → GM 私密记忆；玩家上下文不得召回。
    assert delta["visibility"] == "gm"


def test_receipt_summary_carries_deterministic_semantics() -> None:
    """§7.5：receipt.summary 是确定性 "k=v" 语义，不依赖 LLM。"""

    from src.engine.world.receipts import receipts_from_applied

    receipts = receipts_from_applied(
        [
            {"op": "add_relation", "relation_id": "rel:harbor", "kind": "alliance",
             "visibility": "public"},
            {"op": "complete_process", "process_id": "ritual", "status": "completed",
             "visibility": "gm"},
        ],
        revision=12,
        clock={"day": 2, "minute": 0},
        source_round=4,
    )

    assert receipts[0]["summary"] == "kind=alliance"
    assert receipts[1]["summary"] == "status=completed"
    delta = world_memory_delta(receipts[1])
    assert delta is not None
    assert "status=completed" in delta["add"][0]["value"]
    assert "rev 12" in delta["add"][0]["value"]


def test_receipt_without_subject_is_not_promoted() -> None:
    assert world_memory_delta(_receipt("entity_registered", None)) is None


# ---------- outbox 幂等 ----------


def test_queue_world_memory_is_idempotent_per_event() -> None:
    instance = make_instance()
    receipt = _receipt("entity_registered", "npc:count")
    first = queue_world_memory(instance, [receipt], round_number=3)
    assert len(first) == 1
    assert first[0]["id"] == "memory:worldevent:evt:000001:0:x"
    again = queue_world_memory(instance, [receipt], round_number=3)
    assert again == first
    deliveries = instance.economy["external_effects_outbox"]
    assert len(deliveries) == 1
    # 投递负载携带 provenance 与 GM 可见性。
    assert deliveries[0]["payload"]["memory_kind"] == "authoritative_world"


def test_queued_memory_never_touches_world_state() -> None:
    instance = make_instance()
    apply_world_ops(instance, [{"op": "set_fact", "key": "a.b", "value": 1}])
    before = instance.world_state["revision"]
    queue_world_memory(
        instance, [_receipt("entity_retired", "npc:count", revision=9)], round_number=3,
    )
    assert instance.world_state["revision"] == before
    assert fact_value(instance.world_state, "npc:count") is None


# ---------- MemoryStore 集成 ----------


def _open_store(tmp_path) -> MemoryStore:
    store = MemoryStore(tmp_path / "memory.db")
    store.open()
    return store


def test_store_stamps_provenance_on_new_rows(tmp_path) -> None:
    store = _open_store(tmp_path)
    try:
        asyncio.run(store.apply_delta("gk", {
            "add": [{"entity": "rel:east-harbor", "relation": "world_event", "value": "severed"}],
            "memory_kind": "authoritative_world",
            "source_kind": "worldevent",
            "source_id": "evt:000001:0:x",
            "world_revision": 7,
            "visibility": "gm",
        }, 3))
        row = store._conn.execute(
            "SELECT memory_kind, source_kind, source_id, world_revision, visibility "
            "FROM memory_entries WHERE game_key='gk'",
        ).fetchone()
        assert tuple(row) == ("authoritative_world", "worldevent", "evt:000001:0:x", 7, "gm")
    finally:
        store.close()


def test_plain_llm_delta_defaults_to_soft(tmp_path) -> None:
    store = _open_store(tmp_path)
    try:
        asyncio.run(store.apply_delta("gk", {
            "add": [{"entity": "铁匠", "relation": "记录", "value": "提到桥坏了"}],
        }, 2))
        row = store._conn.execute(
            "SELECT memory_kind, source_kind FROM memory_entries WHERE game_key='gk'",
        ).fetchone()
        assert tuple(row) == ("soft", None)
    finally:
        store.close()


def test_legacy_rows_without_kind_read_as_legacy_soft(tmp_path) -> None:
    store = _open_store(tmp_path)
    try:
        asyncio.run(store.apply_delta("gk", {
            "add": [{"entity": "老人", "relation": "记录", "value": "说过仪式的事"}],
        }, 1))
        # 模拟升级前的旧行：provenance 列为 NULL。
        store._conn.execute(
            "UPDATE memory_entries SET memory_kind=NULL, source_kind=NULL WHERE game_key='gk'",
        )
        store._conn.commit()
        row = store._conn.execute(
            "SELECT memory_kind FROM memory_entries WHERE game_key='gk'",
        ).fetchone()
        assert row["memory_kind"] is None
        from src.memory.delta import memory_kind_of
        assert memory_kind_of(row["memory_kind"]) == "legacy_soft"
    finally:
        store.close()


def test_delta_provenance_degrades_unknown_kind_to_soft() -> None:
    provenance = _delta_provenance({"memory_kind": "super_authority", "visibility": "nope"})
    assert provenance["memory_kind"] == "soft"
    assert provenance["visibility"] is None
    assert set(_delta_provenance({})) == {
        "memory_kind", "source_kind", "source_id", "world_revision", "visibility",
    }


def test_memory_kind_vocabulary_is_closed() -> None:
    assert set(MEMORY_KINDS) == {"authoritative_world", "soft", "legacy_soft"}
