"""Adventure v2 progress model 测试（ADV2-06，母方案 §122/§190）。

覆盖：初始进度（多入口）、节点推进与 gate 分支激活、回流/并行边 no-op、
幂等与非法推进 fail closed、objectives/milestones、有界历史、gate 证据
不足不放行。
"""

from __future__ import annotations

import pytest

from src.adventures.graph_v2 import validate_graph_v2
from src.adventures.progress import (
    MAX_HISTORY,
    ProgressError,
    complete_milestone,
    complete_node,
    complete_objective,
    new_progress,
)


def _graph(**overrides: object) -> dict:
    record: dict = {
        "id": "castle_quest",
        "format": "diceframe:adventure-graph-v2",
        "chapters": [{"id": "ch1", "name": "第一章"}],
        "nodes": [
            {"id": "gate", "type": "scene", "chapter_id": "ch1",
             "transitions": [{"to": "bridge"}, {"to": "tunnel", "conditions": [
                 {"type": "world.fact_equals", "key": "gate.open", "value": True},
             ]}]},
            {"id": "bridge", "type": "encounter", "chapter_id": "ch1",
             "transitions": [{"to": "throne"}]},
            {"id": "tunnel", "type": "scene", "chapter_id": "ch1", "optional": True,
             "transitions": [{"to": "throne"}]},
            {"id": "throne", "type": "scene", "chapter_id": "ch1",
             "transitions": [{"to": "gate"}]},
        ],
        "objectives": [{"id": "obj_crown", "name": "取回王冠", "node_ids": ["throne"]}],
        "milestones": [{"id": "mile_castle", "name": "进入城堡", "node_ids": ["gate"]}],
        "start_node_ids": ["gate"],
    }
    record.update(overrides)
    return validate_graph_v2(record)


def test_new_progress_activates_start_nodes() -> None:
    graph = _graph()
    graph["start_node_ids"] = ["gate", "tunnel"]
    progress = new_progress(graph)
    assert progress["active_nodes"] == ["gate", "tunnel"]
    assert progress["completed_nodes"] == []
    # 初始激活也有历史（诊断可追溯）。
    assert progress["history"] == [
        {"kind": "node_activated", "id": "gate"},
        {"kind": "node_activated", "id": "tunnel"},
    ]


def test_complete_node_activates_unconditional_successors() -> None:
    graph = _graph()
    progress = new_progress(graph)
    activated = complete_node(progress, graph, "gate")
    assert activated == ["bridge"]          # tunnel 的 gate 未满足
    assert progress["completed_nodes"] == ["gate"]
    assert progress["active_nodes"] == ["bridge"]
    # history[0] 是初始激活（new_progress 记录），完成事件在其后。
    assert progress["history"][1] == {"kind": "node_completed", "id": "gate"}
    assert progress["history"][2] == {"kind": "node_activated", "id": "bridge"}


def test_gated_branch_activates_when_world_fact_satisfied() -> None:
    from src.engine.game_instance import GameInstance
    from src.engine.world_state import apply_world_ops

    instance = GameInstance(game_key=("web", "adv2-progress", "bot"))
    apply_world_ops(instance, [{"op": "set_fact", "key": "gate.open", "value": True}])
    graph = _graph()
    progress = new_progress(graph)
    activated = complete_node(progress, graph, "gate", instance=instance)
    assert sorted(activated) == ["bridge", "tunnel"]  # gate 满足：双分支激活


def test_backflow_and_parallel_edges_are_noop() -> None:
    graph = _graph()
    progress = new_progress(graph)
    complete_node(progress, graph, "gate")
    complete_node(progress, graph, "bridge")
    complete_node(progress, graph, "throne")
    # throne → gate 回流：gate 已在 completed，重激活为 no-op（不重复激活）。
    assert progress["active_nodes"] == []
    activations = [
        entry["id"] for entry in progress["history"]
        if entry["kind"] == "node_activated" and entry["id"] == "gate"
    ]
    assert activations == ["gate"]  # 只有初始激活一次


def test_completing_inactive_node_fails_closed() -> None:
    graph = _graph()
    progress = new_progress(graph)
    with pytest.raises(ProgressError, match="not active"):
        complete_node(progress, graph, "throne")
    complete_node(progress, graph, "gate")
    with pytest.raises(ProgressError, match="not active"):
        complete_node(progress, graph, "gate")  # 重复推进同一节点
    with pytest.raises(ProgressError, match="does not exist"):
        complete_node(progress, graph, "ghost")


def test_objectives_and_milestones_are_idempotent() -> None:
    graph = _graph()
    progress = new_progress(graph)
    assert complete_objective(progress, "obj_crown", graph=graph) is True
    assert complete_objective(progress, "obj_crown", graph=graph) is False
    assert complete_milestone(progress, "mile_castle", graph=graph) is True
    assert complete_milestone(progress, "mile_castle", graph=graph) is False


def test_unknown_objective_and_milestone_fail_closed() -> None:
    """FIX-04 §6.3：进度只能推进图上真实存在的 objective / milestone。"""

    graph = _graph()
    progress = new_progress(graph)

    with pytest.raises(ProgressError, match="objective does not exist"):
        complete_objective(progress, "obj_ghost", graph=graph)
    with pytest.raises(ProgressError, match="milestone does not exist"):
        complete_milestone(progress, "mile_ghost", graph=graph)


def test_history_is_bounded() -> None:
    graph = _graph()
    progress = new_progress(graph)
    for index in range(MAX_HISTORY + 20):
        progress["active_nodes"] = ["gate"]
        complete_node(progress, graph, "gate")
        progress["completed_nodes"].clear()
    assert len(progress["history"]) == MAX_HISTORY
