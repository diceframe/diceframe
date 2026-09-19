"""Adventure world-seed derivation (WR-09, 母方案 §27/§102).

本模块是 **纯函数侧** 的 materialization seam：把一个已加载的 Adventure
Bundle（v1 graph）推导为 WorldState v2 的初始 ``register_entity`` ops
（plain data）。它**不 import world authority、不写任何状态**——定义层
（Adventure）不直接触碰权威世界；应用的执行由 engine 侧的
``src.engine.world.materialization.materialize_world_seed`` 经既有唯一写入口
``apply_world_ops`` 完成（母方案 §26：定义声明，适配器转成 World Ops）。

边界：

- 只物化"世界里的存在"（npc / map_location）；encounter preset 的
  hp / armor_class 等机制数据属于 rules runtime（母方案 §187/§188），不进
  world 实体。
- Adventure v1 无位置拓扑：v1 bundle 不产生 relation ops；relation / fact /
  process 种子随 Adventure v2 world seed（ADV2-03）接入。
- world 侧 source_ref 统一为 ``adventure:<adventure_id>``：可追溯到来源包，
  且满足 world contracts 的 canonical 语法（不透传 bundle 内部含 "/" 的
  原始 ref）。
"""

from __future__ import annotations

from typing import Any

from src.adventures.bundle import LoadedAdventureBundle

# v1 bundle 实体 kind → world entity kind（只物化"世界中的存在"）。
_BUNDLE_WORLD_KINDS = {
    "npc": "npc",
    "map_location": "location",
}
_WORLD_ENTITY_PREFIX = {
    "npc": "npc",
    "map_location": "location",
}
# 与 world_state 单批上限一致：种子超过一批时由执行侧分批提交。
MAX_OPS_PER_BATCH = 64


def world_entity_id(bundle_kind: str, entity_id: str) -> str:
    """The canonical world entity id for one bundle entity."""

    prefix = _WORLD_ENTITY_PREFIX[bundle_kind]
    return f"{prefix}:{entity_id}"


def world_seed_ops(bundle: LoadedAdventureBundle) -> list[dict[str, Any]]:
    """Derive the deterministic initial world ops for a bundle.

    v1 bundle：只物化 npc / map_location 实体。v2 bundle：额外展开 adventure
    实体声明的 world_seed（entity / relation / fact / process 四类，ADV2-03）。
    种子是纯数据推导；合法性由物化时的唯一写入口把
    关（fail closed）。
    """

    source_ref = f"adventure:{bundle.manifest.adventure_id}"
    ops: list[dict[str, Any]] = []
    for bundle_kind, world_kind in _BUNDLE_WORLD_KINDS.items():
        for entity_id in (bundle.entities.get(bundle_kind) or {}):
            ops.append({
                "op": "register_entity",
                "entity_id": world_entity_id(bundle_kind, str(entity_id)),
                "kind": world_kind,
                "visibility": "public",
                "source_ref": source_ref,
            })
    adventures = bundle.entities.get("adventure") or {}
    if adventures:
        graph = next(iter(adventures.values()))
        seed = graph.get("world_seed") or {}
        for entity in seed.get("entities") or []:
            ops.append({"op": "register_entity", **entity, "source_ref": entity.get("source_ref") or source_ref})
        for relation in seed.get("relations") or []:
            ops.append({"op": "add_relation", **relation, "source_ref": relation.get("source_ref") or source_ref})
        for fact in seed.get("facts") or []:
            ops.append({"op": "set_fact", **fact})
        for process in seed.get("processes") or []:
            ops.append({"op": "start_process", **process, "source_ref": process.get("source_ref") or source_ref})
    return ops


__all__ = ["MAX_OPS_PER_BATCH", "world_entity_id", "world_seed_ops"]
