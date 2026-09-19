"""Adventure v2 progress model (ADV2-06, 母方案 §122/§190).

非铁路式进度：不再是 v1 的 ``current_step_id`` 单指针，而是集合语义：

\`\`\`text
progress
├─ active_nodes            当前开放（等待玩家推进）的节点
├─ completed_nodes         已完成节点（可回流重访）
├─ completed_objectives    并行目标
├─ completed_milestones    里程碑
└─ history                 有界事件历史（最近 100 条）
\`\`\`

语义：

- **推进经 transition**：完成节点时，其 transitions 的目标在 gate 满足后
  进入 active（gate 评估见 ADV2-02；证据不足的分支不激活，不猜）；
- **回流安全**：目标已在 completed/active 中时 no-op（history 不重复）；
- **幂等**：重复完成同一节点只产生一次效果；
- **持久化形态是 plain dict**：存放位置由接线方决定（v2 走 application 层
  的 adventure progress state；v1 campaign 的 ruleset_state 路径原样保留，
  两者并存互不迁移——母方案 §71/§122 的 v1 兼容）。
"""

from __future__ import annotations

from typing import Any

from src.adventures.gates import GateError, gates_satisfied

MAX_HISTORY = 100


class ProgressError(ValueError):
    """A progress mutation is invalid against the graph: fail closed."""


def new_progress(graph: dict[str, Any]) -> dict[str, Any]:
    """Initial progress for one validated v2 graph."""

    start_nodes = list(graph.get("start_node_ids") or [])
    return {
        "active_nodes": start_nodes,
        "completed_nodes": [],
        "completed_objectives": [],
        "completed_milestones": [],
        "history": [
            {"kind": "node_activated", "id": node_id} for node_id in start_nodes
        ],
    }


def _append_history(progress: dict[str, Any], entry: dict[str, Any]) -> None:
    history = progress.setdefault("history", [])
    history.append(entry)
    if len(history) > MAX_HISTORY:
        del history[:-MAX_HISTORY]


def _node_map(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in graph.get("nodes", [])}


def complete_node(
    progress: dict[str, Any],
    graph: dict[str, Any],
    node_id: str,
    *,
    instance: Any = None,
    rules_evaluator: Any = None,
) -> list[str]:
    """Complete one active node; activate gate-satisfied successors.

    返回本次新激活的节点 id。节点不在 active 中（已完成/未激活/不存在）时
    抛 :class:`ProgressError`——进度推进必须是显式的合法动作，不是 no-op。
    """

    nodes = _node_map(graph)
    if node_id not in nodes:
        raise ProgressError(f"node does not exist: {node_id!r}")
    active = progress.setdefault("active_nodes", [])
    if node_id not in active:
        raise ProgressError(
            f"node is not active: {node_id!r} (active: {sorted(active)})"
        )
    active.remove(node_id)
    completed = progress.setdefault("completed_nodes", [])
    if node_id not in completed:
        completed.append(node_id)
    _append_history(progress, {"kind": "node_completed", "id": node_id})

    context = {"progress": progress}
    activated: list[str] = []
    node = nodes[node_id]
    for transition in node.get("transitions", []):
        target = str(transition.get("to") or "")
        if target in active or target in completed:
            continue  # 回流/并行边：目标已开放则 no-op。
        conditions = transition.get("conditions") or []
        try:
            satisfied = gates_satisfied(
                conditions, instance=instance, progress=progress,
                rules_evaluator=rules_evaluator,
            )
        except GateError:
            satisfied = False  # 坏 gate = 不放行（fail closed）。
        if satisfied:
            active.append(target)
            activated.append(target)
            _append_history(progress, {"kind": "node_activated", "id": target})
    return activated


def complete_objective(progress: dict[str, Any], objective_id: str) -> bool:
    completed = progress.setdefault("completed_objectives", [])
    if objective_id in completed:
        return False
    completed.append(objective_id)
    _append_history(progress, {"kind": "objective_completed", "id": objective_id})
    return True


def complete_milestone(progress: dict[str, Any], milestone_id: str) -> bool:
    completed = progress.setdefault("completed_milestones", [])
    if milestone_id in completed:
        return False
    completed.append(milestone_id)
    _append_history(progress, {"kind": "milestone_reached", "id": milestone_id})
    return True


__all__ = [
    "MAX_HISTORY",
    "ProgressError",
    "complete_milestone",
    "complete_node",
    "complete_objective",
    "new_progress",
]
