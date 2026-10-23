"""Adventure v2 progress model (ADV2-06, 母方案 §122/§190).

非铁路式进度：不再是 v1 的 ``current_step_id`` 单指针，而是集合语义：

\`\`\`text
progress
├─ active_nodes            当前开放（等待玩家推进）的节点
├─ completed_nodes         已完成节点（可回流重访）
├─ completed_objectives    并行目标
├─ completed_milestones    里程碑
├─ ending_node_id          已达成的结局节点（仅结束后存在）
└─ history                 有界事件历史（最近 100 条）
\`\`\`

语义：

- **推进经 transition**：完成节点时，其 transitions 的目标在 gate 满足后
  进入 active（gate 评估见 ADV2-02；证据不足的分支不激活，不猜）；
- **回流安全**：目标已在 completed/active 中时 no-op（history 不重复）；
- **幂等**：重复完成同一节点只产生一次效果；
- **结局即终局**（GAL 轨道 P4）：完成 ending 节点记录 ``ending_node_id``，
  关闭其余开放节点与选择请求；此后任何节点都不能再完成；
- **持久化形态是 plain dict**：存放位置由接线方决定（v2 走 application 层
  的 adventure progress state；v1 campaign 的 ruleset_state 路径原样保留，
  两者并存互不迁移——母方案 §71/§122 的 v1 兼容）。
"""

from __future__ import annotations

from typing import Any

from src.adventures.gates import GateError, gates_satisfied
from src.adventures.graph_v2 import ENDING_NODE_TYPE

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


def ending_node_id(progress: Any) -> str:
    """The reached ending node id, or ``""`` while the adventure continues."""

    value = progress.get("ending_node_id") if isinstance(progress, dict) else None
    return value if isinstance(value, str) else ""


def _append_history(progress: dict[str, Any], entry: dict[str, Any]) -> None:
    history = progress.setdefault("history", [])
    history.append(entry)
    if len(history) > MAX_HISTORY:
        del history[:-MAX_HISTORY]


def _node_map(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in graph.get("nodes", [])}


def validate_choice_state(progress: dict[str, Any], graph: dict[str, Any]) -> None:
    if not isinstance(progress, dict):
        raise ProgressError('adventure progress is invalid')
    choices = {node['id']: {transition['choice_id'] for transition in node['transitions']} for node in graph['nodes'] if node.get('choice_mode') == 'exclusive'}
    selected = progress.get('selected_choices', {})
    requests = progress.get('choice_requests', [])
    if not isinstance(selected, dict) or len(selected) > 256 or any(node not in choices or not isinstance(choice, str) or choice not in choices[node] for node, choice in selected.items()):
        raise ProgressError('saved selected choices are invalid')
    if any(node in choices and node not in selected for node in progress.get('completed_nodes', [])):
        raise ProgressError('completed exclusive node has no recorded choice')
    if not isinstance(requests, list) or len(requests) > 256:
        raise ProgressError('saved choice requests are invalid')
    for row in requests:
        if not isinstance(row, dict) or set(row) != {'node_id', 'choice_id', 'actor_uid'} or any(not isinstance(value, str) for value in row.values()):
            raise ProgressError('saved choice requests are invalid')
        if row['node_id'] not in choices or row['choice_id'] not in choices[row['node_id']] or not row['actor_uid']:
            raise ProgressError('saved choice request does not match the graph')


def complete_node(
    progress: dict[str, Any],
    graph: dict[str, Any],
    node_id: str,
    *,
    instance: Any = None,
    rules_evaluator: Any = None,
    choice_id: str = "",
) -> list[str]:
    """Complete one active node; activate gate-satisfied successors.

    返回本次新激活的节点 id。节点不在 active 中（已完成/未激活/不存在）时
    抛 :class:`ProgressError`——进度推进必须是显式的合法动作，不是 no-op。
    """

    nodes = _node_map(graph)
    validate_choice_state(progress, graph)
    if "ending_node_id" in progress:
        raise ProgressError(f"adventure has ended: {progress.get('ending_node_id')!r}")
    if node_id not in nodes:
        raise ProgressError(f"node does not exist: {node_id!r}")
    active = progress.setdefault("active_nodes", [])
    if node_id not in active:
        raise ProgressError(
            f"node is not active: {node_id!r} (active: {sorted(active)})"
        )
    node = nodes[node_id]
    transitions = node.get("transitions", [])
    if node.get("choice_mode") == "exclusive":
        selected = next((transition for transition in transitions if transition.get("choice_id") == choice_id), None)
        if selected is None:
            raise ProgressError("an explicit declared choice is required")
        try:
            satisfied = gates_satisfied(selected.get("conditions") or [], instance=instance, progress=progress, rules_evaluator=rules_evaluator)
        except GateError:
            satisfied = False
        if not satisfied:
            raise ProgressError("selected choice conditions are not satisfied")
        transitions = [selected]
    elif choice_id:
        raise ProgressError("parallel nodes do not accept a choice")
    active.remove(node_id)
    completed = progress.setdefault("completed_nodes", [])
    if node_id not in completed:
        completed.append(node_id)
    _append_history(progress, {"kind": "node_completed", "id": node_id})

    if node.get("choice_mode") == "exclusive":
        progress.setdefault("selected_choices", {})[node_id] = choice_id
        _append_history(progress, {"kind": "choice_selected", "id": node_id, "choice_id": choice_id})
        # Requests are advisory; resolution clears all requests for this node.
        progress["choice_requests"] = [request for request in progress.get("choice_requests", []) if request.get("node_id") != node_id]
    if node.get("type") == ENDING_NODE_TYPE:
        progress["ending_node_id"] = node_id
        progress["active_nodes"] = []
        progress["choice_requests"] = []
        _append_history(progress, {"kind": "ending_reached", "id": node_id})
        return []
    activated: list[str] = []
    for transition in transitions:
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


def complete_objective(
    progress: dict[str, Any], objective_id: str, *, graph: dict[str, Any],
) -> bool:
    """Complete one declared objective; unknown ids fail closed (§6.3)."""

    wanted = str(objective_id or "")
    declared = {
        str(item.get("id") or "")
        for item in graph.get("objectives", [])
        if isinstance(item, dict)
    }
    if wanted not in declared:
        raise ProgressError(f"objective does not exist: {wanted!r}")
    completed = progress.setdefault("completed_objectives", [])
    if wanted in completed:
        return False
    completed.append(wanted)
    _append_history(progress, {"kind": "objective_completed", "id": wanted})
    return True


def complete_milestone(
    progress: dict[str, Any], milestone_id: str, *, graph: dict[str, Any],
) -> bool:
    """Reach one declared milestone; unknown ids fail closed (§6.3)."""

    wanted = str(milestone_id or "")
    declared = {
        str(item.get("id") or "")
        for item in graph.get("milestones", [])
        if isinstance(item, dict)
    }
    if wanted not in declared:
        raise ProgressError(f"milestone does not exist: {wanted!r}")
    completed = progress.setdefault("completed_milestones", [])
    if wanted in completed:
        return False
    completed.append(wanted)
    _append_history(progress, {"kind": "milestone_reached", "id": wanted})
    return True


__all__ = [
    "MAX_HISTORY",
    "ProgressError",
    "complete_milestone",
    "complete_node",
    "complete_objective",
    "ending_node_id",
    "new_progress",
]
