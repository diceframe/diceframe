"""Adventure v2 application runtime (FIX-04 §6.4–§6.8, 母方案 §23/§120/§122).

把"Graph + Gates + Progress helpers"变成**可运行的冒险**：创建事务内的初始化、
节点完成的权威事务、进度门控推进、以及时间推进后的后果结算。

```text
initialize_adventure_run   §6.5/§6.6  进度初始化 + 世界种子原子物化（创建事务内）
complete_adventure_node    §6.4/§6.7  完成节点 → gate 评估 → outcome 权威事务
advance_adventure_world    §6.8      逻辑时间推进后（进程 due_at 结算）重新评估 gate
```

硬边界：

- Adventure 定义层不写 authority：world ops 只经 ``apply_world_ops``，奖励只经
  注入的 reward sink（既有 proposal 权威），进度只写 ``instance.adventure_progress``；
- **全有或全无**（§6.7）：任一环节失败 → 恢复进度与世界状态的 before-image；
- v2 进度与 v1 campaign 并存互不迁移（§6.9/母方案 §71）：本模块只在绑定为 v2
  图上工作，v1 路径完全不变。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.adventures.graph_v2 import (
    ADVENTURE_GRAPH_FORMAT_V2,
    AdventureGraphV2Error,
    validate_graph_v2,
)
from src.adventures.outcomes import OutcomeError, outcomes_to_intents
from src.adventures.progress import (
    ProgressError,
    complete_milestone,
    complete_node,
    complete_objective,
    new_progress,
)

logger = logging.getLogger("trpg")

class AdventureRuntimeError(ValueError):
    """An adventure runtime operation is invalid: fail closed."""


@dataclass(frozen=True)
class AdventureRuntimeDependencies:
    """Explicit application seams; every authority stays outside this module."""

    # binding → source-aware resolution（FIX-02 的唯一 resolver）
    resolve_binding: Callable[[Any], Any]
    # 世界种子物化的 engine 侧适配器（§7.1 后属于 application adapter）
    materialize_world_seed: Callable[[Any, Any], dict[str, Any]]
    # rules.* gate 的 runtime adapter（§6.4：由 D&D Runtime 判断）
    rules_evaluator: Callable[[Any], Callable[[str, str, Any], bool] | None] | None = None
    # item_reward → reward intent 的 runtime 转换器（DNDMOD-03）
    reward_converter: Callable[[Any], Callable[[Any, str, str], dict[str, Any]] | None] | None = None
    # reward intent 的权威出口（既有 proposal 权威）；缺席时含 item_reward 的
    # 节点完成必须 fail closed，绝不"世界改了、奖励没发"。
    queue_reward_intents: Callable[[Any, list[dict[str, Any]]], list[dict[str, Any]]] | None = None
    save_instance: Callable[[Any], Awaitable[None]] | None = None


def adventure_graph(resolution: Any) -> dict[str, Any] | None:
    """The validated v2 graph for a resolved package, or None for v1 packages."""

    bundle = getattr(resolution, "bundle", resolution)
    manifest = getattr(bundle, "manifest", None)
    if manifest is None or str(manifest.format) != ADVENTURE_GRAPH_FORMAT_V2:
        return None
    try:
        return validate_graph_v2(bundle.adventure)
    except AdventureGraphV2Error as exc:
        raise AdventureRuntimeError(f"adventure graph is invalid: {exc}") from exc


def resolve_instance_adventure(
    dependencies: AdventureRuntimeDependencies, instance: Any,
) -> Any | None:
    """Resolve the instance's bound package, or None when unbound."""

    binding = getattr(instance, "adventure_binding", None)
    if not isinstance(binding, dict) or not binding.get("adventure_id"):
        return None
    return dependencies.resolve_binding(instance)


def initialize_adventure_run(
    dependencies: AdventureRuntimeDependencies, instance: Any,
) -> dict[str, Any]:
    """§6.5/§6.6：创建事务内的 v2 初始化（进度 + 原子世界种子）。

    调用方持有创建事务：本函数只改内存聚合（进度、世界状态），不做 save。
    任何失败都不会留下半个世界（种子物化是单次提交）或半个进度（进度在种子
    成功之后才写入）。
    """

    resolution = resolve_instance_adventure(dependencies, instance)
    if resolution is None:
        return {"ok": True, "initialized": False, "reason": "unbound"}
    graph = adventure_graph(resolution)
    if graph is None:
        # v1：走既有 campaign/ruleset 路径，行为不变。
        return {"ok": True, "initialized": False, "reason": "v1"}
    progress = new_progress(graph)
    receipt = dependencies.materialize_world_seed(instance, resolution.bundle)
    instance.adventure_progress = progress
    return {
        "ok": True,
        "initialized": True,
        "adventure_id": graph.get("id"),
        "active_nodes": list(progress["active_nodes"]),
        "world_seed": dict(receipt),
    }


def _apply_progress_events(
    progress: dict[str, Any], graph: dict[str, Any], events: list[dict[str, Any]],
) -> list[str]:
    activated: list[str] = []
    for event in events:
        kind = str(event.get("kind") or "")
        event_id = str(event.get("id") or "")
        if kind == "node_completed":
            activated.extend(complete_node(progress, graph, event_id))
        elif kind == "objective_completed":
            complete_objective(progress, event_id, graph=graph)
        elif kind == "milestone_reached":
            complete_milestone(progress, event_id, graph=graph)
        else:  # pragma: no cover - validate_outcome 已封闭词表
            raise AdventureRuntimeError(f"progress event kind is invalid: {kind!r}")
    return activated


def complete_adventure_node(
    dependencies: AdventureRuntimeDependencies,
    instance: Any,
    node_id: str,
    *,
    recipient_uid: str = "",
    actor_uid: str = "",
) -> dict[str, Any]:
    """§6.7：完成一个节点，在**一个权威事务**里执行它的 outcome 声明。

    顺序：校验 transition/progress → 准备 outcomes → 应用 world ops → 进度事件
    → 奖励 intent 出口；任一失败恢复进度与世界状态的 before-image（全有或全无）。
    """

    resolution = resolve_instance_adventure(dependencies, instance)
    if resolution is None:
        raise AdventureRuntimeError("game has no bound adventure")
    graph = adventure_graph(resolution)
    if graph is None:
        raise AdventureRuntimeError("bound adventure is not an Adventure v2 graph")
    wanted = str(node_id or "")
    node = next(
        (item for item in graph.get("nodes", []) if item.get("id") == wanted), None,
    )
    if node is None:
        raise AdventureRuntimeError(f"node does not exist: {wanted!r}")

    progress = getattr(instance, "adventure_progress", None)
    if not isinstance(progress, dict) or not progress.get("active_nodes"):
        if not isinstance(progress, dict) or not progress:
            raise AdventureRuntimeError("adventure progress is not initialized")
    # 先做只读的合法性检查：非法推进不得产生任何 world op 副作用。
    if wanted not in {str(item) for item in progress.get("active_nodes") or []}:
        raise ProgressError(
            f"node is not active: {wanted!r} "
            f"(active: {sorted(str(item) for item in progress.get('active_nodes') or [])})"
        )

    evaluator = (
        dependencies.rules_evaluator(instance)
        if callable(dependencies.rules_evaluator) else None
    )
    converter = (
        dependencies.reward_converter(instance)
        if callable(dependencies.reward_converter) else None
    )
    # 裸 ContentRef（``item:brass_key``）的默认来源**由 runtime 转换器决定**
    # （DNDMOD-03 知道本实例的 catalog 来源身份：owning module / adventure
    # local）。这里如果按**绑定的来源身份**（plugin/user/builtin）拼一个 source，
    # 既不属于 ContentRef 的 source 词表（core/module/adventure/world/...），又
    # 会盖掉 runtime 的正确默认值，让最自然的裸 ref 写法整体 fail closed。
    # 传空串 = 把"默认归属哪个来源"交回 runtime；显式来源的 ref 不受影响。
    content_source = ""
    # ① 准备 outcomes（纯声明层；未知类型/字段 fail closed）
    intents = outcomes_to_intents(
        node.get("on_complete"),
        reward_converter=converter,
        default_source=content_source,
        recipient_uid=recipient_uid,
    )
    if intents["reward_intents"] and dependencies.queue_reward_intents is None:
        raise AdventureRuntimeError(
            "item_reward outcome requires the authoritative reward sink"
        )

    before_progress = deepcopy(progress)
    before_world = deepcopy(getattr(instance, "world_state", None))
    # ``item_reward`` is queued by the Economy authority.  It is still part of
    # this aggregate transaction: a failed queue must not leave a pending
    # proposal behind after the graph/world changes have been compensated.
    before_economy = deepcopy(getattr(instance, "economy", None))
    activated: list[str] = []
    try:
        # ② 节点完成的后果先落世界（§6.4/§6.7）：gate 评估必须看到本次完成的
        #    效果——"开锁 → 门开了 → 进入下一间"是同一时刻的因果顺序。
        world_summary: dict[str, Any] | None = None
        if intents["world_ops"]:
            from src.engine.world_state import apply_world_ops

            world_summary = apply_world_ops(instance, intents["world_ops"])
        # ③ 进度推进（含 gate 评估 → 开放合法后继）
        activated = complete_node(
            progress, graph, wanted, instance=instance, rules_evaluator=evaluator,
        )
        # ④ 进度事件（同一事务内）
        activated.extend(_apply_progress_events(
            progress, graph, intents["progress_events"],
        ))
        # ⑤ 奖励 intent 走既有权威出口
        queued_rewards = (
            dependencies.queue_reward_intents(instance, intents["reward_intents"])
            if intents["reward_intents"] and dependencies.queue_reward_intents
            else []
        )
        instance.adventure_progress = progress
    except (ProgressError, OutcomeError, AdventureRuntimeError):
        instance.adventure_progress = before_progress
        if before_world is not None:
            instance.world_state = before_world
        if before_economy is not None:
            instance.economy = before_economy
        raise
    except Exception:
        instance.adventure_progress = before_progress
        if before_world is not None:
            instance.world_state = before_world
        if before_economy is not None:
            instance.economy = before_economy
        raise

    return {
        "ok": True,
        "node_id": wanted,
        "activated_nodes": activated,
        "world_ops": len(intents["world_ops"]),
        "world_summary": world_summary,
        "progress_events": list(intents["progress_events"]),
        "reward_intents": list(intents["reward_intents"]),
        "queued_rewards": list(queued_rewards or []),
        "actor_uid": str(actor_uid or ""),
    }


def advance_adventure_world(
    dependencies: AdventureRuntimeDependencies, instance: Any,
) -> dict[str, Any]:
    """§6.8：逻辑时间推进（进程 due_at 结算）后重新评估 gate。

    GM 的秘密进程（例如 ``start_process`` 的 due_at）由 engine 在时间推进时结算为
    ``completed``；本函数随后重新评估**已完成节点**的出边，把现在才满足 gate 的
    后继节点开放——这正是"进程完成后公开后果"的表达方式（不发明新字段：
    gate 词表已有 ``world.process_status``，后继节点的 ``on_complete`` 声明后果）。

    幂等：已 active / 已完成的目标是 no-op；无 v2 绑定时直接返回。
    """

    resolution = resolve_instance_adventure(dependencies, instance)
    if resolution is None:
        return {"ok": True, "advanced": False, "reason": "unbound"}
    graph = adventure_graph(resolution)
    if graph is None:
        return {"ok": True, "advanced": False, "reason": "v1"}
    progress = getattr(instance, "adventure_progress", None)
    if not isinstance(progress, dict) or not progress:
        raise AdventureRuntimeError("adventure progress is not initialized")

    from src.adventures.gates import GateError, gates_satisfied

    active = list(progress.get("active_nodes") or [])
    completed = {str(item) for item in progress.get("completed_nodes") or []}
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    opened: list[str] = []
    for node_id in sorted(completed):
        node = nodes.get(node_id)
        if node is None:
            continue
        for transition in node.get("transitions", []):
            target = str(transition.get("to") or "")
            if not target or target in active or target in completed:
                continue
            try:
                satisfied = gates_satisfied(
                    transition.get("conditions") or [],
                    instance=instance,
                    progress=progress,
                    rules_evaluator=(
                        dependencies.rules_evaluator(instance)
                        if callable(dependencies.rules_evaluator) else None
                    ),
                )
            except GateError:
                satisfied = False
            if not satisfied:
                continue
            active.append(target)
            opened.append(target)
            progress.setdefault("history", []).append({
                "kind": "node_activated", "id": target, "reason": "gate_satisfied",
            })
    if opened:
        progress["active_nodes"] = active
    return {"ok": True, "advanced": True, "activated_nodes": opened}


__all__ = [
    "AdventureRuntimeDependencies",
    "AdventureRuntimeError",
    "advance_adventure_world",
    "adventure_graph",
    "complete_adventure_node",
    "initialize_adventure_run",
    "resolve_instance_adventure",
]
