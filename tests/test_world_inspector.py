"""World inspector 测试（WR-10，母方案 §103/§169）。

覆盖：GM 全量视图（含 source_ref 溯源与最近结算）、玩家视图（public-only、
溯源字段剥离、最近结算隐藏）、损坏容器降级、MemoryStore kind 汇总。
"""

from __future__ import annotations

import asyncio

from src.engine.game_instance import GameInstance
from src.engine.world.inspector import world_inspector
from src.engine.world_state import apply_world_ops
from src.memory.delta import MemoryStore


def make_instance() -> GameInstance:
    return GameInstance(game_key=("web", "wr2-inspector", "bot"))


def _populated_instance() -> GameInstance:
    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "register_entity", "entity_id": "npc:count", "kind": "npc",
         "visibility": "public", "source_ref": "adventure:castle"},
        {"op": "register_entity", "entity_id": "npc:secret_patron", "kind": "npc",
         "visibility": "gm", "source_ref": "gm:story"},
        {"op": "add_relation", "relation_id": "rel:east-harbor", "kind": "connects",
         "from_ref": "location:east", "to_ref": "location:harbor",
         "visibility": "public", "source_ref": "adventure:castle"},
        {"op": "set_fact", "key": "location:east.passable", "value": True},
        {"op": "start_process", "process_id": "process:ritual", "kind": "ritual",
         "visibility": "gm"},
    ])
    instance.last_world_events = [
        {"event_id": "ritual", "label": "仪式", "status": "applied"},
    ]
    return instance


def test_gm_view_shows_everything_with_provenance() -> None:
    instance = _populated_instance()
    view = world_inspector(instance, viewer_is_gm=True)

    assert view["viewer"] == "gm"
    assert view["revision"] == instance.world_state["revision"]
    assert {item["id"] for item in view["entities"]} == {"npc:count", "npc:secret_patron"}
    assert view["entities"][0]["source_ref"] == "adventure:castle"
    assert {item["id"] for item in view["relations"]} == {"rel:east-harbor"}
    assert {item["id"] for item in view["processes"]} == {"process:ritual"}
    assert view["fact_count"] == 1
    assert view["scheduled_event_count"] == 0
    assert view["recent_events"]


def test_player_view_is_public_only_and_strips_provenance() -> None:
    instance = _populated_instance()
    view = world_inspector(instance, viewer_is_gm=False)

    assert view["viewer"] == "player"
    assert {item["id"] for item in view["entities"]} == {"npc:count"}
    assert all("source_ref" not in item for item in view["entities"])
    assert all("source_ref" not in item for item in view["relations"])
    assert {item["id"] for item in view["processes"]} == set()  # gm 进程不可见
    assert view["recent_events"] == []
    # 玩家视图仍可见公开世界的 revision/clock（无隐藏事实内容）。
    assert view["revision"] == instance.world_state["revision"]


def test_inspector_degrades_silently_on_corrupt_world() -> None:
    instance = make_instance()
    instance.world_state = {"schema_version": 99, "facts": {}}
    view = world_inspector(instance, viewer_is_gm=True)
    assert view["entities"] == []
    assert view["revision"] == 0
    assert view["clock"] == {"day": 1, "minute": 0}


def test_memory_kind_summary_counts_by_classification(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.open()
    try:
        asyncio.run(store.apply_delta("gk", {
            "add": [{"entity": "桥", "relation": "world_event", "value": "destroyed"}],
            "memory_kind": "authoritative_world",
            "source_kind": "worldevent",
            "source_id": "evt:1",
            "world_revision": 4,
            "visibility": "public",
        }, 2))
        asyncio.run(store.apply_delta("gk", {
            "add": [{"entity": "铁匠", "relation": "记录", "value": "提到桥"}],
        }, 2))
        summary = store.kind_summary("gk")
        assert summary["authoritative_world"] == 1
        assert summary["soft"] == 1
        assert summary["legacy_soft"] == 0
        assert store.kind_summary("other-game")["authoritative_world"] == 0
    finally:
        store.close()
