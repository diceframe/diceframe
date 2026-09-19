"""Authoritative execution of adventure world seeds (FIX-05 §7.1).

母方案 §26/§27 的 **application 侧适配器**：把 ``src.adventures.materialization.
world_seed_ops`` 推导出的初始种子 op 经唯一写入口 ``apply_world_ops`` 提交，
并产出物化 receipt。

依赖方向（§7.1/§7.2）：World Runtime 自己**不认识 Adventure**，所以"定义层 → 世界
写入口"的桥必须留在 application seam（本模块）：

```text
src.adventures.materialization   （定义层：纯数据推导，只读）
        ↓
src.webui.services.adventure_materialization   ← 本模块（application adapter）
        ↓
src.engine.world_state.apply_world_ops          （唯一 authority 写入口）
```

- 本模块可以同时知道 Adventure Definition 与 World Ops；``src/engine/world*``
  不允许 import ``src.adventures``（architecture guard 强制）。
- **幂等**（母方案 §27 MUST）：以 canonical world entity id 为键，已存在的实体跳过；
  重放/重启后重复物化只补缺失的 id，不报错、不重复创建。
- **原子**（FIX-04 §6.6）：所有批次叠加到同一份 detached draft 上校验，全部通过后
  只提交一次；任何失败都不会改动世界。
- receipt 形状：``{adventure_id, content_digest, created_entity_ids,
  skipped_entity_ids}``。
"""

from __future__ import annotations

from typing import Any

from src.adventures.bundle import LoadedAdventureBundle
from src.adventures.materialization import MAX_OPS_PER_BATCH, world_seed_ops
from src.engine.world.read import (
    world_entities,
    world_facts,
    world_processes,
    world_relations,
)
from src.engine.world_state import apply_ops_to_state


def _source_round(instance: Any, explicit: int | None) -> int:
    if explicit is not None:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            return 0
    try:
        return int(getattr(instance, "round_number", 0) or 0)
    except (TypeError, ValueError):
        return 0


def materialize_world_seed(
    instance: Any,
    bundle: LoadedAdventureBundle,
    *,
    source_round: int | None = None,
) -> dict[str, Any]:
    """Materialize a bundle's initial world seed; idempotent and atomic."""

    existing = world_entities(instance.world_state)
    existing_relations = world_relations(instance.world_state)
    created: list[str] = []
    skipped: list[str] = []
    pending: list[dict[str, Any]] = []
    for op in world_seed_ops(bundle):
        # 幂等键：register_entity/add_relation 按 id 跳过已存在的；fact /
        # process 种子（set_fact/start_process）没有独立 id 身份，重放语义
        # 由 op 本身的幂等性保证（set_fact 幂等；start_process 重 id 拒绝）。
        if op["op"] == "register_entity":
            entity_id = str(op["entity_id"])
            if entity_id in existing:
                skipped.append(entity_id)
                continue
            pending.append(op)
        elif op["op"] == "add_relation":
            relation_id = str(op["relation_id"])
            if relation_id in world_relations(instance.world_state) or relation_id in existing_relations:
                skipped.append(relation_id)
                continue
            pending.append(op)
        elif op["op"] == "start_process":
            if str(op.get("process_id") or "") in world_processes(instance.world_state):
                skipped.append(str(op.get("process_id") or ""))
                continue
            pending.append(op)
        elif op["op"] == "set_fact":
            current = world_facts(instance.world_state).get(str(op.get("key") or ""))
            if (
                current is not None
                and current.get("value") == op.get("value")
                and str(current.get("visibility") or "public")
                == str(op.get("visibility") or "public")
            ):
                # 相同值/可见性的字段重放 = 无操作（幂等，不推进 revision）。
                skipped.append(str(op.get("key") or ""))
                continue
            pending.append(op)
        else:
            pending.append(op)

    # 不要逐批提交（"64 ops commit, 64 ops commit, 失败"会在世界里留下半个种子）。
    draft: dict[str, Any] | None = None
    for start in range(0, len(pending), MAX_OPS_PER_BATCH):
        batch = pending[start:start + MAX_OPS_PER_BATCH]
        draft, _summary = apply_ops_to_state(
            draft if draft is not None else instance.world_state,
            batch,
            source_round=_source_round(instance, source_round),
        )
        created.extend(
            str(op.get("entity_id") or op.get("relation_id") or op.get("key") or op.get("process_id") or "")
            for op in batch
        )
    if draft is not None:
        instance.world_state = draft

    return {
        "adventure_id": str(bundle.manifest.adventure_id),
        "content_digest": str(bundle.content_digest),
        "created_entity_ids": created,
        "skipped_entity_ids": skipped,
    }


__all__ = ["materialize_world_seed"]
