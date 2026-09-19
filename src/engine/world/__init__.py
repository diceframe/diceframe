"""World Runtime v2 package: contracts, ids, ops, projection, migration.

`src/engine/world/` 是 World Runtime v2 的实现包（母方案 00_MASTER_PLAN §84）。
它继承 WorldState v1 的既有边界：

- ``GameInstance.world_state`` 仍是当前世界唯一 authority 的载体；
- 唯一写入口仍是 world ops（先 v1 的 ``apply_world_ops``，v2 在其上扩展）；
- 无后台 tick、无全局 EventBus、无万能 DSL、不执行模组代码；
- HP / AC / spell slots / wallet / combat initiative 永远不进 World Runtime
  （它们属于 rules runtime / economy）。

既有 ``src/engine/world_state.py`` / ``world_events.py`` / ``world_legality.py``
保持原位继续工作；新实现按 PR 逐步落进本包，不强制一次性搬完旧文件。
"""

from src.engine.world.contracts import (
    ENTITY_KINDS,
    ENTITY_STATUSES,
    EVENT_OP_KINDS,
    EVENT_STATUSES,
    PROCESS_STATUSES,
    RELATION_KINDS,
    RELATION_STATUSES,
    SOURCE_REF_KINDS,
    VISIBILITIES,
    WORLD_EVENT_KINDS,
    WorldContractError,
    canonical_id,
    validate_entity_record,
    validate_process_record,
    validate_relation_record,
    validate_source_ref,
    validate_world_event_record,
)

__all__ = [
    "ENTITY_KINDS",
    "ENTITY_STATUSES",
    "EVENT_OP_KINDS",
    "EVENT_STATUSES",
    "PROCESS_STATUSES",
    "RELATION_KINDS",
    "RELATION_STATUSES",
    "SOURCE_REF_KINDS",
    "VISIBILITIES",
    "WORLD_EVENT_KINDS",
    "WorldContractError",
    "canonical_id",
    "validate_entity_record",
    "validate_process_record",
    "validate_relation_record",
    "validate_source_ref",
    "validate_world_event_record",
]
