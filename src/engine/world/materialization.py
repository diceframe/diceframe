"""Authoritative execution of adventure world seeds (WR-09).

母方案 §26/§27 的 **engine 侧执行半区**：把
``src.adventures.materialization.world_seed_ops`` 推导出的初始种子 op 经
既有唯一写入口 ``apply_world_ops`` 提交，并产出物化 receipt。

- Adventure（定义层）不直接写 authority；只有这里（engine 侧显式适配器）
  触碰世界。
- **幂等**（母方案 §27 MUST）：以 canonical world entity id 为键，已存在的
  实体跳过；重放/重启后重复物化只补缺失的 id，不报错、不重复创建。
- receipt 形状：``{adventure_id, content_digest, created_entity_ids,
  skipped_entity_ids}``。
"""

from __future__ import annotations

from typing import Any

from src.adventures.bundle import LoadedAdventureBundle
from src.adventures.materialization import MAX_OPS_PER_BATCH, world_seed_ops
from src.engine.world_state import apply_world_ops, world_entities


def materialize_world_seed(
    instance: Any,
    bundle: LoadedAdventureBundle,
    *,
    source_round: int | None = None,
) -> dict[str, Any]:
    """Materialize a bundle's initial world seed; idempotent."""

    existing = world_entities(instance.world_state)
    created: list[str] = []
    skipped: list[str] = []
    pending: list[dict[str, Any]] = []
    for op in world_seed_ops(bundle):
        entity_id = str(op["entity_id"])
        if entity_id in existing:
            skipped.append(entity_id)
        else:
            pending.append(op)

    for start in range(0, len(pending), MAX_OPS_PER_BATCH):
        batch = pending[start:start + MAX_OPS_PER_BATCH]
        apply_world_ops(
            instance, batch,
            source_round=source_round,
        )
        created.extend(str(op["entity_id"]) for op in batch)

    return {
        "adventure_id": str(bundle.manifest.adventure_id),
        "content_digest": str(bundle.content_digest),
        "created_entity_ids": created,
        "skipped_entity_ids": skipped,
    }


__all__ = ["materialize_world_seed"]
